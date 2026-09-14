"""Skill representation — schema, loading, and serialization.

A skill on disk:
  skills/<skill-name>/
    SKILL.md            human-readable description
    workflow.yaml       execution workflow definition
    metadata.json       skill metadata (version, author, metrics)
    eval.yaml           evaluation configuration
    tests/              DeepEval test cases
    versions/           version history
"""

from __future__ import annotations


class Skill:
    """A single versioned skill definition."""

    # TODO: load/save skill bundle, validate structure (SKILL.md, workflow.yaml, metadata.json, eval.yaml)
    pass