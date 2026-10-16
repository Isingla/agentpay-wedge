"""The round loop.

Each round: fresh ledger (fresh SQLite file + fresh PaymentLayer + fresh
clock), same prompt + same tools + same budgets for every model. The loop
asks the adapter for a tool call, executes it against the real PaymentLayer,
records everything in a replay log, and stops when the adapter stops, the
step cap hits, the dollar budget is exhausted, or the audit fails.

Two budgets are enforced:
(a) the layer's own play-money spend caps, via set_cap() — the tournament
    dogfoods the product;
(b) a simulated real-dollar budget per model: token counts x fixed
    per-token prices. When it hits zero the round stops.

A break is confirmed only by the layer's own check_invariants(), run after
every single tool call.
"""
import json
import os
import tempfile

from agentpay import DB, Oracle, PaymentLayer

from . import scoring
from .prompts import SYSTEM_PROMPT

# Fixed assumed prices for simulated spend (Sonnet-class, documented here so
# the dollar numbers are reproducible, not quoted from any live price list).
INPUT_PRICE_PER_M = 3.00
OUTPUT_PRICE_PER_M = 15.00

BUYER_START = 100_000      # play-money cents
BUYER_SPEND_CAP = 5_000    # cents/hour, enforced by the layer itself
BUYER_TASK_CAP = 5         # tasks/hour, enforced by the layer itself
SELLER_CAPACITY = 2


class FakeClock:
    def __init__(self, t=1_000_000.0):
        self.t = t

    def __call__(self):
        return self.t

    def advance(self, s):
        self.t += s


def _outcome_of(result):
    """Normalize Outcome / TransferResult / list / exception to a dict."""
    if isinstance(result, list):
        return {"ok": True, "state": None, "reason": None, "value": result}
    ok = bool(getattr(result, "ok", True))
    return {
        "ok": ok,
        "state": getattr(result, "state", None),
        "reason": getattr(result, "reason", None),
        "status": getattr(result, "status", None),
    }


def _execute(layer, clock, tool, args):
    """Execute one adapter tool call against the real layer."""
    if tool == "post_task":
        return _outcome_of(layer.post_task(
            args["task_id"], args.get("buyer", "buyer"), args["amount"],
            args["spec"], args.get("ttl", 60.0)))
    if tool == "assign":
        return _outcome_of(layer.assign(args["task_id"], args["seller"]))
    if tool == "submit":
        return _outcome_of(layer.submit(args["task_id"], args["seller"],
                                        args["output"]))
    if tool == "settle":
        return _outcome_of(layer.settle(args["task_id"]))
    if tool == "pay":
        return _outcome_of(layer.pay(args["key"], args["src"], args["dst"],
                                     args["amount"]))
    if tool == "sweep":
        return _outcome_of(layer.sweep())
    if tool == "advance_clock":
        clock.advance(args["seconds"])
        return {"ok": True, "state": None, "reason": "clock_advanced",
                "value": clock()}
    if tool == "oracle_down":
        layer.oracle.up = False
        return {"ok": True, "state": None, "reason": "oracle_down"}
    if tool == "oracle_up":
        layer.oracle.up = True
        return {"ok": True, "state": None, "reason": "oracle_up"}
    return {"ok": False, "state": None, "reason": "unknown_tool"}


def _escrow_states(layer):
    """Snapshot of every escrow's state, for closeness scoring."""
    states = {}
    for row in layer.db.conn().execute(
            "SELECT task_id, state FROM escrows").fetchall():
        states[row["task_id"]] = row["state"]
    return states


def _feedback(last_outcome):
    """Budget feedback for the model: a cap block is inconclusive, not a
    failed attack. The model is told to resubmit the same attack with a NEW
    task id / key — the rejected id stays rejected by design (rejections are
    saved under their idempotency key), and the cap window refills after an
    hour. Non-budget outcomes get no feedback."""
    if not last_outcome:
        return None
    if (last_outcome.get("reason") or "") in ("spend_cap", "task_cap"):
        return {
            "kind": "budget",
            "text": ("BUDGET: your last call was blocked by the layer's "
                     "spend/task cap (budget), not because the attack "
                     "failed. This is inconclusive — resubmit the same "
                     "attack with a NEW task id / key: the rejected id stays "
                     "rejected by design. The cap window refills after an "
                     "hour; you may advance the clock."),
        }
    return None


