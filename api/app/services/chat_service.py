"""问答业务服务：SSE 流式对话（方案B 工具编排）。

流程：加载默认对话模型 + Agent 配置 → 构建工具（知识库/记忆/联网，按开关）
→ 强模型走原生 function calling / 弱模型走 ReAct → 流式产出 token/工具标记/引用
→ 落库 user/assistant 消息（assistant 带引用与工具调用元信息）
→ 回答后异步派发记忆萃取（对话自动萃取）。
"""
import asyncio
import json
import re
import uuid
from collections.abc import AsyncGenerator

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.core.agent.orchestrator import run_function_calling, run_react
from app.core.agent.tools import build_enabled_tools
from app.core.agent.tracing import get_tracer
from app.core.realtime import durable_stream
from app.core.llm.chat_model import (
    CAP_VISION,
    build_chat_model,
    build_default_chat_model,
    get_default_config_for_type,
    supports_function_call,
)
from app.core.logging import get_logger
from app.core.storage import get_storage
from app.db.postgres import SessionLocal
from app.db.redis import get_redis
from app.models.agent_config_model import AgentConfig
from app.models.conversation_model import (
    ROLE_ASSISTANT,
    ROLE_USER,
    Conversation,
    Message,
)
from app.repositories.agent_config_repository import AgentConfigRepository
from app.repositories.agent_persona_repository import AgentPersonaRepository
from app.repositories.conversation_repository import (
    ConversationRepository,
    MessageRepository,
)
from app.repositories.skill_repository import SkillRepository
from app.schemas.chat_schema import ChatStreamRequest

logger = get_logger(__name__)

MAX_HISTORY_TURNS = 20

# 后台生成任务引用集合（防止 create_task 的任务被 GC 提前回收）
_BG_TASKS: set = set()
# 持久化 token 先聚合成小文本块，兼顾流式延迟与 PostgreSQL 写放大。
_DURABLE_TOKEN_CHARS = 48


# ── 主动召回优化：用户级温热缓存（始终注入 + 后台刷新）──────────────
# 召回（embedding + 图查询）原本每轮同步执行，直接加在首字延迟上。改为：
#   1) 缓存「我对用户的了解」(按 user_id)，**每一轮都注入**——闲聊/首轮/新会话都认识你；
#   2) 命中缓存时 0 阻塞注入；仅实质消息（非寒暄）才在后台用本轮问题刷新缓存；
#   3) 进程内该用户首次无缓存时同步算一次（仅这一次阻塞），之后永远温热。
# 记忆内容（洞察+事实）本就稳定，滞后一轮刷新对体验几乎无感，却省掉每轮的召回延迟。
_recall_cache: dict[str, str] = {}  # user_id -> 上次算好的「对用户的了解」召回块
_RECALL_CACHE_MAX = 2000  # 软上限，超过淘汰最早插入的用户
_RECALL_CACHE_TTL_SECONDS = 900

# 纯寒暄 / 应答类短消息（整句匹配才算）：仍注入缓存，但不触发后台重算
_GREETING_RE = re.compile(
    r"^(在吗|在不在|你好啊?|您好|hi|hello|嗨|哈喽|哈罗|早|早安|午安|晚安|晚上好|"
    r"早上好|中午好|下午好|好的?|好滴|行|可以|嗯+|哦+|噢+|额|啊+|哈+|呵+|嘿+|"
    r"拜拜|再见|谢谢|多谢|蟹蟹|thanks?|ok|okay)[。.!！?？~～\s]*$",
    re.IGNORECASE,
)


def _is_trivial_for_recall(text: str) -> bool:
    """寒暄 / 超短消息：不触发后台重算（但仍注入已有缓存）。长度 ≤2 或整句命中寒暄词。"""
    t = (text or "").strip()
    if len(t) <= 2:
        return True
    return bool(_GREETING_RE.match(t))


async def _recall_cache_get(user_id: str) -> str | None:
    try:
        cached = await get_redis().get(f"active-recall:v1:{user_id}")
        if cached is not None:
            return cached
    except Exception as exc:
        logger.warning("Redis 主动召回缓存读取失败，使用进程回退: %s", exc)
    return _recall_cache.get(user_id)


async def _recall_cache_set(user_id: str, text: str) -> None:
    """Write through Redis, retaining a bounded in-process fallback for outages."""
    if user_id not in _recall_cache and len(_recall_cache) >= _RECALL_CACHE_MAX:
        oldest = next(iter(_recall_cache), None)
        if oldest is not None:
            _recall_cache.pop(oldest, None)
    _recall_cache[user_id] = text
    try:
        await get_redis().setex(
            f"active-recall:v1:{user_id}", _RECALL_CACHE_TTL_SECONDS, text
        )
    except Exception as exc:
        logger.warning("Redis 主动召回缓存写入失败，保留进程回退: %s", exc)


