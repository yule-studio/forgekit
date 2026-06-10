# yule-memory

Local-first memory layer for Yule Studio agents: a SQLite/FTS5-backed
index over Obsidian vault notes, repo policy docs, and workflow session
artifacts. Retrieval is fully local, deterministic, and testable without
a vector store.

## Responsibility

- **`models`** — `MemoryDocument` (what goes into the index) and
  `MemorySearchResult` (what comes out), plus the stable
  `SOURCE_*` / `NOTE_KIND_*` contract constants.
- **`indexer`** — idempotent SQLite/FTS5 schema + `open_memory_index`,
  `reindex_paths`, `reindex_workflow_sessions`. Parses Obsidian/vault
  Markdown frontmatter and workflow session payloads into documents.
- **`search`** — thin FTS5 query wrapper returning ranked
  `MemorySearchResult` hits with metadata filters.

## Dependency rule

`yule_memory` depends on **stdlib + sqlite3 only** — no third-party
runtime deps (`dependencies = []`). It MUST NOT import
`yule_engineering` runtime, Discord, or agent internals, and MUST NOT
add LLM calls, Discord sends, or agent execution logic. This keeps the
memory index reusable and independently testable.

Role-aware retrieval (`fetch_role_context`) that depends on agent
internals deliberately stays in `yule_engineering.memory.retrieval`,
NOT here.

## Compatibility

`yule_engineering.memory.{__init__,models,indexer,search}` are thin
shims that re-export from this package, so existing
`from yule_engineering.memory import ...` imports keep resolving to the
identical objects.

## Public API

`MEMORY_DB_ENV`, `MemoryDocument`, `MemoryIndex`, `MemorySearchResult`,
`open_memory_index`, `reindex_paths`, `reindex_workflow_sessions`,
`search`.
