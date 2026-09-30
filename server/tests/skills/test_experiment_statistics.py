"""The two readings every experiment leans on: z-test and posterior."""

from __future__ import annotations

import pytest

from server.src.skills.experiments.statistics import (
    BinaryArm,
    bayesian_difference_probability,
    pairwise_evidence,
    two_proportion_evidence,
)


def test_clear_treatment_win_has_small_frequentist_p_value() -> None:
    control = BinaryArm(version="1.0.0", successes=60, total=100)
    treatment = BinaryArm(version="1.1.0", successes=82, total=100)

    z_score, p_superiority, p_harm = two_proportion_evidence(control, treatment)

    assert z_score > 0
    assert p_superiority < 0.01
    assert p_harm > 0.99


def test_clear_treatment_harm_has_small_harm_p_value() -> None:
    control = BinaryArm(version="1.0.0", successes=85, total=100)
    treatment = BinaryArm(version="1.1.0", successes=60, total=100)

    z_score, p_superiority, p_harm = two_proportion_evidence(control, treatment)

    assert z_score < 0
    assert p_harm < 0.01
    assert p_superiority > 0.99


def test_bayesian_probability_is_deterministic() -> None:
    control = BinaryArm(version="1.0.0", successes=50, total=100)
    treatment = BinaryArm(version="1.1.0", successes=75, total=100)

    first = bayesian_difference_probability(
        control,
        treatment,
        superiority_margin=0.03,
        harm_margin=0.05,
        draws=2000,
        seed_key="stable-test",
    )
    second = bayesian_difference_probability(
        control,
        treatment,
        superiority_margin=0.03,
        harm_margin=0.05,
        draws=2000,
        seed_key="stable-test",
    )

    assert first == second
    assert first[0] > 0.95
    assert first[1] < 0.05


def test_pairwise_evidence_reports_effect_size() -> None:
    control = BinaryArm(version="1.0.0", successes=70, total=100)
    treatment = BinaryArm(version="1.1.0", successes=80, total=100)

    evidence = pairwise_evidence(
        control,
        treatment,
        minimum_effect=0.03,
        harm_effect=0.05,
        bayesian_draws=2000,
        seed_key="pairwise-test",
    )

    assert evidence.delta == pytest.approx(0.1)
    assert evidence.treatment_rate == 0.8
    assert evidence.control_rate == 0.7


def test_empty_arms_neutral_evidence() -> None:
    control = BinaryArm(version="1.0.0", successes=0, total=0)
    treatment = BinaryArm(version="1.1.0", successes=0, total=0)

    z_score, p_superiority, p_harm = two_proportion_evidence(control, treatment)

    assert z_score == 0.0
    assert p_superiority == 1.0
    assert p_harm == 1.0
    assert control.rate == 0.0
