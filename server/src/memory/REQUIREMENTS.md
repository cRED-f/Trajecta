# Memory System Requirements

## Overview

Trajecta uses **LangChain Deep Agents Memory** for agent context, plus SQLite,
FTS5 and embedded Qdrant for application records and retrieval. The Trajectory
Store (in `server/src/skills/trajectory_store/`) is separate: it holds raw
execution evidence for attribution, experience learning, and optional evaluation.

**Current implementation limitation:** Episodic memory can search existing
LangGraph threads but does not yet consolidate them into indexed episodes.
Feedback-backed procedure suggestions live in `learned_experiences` and do
not automatically become executable skills in `/skills/`.

## Memory Architecture

Deep Agents Memory provides four memory tiers:

### Short-Term Memory
- Current task/thread state
- Current goal, plan, active files, recent tool outputs
- Temporary reasoning state, execution errors, subagent state
- Belongs to the active task — not long-term knowledge

### Semantic Memory
- User preferences
- Project structure and repository information
- Environment details
- Frequently used tools
- Discovered system facts
- Persistent configuration knowledge

### Episodic Memory
- Selected useful experiences from previous tasks
- Successful and failed approaches
- Corrections and outcomes
- Important execution events
- Curated — not every raw event

### Procedural Memory
- Verified skills promoted through Trajecta's evaluation pipeline
- Debugging procedures, project-specific workflows
- Tool-use strategies, reusable instructions
- Learned execution patterns

## Key Separation

```
Deep Agents Memory  = what the agent should remember and reuse
Trajectory Store    = raw execution history used to determine what to learn
```

## Data Flow

```
User Task → Deep Agent → Memory (all 4 tiers)
  → Plan + Execute → Tools / MCP / Subagents
  → Verification → Final Outcome
      ├──→ Selected useful knowledge → Deep Agents Memory
      └──→ Raw Trajectory → Trajectory Store → Skill Miner
           → Candidate Skill → Evaluation
              ├── FAIL → Reject / Improve
              └── PASS → Procedural Memory
```

## Storage

| Backend | Purpose | Location |
|---------|---------|----------|
| LangGraph checkpointer (SQLite) | Short-term/episodic substrate, thread state | `.trajecta/data/langgraph.db` |
| LangGraph long-term store (SQLite) | `/memories/` + `/skills/` (Deep Agents memory files) | `.trajecta/data/langgraph.db` |
| SQLite (Trajecta's own) | `tasks`, `trajectories`, `skills` registry, `memories` mirror | `.trajecta/data/trajecta.db` |
| SQLite FTS5 | Lexical search over memories (`memories_fts`, manual sync) | `.trajecta/data/trajecta.db` |
| Qdrant (embedded) | Vector embeddings for memory + episode retrieval | `.trajecta/data/qdrant` |

Two aiosqlite connections to `langgraph.db`: the checkpointer (`AsyncSqliteSaver`) keeps default isolation_level; the store (`AsyncSqliteStore`) uses `isolation_level=None` so LangGraph controls `BEGIN`/`COMMIT` explicitly (matching how LangGraph constructs it internally) — this avoids the "cannot start a transaction within a transaction" failure that `setup()` otherwise triggers.

## Implementation Notes

- **Semantic memory** writes durable facts to `/memories/<key>` via the store backend (source of truth for the agent), then **mirrors** each fact into FTS5 + embedded Qdrant for offline retrieval. `adelete` removes all three copies.
- **FTS5 is kept in sync manually** (not via triggers) because FTS5 requires integer rowids while `memories.id` is TEXT. A deterministic 63-bit rowid is derived from the memory id via SHA-256. `add`/`update`/`remove` each run `memories` + `memories_fts` in a single transaction.
- **Episodic memory** searches checkpointed threads via `langgraph-sdk` `client.threads.search(metadata={"user_id": ...})` against the local `langgraph dev` server. The SDK client is injectable/fakeable so offline verify works without a live server.
- **Procedural memory** promotes verified skills to `/skills/<name>/SKILL.md` in the store (the Deep Agents `skills=` path). `alist` reads `LsResult.entries` from `backend.als()`.
- **Qdrant** runs in embedded mode (`QdrantClient(path=...)`, in-process, no server) and is resilient: if unavailable, memory still works via FTS + SQLite (a class latch `RESILIENT_BROKEN` keeps it a no-op).

## File Map

```
server/src/memory/
├── REQUIREMENTS.md
├── __init__.py
├── provider.py           # Deep Agents Memory provider setup and config
├── short_term/
│   ├── __init__.py
│   └── store.py          # Short-term memory per task/thread
├── semantic/
│   ├── __init__.py
│   └── store.py          # Semantic memory wrapper
├── episodic/
│   ├── __init__.py
│   └── store.py          # Episodic memory wrapper
├── procedural/
│   ├── __init__.py
│   └── store.py          # Procedural memory (skill integration)
└── storage/
    ├── __init__.py
    ├── sqlite.py          # SQLite connection + migrations
    ├── fts.py             # FTS5 search helpers (manual sync, 63-bit rowids)
    └── vector.py          # Qdrant client wrapper (embedded, resilient)
```

## Verify

```bash
python -m server.src.memory.verify
```

Runs fully offline: temp dirs, opening the provider, exercising short-term threads (checkpointer), semantic put/get/search/delete (via real StoreBackend + FTS), ephemeral fake episodic search + tool (injected SDK client), procedural promote/list/load/search, and confirming `agent_kwargs()` assembles. All 5 checks pass without a live `langgraph dev` server or network.

## Key Interfaces

Each memory wrapper exposes async methods:
- `ShortTermStore`: `new_thread()`, `save_thread_state(state, thread_id)`, `get_thread_state(thread_id)`, `list_recent(limit)`
- `SemanticMemory`: `aput(key, content)`, `aget(key)`, `adelete(key)`, `asearch(query, limit)`
- `EpisodicMemory`: `set_user(user_id)`, `search(query, limit, user_id)`, `get_history(thread_id, limit)`, `as_tool(name)`
- `ProceduralMemory`: `apromote(name, content)`, `alist()`, `aload(name)`, `asearch(query, limit)`
- `MemoryProvider`: `open()`/`close()` (async), `agent_kwargs()`, module-level `get_memory_provider()` / `reset_memory_provider()`
