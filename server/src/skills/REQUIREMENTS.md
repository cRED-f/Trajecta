# Trajecta skills architecture

## Task → candidate → verified active skill

1. A chat run writes append-only `trajectory_events` and references a persistent logical `tasks.id`.
2. The durable task learning worker consolidates all linked runs into one episode and reviews evidence in background using Bifrost.
3. An evidence-linked procedural insight triggers `AutonomousSkillDiscovery`, which synthesizes a candidate from observed tool sequence and explicit current-context instructions. No review button is required.
4. The existing `SkillEvaluator` supplies replay, independent held-out cases, regression and safety gates. The promotion service activates only a passing candidate; non-read-only candidates remain inactive automatically. Historical success or thumbs-up alone never constitutes verification.
5. Runtime attribution and regression monitoring remain active, and tool actions still use the permission policy (`ALLOW / ASK / DENY`).

The old mining/refinement modules, automatic mining routes, and manual procedure approval UI were removed. Old SQLite tables and evaluation histories are kept so upgrades do not destroy data or break foreign keys. A candidate awaiting evidence is not an active skill.

## Implementation paths

- `memory/learning/tracker.py`: bounded, zero-LLM task association
- `skills/trajectory_store/store.py`: run evidence and logical task links
- `memory/learning/worker.py`: durable model-based task review
- `memory/learning/skills.py`: candidate generation and cautious auto promotion
- `skills/repository.py`: candidate/version/registry persistence
- `skills/evaluation/`: independent replay and scoring
- `skills/promotion/`: guarded activation
- `skills/regression/`: production metrics and rollback
