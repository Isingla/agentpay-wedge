"""agentpay tournament: model attackers vs. the payment layer."""
from .adapters import FakeModel, ModelAdapter
from .harness import run_round, run_tournament
from .prompts import SYSTEM_PROMPT

__all__ = ["FakeModel", "ModelAdapter", "run_round", "run_tournament",
           "SYSTEM_PROMPT"]
