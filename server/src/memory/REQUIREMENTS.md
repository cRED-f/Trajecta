# Memory System Requirements

## Overview

Trajecta uses **LangChain Deep Agents Memory** for agent context, plus SQLite,
FTS5 and embedded Qdrant for application records and retrieval. The Trajectory
Store (in `server/src/skills/trajectory_store/`) is separate: it holds raw
execution evidence for attribution, experience learning, and optional evaluation.

**Current implementation:** Completed, tool-using task trajectories are
consolidated into durable, evidence-linked episodes in SQLite, with an FTS5
index and optional embedded Qdrant retrieval. This is deterministic extraction,
not an LLM reflection or a guarantee of task success. Simple chats and interrupted
runs are skipped. Automatic capture respects the `automatic_memory` setting.
Feedback-backed procedure suggestions remain in `learned_experiences` and do
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
| LangGraph checkpointer (SQLite) | Short-term conversation state and checkpoints | `.trajecta/data/langgraph.db` |
| LangGraph long-term store (SQLite) | `/memories/` + `/skills/` (Deep Agents memory files) | `.trajecta/data/langgraph.db` |
| SQLite (Trajecta's own) | `tasks`, `trajectories`, `episodes`, `skills`, `memories` | `.trajecta/data/trajecta.db` |
| SQLite FTS5 | `memories_fts` (manual sync), `episodes_fts` (triggers) | `.trajecta/data/trajecta.db` |
| Qdrant (embedded) | Vector embeddings for memory + episode retrieval | `.trajecta/data/qdrant` |

Two aiosqlite connections to `langgraph.db`: the checkpointer (`AsyncSqliteSaver`) keeps default isolation_level; the store (`AsyncSqliteStore`) uses `isolation_level=None` so LangGraph controls `BEGIN`/`COMMIT` explicitly (matching how LangGraph constructs it internally) — this avoids the "cannot start a transaction within a transaction" failure that `setup()` otherwise triggers.

## Implementation Notes

- **Semantic memory** writes durable facts to `/memories/<key>` via the store backend (source of truth for the agent), then **mirrors** each fact into FTS5 + embedded Qdrant for offline retrieval. `adelete` removes all three copies.
- **FTS5 is kept in sync manually** (not via triggers) because FTS5 requires integer rowids while `memories.id` is TEXT. A deterministic 63-bit rowid is derived from the memory id via SHA-256. `add`/`update`/`remove` each run `memories` + `memories_fts` in a single transaction.
- **Episodic memory** extracts bounded summaries from meaningful finished trajectories, stores them in the `episodes` table, maintains `episodes_fts` with SQLite triggers, and optionally indexes summaries in the Qdrant `episodes` collection. Search uses FTS5 + vector candidates and checks authorization in SQLite. Every episode references its source trajectory and remains **unverified** until independent evidence is added. Tool outputs/arguments are not copied into the index.
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

Checks opening the provider, short-term threads, semantic CRUD, trajectory-backed episodic consolidation and retrieval, procedural skills, and `agent_kwargs()` without a separate graph server.

## Key Interfaces

Each memory wrapper exposes async methods:
- `ShortTermStore`: `new_thread()`, `save_thread_state(state, thread_id)`, `get_thread_state(thread_id)`, `list_recent(limit)`
- `SemanticMemory`: `aput(key, content)`, `aget(key)`, `adelete(key)`, `asearch(query, limit)`
- `EpisodicMemory`: `consolidate(trajectory_id)`, `list(limit, user_id, scope)`, `get(id, user_id, scope)`, `search(query, limit, user_id, scope)`, `delete(id, user_id, scope)`, `as_tool(name)`
- `ProceduralMemory`: `apromote(name, content)`, `alist()`, `aload(name)`, `asearch(query, limit)`
- `MemoryProvider`: `open()`/`close()` (async), `agent_kwargs()`, module-level `get_memory_provider()` / `reset_memory_provider()`

## Bounded background reflection (migration v18)

The experience-first reflection worker queues meaningful completed trajectories and
explicit feedback in `reflection_jobs`. Atomic leases, retry/backoff, and recovery
protect the queue across restarts. It uses a configured Bifrost model to produce
strictly structured, event-grounded lessons and procedure suggestions. The
`automatic_memory` switch applies; model calls have daily, input and output limits.
Suggested experiences remain `needs_review` and never grant tool permissions,
activate executable skills, or trigger replay evaluation. Workspace-scoped and
non-local user insights stay in the audit job record until scoped learning is
implemented. `GET /api/v1/learning/reflection/status` reports queue activity.
