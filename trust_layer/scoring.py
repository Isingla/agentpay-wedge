"""Trust-layer scoring: the three losses, per attack class.

Headline metrics (step 3):
- attacks_attempted: attack jobs run.
- attacks_paid: attack jobs that reached RELEASED. Any number above
  zero is a break of the trust layer (the oracle paid an attacker).
- honest_wrongly_rejected: honest jobs that did NOT reach RELEASED.
  The false-alarm cost — a layer that rejects honest work is a layer
  sellers leave.

Per-class tables carry the attack-specific rates (detection,
flip, exploit) computed by the scenario runners in detectors.py and
the suite tests. This module aggregates transcripts; it does not
re-derive scenario rates from thin air.
"""


def score_transcripts(transcripts):
    attacks = [t for t in transcripts if t.get("attack")]
    honest = [t for t in transcripts if not t.get("attack")]
    attacks_paid = [t for t in attacks if t["outcome"] == "RELEASED"]
    honest_rejected = [t for t in honest if t["outcome"] != "RELEASED"]
    breaks = [t for t in transcripts if t.get("violations")]
    by_kind = {}
    for t in transcripts:
        k = t["kind"]
        d = by_kind.setdefault(k, {"attempted": 0, "paid": 0,
                                   "rejected": 0, "parked": 0})
        d["attempted"] += 1
        if t["outcome"] == "RELEASED":
            d["paid"] += 1
        if t["outcome"] == "REFUNDED":
            d["rejected"] += 1
        if t["outcome"] == "parked_no_approval":
            d["parked"] += 1
    return {
        "jobs": len(transcripts),
        "attacks_attempted": len(attacks),
        "attacks_paid": len(attacks_paid),
        "honest_wrongly_rejected": len(honest_rejected),
        "invariant_breaks": len(breaks),
        "by_kind": by_kind,
    }


def score_suite(all_transcripts):
    """all_transcripts: {seed: [transcripts]}. Aggregates + monitor."""
    from .monitor import (escalation_queue, honest_false_flags,
                          marginal_catch_rate, seller_stats)
    flat = [t for ts in all_transcripts.values() for t in ts]
    agg = score_transcripts(flat)
    stats = seller_stats(flat)
    agg["monitor"] = {
        "seller_stats": stats,
        "escalation_queue": escalation_queue(flat, stats),
        "marginal_catch_rate": marginal_catch_rate(flat, stats),
        "honest_false_flags": honest_false_flags(flat, stats),
    }
    # NOT "seeds": callers keep "seeds" as the run count, and the report
    # builds the reproduce command from it.
    agg["seed_list"] = sorted(all_transcripts)
    return agg
