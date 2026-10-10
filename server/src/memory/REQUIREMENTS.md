# Trajecta memory architecture

## Storage and retrieval

- **Semantic:** explicit user preferences and facts (`SemanticMemory` and active learned experiences). Memory isolation is enforced by user/workspace. Untrusted retrieved content is context, not an instruction.
- **Episodic:** one evolving `episodes` record per logical task; includes observable outcomes, tool names, and event references. A completed answer is _not_ independently verified success.
- **Procedural:** versioned skills with replay evaluation, safety gates, activation, regression monitoring and rollback. Candidate instructions never expand tool permissions.

SQLite is authoritative; Qdrant is a rebuildable semantic index. FTS5 offers lexical retrieval. Old `reflection_jobs` and `procedure_drafts` tables are retained as read-only historical data for safe upgrades. They have **no active worker or review endpoints**.

## Task-level background pipeline

`memory/learning/tracker.py` resolves logical tasks through bounded, deterministic heuristics. Each agent run is still stored as an immutable trajectory and can be associated with an existing task, including explicit continuation across sessions. Uncertain matches create a new task rather than merge unrelated data.

`memory/learning/worker.py` uses `task_learning_jobs`: durable, deduplicated jobs keyed by task and event watermark with leases, retries, settings and bounded Bifrost reflection. All model-based reflection, episodic indexing, candidate synthesis, and evaluation run **outside the streaming response path**. A task is checkpointed after meaningful turns and can be reopened; inactivity marks it paused, not verified.

A reflection returns small evidence-linked insights only. `memory/learning/skills.py` converts observed tool sequences and a cited procedural insight into a candidate automatically, deduplicates candidates, and permits promotion only after independent verified held-out cases pass the existing evaluator and read-only policy. Because existing history does not automatically supply verified outcome assertions, generated candidates normally remain inactive until stronger evidence is available.

`GET /api/v1/learning/tasks`, `GET /api/v1/learning/reflection/status` and the reflection settings APIs expose diagnostics; normal chat does not require approvals for knowledge writes. User consent is still required for sensitive actions through the existing `ALLOW/ASK/DENY` gate.

## Current limitations

Task association is conservative lexical matching rather than semantic cross-session clustering. A chat containing unrelated concurrent goals may split them imperfectly. LLM insights and historical tool outputs are never treated as ground truth; automatic skill activation requires separately verified evaluation evidence. Real-time retrieval still adds its existing bounded timeout to chat.