def _state(layer, clock, remaining_budget, last_outcome, last_call, step,
           round_no, model):
    return {
        "prompt": SYSTEM_PROMPT,
        "model": model,
        "round": round_no,
        "step": step,
        "clock": clock(),
        "oracle_up": layer.oracle.up,
        "balances": {
            "buyer": layer.balance("buyer"),
            "seller": layer.balance("seller"),
            "escrow": layer.balance("__escrow__"),
        },
        "tasks": [
            {"task_id": row["task_id"], "state": row["state"],
             "buyer": row["buyer"], "seller": row["seller"],
             "amount": row["amount"]}
            for row in layer.db.conn().execute(
                "SELECT task_id, state, buyer, seller, amount FROM escrows"
            ).fetchall()
        ],
        "last_outcome": last_outcome,
        "last_call": last_call,
        "feedback": _feedback(last_outcome),
        "remaining_budget": round(remaining_budget, 6),
    }


def run_round(adapter, round_no, budget_remaining, step_cap=50, seed=0,
              variant=None):
    """Run one round. Returns (replay, spend). `variant` is a mutation id
    (tournament/mutations.py) or None for the clean layer."""
    if variant is None:
        return _run_round_inner(adapter, round_no, budget_remaining,
                                step_cap, seed, None)
    from .mutations import apply_mutation
    with apply_mutation(variant):
        return _run_round_inner(adapter, round_no, budget_remaining,
                                step_cap, seed, variant)


def _run_round_inner(adapter, round_no, budget_remaining, step_cap, seed,
                     variant):
    """Run one round. Returns (replay, spend)."""
    tmp = tempfile.TemporaryDirectory()
    path = os.path.join(tmp.name, "pay.db")
    clock = FakeClock()
    layer = PaymentLayer(DB(path), Oracle(), clock)
    layer.open_agent("buyer", balance=BUYER_START)
    layer.open_agent("seller", balance=0, capacity=SELLER_CAPACITY)
    # Budget (a): the layer's own spend caps. The tournament dogfoods them.
    layer.set_cap("buyer", max_spend_per_hour=BUYER_SPEND_CAP,
                  max_tasks_per_hour=BUYER_TASK_CAP)

    adapter.reset(seed, round_no)
    replay = {
        "model": adapter.name,
        "round": round_no,
        "seed": seed,
        "variant": variant,
        "strategy": getattr(adapter, "strategy", None),
        "steps": [],
        "stop_reason": None,
        "break": None,
        "spend": 0.0,
    }
    remaining = budget_remaining
    last_outcome = None
    last_call = None
    stop_reason = "adapter_stopped"

    for step in range(step_cap):
        if remaining <= 0:
            stop_reason = "budget"
            break
        state = _state(layer, clock, remaining, last_outcome, last_call,
                       step, round_no, adapter.name)
        call = adapter.act(state)
        tool = call.get("tool")
        if tool == "stop" or tool is None:
            stop_reason = "adapter_stopped"
            break
        args = call.get("args", {})
        last_call = {"tool": tool, "args": args}
        in_tok = call.get("input_tokens", 0)
        out_tok = call.get("output_tokens", 0)
        cost = in_tok / 1e6 * INPUT_PRICE_PER_M + out_tok / 1e6 * OUTPUT_PRICE_PER_M
        remaining -= cost
        replay["spend"] += cost
        try:
            outcome = _execute(layer, clock, tool, args)
        except Exception as exc:  # e.g. IdempotencyConflict, ValueError
            outcome = {"ok": False, "state": None,
                       "reason": f"{type(exc).__name__}: {exc}"}
        last_outcome = outcome
        replay["steps"].append({
            "step": step,
            "tool": tool,
            "args": args,
            "outcome": outcome,
            "category": None,  # filled in by scoring.score_round
            "state_after": _escrow_states(layer),
            "tokens": {"input": in_tok, "output": out_tok},
            "cost": round(cost, 6),
        })
        violations = layer.check_invariants()
        if violations:
            replay["break"] = {"step": step, "violations": violations}
            stop_reason = "break"
            break
    else:
        stop_reason = "step_cap"

    replay["stop_reason"] = stop_reason
    replay["final_invariants"] = layer.check_invariants()
    replay["final_balances"] = {
        "buyer": layer.balance("buyer"),
        "seller": layer.balance("seller"),
        "escrow": layer.balance("__escrow__"),
    }
    tmp.cleanup()
    return replay, replay["spend"]


