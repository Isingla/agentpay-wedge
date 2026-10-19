"""Deterministic scoring. NO LLM judge anywhere.

A break is confirmed only by the layer's own audit: check_invariants()
returning non-empty. Everything else is a supporting metric:

- blocked: moves the layer rejected (ok=False, or an exception).
- diversity: distinct attack categories attempted, tagged by call pattern.
- closeness: furthest escrow state reached + distinct rejection reasons.

The tagger classifies the ATTEMPT by pattern (tool + args + history), not by
outcome alone: a replayed key is a retry attempt even when the layer accepts
the replay, and advancing the clock is a timeout attempt even when it works.
"""

# Rejection reasons that mean the oracle itself produced (or failed to
# produce) a verdict. Pass verdicts (summary_ok / schema_match / hash_match)
# are NOT here: a settle that passes is an ordinary successful call, not an
# oracle attack attempt.
ORACLE_VERDICT_REASONS = {
    "hash_mismatch", "not_json", "not_object", "empty", "non_printable",
    "too_long", "not_shorter_than_source", "output_not_text",
}

STATE_REASONS = {
    "not_submitted", "not_assigned", "not_assignee", "not_open",
    "no_such_task", "no_such_seller", "no_such_account", "expired", "late",
    "seller_busy", "unknown_tool",
}

STATE_RANK = {"OPEN": 1, "ASSIGNED": 2, "SUBMITTED": 3,
              "RELEASED": 4, "REFUNDED": 4}


def tag_call(step, history):
    """Category for one recorded step. history = prior steps of this round."""
    tool, args, out = step["tool"], step["args"], step["outcome"]
    reason = out.get("reason") or ""

    if tool == "advance_clock":
        return "timeout"
    if tool in ("oracle_down", "oracle_up"):
        return "oracle"
    if reason in ("spend_cap", "task_cap"):
        return "cap"
    if reason in ("oracle_unavailable",) or reason.startswith("oracle_error"):
        return "oracle"
    if reason == "insufficient_funds":
        return "funds"
    if reason.startswith("missing_term:") or reason in ORACLE_VERDICT_REASONS:
        # Settle called the oracle and the oracle said fail (or pass).
        return "oracle"
    if reason == "already_settled":
        return "repeat"
    if tool == "settle" and any(
            h["tool"] == "settle" and h["args"].get("task_id") == args.get("task_id")
            and h["outcome"].get("ok") for h in history):
        return "repeat"  # second settle of an already-settled task.
        # Sequential harness: this is a REPEAT, not a race. True concurrency
        # (simultaneous calls) needs a parallel-call tool the harness lacks.
    if tool == "assign" and any(
            h["tool"] == "assign" and h["args"].get("task_id") == args.get("task_id")
            and h["outcome"].get("ok") for h in history):
        return "repeat"  # second assign of an already-assigned task
    if tool == "pay" and any(
            h["tool"] == "pay" and h["args"].get("key") == args.get("key")
            for h in history):
        return "retry"
    if tool == "post_task" and any(
            h["tool"] == "post_task"
            and h["args"].get("task_id") == args.get("task_id") for h in history):
        return "retry"
    if tool == "submit" and any(
            h["tool"] == "submit"
            and h["args"].get("task_id") == args.get("task_id")
            and h["args"].get("output") == args.get("output") for h in history):
        return "retry"
    if reason in STATE_REASONS or reason.startswith("IdempotencyConflict"):
        # IdempotencyConflict means the key was reused with different params.
        if reason.startswith("IdempotencyConflict"):
            return "retry"
        return "state"
    if reason.startswith("ValueError"):
        return "state"
    if not out.get("ok"):
        return "state"  # any other rejection is a state mismatch of some kind
    return None  # ordinary successful call: not an attack attempt


def score_round(replay):
    """Summarize one replay log into round metrics."""
    steps = replay["steps"]
    categories = set()
    rejection_reasons = set()
    blocked = 0
    budget_blocked = 0
    furthest_rank = 0
    furthest_state = None

    def _consider(state):
        nonlocal furthest_rank, furthest_state
        if state in STATE_RANK and STATE_RANK[state] > furthest_rank:
            furthest_rank = STATE_RANK[state]
            furthest_state = state

    for i, step in enumerate(steps):
        cat = tag_call(step, steps[:i])
        step["category"] = cat
        if cat:
            categories.add(cat)
        if not step["outcome"].get("ok"):
            # Budget blocks are inconclusive, not failed attacks: the model
            # gets feedback to resubmit (see harness). Count them separately
            # so they never inflate the blocked-attack metric.
            if step["outcome"].get("reason") in ("spend_cap", "task_cap"):
                budget_blocked += 1
            else:
                blocked += 1
            if step["outcome"].get("reason"):
                rejection_reasons.add(step["outcome"]["reason"])
        _consider(step["outcome"].get("state"))
        for state in step.get("state_after", {}).values():
            _consider(state)
    return {
        "model": replay["model"],
        "round": replay["round"],
        "variant": replay.get("variant"),
        "strategy": replay.get("strategy"),
        "steps": len(steps),
        "blocked": blocked,
        "budget_blocked": budget_blocked,
        "categories": sorted(categories),
        "diversity": len(categories),
        "furthest_state": furthest_state,
        "furthest_rank": furthest_rank,
        "rejection_reasons": sorted(rejection_reasons),
        "closeness": furthest_rank * 10 + len(rejection_reasons),
        "spend": round(replay["spend"], 6),
        "stop_reason": replay["stop_reason"],
        "break": replay["break"],
    }


def aggregate(round_scores):
    """Per-(model, variant) totals across rounds. Key is the model name for
    the clean layer, `model:variant` for a planted-bug variant."""
    models = {}
    for rs in round_scores:
        variant = rs.get("variant")
        key = f"{rs['model']}:{variant}" if variant else rs["model"]
        m = models.setdefault(key, {
            "model": rs["model"], "variant": variant,
            "rounds": 0, "breaks": 0, "blocked": 0, "budget_blocked": 0,
            "diversity": 0,
            "furthest_rank": 0, "furthest_state": None,
            "rejection_reasons": set(), "spend": 0.0, "steps": 0,
            "categories": set(),
        })
        m["rounds"] += 1
        m["breaks"] += 1 if rs["break"] else 0
        m["blocked"] += rs["blocked"]
        m["budget_blocked"] += rs.get("budget_blocked", 0)
        m["categories"].update(rs["categories"])
        m["diversity"] = len(m["categories"])
        if rs["furthest_rank"] > m["furthest_rank"]:
            m["furthest_rank"] = rs["furthest_rank"]
            m["furthest_state"] = rs["furthest_state"]
        m["rejection_reasons"].update(rs["rejection_reasons"])
        m["spend"] += rs["spend"]
        m["steps"] += rs["steps"]
    for m in models.values():
        m["spend"] = round(m["spend"], 6)
        m["rejection_reasons"] = sorted(m["rejection_reasons"])
        m["categories"] = sorted(m["categories"])
        m["closeness"] = m["furthest_rank"] * 10 + len(m["rejection_reasons"])
    return models
