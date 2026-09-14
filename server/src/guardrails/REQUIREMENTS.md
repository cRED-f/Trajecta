# Guardrails and Safety Requirements

## Overview

Three-layer safety system: Guardrails AI for validation, a custom deterministic policy engine for tool/action permissions, and Deep Agents human-in-the-loop for sensitive operations.

## Layers

### 1. Guardrails AI
- Input validation before agent processing
- Output validation after agent generation
- Structured response validation
- Custom validators for domain-specific rules
- Safety checks on generated content

### 2. Custom Deterministic Policy Engine
- Tool permission enforcement
- Filesystem access restrictions
- Destructive command blocking
- Action risk classification

#### Risk Classes

| Class | Behavior |
|-------|----------|
| SAFE | Execute automatically, no approval needed |
| SENSITIVE | Require user approval before execution |
| DENIED | Block execution entirely |

### 3. Human-in-the-Loop (Deep Agents)
- Approval workflow for sensitive operations
- User can inspect tool arguments before approval
- Timeout and default behavior for unattended approvals
- Approval history for auditing

## File Map

```
server/src/guardrails/
├── REQUIREMENTS.md
├── __init__.py
├── validators/
│   ├── __init__.py
│   ├── input.py          # Input validation (Guardrails AI)
│   ├── output.py         # Output validation (Guardrails AI)
│   └── structured.py     # Structured response validation
├── permissions/
│   ├── __init__.py
│   ├── engine.py         # Deterministic permission engine
│   ├── rules.py          # Rule definitions and loading
│   └── approval.py       # Human approval workflow
└── risk/
    ├── __init__.py
    └── classifier.py     # Action risk classification
```

## Key Interfaces

- `classify_action(action) -> RiskLevel` — determine SAFE / SENSITIVE / DENIED
- `check_permission(tool, args, context) -> bool` — whether tool call is allowed
- `validate_input(content) -> ValidationResult` — input guardrail check
- `validate_output(content) -> ValidationResult` — output guardrail check
- `request_approval(action, timeout) -> ApprovalResult` — human-in-the-loop
