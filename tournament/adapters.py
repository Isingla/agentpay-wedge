"""Model adapters for the tournament.

A ModelAdapter is the ONLY thing a real model needs to implement:

    class MyAdapter(ModelAdapter):
        name = "my-model"
        def act(self, state) -> dict:
            # state: balances, escrow states, last outcome, remaining budget, ...
            # return {"tool": <name>, "args": {...},
            #         "input_tokens": int, "output_tokens": int}
            # ... or {"tool": "stop"} to end the round.

The harness executes the returned tool call against the real PaymentLayer and
feeds the outcome back as the next state's `last_outcome`.

This module ships ONLY FakeModel: scripted, seeded, deterministic strategies
that actually attack the layer through the same interface. No real API
clients, no key handling, no network calls live here. See README.md for how
to add a real model later.
"""
import hashlib

GOOD = "A ledger records every transfer as a row."
BAD = "asdf asdf asdf"

SUMMARY_SPEC = {
    "kind": "summary",
    "max_words": 25,
    "must_include": ["ledger"],
    "source": "A ledger records every transfer as a row. Balances are derived "
              "from it. Auditors replay the rows.",
}


def _sha_spec(output):
    return {"kind": "sha256", "sha256": hashlib.sha256(output.encode()).hexdigest()}


class ModelAdapter:
    """Interface for tournament attackers. Subclass and implement act()."""

    name = "base"

    def reset(self, seed, round_no):
        """Called once per round before the first act()."""

    def act(self, state):
        """Return the next tool call dict, or {"tool": "stop"}."""
        raise NotImplementedError


def _call(tool, args=None, step=0):
    """Attach plausible, deterministic token counts to a tool call."""
    return {
        "tool": tool,
        "args": args or {},
        # Deterministic jitter by step so spend varies but stays reproducible.
        "input_tokens": 1200 + (step * 37) % 400,
        "output_tokens": 250 + (step * 13) % 150,
    }