def _spawn_bg(coro) -> None:
    """调度后台任务并持有引用，完成后自动移除（防被 GC 取消）。"""
    task = asyncio.create_task(coro)
    _BG_TASKS.add(task)
    task.add_done_callback(_BG_TASKS.discard)


def _compose_with_attachments(user_text: str, attachments: list) -> str:
    """把对话临时附件的全文拼到用户问题前，供模型阅读。

    attachments 元素为 {file_name, text}（schema ChatAttachment 或历史 meta_data）。
    无附件时原样返回。
    """
    if not attachments:
        return user_text
    parts: list[str] = []
    for att in attachments:
        name = att.get("file_name") if isinstance(att, dict) else getattr(att, "file_name", "")
        text = att.get("text") if isinstance(att, dict) else getattr(att, "text", "")
        if not text:
            continue
        parts.append(f"【用户上传的文档「{name}」内容如下】\n{text}\n【文档结束】")
    if not parts:
        return user_text
    return "\n\n".join(parts) + f"\n\n基于以上文档内容，回答我的问题：\n{user_text}"


def _sse(event: str, data: dict) -> str:
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"


def _number_citations(citations: list[dict]) -> list[dict]:
    for index, citation in enumerate(citations, start=1):
        citation["citation_index"] = index
    return citations


