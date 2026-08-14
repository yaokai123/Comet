"""Built-in, allowlisted connector plugins and source materialization."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

from app.config import settings
from app.core.knowledge.connectors import (
    ChangeKind,
    ConnectorCursor,
    KnowledgeConnector,
    SourceChange,
    SyncBatch,
)
from app.core.rag.parser import SUPPORTED_EXTS
from app.core.rag.web_crawler import fetch_url_content


@dataclass(slots=True, frozen=True)
class MaterializedSource:
    file_name: str
    content: bytes
    source_uri: str
    metadata: dict[str, Any]


class MaterializingConnector(KnowledgeConnector, Protocol):
    async def materialize(self, change: SourceChange) -> MaterializedSource: ...


def _decode_cursor(cursor: ConnectorCursor) -> dict[str, str]:
    if not cursor.value:
        return {}
    try:
        data = json.loads(cursor.value)
    except (TypeError, ValueError):
        return {}
    items = data.get("items", {}) if isinstance(data, dict) else {}
    return {str(key): str(value) for key, value in items.items()}


def _encode_cursor(items: dict[str, str]) -> ConnectorCursor:
    return ConnectorCursor(json.dumps({"items": items}, ensure_ascii=False, sort_keys=True))


class LocalFolderConnector:
    connector_type = "local_folder"

    def __init__(self, config: dict[str, Any]) -> None:
        configured = str(config.get("root", "")).strip()
        if not configured:
            raise ValueError("local_folder requires config.root")
        root = Path(configured).resolve()
        allowed = [
            Path(item.strip()).resolve()
            for item in settings.connector_local_roots.split(",")
            if item.strip()
        ]
        if not allowed:
            raise ValueError("local_folder is disabled; configure CONNECTOR_LOCAL_ROOTS")
        if not any(root == base or root.is_relative_to(base) for base in allowed):
            raise ValueError("local_folder root is outside CONNECTOR_LOCAL_ROOTS")
        if not root.is_dir():
            raise ValueError("local_folder root does not exist or is not a directory")
        self.root = root
        self.recursive = bool(config.get("recursive", True))

    def _snapshot(self) -> dict[str, str]:
        pattern = "**/*" if self.recursive else "*"
        snapshot: dict[str, str] = {}
        for path in self.root.glob(pattern):
            if not path.is_file() or path.suffix.lower() not in SUPPORTED_EXTS:
                continue
            resolved = path.resolve()
            if not resolved.is_relative_to(self.root):
                continue
            stat = resolved.stat()
            relative = resolved.relative_to(self.root).as_posix()
            snapshot[relative] = f"{stat.st_mtime_ns}:{stat.st_size}"
        return snapshot

    async def pull(self, cursor: ConnectorCursor, *, limit: int = 100) -> SyncBatch:
        previous = _decode_cursor(cursor)
        current = self._snapshot()
        changes: list[SourceChange] = []
        for external_id in sorted(set(previous) | set(current)):
            if len(changes) >= limit:
                break
            if external_id not in current:
                changes.append(
                    SourceChange(external_id, ChangeKind.DELETE, previous[external_id], None)
                )
            elif current[external_id] != previous.get(external_id):
                path = (self.root / external_id).resolve()
                changes.append(
                    SourceChange(
                        external_id,
                        ChangeKind.UPSERT,
                        current[external_id],
                        path.as_uri(),
                        {"file_name": path.name},
                    )
                )
        next_items = dict(previous)
        for change in changes:
            if change.kind == ChangeKind.DELETE:
                next_items.pop(change.external_id, None)
            else:
                next_items[change.external_id] = change.version
        has_more = next_items != current
        return SyncBatch(changes, _encode_cursor(next_items), has_more=has_more)

    async def materialize(self, change: SourceChange) -> MaterializedSource:
        path = (self.root / change.external_id).resolve()
        if not path.is_relative_to(self.root) or not path.is_file():
            raise ValueError("connector file disappeared or escaped the configured root")
        content = path.read_bytes()
        if len(content) > settings.connector_max_file_bytes:
            raise ValueError("connector file exceeds CONNECTOR_MAX_FILE_BYTES")
        return MaterializedSource(
            file_name=path.name,
            content=content,
            source_uri=path.as_uri(),
            metadata={"relative_path": change.external_id},
        )


class WebPageConnector:
    connector_type = "web_pages"

    def __init__(self, config: dict[str, Any]) -> None:
        urls = config.get("urls")
        if not isinstance(urls, list) or not urls:
            raise ValueError("web_pages requires a non-empty config.urls list")
        self.urls = list(dict.fromkeys(str(url).strip() for url in urls if str(url).strip()))
        if len(self.urls) > 500:
            raise ValueError("web_pages supports at most 500 URLs per connector")

    async def pull(self, cursor: ConnectorCursor, *, limit: int = 100) -> SyncBatch:
        previous = _decode_cursor(cursor)
        current: dict[str, str] = {}
        materialized: dict[str, tuple[str, str]] = {}
        for url in self.urls:
            title, text = await fetch_url_content(url)
            version = hashlib.sha256(text.encode("utf-8")).hexdigest()
            current[url] = version
            materialized[url] = (title, text)

        pending: list[SourceChange] = []
        for url in sorted(set(previous) | set(current)):
            if url not in current:
                pending.append(SourceChange(url, ChangeKind.DELETE, previous[url], None))
            elif current[url] != previous.get(url):
                title, _ = materialized[url]
                pending.append(
                    SourceChange(
                        url,
                        ChangeKind.UPSERT,
                        current[url],
                        url,
                        {"file_name": f"{title[:180]}.txt", "title": title},
                    )
                )
        changes = pending[:limit]
        next_items = dict(previous)
        for change in changes:
            if change.kind == ChangeKind.DELETE:
                next_items.pop(change.external_id, None)
            else:
                next_items[change.external_id] = change.version
        return SyncBatch(changes, _encode_cursor(next_items), has_more=next_items != current)

    async def materialize(self, change: SourceChange) -> MaterializedSource:
        title, text = await fetch_url_content(change.external_id)
        content = text.encode("utf-8")
        if len(content) > settings.connector_max_file_bytes:
            raise ValueError("web page exceeds CONNECTOR_MAX_FILE_BYTES")
        safe_title = "".join(char for char in title[:180] if char not in '\\/:*?"<>|').strip()
        return MaterializedSource(
            file_name=f"{safe_title or 'web-page'}.txt",
            content=content,
            source_uri=change.external_id,
            metadata={"title": title},
        )


def build_connector(connector_type: str, config: dict[str, Any]) -> MaterializingConnector:
    implementations = {
        LocalFolderConnector.connector_type: LocalFolderConnector,
        WebPageConnector.connector_type: WebPageConnector,
    }
    factory = implementations.get(connector_type)
    if factory is None:
        raise ValueError(f"unsupported connector type: {connector_type}")
    return factory(config)


SUPPORTED_CONNECTOR_TYPES = frozenset({"local_folder", "web_pages"})
