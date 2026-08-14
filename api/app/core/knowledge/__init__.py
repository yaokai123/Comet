"""Enterprise knowledge domain primitives.

The package is intentionally framework-independent.  HTTP controllers, Celery
workers and storage adapters depend on these contracts, not the other way round.
"""

from app.core.knowledge.ir import BlockKind, DocumentBlock, DocumentIR, SourceAnchor

__all__ = ["BlockKind", "DocumentBlock", "DocumentIR", "SourceAnchor"]