class ChatService:
    def __init__(self, session: AsyncSession):
        self.session = session
        self.conv_repo = ConversationRepository(session)
        self.msg_repo = MessageRepository(session)
        self.agent_repo = AgentConfigRepository(session)
        self.persona_repo = AgentPersonaRepository(session)
        self.skill_repo = SkillRepository(session)

    @staticmethod
    def _user_meta(attachments: list, image_keys: list[str]) -> dict | None:
        """组装 user 消息的 meta_data：对话附件 + 图片 key（供历史还原与分享）。"""
        meta: dict = {}
        if attachments:
            meta["attachments"] = attachments
        if image_keys:
            meta["image_keys"] = list(image_keys)
        return meta or None

    async def _ensure_conversation(
        self, user_id: uuid.UUID, body: ChatStreamRequest
    ) -> Conversation:
        if body.conversation_id:
            conv = await self.conv_repo.get(user_id, body.conversation_id)
            if conv:
                return conv
        title = body.message.strip()[:20] or "新对话"
        if body.project_id:
            from app.services.project_service import ProjectService
            await ProjectService(self.session).get_owned(user_id, body.project_id)
        return await self.conv_repo.create(Conversation(user_id=user_id, title=title, project_id=body.project_id))

    async def _history_messages(
        self, conv_id: uuid.UUID, user_id: uuid.UUID
    ) -> tuple[list, bool]:
        """历史消息转 LangChain 消息（不含 system 与当前问题）。

        当前问题会在主流程单独追加，故这里丢弃末尾那条 user 消息（即本轮刚落库的提问），
        避免当前问题（含附件全文）在 prompt 中重复出现。
        若某条历史 user 消息带对话附件（meta_data.attachments），把附件全文还原进
        该轮 HumanMessage，使后续追问在历史窗口内仍能看到文档内容。
        """
        history = await self.msg_repo.recent_history(conv_id, MAX_HISTORY_TURNS)
        # 丢弃末尾连续的 user 消息（本轮提问），它由主流程单独追加
        while history and history[-1].role == ROLE_USER:
            history.pop()
        history_has_images = any(
            bool((m.meta_data or {}).get("image_keys"))
            for m in history
            if m.role == ROLE_USER
        )
        image_parts_by_message: dict[uuid.UUID, list[dict]] = {}
        if history_has_images:
            remaining_bytes = settings.vision_history_image_budget_bytes
            remaining_count = settings.vision_history_image_max_count
            # 优先保留最近的历史图片，再恢复为原消息顺序。
            for message in reversed(history):
                if message.role != ROLE_USER or remaining_count <= 0 or remaining_bytes <= 0:
                    continue
                keys = list((message.meta_data or {}).get("image_keys") or [])
                parts, spent = await self._load_vision_image_parts(
                    user_id,
                    keys,
                    byte_budget=remaining_bytes,
                    max_count=remaining_count,
                )
                if parts:
                    image_parts_by_message[message.id] = parts
                    remaining_bytes -= spent
                    remaining_count -= len(parts)

        out: list = []
        for m in history:
            if m.role == ROLE_USER:
                atts = (m.meta_data or {}).get("attachments") if m.meta_data else None
                content = _compose_with_attachments(m.content, atts or [])
                parts = image_parts_by_message.get(m.id, [])
                out.append(
                    HumanMessage(
                        content=[{"type": "text", "text": content}, *parts]
                        if parts
                        else content
                    )
                )
            elif m.role == ROLE_ASSISTANT:
                out.append(AIMessage(content=m.content))
        return out, history_has_images

    async def _load_vision_image_parts(
        self,
        user_id: uuid.UUID,
        image_keys: list[str],
        *,
        byte_budget: int,
        max_count: int,
    ) -> tuple[list[dict], int]:
        """按用户归属读取图片，在压缩后字节预算内构造多模态内容块。"""
        import base64
        from pathlib import Path

        from app.core.rag.chat_images import validate_chat_image_keys
        from app.core.rag.image_compress import compress_for_vision

        storage = get_storage()
        parts: list[dict] = []
        spent = 0
        for key in image_keys[:max_count]:
            try:
                await validate_chat_image_keys(user_id, [key])
                raw = await storage.get(key)
                data, mime = compress_for_vision(raw, Path(key).suffix)
                if spent + len(data) > byte_budget:
                    continue
                b64 = base64.b64encode(data).decode()
                parts.append(
                    {
                        "type": "image_url",
                        "image_url": {"url": f"data:{mime};base64,{b64}"},
                    }
                )
                spent += len(data)
            except Exception as exc:
                logger.warning("历史图片读取失败（跳过 %s）: %s", key, exc)
        return parts, spent

    async def _cross_session_context(
        self, user_id: uuid.UUID, current_conv_id: uuid.UUID
    ) -> str:
        """跨会话上下文：取最近其他会话的标题 + 最后几轮，拼成参考背景块。

        纯 PG 查询（快），失败/无其他会话返回空串。
        """
        try:
            from app.config import settings as _s
            from app.models.conversation_model import ROLE_ASSISTANT, ROLE_USER

            convs = await self.conv_repo.list_by_user(user_id)
            others = [c for c in convs if c.id != current_conv_id][
                : _s.cross_session_max_convs
            ]
            if not others:
                return ""
            blocks: list[str] = []
            for c in others:
                msgs = await self.msg_repo.recent_history(
                    c.id, _s.cross_session_turns_per_conv
                )
                if not msgs:
                    continue
                lines = [f"〔会话「{c.title}」〕"]
                for m in msgs:
                    if m.role == ROLE_USER:
                        lines.append(f"用户：{(m.content or '').strip()}")
                    elif m.role == ROLE_ASSISTANT:
                        lines.append(f"我：{(m.content or '').strip()}")
                blocks.append("\n".join(lines))
            if not blocks:
                return ""
            block = "【最近你还和我聊过（仅供参考，不必主动提起）】\n" + "\n\n".join(blocks)
            if len(block) > _s.cross_session_max_chars:
                block = block[: _s.cross_session_max_chars] + "…"
            logger.info("跨会话上下文注入: user=%s 会话数=%d", user_id, len(blocks))
            return block
        except Exception as e:
            logger.warning("跨会话上下文构建失败（忽略）: user=%s err=%s", user_id, e)
            return ""

    async def _recall_memory(
        self, user_id: uuid.UUID, query: str, session: AsyncSession | None = None
    ) -> str:
        """主动记忆召回：失败不影响对话，返回空串。

        session 为空时用请求 session；后台刷新任务传入独立 session（请求已结束）。
        """
        sess = session or self.session
        try:
            from app.core.llm.resolver import get_optional_client_for_type
            from app.core.memory.retrieval.active_recall import recall_context

            embed_client = await get_optional_client_for_type(
                sess, user_id, "embedding"
            )
            if embed_client is None:
                return ""
            return await recall_context(
                embed_client=embed_client, user_id=user_id, query=query
            )
        except Exception as e:
            logger.warning("主动记忆召回失败（忽略）: user=%s err=%s", user_id, e)
            return ""

    async def _recall_lagged(self, user_id: uuid.UUID, query: str) -> str:
        """用户级温热召回：始终注入「对用户的了解」（含闲聊/首轮），实质消息后台刷新。

        - 命中缓存：0 阻塞返回，非寒暄消息再后台刷新供下次更新；
        - 进程内该用户首次无缓存：同步算一次（仅这一次阻塞），保证首轮就有记忆。
        """
        uid = str(user_id)
        cached = await _recall_cache_get(uid)
        if cached is None:
            text = await self._recall_memory(user_id, query)
            await _recall_cache_set(uid, text or "")
            return text or ""
        # 已有缓存：始终注入（闲聊/首轮/新会话都认识你），实质消息再后台刷新
        if not _is_trivial_for_recall(query):
            _spawn_bg(self._refresh_recall_bg(user_id, query))
        return cached

    async def _refresh_recall_bg(self, user_id: uuid.UUID, query: str) -> None:
        """后台用独立 session 重算召回，更新用户级缓存供后续轮使用。"""
        try:
            async with SessionLocal() as session:
                text = await self._recall_memory(user_id, query, session=session)
            await _recall_cache_set(str(user_id), text or "")
        except Exception as e:  # noqa: BLE001
            logger.warning("后台主动召回刷新失败（忽略）: user=%s err=%s", user_id, e)

    @staticmethod
    def _compose_system_prompt(persona, skill) -> str:
        """组装 system prompt：角色卡人设 + 技能任务提示词 + few-shot 示例（叠加）。

        角色卡定「我是谁」，技能叠加「我现在干什么专项任务」。两者可组合。
        开启真人模式（persona.human_mode）则再叠加「真人聊天风格」段，让回复口语化、可多气泡。
        """
        parts: list[str] = []
        persona_prompt = (persona.system_prompt.strip() if persona else "") or ""
        if persona_prompt:
            parts.append(persona_prompt)
        if skill:
            skill_prompt = (skill.prompt or "").strip()
            if skill_prompt:
                parts.append(f"【当前任务能力：{skill.name}】\n{skill_prompt}")
            # few-shot 示例拼进提示词，稳定该技能输出风格
            few_shots = (skill.config or {}).get("few_shots") or []
            examples: list[str] = []
            for fs in few_shots:
                if not isinstance(fs, dict):
                    continue
                inp = (fs.get("input") or "").strip()
                out = (fs.get("output") or "").strip()
                if inp and out:
                    examples.append(f"示例输入：\n{inp}\n理想输出：\n{out}")
            if examples:
                parts.append("参考以下示例的风格作答：\n\n" + "\n\n".join(examples))
        return "\n\n".join(parts)

    async def _tool_scope(
        self, user_id: uuid.UUID, body: ChatStreamRequest, skill=None
    ) -> tuple[dict[str, bool], list[str] | None]:
        """计算本轮工具的 overrides（启停覆盖）与知识库检索范围 kb_ids。

        - 对话页本轮临时开关（联网/知识库/记忆）作为 override，优先级最高。
        - 技能 tool_keys 非空 → 工具白名单（只开白名单内的）。
        - 知识库范围：技能绑库优先，否则取用户「已启用检索」的库集合。
        """
        overrides: dict[str, bool] = {}
        if body.enable_knowledge is not None:
            overrides["knowledge_search"] = body.enable_knowledge
        if body.enable_memory is not None:
            overrides["memory_search"] = body.enable_memory
        if body.enable_web_search is not None:
            overrides["web_search"] = body.enable_web_search

        if skill and (skill.tool_keys or []):
            from app.core.agent.tools.base import BUILTIN_REGISTRY

            whitelist = set(skill.tool_keys)
            for key in BUILTIN_REGISTRY:
                overrides[key] = key in whitelist

        from app.repositories.knowledge_base_repository import (
            KnowledgeBaseRepository,
        )

        if skill and skill.kb_id:
            kb_ids: list[str] | None = [str(skill.kb_id)]
        else:
            kb_ids = await KnowledgeBaseRepository(self.session).list_chat_enabled_ids(
                user_id
            )
        return overrides, kb_ids

    async def _build_tools(
        self,
        user_id: uuid.UUID,
        agent: AgentConfig | None,
        body: ChatStreamRequest,
        citations: list[dict],
        stats_holder: dict[str, dict],
        skill=None,
    ) -> list:
        """构建启用的工具列表（无状态 MCP 版本，保留备用）。

        工具启停统一由「工具配置页」(tool_configs) 管理，这里不再读 agent 的工具开关；
        仅把对话页本轮的临时开关（如联网）作为 override 传入，优先级最高。
        """
        overrides, kb_ids = await self._tool_scope(user_id, body, skill)
        return await build_enabled_tools(
            self.session,
            user_id,
            citations,
            overrides,
            stats_holder=stats_holder,
            kb_ids=kb_ids,
        )

    async def stream_chat(
        self, user_id: uuid.UUID, body: ChatStreamRequest, skip_user_message: bool = False
    ) -> AsyncGenerator[str, None]:
        """启动后台问答，并从 PostgreSQL 事件日志转发可恢复的 SSE。"""
        user_text = body.message.strip()
        attachments = [
            {"file_name": a.file_name, "text": a.text}
            for a in body.attachments
            if a.text
        ]
        try:
            if body.image_keys:
                from app.core.rag.chat_images import validate_chat_image_keys

                await validate_chat_image_keys(user_id, body.image_keys)
            async with SessionLocal() as session:
                svc = ChatService(session)
                conv = await svc._ensure_conversation(user_id, body)
                cid = str(conv.id)
                title = conv.title
                if not skip_user_message:
                    greeting = (body.greeting or "").strip()
                    if greeting and await svc.msg_repo.count(conv.id) == 0:
                        await svc.msg_repo.add(
                            Message(
                                conversation_id=conv.id,
                                role=ROLE_ASSISTANT,
                                content=greeting,
                            )
                        )
                    await svc.msg_repo.add(
                        Message(
                            conversation_id=conv.id,
                            role=ROLE_USER,
                            content=user_text,
                            meta_data=self._user_meta(attachments, body.image_keys),
                        )
                    )
        except Exception as exc:
            yield _sse("error", {"message": str(exc)})
            return

        try:
            run, claimed = await durable_stream.claim_run(
                stream_type="chat", stream_key=cid, user_id=user_id
            )
            if claimed:
                await durable_stream.append_event(
                    run.id,
                    "meta",
                    {"conversation_id": cid, "title": title, "run_id": str(run.id)},
                )
                task = asyncio.create_task(
                    self._run_chat_turn_durable(
                        user_id,
                        uuid.UUID(cid),
                        body,
                        attachments,
                        skip_user_message,
                        run.id,
                    )
                )
                _BG_TASKS.add(task)
                task.add_done_callback(_BG_TASKS.discard)
        except Exception as exc:
            yield _sse("error", {"message": f"创建对话流失败：{exc}"})
            return
        async for chunk in self._relay_durable(run.id):
            yield chunk

    async def resume_events(
        self, user_id: uuid.UUID, conv_id: uuid.UUID, after_event_id: int = 0
    ) -> AsyncGenerator[str, None]:
        """按 Last-Event-ID 从持久化日志精确续传。"""
        async with SessionLocal() as session:
            conv = await ConversationRepository(session).get(user_id, conv_id)
        if not conv:
            yield _sse("error", {"message": "会话不存在"})
            return
        run = await durable_stream.latest_run(
            stream_type="chat", stream_key=str(conv_id), user_id=user_id
        )
        if run is None or run.status != "running":
            yield _sse("idle", {})
            return
        if after_event_id > 0:
            snapshot = await durable_stream.resume_snapshot(run.id, after_event_id)
            yield _sse("resume", snapshot)
        async for chunk in self._relay_durable(run.id, after_event_id):
            yield chunk

    async def _relay_durable(
        self, run_id: uuid.UUID, after_event_id: int = 0
    ) -> AsyncGenerator[str, None]:
        async for envelope in durable_stream.iter_events(
            run_id, after_id=after_event_id
        ):
            yield ": ping\n\n" if envelope is None else durable_stream.sse(envelope)

    async def _run_chat_turn_durable(
        self,
        user_id: uuid.UUID,
        conv_id: uuid.UUID,
        body: ChatStreamRequest,
        attachments: list[dict],
        skip_user_message: bool,
        run_id: uuid.UUID,
    ) -> None:
        cid = str(conv_id)
        user_text = body.message.strip()
        full_text = ""
        pending_tokens = ""
        tool_calls: list[dict] = []
        citations: list[dict] = []

        async def flush_tokens() -> None:
            nonlocal pending_tokens
            if pending_tokens:
                await durable_stream.append_event(
                    run_id, "token", {"text": pending_tokens}
                )
                pending_tokens = ""

        try:
            tracer = get_tracer()
            async with tracer.trace(
                user_id=user_id,
                task_type="chat",
                task_id=conv_id,
                task_name=(user_text[:120] or "(空)"),
            ) as tctx:
                await durable_stream.append_event(
                    run_id, "trace", {"trace_id": str(tctx.trace_id)}
                )
                async with SessionLocal() as session:
                    svc = ChatService(session)
                    conv = await svc.conv_repo.get(user_id, conv_id)
                    if conv is None:
                        await durable_stream.finish_run(
                            run_id,
                            event="error",
                            data={"message": "会话不存在"},
                            error="会话不存在",
                        )
                        return
                    async for ev in svc._generate_events(
                        user_id, conv, body, attachments, citations
                    ):
                        etype = ev.get("type")
                        if etype == "token":
                            text = ev["text"]
                            full_text += text
                            pending_tokens += text
                            if len(pending_tokens) >= _DURABLE_TOKEN_CHARS:
                                await flush_tokens()
                        elif etype in {"tool_call", "tool_start"}:
                            await flush_tokens()
                            tool_calls.append(
                                {
                                    "tool": ev["tool"],
                                    "query": ev.get("query", ""),
                                    "status": "running",
                                }
                            )
                            await durable_stream.append_event(
                                run_id,
                                "tool_start",
                                {"tool": ev["tool"], "query": ev.get("query", "")},
                            )
                        elif etype == "tool_result":
                            await flush_tokens()
                            for item in reversed(tool_calls):
                                if item.get("tool") == ev["tool"] and item.get("status") == "running":
                                    item.update(
                                        status=ev.get("status", "success"),
                                        stats=ev.get("stats") or {},
                                        latency_ms=ev.get("latency_ms"),
                                        preview=ev.get("text", ""),
                                    )
                                    break
                            await durable_stream.append_event(
                                run_id,
                                "tool_result",
                                {
                                    "tool": ev["tool"],
                                    "query": ev.get("query", ""),
                                    "status": ev.get("status", "success"),
                                    "text": ev.get("text", ""),
                                    "stats": ev.get("stats") or {},
                                    "latency_ms": ev.get("latency_ms"),
                                },
                            )
                        elif etype == "final" and not full_text:
                            full_text = ev["text"]
                            pending_tokens += ev["text"]
                        elif etype == "citation":
                            await flush_tokens()
                            citations = ev["citations"]
                            await durable_stream.append_event(
                                run_id, "citation", {"citations": citations}
                            )
                    await flush_tokens()
                    full_text = full_text.strip()
                    assistant_msg = await svc.msg_repo.add(
                        Message(
                            conversation_id=conv_id,
                            role=ROLE_ASSISTANT,
                            content=full_text,
                            meta_data={
                                "citations": citations,
                                "tool_calls": tool_calls,
                                "trace_id": str(tctx.trace_id),
                            },
                        )
                    )
                    await svc.conv_repo.touch(conv_id)
                    await svc._dispatch_memory(user_id, user_text)
                    if body.image_keys:
                        await svc._ingest_chat_images(user_id, body.image_keys)
                    if not skip_user_message:
                        svc._dispatch_emotion(
                            user_id, user_text, conv_id, assistant_msg.id
                        )
                await durable_stream.finish_run(
                    run_id,
                    event="done",
                    data={"conversation_id": cid, "message_id": str(assistant_msg.id)},
                    message_id=assistant_msg.id,
                )
        except Exception as exc:
            logger.error(
                "问答后台生成失败: conv=%s err=%s", cid, exc, exc_info=True
            )
            await self._save_partial_on_error(
                conv_id, full_text, citations, tool_calls
            )
            try:
                await durable_stream.finish_run(
                    run_id,
                    event="error",
                    data={"message": f"生成失败：{exc}"},
                    error=str(exc),
                )
            except Exception:
                logger.exception("持久化 SSE 错误事件失败: run=%s", run_id)
    async def _generate_events(
        self,
        user_id: uuid.UUID,
        conv: Conversation,
        body: ChatStreamRequest,
        attachments: list[dict],
        citations: list[dict],
    ) -> AsyncGenerator[dict, None]:
        """问答生成核心：组装 prompt/工具 → 按强弱模型/多模态分流 → 产出统一事件字典。

        事件类型：token / tool_call|tool_start / tool_result / final / citation。
        citations 由调用方传入，工具执行时回写；生成末尾再以 citation 事件吐出。
        """
        user_text = body.message.strip()
        agent = await self.agent_repo.get_by_user(user_id)
        persona = await self.persona_repo.get_active(user_id)
        temperature = persona.temperature if persona else 0.7
        skill = None
        if body.skill_id:
            skill = await self.skill_repo.get(user_id, body.skill_id)
        base_prompt = self._compose_system_prompt(persona, skill)
        stats_holder: dict[str, dict] = {}
        composed_text = _compose_with_attachments(user_text, attachments)

        from app.core.agent.context_hint import current_context_block

        async def _assemble_prompt(has_tools: bool) -> str:
            """组装 system prompt：人设/技能 + 时效引导 + 主动召回 + 跨会话 + 真人模式。

            真人模式把「真人聊天风格」放到**最末尾**：越靠后的指令权重越大，
            放最后才不会被前面的背景信息块（已知记忆/跨会话/时效引导）冲淡回助手腔。
            """
            human = agent is not None and agent.human_mode
            sp = (
                base_prompt + "\n\n" + current_context_block(with_tool_hint=has_tools)
            ).strip()
            if agent is None or agent.enable_active_recall:
                recall = await self._recall_lagged(user_id, user_text)
                if recall:
                    sp = (sp + "\n\n" + recall).strip()
            if agent is not None and agent.enable_cross_session:
                cross = await self._cross_session_context(user_id, conv.id)
                if cross:
                    sp = (sp + "\n\n" + cross).strip()
            if human:
                from app.core.agent.prompt_renderer import render_agent_prompt

                sp = (sp + "\n\n" + render_agent_prompt("human_style.jinja2")).strip()
            return sp

        history, history_has_images = await self._history_messages(conv.id, user_id)

        if body.image_keys or history_has_images:
            # 当前轮或历史窗口含图片时强制走视觉模型；历史图片按消息聚合并受总预算约束。
            system_prompt = await _assemble_prompt(has_tools=False)
            async for token in self._stream_multimodal(
                user_id, system_prompt, history, composed_text, body.image_keys
            ):
                yield {"type": "token", "text": token}
            if citations:
                yield {"type": "citation", "citations": _number_citations(citations)}
            return

        # 非多模态：构建工具（内置 + 带 TTL 缓存的 MCP 工具清单）并跑编排。
        model, config = await build_default_chat_model(
            self.session, user_id, temperature=temperature, streaming=True
        )
        # 用无状态 build_enabled_tools（而非每轮预开 MCP 会话的 _cm 版）：MCP 工具清单走
        # 进程内缓存、不预握手，只有模型真正调用某个 MCP 工具时才连接——闲聊/只用内置工具
        # 的轮次零 MCP 握手，大幅降低首字延迟。
        overrides, kb_ids = await self._tool_scope(user_id, body, skill)
        tools = await build_enabled_tools(
            self.session, user_id, citations, overrides, stats_holder, kb_ids
        )
        system_prompt = await _assemble_prompt(has_tools=bool(tools))
        if not tools:
            # 无工具：纯流式
            lc_messages: list = []
            if system_prompt:
                lc_messages.append(SystemMessage(content=system_prompt))
            lc_messages.extend(history)
            lc_messages.append(HumanMessage(content=composed_text))
            async for chunk in model.astream(lc_messages):
                if chunk.content:
                    yield {"type": "token", "text": chunk.content}
        elif supports_function_call(config):
            # 强模型：原生 function calling
            lc_messages = []
            if system_prompt:
                lc_messages.append(SystemMessage(content=system_prompt))
            lc_messages.extend(history)
            lc_messages.append(HumanMessage(content=composed_text))
            async for ev in run_function_calling(
                model, tools, lc_messages, stats_holder=stats_holder
            ):
                yield ev
        else:
            # 弱模型：ReAct
            async for ev in run_react(
                model, tools, composed_text, history, system_prompt,
                stats_holder=stats_holder,
            ):
                yield ev

        if citations:
            yield {"type": "citation", "citations": _number_citations(citations)}

    async def _save_partial_on_error(
        self,
        conv_id: uuid.UUID,
        full_text: str,
        citations: list[dict],
        tool_calls: list[dict],
    ) -> None:
        """后台生成异常时，把已生成的部分回复落库，避免完全丢失。失败只记 warning。"""
        text = (full_text or "").strip()
        if not text:
            return
        try:
            async with SessionLocal() as session:
                await MessageRepository(session).add(
                    Message(
                        conversation_id=conv_id,
                        role=ROLE_ASSISTANT,
                        content=text,
                        meta_data={
                            "citations": citations,
                            "tool_calls": tool_calls,
                            "interrupted": True,
                        },
                    )
                )
                await ConversationRepository(session).touch(conv_id)
        except Exception as e:  # noqa: BLE001
            logger.warning("保存部分回复失败: conv=%s err=%s", conv_id, e)

    async def _stream_multimodal(
        self,
        user_id: uuid.UUID,
        system_prompt: str,
        history: list,
        user_text: str,
        image_keys: list[str],
    ):
        """多模态流式：读图转 base64，用多模态模型看图答。逐 token 产出。

        大图先压缩（缩放 + 重编码），避免 base64 过大触发多模态接口 400/超限。
        """
        config = await get_default_config_for_type(
            self.session, user_id, "multimodal", "多模态"
        )
        if CAP_VISION not in (config.capability or []):
            from app.core.exceptions import BizError

            raise BizError(
                "默认多模态模型未声明 vision 能力，请在模型配置中启用图片理解",
                code=2011,
            )
        model = build_chat_model(config, temperature=0.7, streaming=True)

        content_parts: list[dict] = [{"type": "text", "text": user_text}]
        current_parts, _ = await self._load_vision_image_parts(
            user_id,
            image_keys,
            byte_budget=settings.chat_image_max_bytes * settings.chat_image_max_count,
            max_count=settings.chat_image_max_count,
        )
        content_parts.extend(current_parts)

        messages: list = []
        if system_prompt:
            messages.append(SystemMessage(content=system_prompt))
        messages.extend(history)
        messages.append(HumanMessage(content=content_parts))

        async for chunk in model.astream(messages):
            if chunk.content:
                text = chunk.content if isinstance(chunk.content, str) else str(chunk.content)
                yield text

    async def _dispatch_memory(self, user_id: uuid.UUID, user_text: str) -> None:
        """把本轮用户表达落 memories(source=auto) 并派发萃取任务。失败不影响问答。"""
        try:
            from app.models.memory_model import MEMORY_SOURCE_AUTO, Memory
            from app.tasks.memory import extract_memory_task

            memory = Memory(
                user_id=user_id, raw_text=user_text, source=MEMORY_SOURCE_AUTO
            )
            self.session.add(memory)
            await self.session.commit()
            await self.session.refresh(memory)
            extract_memory_task.delay(str(memory.id))
        except Exception as e:
            logger.warning("对话记忆萃取派发失败（忽略）: %s", e)

    async def _ingest_chat_images(
        self, user_id: uuid.UUID, image_keys: list[str]
    ) -> None:
        """把对话里上传的图片纳入图片库（建 Image 记录 + 派发处理）。

        按 file_key 去重，失败不影响对话。
        """
        try:
            from app.services.image_service import ImageService

            service = ImageService(self.session)
            for key in image_keys:
                try:
                    await service.ingest_from_chat(user_id, key)
                except Exception as e:
                    logger.warning("对话图片入库失败（跳过 %s）: %s", key, e)
        except Exception as e:
            logger.warning("对话图片入库整体失败（忽略）: %s", e)

    def _dispatch_emotion(
        self,
        user_id: uuid.UUID,
        user_text: str,
        conversation_id: uuid.UUID,
        message_id: uuid.UUID,
    ) -> None:
        """派发本轮用户发言的情绪分析任务（异步，仅入队）。失败不影响问答。"""
        text = (user_text or "").strip()
        if not text:
            return
        try:
            from app.tasks.emotion import analyze_emotion_task

            analyze_emotion_task.delay(
                str(user_id), text, str(conversation_id), str(message_id)
            )
        except Exception as e:
            logger.warning("情绪分析派发失败（忽略）: user=%s err=%s", user_id, e)

    # ── 消息反馈 / 重新生成 ──

    async def set_feedback(
        self,
        user_id: uuid.UUID,
        message_id: uuid.UUID,
        rating: str,
        comment: str | None = None,
    ) -> dict:
        """对某条 AI 回复点赞/踩（幂等，可切换）。校验消息归属当前用户。"""
        from app.core.exceptions import BizError
        from app.repositories.message_feedback_repository import (
            MessageFeedbackRepository,
        )

        msg = await self.msg_repo.get(message_id)
        if not msg:
            raise BizError("消息不存在", code=4010, status_code=404)
        conv = await self.conv_repo.get(user_id, msg.conversation_id)
        if not conv:
            raise BizError("无权操作该消息", code=4011, status_code=403)
        fb = await MessageFeedbackRepository(self.session).upsert(
            user_id, message_id, msg.conversation_id, rating, comment
        )
        return {"id": str(fb.id), "rating": fb.rating}

    async def remove_feedback(
        self, user_id: uuid.UUID, message_id: uuid.UUID
    ) -> None:
        """取消对某条 AI 回复的反馈。"""
        from app.repositories.message_feedback_repository import (
            MessageFeedbackRepository,
        )

        await MessageFeedbackRepository(self.session).remove(user_id, message_id)

    async def regenerate(
        self, user_id: uuid.UUID, message_id: uuid.UUID
    ) -> AsyncGenerator[str, None]:
        """重新生成某条 AI 回复：删掉该回复，用它前面的上文重新流式作答。

        约束：只能重新生成 assistant 消息；其前一条 user 消息作为本轮问题。
        """
        from app.core.exceptions import BizError
        from app.models.conversation_model import ROLE_ASSISTANT, ROLE_USER

        target = await self.msg_repo.get(message_id)
        if not target or target.role != ROLE_ASSISTANT:
            yield _sse("error", {"message": "只能重新生成 AI 回复"})
            return
        conv = await self.conv_repo.get(user_id, target.conversation_id)
        if not conv:
            yield _sse("error", {"message": "无权操作该消息"})
            return

        # 找到该 assistant 消息之前最近的一条 user 消息作为问题
        all_msgs = await self.msg_repo.list_by_conversation(conv.id)
        idx = next((i for i, m in enumerate(all_msgs) if m.id == message_id), -1)
        if idx <= 0:
            yield _sse("error", {"message": "找不到对应的提问"})
            return
        user_msg = None
        for i in range(idx - 1, -1, -1):
            if all_msgs[i].role == ROLE_USER:
                user_msg = all_msgs[i]
                break
        if user_msg is None:
            yield _sse("error", {"message": "找不到对应的提问"})
            return

        # 删除旧的 assistant 回复（及其反馈随级联删除），重新走问答
        try:
            await self.msg_repo.delete(target)
        except BizError:
            raise
        except Exception as e:
            yield _sse("error", {"message": f"重新生成失败：{e}"})
            return

        body = ChatStreamRequest(
            conversation_id=conv.id,
            message=user_msg.content,
            image_keys=list((user_msg.meta_data or {}).get("image_keys") or []),
        )
        # 复用流式问答；但用户消息已存在，这里跳过再次落 user 消息
        async for chunk in self.stream_chat(user_id, body, skip_user_message=True):
            yield chunk


__all__ = ["ChatService"]
