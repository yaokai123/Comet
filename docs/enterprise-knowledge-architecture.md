# Enterprise private knowledge architecture

This document describes the compatibility-first migration from Comet's personal
knowledge feature to a continuously synchronized enterprise knowledge platform.

## Design rules

1. PostgreSQL is the source of truth for source versions, connector cursors,
   durable jobs, Wiki versions, evidence and quality findings.
2. Elasticsearch is a derived retrieval index. Every indexed item carries its
   document version, chunk strategy and source anchors.
3. Retrieved content is untrusted evidence, never an instruction to an agent.
4. A connector cursor advances in the same database transaction that enqueues
   all changes returned for that cursor.
5. Generated Wiki text is never authoritative by itself. Every version keeps
   exact Chunk evidence and the source document version.

## Canonical document representation

All parsers should return `DocumentIR` from `app.core.knowledge.ir`. During the
migration, legacy text parsers are adapted through `infer_plain_text_ir`.

Parser adapters should preserve:

- document and source version;
- block type and reading order;
- page and bounding box;
- heading path;
- region ID for tables, charts and images;
- logical table ID for multi-page tables;
- original image/table artifact path.

PDF production parsing should map MinerU `content_list_v2.json` into this IR.
Plain Markdown is a presentation output and must not be the indexing contract.

## Adaptive chunking

`AdaptiveChunker` analyzes the IR and selects one of:

- `heading`: stable headings or section paths;
- `heuristic`: paragraph-level blocks without reliable headings;
- `recursive`: sparse, malformed or oversized content.

Tables, table rows, images and charts remain atomic under heuristic chunking.
If a structural strategy fails, the decision records its reason and degrades to
recursive chunking. The applied strategy is written to Elasticsearch.

## Staged RAG

`StagedRAGPipeline` defines replaceable stages and records latency, candidate
counts, implementation and failure state for each stage:

1. question understanding;
2. hybrid recall;
3. query expansion;
4. rerank;
5. parent expansion;
6. evidence merge.

The existing production hybrid search now uses weighted reciprocal-rank fusion.
The reranker ranking is fused with, rather than replacing, the first-stage
ranking. New implementations should use the stage contract and persist the
observations in the existing tracing subsystem.

## Auto-Wiki and evidence

`AutoWikiPlanner` extracts concepts from source chunks, plans pages, merges
evidence and creates bidirectional links. Its heuristic extractor is a safe
offline fallback; a configured LLM extractor can replace it through the
`ConceptExtractor` protocol.

`WikiRepository.publish` versions changed pages, preserves unchanged versions,
rebuilds links and archives pages absent from the new build. Evidence points to:

- Wiki page version;
- document version;
- Chunk and optional Block;
- page range and bounding box;
- a hash of the cited source text.

## Continuous synchronization

Connector implementations conform to `KnowledgeConnector` and return a
`SyncBatch` with a new cursor. `ConnectorSyncService` writes versioned changes
to `PostgresDurableQueue` before advancing the cursor.

Jobs are idempotent by connector, external item, source version and operation.
Workers lease rows with `FOR UPDATE SKIP LOCKED`, retry with exponential backoff
and move exhausted jobs to `dead_letter`.

Secrets must not be stored in connector JSON. Store a secret-manager reference
in `secret_ref`.

## Continuous quality inspection

Celery Beat runs `app.tasks.knowledge_maintenance.inspect` hourly at minute 15.
The first implementation detects:

- orphan Wiki pages;
- broken links;
- pages without Chunk evidence;
- duplicate normalized page titles;
- stale references when source version information is supplied.

Findings are idempotently reconciled in `knowledge_quality_issues`; findings no
longer observed are marked resolved.

## Deployment and migration

Run the database migration before deploying workers using the new parser:

```powershell
cd api
.\.venv\Scripts\python.exe -m alembic upgrade head
```

The migration creates document versions, connectors, durable sync jobs, Wiki
pages and versions, Wiki links and evidence, and quality issues. Elasticsearch
adds new metadata fields in place on startup.

Recommended rollout:

1. apply the PostgreSQL migration;
2. restart the API, Celery workers and Beat;
3. reparse a small knowledge base to populate versioned adaptive chunks;
4. compare old and new retrieval traces and answer citations;
5. backfill remaining documents;
6. enable connectors and Auto-Wiki per knowledge base.

Do not remove the existing `comet_chunks` index during rollout. The schema
extension is additive and the current API remains compatible.
