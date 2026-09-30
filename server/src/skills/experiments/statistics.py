"""Statistical helpers for verified-skill experiments.

Every live experiment gets both readings: a frequentist two-proportion
z-test and a Beta(1,1) posterior over the success-rate difference. An
automatic decision only happens when the two systems agree, so a lucky
streak on a handful of samples cannot promote a version on its own.
"""

from __future__ import annotations

import hashlib
import math
import random

from dataclasses import dataclass


@dataclass(slots=True, frozen=True)
class BinaryArm:
    """Success counts for one experiment arm."""

    version: str
    successes: int
    total: int

    @property
    def failures(self) -> int:
        return max(0, self.total - self.successes)

    @property
    def rate(self) -> float:
        if not self.total:
            return 0.0

        return self.successes / self.total


@dataclass(slots=True, frozen=True)
class PairwiseEvidence:
    """Frequentist and Bayesian readings for one control/treatment pair."""

    control_version: str
    treatment_version: str

    control_rate: float
    treatment_rate: float

    delta: float

    z_score: float

    p_superiority: float
    p_harm: float

    bayes_superiority: float
    bayes_harm: float


def two_proportion_evidence(
    control: BinaryArm,
    treatment: BinaryArm,
) -> tuple[float, float, float]:
    """Return z, one-sided superiority p, and one-sided harm p.

    The pooled-variance z-test is undefined when either arm is empty or
    when both arms are perfect (variance 0), so those degenerate cases
    return neutral readings instead of raising.
    """

    if control.total <= 0 or treatment.total <= 0:
        return (0.0, 1.0, 1.0)

    p0 = control.rate
    p1 = treatment.rate

    pooled = (control.successes + treatment.successes) / (
        control.total + treatment.total
    )

    variance = pooled * (1.0 - pooled) * (1.0 / control.total + 1.0 / treatment.total)

    if variance <= 0.0:
        # A degenerate split: everyone in one arm passed, no one in the
        # other. The direction still tells us something even though the
        # test statistic has no finite value.
        if p1 > p0:
            return (math.inf, 0.0, 1.0)

        if p1 < p0:
            return (-math.inf, 1.0, 0.0)

        return (0.0, 1.0, 1.0)

    z = (p1 - p0) / math.sqrt(variance)

    p_superiority = 0.5 * math.erfc(z / math.sqrt(2.0))
    p_harm = 0.5 * math.erfc(-z / math.sqrt(2.0))

    return (z, p_superiority, p_harm)


def bayesian_difference_probability(
    control: BinaryArm,
    treatment: BinaryArm,
    *,
    superiority_margin: float,
    harm_margin: float,
    draws: int,
    seed_key: str,
) -> tuple[float, float]:
    """Beta-Binomial posterior probability, with Beta(1,1) priors.

    Monte Carlo is deterministic for the same experiment/arm pair so API
    responses and tests do not randomly change between calls: the seed is
    derived from ``seed_key`` rather than drawn from global state.
    """

    digest = hashlib.sha256(seed_key.encode("utf-8")).digest()
    rng = random.Random(int.from_bytes(digest[:8], "big"))

    superiority = 0
    harm = 0

    draws = max(1000, int(draws))

    for _ in range(draws):
        p0 = rng.betavariate(control.successes + 1, control.failures + 1)
        p1 = rng.betavariate(treatment.successes + 1, treatment.failures + 1)

        delta = p1 - p0

        if delta >= superiority_margin:
            superiority += 1

        if delta <= -harm_margin:
            harm += 1

    return (superiority / draws, harm / draws)


def pairwise_evidence(
    control: BinaryArm,
    treatment: BinaryArm,
    *,
    minimum_effect: float,
    harm_effect: float,
    bayesian_draws: int,
    seed_key: str,
) -> PairwiseEvidence:
    """Both readings for one control/treatment comparison."""

    z, p_superiority, p_harm = two_proportion_evidence(control, treatment)

    bayes_superiority, bayes_harm = bayesian_difference_probability(
        control,
        treatment,
        superiority_margin=minimum_effect,
        harm_margin=harm_effect,
        draws=bayesian_draws,
        seed_key=seed_key,
    )

    return PairwiseEvidence(
        control_version=control.version,
        treatment_version=treatment.version,
        control_rate=control.rate,
        treatment_rate=treatment.rate,
        delta=treatment.rate - control.rate,
        z_score=z,
        p_superiority=p_superiority,
        p_harm=p_harm,
        bayes_superiority=bayes_superiority,
        bayes_harm=bayes_harm,
    )
