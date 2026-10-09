# Skill Learning System Requirements

## Overview

Trajecta's default learning path is **experience-first**: the chat runtime records
trajectories, while `ExperienceLearningService` saves explicit preferences,
corrections for review, and feedback-backed procedure suggestions. This path
does not mine candidates or evaluate them at the end of a conversation.

The older skill miner, replay evaluator, versioner, promoter, experiments,
and rollback services are retained for explicit/manual or opt-in workflows.
The automatic learning coordinator and its threshold-based worker are retired.

## Optional skill-evaluation pipeline (not automatic)

```
Recorded trajectories / existing candidate
  → Trajectory Store (raw data)
  → Optional standalone Skill Miner (pattern extraction)
  → Candidate Skill
  → Explicit replay + evaluation
  → PASS / FAIL
  → Promote to Procedural Memory (or Reject)
  → Versioned Skill Registry
```

## Subsystems

### Trajectory Store
Raw execution data per task. Separate from Deep Agents Memory.

Stores: agent states, plans, model calls, tool calls, arguments, outputs, errors, retries, guardrail events, metrics, final outcome.

### Skill Miner
Analyzes successful trajectories to identify:
- Repeated tool sequences
- Repeated reasoning structures
- Repeated debugging procedures
- Repeated project workflows
- Reusable task patterns

Produces candidate skills.

### Skill Representation

```
skills/<skill-name>/
├── SKILL.md           # Human-readable skill description
├── workflow.yaml      # Execution workflow definition
├── metadata.json      # Skill metadata (version, author, metrics)
├── eval.yaml          # Evaluation configuration
├── tests/             # Evaluation test cases
│   ├── case_001.yaml
│   ├── case_002.yaml
│   └── case_003.yaml
└── versions/          # Version history
```

### Skill Evaluation

Compares candidate skill vs. baseline agent behavior on dimensions:
- Task success rate
- Tool-call correctness
- Number of tool calls
- Token usage
- Execution time
- Retry count
- Failure count
- Guardrail violations
- Human intervention count

### Skill Lifecycle

```
Candidate → Evaluate → PASS/FAIL
  FAIL → Reject
  PASS → Promote → Active Skill → Version
    → Upgrade (new version)
    → Rollback (revert to previous)
```

### Versioning
- Each promotion creates a versioned snapshot
- Rollback restores a previous version
- Version metadata tracks performance metrics per version

## File Map

```
server/src/skills/
├── REQUIREMENTS.md
├── __init__.py
├── trajectory_store/
│   ├── __init__.py
│   └── store.py          # TrajectoryStore — raw execution capture
├── skill_miner/
│   ├── __init__.py
│   └── miner.py          # Pattern extraction from trajectories
├── representation/
│   ├── __init__.py
│   └── skill.py          # Skill schema, loading, serialization
├── evaluation/
│   ├── __init__.py
│   ├── evaluator.py      # Skill vs baseline comparison
│   └── replay.py         # Skill replay engine
├── versioning/
│   ├── __init__.py
│   └── versioner.py      # Skill version management
└── promotion/
    ├── __init__.py
    └── promoter.py       # Promotion + rollback logic
```
