# Skill Learning System Requirements

## Overview

The core differentiating subsystem of Trajecta. Captures trajectories, mines patterns, generates candidate skills, evaluates them against baselines, and promotes only verified improvements into procedural memory.

## Pipeline

```
Task Execution
  → Trajectory Store (raw data)
  → Skill Miner (pattern extraction)
  → Candidate Skill
  → Replay + Evaluation
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