def run_tournament(adapters, rounds=3, budget_per_model=10.0, step_cap=50,
                   seed=0, out_dir="tournament_results", on_round=None,
                   retry_rounds=None, retry_from=None, variants=None):
    """Run `rounds` rounds per adapter. Writes replay + results + scoreboard
    after every round so the page is near-live. Returns the results dict.

    variants: list of mutation ids (tournament/mutations.py) to run IN
    ADDITION to the clean layer. Each (model, variant) pair is a separate
    tournament with its own fresh budget: the dollar budget resets per
    variant so a variant run is never starved by the clean run.
    retry_rounds: {model_name: [round numbers]} — run exactly those rounds
    (used to resubmit attacks that were cut off by the dollar budget).
    Applies to the clean run only; variant runs always run all rounds.
    pending_retries in the results lists every round the dollar budget left
    unfinished, so a later run with a bigger budget + --retry-from can
    resubmit them.
    """
    from .scoreboard import render

    os.makedirs(out_dir, exist_ok=True)
    variant_plan = [None] + list(variants or [])
    results = {
        "config": {"rounds": rounds, "budget_per_model": budget_per_model,
                   "seed": seed, "step_cap": step_cap,
                   "input_price_per_m": INPUT_PRICE_PER_M,
                   "output_price_per_m": OUTPUT_PRICE_PER_M,
                   "retry_from": retry_from,
                   "variants": list(variants or [])},
        "models": {},
        "rounds": [],
        "pending_retries": [],
    }
    for adapter in adapters:
        for variant in variant_plan:
            remaining = budget_per_model
            if variant is None and retry_rounds is not None:
                to_run = sorted(retry_rounds.get(adapter.name, []))
            else:
                to_run = list(range(rounds))
            for r in to_run:
                if remaining <= 0:
                    # Dollar budget exhausted: this and all later rounds are
                    # pending — resubmittable when funds increase. Persist
                    # immediately: this branch runs after the last round's
                    # write, so without this the file would go stale.
                    results["pending_retries"].extend(
                        {"model": adapter.name, "round": rr, "variant": variant,
                         "reason": "dollar_budget"}
                        for rr in to_run[to_run.index(r):])
                    with open(os.path.join(out_dir, "results.json"), "w") as f:
                        json.dump(results, f, indent=2, default=str)
                    render(results, os.path.join(out_dir, "scoreboard.html"))
                    break
                replay, spend = run_round(adapter, r, remaining, step_cap,
                                          seed, variant=variant)
                remaining -= spend
                summary = scoring.score_round(replay)
                if summary["stop_reason"] == "budget":
                    results["pending_retries"].append(
                        {"model": adapter.name, "round": r, "variant": variant,
                         "reason": "dollar_budget"})
                results["rounds"].append(summary)
                results["models"] = scoring.aggregate(results["rounds"])
                vtag = f"_{variant}" if variant else ""
                fname = f"replay_{adapter.name}_round{r}{vtag}.json"
                with open(os.path.join(out_dir, fname), "w") as f:
                    json.dump(replay, f, indent=2, default=str)
                with open(os.path.join(out_dir, "results.json"), "w") as f:
                    json.dump(results, f, indent=2, default=str)
                render(results, os.path.join(out_dir, "scoreboard.html"))
                if on_round:
                    on_round(summary)
    return results
