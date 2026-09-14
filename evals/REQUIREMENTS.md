# Evaluation System Requirements

## Overview

Multi-level evaluation using **DeepEval** as the evaluation framework. Covers agent behavior, tool usage, memory quality, skill effectiveness, and model performance. Evaluations are the gatekeeping mechanism for skill promotion.

## Evaluation Framework: DeepEval

- Test case definitions with inputs, expected outputs, and metrics
- Built-in metrics: answer relevancy, faithfulness, hallucination, toxicity, bias
- Custom metric support for Trajecta-specific dimensions
- Comparison runs (baseline vs. candidate)
- Result tracking and reporting

## Evaluation Levels

### Agent Evaluation
- Final task success (binary + graded)
- Correct task completion (matches intent)
- Trajectory quality (minimal wasted steps)
- Unnecessary loops detected
- Failure recovery quality

### Tool Evaluation
- Correct tool selection (right tool for the task)
- Correct arguments (valid, complete, non-redundant)
- Tool-call success rate
- Retry behavior (appropriate retries, no infinite loops)

### Memory Evaluation
- Relevant retrieval (returned memories are useful)
- Incorrect memory usage (memories that misled the agent)
- Stale information detection
- Memory usefulness scoring

### Skill Evaluation
- Improvement over baseline (skill vs. no-skill performance)
- Regression detection (skill makes things worse)
- Generalization (skill works on held-out tasks)
- Efficiency improvement (fewer tokens, tool calls, time)

### Model Evaluation
- Task quality per model
- Latency per model
- Token consumption per model
- Cost per model
- Local vs. hosted performance comparison

## Evaluation Suites

Each suite is a collection of DeepEval test cases with:
- Task description
- Expected outcome (or evaluation criteria)
- Baseline results (for comparison)
- Difficulty level
- Tags (domain, tool type, etc.)

## File Map

```
evals/
├── REQUIREMENTS.md
├── benchmarks/            # Baseline benchmark definitions
├── suites/
│   ├── agent/            # Agent-level evaluation suites
│   ├── tools/            # Tool-level evaluation suites
│   ├── memory/           # Memory evaluation suites
│   ├── skills/           # Skill evaluation suites
│   └── models/           # Model comparison suites
└── results/              # Evaluation run results (gitignored)
```

## Key Interfaces

- `run_evaluation(suite_id, config) -> EvalResult` — execute an evaluation suite via DeepEval
- `compare_results(baseline, candidate) -> ComparisonReport` — compare two runs
- `get_skill_verdict(candidate_skill) -> Verdict` — PASS/FAIL for promotion
- `list_suites(filter) -> list[EvalSuite]` — available evaluation suites
- `create_test_case(input, expected, metrics) -> DeepEvalTestCase` — define a test case
