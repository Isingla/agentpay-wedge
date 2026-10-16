"""Adaptive rounds (step 7): attackers that change strategy between rounds.

An AdaptiveAttacker cycles attack strategies across rounds and escalates
after failed rounds: substitution -> fake approval -> judge gaming ->
intensify (same strategy, stronger parameters). The exploit curve is
per-round exploit success (attacks paid / attacks attempted) with Wilson
intervals across seeds (stats.py). With scripted attackers the curve is
a DEMO of the plumbing; the real curve comes from step 7's real models.
"""
from .sellers import (FakeApprovalSeller, JudgeGamingSeller,
                      SubstitutionSeller)

STRATEGIES = ("substitution", "fake_approval", "judge_gaming")


class AdaptiveAttacker:
    """Round-robin with escalation: after a round with zero exploit
    success, move to the next strategy; after a full cycle, intensify."""

    def __init__(self, distinguishability=0.5):
        self.round = 0
        self.idx = 0
        self.intensity = 1
        self.distinguishability = distinguishability
        self.curve = []

    @property
    def strategy(self):
        return STRATEGIES[self.idx % len(STRATEGIES)]

    def seller_for_round(self):
        s = self.strategy
        if s == "substitution":
            return SubstitutionSeller(self.distinguishability)
        if s == "fake_approval":
            return FakeApprovalSeller()
        return JudgeGamingSeller()

    def observe_round(self, paid, attempted):
        self.curve.append({"round": self.round, "paid": paid,
                           "attempted": attempted,
                           "rate": paid / attempted if attempted else 0.0,
                           "strategy": self.strategy,
                           "intensity": self.intensity})
        self.round += 1
        if paid == 0:
            self.idx += 1
            if self.idx % len(STRATEGIES) == 0:
                self.intensity += 1
