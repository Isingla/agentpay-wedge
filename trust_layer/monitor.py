"""Behavioral monitor + escalation tier (step 8).

The monitor reads job transcripts (never the layer internals) and flags
sellers on three behavioral signals:

- Pass-rate jump: second-half pass rate minus first-half pass rate above
  threshold (a seller that suddenly starts passing everything).
- Verdict-flip clustering: repeated settle attempts / resubmits on the
  same job.
- Evidence anomalies: duplicate evidence across different jobs, or a
  repeated distinctive template phrase across a seller's deliveries.

Escalation: a job goes to human/expert review when its price is at or
above the threshold OR its seller is flagged. The review queue is an
output file, not an actual human — Ishaan runs the review.

The only metric that justifies the tier's cost is the MARGINAL CATCH
RATE: attacks the oracle passed (RELEASED) that the monitor flagged,
divided by all attacks the oracle passed. A monitor that only flags
what the oracle already caught is pure cost.
"""
from collections import defaultdict

TEMPLATE_PHRASE = "rest assured"  # toy template-reuse signal, see sellers.py
ESCALATION_PRICE_CENTS = 1000
PASS_JUMP_THRESHOLD = 0.4
FLIP_THRESHOLD = 2


def seller_stats(transcripts):
    """Per-seller behavioral stats from transcripts."""
    by_seller = defaultdict(list)
    for t in transcripts:
        by_seller[t["seller"]].append(t)
    stats = {}
    for seller, ts in by_seller.items():
        n = len(ts)
        passed = sum(1 for t in ts if t["outcome"] == "RELEASED")
        half = n // 2
        first = ts[:half] or ts
        second = ts[half:] or ts
        first_rate = (sum(1 for t in first if t["outcome"] == "RELEASED")
                      / len(first))
        second_rate = (sum(1 for t in second if t["outcome"] == "RELEASED")
                       / len(second))
        flips = sum(t.get("settle_attempts", 1) - 1 for t in ts)
        # Duplicate evidence = the SAME delivery submitted for DIFFERENT
        # tasks. Repeating the same correct answer to the same task (e.g.
        # across seeds) is normal, so it is not counted.
        tasks_per_evidence = defaultdict(set)
        for t in ts:
            if t.get("evidence_hash"):
                tasks_per_evidence[t["evidence_hash"]].add(t["job_id"])
        dup_evidence = sum(len(j) - 1 for j in tasks_per_evidence.values()
                           if len(j) > 1)
        template_hits = sum(1 for t in ts
                            if TEMPLATE_PHRASE in (t.get("delivery") or "").lower())
        flags = []
        if second_rate - first_rate >= PASS_JUMP_THRESHOLD:
            flags.append("pass_rate_jump")
        if flips >= FLIP_THRESHOLD:
            flags.append("flip_clustering")
        if dup_evidence > 0:
            flags.append("duplicate_evidence")
        if template_hits >= 2:
            flags.append("template_reuse")
        stats[seller] = {
            "jobs": n,
            "pass_rate": passed / n if n else 0.0,
            "pass_rate_jump": second_rate - first_rate,
            "flips": flips,
            "duplicate_evidence": dup_evidence,
            "template_hits": template_hits,
            "flags": flags,
            "flagged": bool(flags),
        }
    return stats


def escalation_queue(transcripts, stats, price_threshold=ESCALATION_PRICE_CENTS):
    """Jobs needing human/expert review: high price OR flagged seller."""
    queue = []
    for t in transcripts:
        if (t.get("price_cents", 0) >= price_threshold
                or stats.get(t["seller"], {}).get("flagged")):
            queue.append({"job_id": t["job_id"], "seller": t["seller"],
                          "price_cents": t.get("price_cents", 0),
                          "outcome": t["outcome"],
                          "reason": ("high_value" if t.get("price_cents", 0)
                                     >= price_threshold else "flagged_seller")})
    return queue


def honest_false_flags(transcripts, stats):
    """The monitor's false-alarm cost: honest jobs whose seller got
    flagged (and so were escalated for review)."""
    honest = [t for t in transcripts if not t.get("attack")]
    flagged = sum(1 for t in honest
                  if stats.get(t["seller"], {}).get("flagged"))
    return {"honest_jobs": len(honest), "flagged": flagged,
            "rate": flagged / len(honest) if honest else None}


def marginal_catch_rate(transcripts, stats):
    """Attacks the oracle passed that the monitor flagged / all attacks
    the oracle passed. Attacks = transcripts with attack=True."""
    passed_attacks = [t for t in transcripts
                      if t.get("attack") and t["outcome"] == "RELEASED"]
    if not passed_attacks:
        return {"passed_attacks": 0, "caught_by_monitor": 0, "rate": None,
                "note": "no attacks passed the oracle in this run"}
    caught = sum(1 for t in passed_attacks
                 if stats.get(t["seller"], {}).get("flagged"))
    return {"passed_attacks": len(passed_attacks),
            "caught_by_monitor": caught,
            "rate": caught / len(passed_attacks)}