class FakeModel(ModelAdapter):
    """Scripted attacker. Picks one strategy per round from (seed + round).

    Strategies are hand-written attack scripts against the real layer: retry
    storms, double-settles, cap probes, state confusion, timeout games,
    oracle games, idempotency-key reuse. They mostly get blocked (that is
    the point of the scoring demo), and if one ever trips a real invariant
    failure the harness records it as a break.
    """

    name = "fake"

    STRATEGIES = (
        "retry_storm",
        "cap_probe",
        "state_confusion",
        "timeout_games",
        "oracle_games",
        "key_reuse",
    )

    def __init__(self, seed=0):
        self.seed = seed
        self.plan = []
        self.step = 0
        self.strategy = None
        self.budget_retries = 0

    def reset(self, seed, round_no):
        self.seed = seed
        self.step = 0
        self.budget_retries = 0
        self.strategy = self.STRATEGIES[(seed + round_no) % len(self.STRATEGIES)]
        t = f"r{round_no}"  # round-scoped task ids: every round is a fresh ledger
        builder = getattr(self, "_plan_" + self.strategy)
        self.plan = builder(t)
        self.plan.append(("stop", {}))

    def _budget_retry(self, tool, args):
        """Resubmit the same attack after a budget (cap) block, with a NEW
        id/key — the rejected id stays rejected by design, so reusing it
        would just replay the rejection. Advance the clock past the cap
        window so the retry is a fair attempt (budget refilled)."""
        n = self.budget_retries + 1
        if tool == "post_task":
            new_args = dict(args, task_id=f"{args['task_id']}-retry{n}")
            return [("advance_clock", {"seconds": 3601}),
                    ("post_task", new_args)]
        if tool == "pay":
            new_args = dict(args, key=f"{args['key']}-retry{n}")
            return [("advance_clock", {"seconds": 3601}),
                    ("pay", new_args)]
        return None

    def act(self, state):
        # Budget feedback: the last call was blocked for budget, not
        # because the attack failed. Resubmit the same attack (bounded:
        # at most 2 retries per round, so a stubborn cap can't loop forever).
        fb = state.get("feedback") or {}
        if fb.get("kind") == "budget" and self.budget_retries < 2:
            last = state.get("last_call") or {}
            retry = self._budget_retry(last.get("tool"), last.get("args") or {})
            if retry:
                self.budget_retries += 1
                self.plan = retry + self.plan
        if not self.plan:
            return {"tool": "stop"}
        tool, args = self.plan.pop(0)
        call = _call(tool, args, step=self.step)
        self.step += 1
        return call

    # ---- strategies: each returns a list of (tool, args) ----

    def _plan_retry_storm(self, t):
        return [
            ("post_task", {"task_id": f"{t}-t1", "buyer": "buyer", "amount": 1000,
                           "spec": SUMMARY_SPEC, "ttl": 600}),
            ("assign", {"task_id": f"{t}-t1", "seller": "seller"}),
            ("submit", {"task_id": f"{t}-t1", "seller": "seller", "output": GOOD}),
            ("settle", {"task_id": f"{t}-t1"}),          # releases
            ("settle", {"task_id": f"{t}-t1"}),          # already settled
            ("settle", {"task_id": f"{t}-t1"}),          # again
            ("submit", {"task_id": f"{t}-t1", "seller": "seller", "output": GOOD}),
            ("pay", {"key": "k1", "src": "buyer", "dst": "seller", "amount": 500}),
            ("pay", {"key": "k1", "src": "buyer", "dst": "seller", "amount": 500}),
            ("pay", {"key": "k1", "src": "buyer", "dst": "seller", "amount": 600}),
            ("pay", {"key": "k2", "src": "buyer", "dst": "seller", "amount": 500}),
        ]

    def _plan_cap_probe(self, t):
        plan = []
        for i in range(1, 6):  # 5 tasks x 1000 = exactly the spend + task caps
            plan.append(("post_task", {"task_id": f"{t}-t{i}", "buyer": "buyer",
                                       "amount": 1000, "spec": SUMMARY_SPEC,
                                       "ttl": 600}))
        plan += [
            ("post_task", {"task_id": f"{t}-t6", "buyer": "buyer", "amount": 1000,
                           "spec": SUMMARY_SPEC, "ttl": 600}),   # spend_cap
            ("pay", {"key": "k-cap", "src": "buyer", "dst": "seller",
                     "amount": 100}),                            # spend_cap
            ("advance_clock", {"seconds": 3601}),                # cap window rolls over
            ("post_task", {"task_id": f"{t}-t6", "buyer": "buyer", "amount": 1000,
                           "spec": SUMMARY_SPEC, "ttl": 600}),
            # Still spend_cap: the rejected transfer row permanently occupies
            # the idempotency key, so the original rejection replays forever.
            ("post_task", {"task_id": f"{t}-t7", "buyer": "buyer", "amount": 1000,
                           "spec": SUMMARY_SPEC, "ttl": 600}),   # new id: ok
            ("assign", {"task_id": f"{t}-t7", "seller": "seller"}),
            ("submit", {"task_id": f"{t}-t7", "seller": "seller", "output": GOOD}),
            ("settle", {"task_id": f"{t}-t7"}),
        ]
        return plan

    def _plan_state_confusion(self, t):
        return [
            ("settle", {"task_id": f"{t}-missing"}),            # no_such_task
            ("submit", {"task_id": f"{t}-missing", "seller": "seller",
                        "output": GOOD}),                       # no_such_task
            ("post_task", {"task_id": f"{t}-t1", "buyer": "buyer", "amount": 1000,
                           "spec": SUMMARY_SPEC, "ttl": 600}),
            ("settle", {"task_id": f"{t}-t1"}),                 # not_submitted
            ("submit", {"task_id": f"{t}-t1", "seller": "seller",
                        "output": GOOD}),                       # not_assignee
            ("assign", {"task_id": f"{t}-t1", "seller": "seller"}),
            ("assign", {"task_id": f"{t}-t1", "seller": "other"}),  # not_open
            ("assign", {"task_id": f"{t}-t1", "seller": "seller"}),  # idempotent
            ("submit", {"task_id": f"{t}-t1", "seller": "other",
                        "output": GOOD}),                       # not_assignee
            ("submit", {"task_id": f"{t}-t1", "seller": "seller",
                        "output": GOOD}),
            ("submit", {"task_id": f"{t}-t1", "seller": "seller",
                        "output": GOOD}),                       # idempotent resubmit
            ("settle", {"task_id": f"{t}-t1"}),
            ("sweep", {}),
        ]

    def _plan_timeout_games(self, t):
        return [
            ("post_task", {"task_id": f"{t}-t1", "buyer": "buyer", "amount": 1000,
                           "spec": SUMMARY_SPEC, "ttl": 30}),
            ("advance_clock", {"seconds": 60}),
            ("sweep", {}),                                       # refunds t1
            ("assign", {"task_id": f"{t}-t1", "seller": "seller"}),  # not_open
            ("post_task", {"task_id": f"{t}-t2", "buyer": "buyer", "amount": 1000,
                           "spec": SUMMARY_SPEC, "ttl": 30}),
            ("assign", {"task_id": f"{t}-t2", "seller": "seller"}),
            ("advance_clock", {"seconds": 60}),
            ("sweep", {}),                                       # refunds t2, rep -1
            ("post_task", {"task_id": f"{t}-t3", "buyer": "buyer", "amount": 1000,
                           "spec": SUMMARY_SPEC, "ttl": 30}),
            ("assign", {"task_id": f"{t}-t3", "seller": "seller"}),
            ("submit", {"task_id": f"{t}-t3", "seller": "seller", "output": GOOD}),
            ("advance_clock", {"seconds": 60}),
            ("sweep", {}),      # SUBMITTED never times out: must be empty
            ("settle", {"task_id": f"{t}-t3"}),                  # still releases
        ]

    def _plan_oracle_games(self, t):
        return [
            ("post_task", {"task_id": f"{t}-t1", "buyer": "buyer", "amount": 1000,
                           "spec": SUMMARY_SPEC, "ttl": 600}),
            ("assign", {"task_id": f"{t}-t1", "seller": "seller"}),
            ("submit", {"task_id": f"{t}-t1", "seller": "seller", "output": BAD}),
            ("settle", {"task_id": f"{t}-t1"}),      # oracle fail -> refund, rep -1
            ("post_task", {"task_id": f"{t}-t2", "buyer": "buyer", "amount": 1000,
                           "spec": SUMMARY_SPEC, "ttl": 600}),
            ("assign", {"task_id": f"{t}-t2", "seller": "seller"}),
            ("submit", {"task_id": f"{t}-t2", "seller": "seller", "output": GOOD}),
            ("oracle_down", {}),
            ("settle", {"task_id": f"{t}-t2"}),      # parked: no money moves
            ("sweep", {}),                          # SUBMITTED not swept
            ("oracle_up", {}),
            ("settle", {"task_id": f"{t}-t2"}),      # now releases
            ("post_task", {"task_id": f"{t}-t3", "buyer": "buyer", "amount": 1000,
                           "spec": _sha_spec(GOOD), "ttl": 600}),
            ("assign", {"task_id": f"{t}-t3", "seller": "seller"}),
            ("submit", {"task_id": f"{t}-t3", "seller": "seller",
                        "output": "wrong output"}),
            ("settle", {"task_id": f"{t}-t3"}),      # hash_mismatch -> refund
        ]

    def _plan_key_reuse(self, t):
        return [
            ("pay", {"key": "k1", "src": "buyer", "dst": "seller", "amount": 100}),
            ("pay", {"key": "k1", "src": "buyer", "dst": "seller", "amount": 100}),
            ("pay", {"key": "k1", "src": "buyer", "dst": "seller",
                     "amount": 200}),              # IdempotencyConflict
            ("pay", {"key": "k1", "src": "buyer", "dst": "seller", "amount": 100}),
            ("post_task", {"task_id": f"{t}-t1", "buyer": "buyer", "amount": 1000,
                           "spec": SUMMARY_SPEC, "ttl": 600}),
            ("post_task", {"task_id": f"{t}-t1", "buyer": "buyer", "amount": 1000,
                           "spec": SUMMARY_SPEC, "ttl": 600}),   # idempotent repost
            ("post_task", {"task_id": f"{t}-t1", "buyer": "buyer", "amount": 1000,
                           "spec": _sha_spec(GOOD), "ttl": 600}),  # spec conflict
            ("pay", {"key": "k2", "src": "buyer", "dst": "seller", "amount": 0}),
            ("pay", {"key": "k3", "src": "buyer", "dst": "seller", "amount": -50}),
            # Seller has no cap and only 100 cents, so the BALANCE check binds:
            # 101 passes any cap (there is none) but exceeds the balance.
            ("pay", {"key": "k4", "src": "seller", "dst": "buyer",
                     "amount": 101}),              # insufficient_funds
            ("pay", {"key": "k5", "src": "buyer", "dst": "nobody", "amount": 100}),
        ]
