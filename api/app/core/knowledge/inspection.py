"""Continuous quality checks for generated wiki and source provenance."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from app.core.knowledge.wiki import WikiPageDraft


class QualityIssueKind(StrEnum):
    ORPHAN_PAGE = "orphan_page"
    BROKEN_LINK = "broken_link"
    MISSING_EVIDENCE = "missing_evidence"
    STALE_REFERENCE = "stale_reference"
    DUPLICATE_PAGE = "duplicate_page"


@dataclass(slots=True, frozen=True)
class QualityIssue:
    kind: QualityIssueKind
    page_slug: str
    detail: str
    fingerprint: str


def inspect_wiki(
    pages: list[WikiPageDraft],
    *,
    source_versions: dict[str, str] | None = None,
    evidence_versions: dict[str, str] | None = None,
) -> list[QualityIssue]:
    page_map = {page.slug: page for page in pages}
    issues: list[QualityIssue] = []
    title_owner: dict[str, str] = {}
    for page in pages:
        if not page.incoming_slugs and not page.outgoing_slugs:
            issues.append(_issue(QualityIssueKind.ORPHAN_PAGE, page.slug, "page has no links"))
        if not page.evidence:
            issues.append(
                _issue(QualityIssueKind.MISSING_EVIDENCE, page.slug, "page has no chunk evidence")
            )
        for target in page.outgoing_slugs:
            if target not in page_map:
                issues.append(
                    _issue(QualityIssueKind.BROKEN_LINK, page.slug, f"missing target: {target}")
                )
        canonical_title = "".join(page.title.casefold().split())
        owner = title_owner.get(canonical_title)
        if owner and owner != page.slug:
            issues.append(
                _issue(
                    QualityIssueKind.DUPLICATE_PAGE,
                    page.slug,
                    f"same normalized title as {owner}",
                )
            )
        title_owner[canonical_title] = page.slug

    if source_versions and evidence_versions:
        for page in pages:
            for evidence in page.evidence:
                current = source_versions.get(evidence.chunk_id)
                cited = evidence_versions.get(evidence.chunk_id)
                if current and cited and current != cited:
                    issues.append(
                        _issue(
                            QualityIssueKind.STALE_REFERENCE,
                            page.slug,
                            f"chunk {evidence.chunk_id} cites {cited}, current is {current}",
                        )
                    )
    return issues


def _issue(kind: QualityIssueKind, slug: str, detail: str) -> QualityIssue:
    return QualityIssue(
        kind=kind,
        page_slug=slug,
        detail=detail,
        fingerprint=f"{kind.value}:{slug}:{detail}",
    )
