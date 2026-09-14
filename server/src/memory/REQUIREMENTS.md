# Memory System Requirements

## Overview

Trajecta uses **LangChain Deep Agents Memory** as the primary agent memory system. The Trajectory Store (in `server/src/skills/trajectory_store/`) is separate — it holds raw execution data for skill mining and evaluation.

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

| Backend | Purpose |
|---------|---------|
| Deep Agents Memory | Agent-facing memory (short-term, semantic, episodic, procedural) |
| SQLite | Trajectory metadata, task metadata, skill registry, config |
| SQLite FTS | Lexical search over trajectories and memories |
| Qdrant (local) | Vector embeddings for trajectory and skill retrieval |

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
    ├── fts.py             # FTS5 search helpers
    └── vector.py          # Qdrant client wrapper
```

## Key Interfaces

Each memory wrapper should expose:
- `add(entry)` — insert a new memory
- `search(query, limit)` — retrieve relevant memories
- `get(id)` — get a specific entry
- `update(id, fields)` — update an entry
- `delete(id)` — remove an entry
- `list_recent(limit)` — get most recent entries
