"""统一导入所有 ORM 模型，确保 SQLAlchemy metadata 完整。

任何模块导入 app.models 即可让全部表与外键关系正确注册，
避免在 Celery worker 等场景因模型未全部加载导致外键解析失败。
"""
from app.models.agent_config_model import AgentConfig
from app.models.agent_persona_model import AgentPersona
from app.models.agent_task_model import AgentTask
from app.models.agent_trace_model import AgentSpan, AgentTrace
from app.models.chat_image_upload_model import ChatImageUpload
from app.models.conversation_model import Conversation, Message
from app.models.conversation_share_model import ConversationShare
from app.models.daily_review_model import DailyReview
from app.models.document_model import Document
from app.models.document_index_job_model import DocumentIndexJob
from app.models.enterprise_knowledge_model import (
    ConnectorDocumentBinding,
    DocumentVersion,
    KnowledgeConnectorRecord,
    KnowledgeQualityIssue,
    KnowledgeSyncJob,
    WikiEvidence,
    WikiLink,
    WikiPage,
    WikiPageVersion,
)
from app.models.emotion_model import EmotionProfile, EmotionRecord
from app.models.favorite_model import Favorite
from app.models.group_member_model import GroupMember
from app.models.image_model import Image
from app.models.knowledge_base_model import KnowledgeBase
from app.models.loop_model import LoopIteration, LoopRun
from app.models.mcp_server_model import MCPServer
from app.models.memory_model import Memory
from app.models.memory_correction_model import MemoryCorrection
from app.models.message_feedback_model import MessageFeedback
from app.models.model_config_model import ModelConfig
from app.models.notify_channel_model import NotifyChannel
from app.models.persona_group_model import PersonaGroup
from app.models.play_history_model import PlayHistory
from app.models.project_model import Project
from app.models.product_event_model import ProductEvent
from app.models.report_share_model import ReportShare
from app.models.research_report_model import ResearchReport
from app.models.skill_model import Skill
from app.models.stream_event_model import StreamEvent, StreamRun
from app.models.song_model import Song
from app.models.tag_model import Tag, document_tags, image_tags
from app.models.tool_config_model import ToolConfig
from app.models.user_model import User

__all__ = [
    "AgentConfig",
    "AgentPersona",
    "AgentTask",
    "AgentSpan",
    "AgentTrace",
    "ChatImageUpload",
    "Conversation",
    "Message",
    "ConversationShare",
    "ConnectorDocumentBinding",
    "DailyReview",
    "Document",
    "DocumentIndexJob",
    "DocumentVersion",
    "EmotionProfile",
    "EmotionRecord",
    "Favorite",
    "GroupMember",
    "Image",
    "KnowledgeBase",
    "KnowledgeConnectorRecord",
    "KnowledgeQualityIssue",
    "KnowledgeSyncJob",
    "LoopRun",
    "LoopIteration",
    "MCPServer",
    "Memory",
    "MemoryCorrection",
    "MessageFeedback",
    "ModelConfig",
    "NotifyChannel",
    "PersonaGroup",
    "PlayHistory",
    "Project",
    "ProductEvent",
    "ReportShare",
    "ResearchReport",
    "Skill",
    "StreamEvent",
    "StreamRun",
    "Song",
    "Tag",
    "document_tags",
    "image_tags",
    "ToolConfig",
    "User",
    "WikiEvidence",
    "WikiLink",
    "WikiPage",
    "WikiPageVersion",
]
