# Trajecta skills architecture

## Task → candidate → verified active skill

1. A chat run writes append-only `trajectory_events` and references a persistent logical `tasks.id`.
2. The durable task learning worker consolidates all linked runs into one episode and reviews evidence in background using Bifrost.
3. An evidence-linked procedural insight triggers `AutonomousSkillDiscovery`, which synthesizes a candidate from observed tool sequence and explicit current-context instructions. No review button is required.
4. The existing `SkillEvaluator` supplies isolated replay, independently captured held-out cases, regression and safety gates. The promotion service activates only a passing candidate. Safe local filesystem operations may be replayed in disposable workspaces; external/sensitive actions never activate automatically. Historical success or thumbs-up alone never constitutes verification.
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

## Collecting verifiable cases (opt-in)

Candidate generation is automatic whenever reflection extracts a procedure linked to observed successful tool use. Verification of local file changes requires **both** a complete initial replay snapshot and an explicitly user-confirmed successful outcome. Automatic promotion still needs at least two *different, relevant* held-out logical tasks and an independent baseline-vs-candidate replay result with a passing verdict. Read-only web answers and actions affecting external services do not automatically acquire verified outcome assertions.

Workspace snapshots are deliberately disabled by default to avoid costly scans of large folders. To opt in, configure `TRAJECTA_SKILLS__FIXTURES__ENABLED=true` and `TRAJECTA_SKILLS__FIXTURES__CAPTURE_WORKSPACE=true` when starting the backend. Snapshots are bounded by `max_files`, `max_file_mb`, and `max_total_mb`. Snapshots that exceed the limits remain ineligible; no partial capture is treated as verified. Previously captured tasks without initial fixtures cannot be retroactively verified.

The Skills panel now shows candidates, verified case counts, and why each candidate is blocked; finished background reviews are not equivalent to activated skills.
