"""Substitution detection (step 4): probes + spot-checks.

The attack: a seller is paid for a frontier model and serves a cheaper
one. The cheap output is PLAUSIBLE: it passes the oracle's format checks
on normal jobs, so the oracle alone pays for it. Two mechanisms catch it:

1. Probes (before payment): the same task with a known answer, graded
   on that answer at payment time. A substitute that serves cheap output
   on a probe is refunded -> caught before money moves.
2. Spot-checks (after payment): a random fraction of NORMAL jobs are
   re-graded on the known-answer facts after release. A failure is a
   catch, but the money already moved (clawback territory). This is the
   backstop against selective routing: an attacker that spots probes and
   serves the real model only on them still gets caught on spot-checks.

Both checks are FACT checks (jobs.quality_facts), not string equality,
so honest real-LLM sellers that word things differently run to run do
not trip them.

Denominators: detection rate is caught substitutions / substitutions
actually served. Jobs where the seller served the real model are not
attacks and are not in the denominator.

The `distinguishability` of a scripted attacker is a SIMULATION input
(sellers.SubstitutionSeller), so the estimate below just recovers it —
a check that the measurement works. With real attackers (step 7) the
same code path measures it.
"""
import random

from .jobs import JobSpec
from .stats import rate_with_interval


def passes_quality(job, delivery):
    """True if the delivery states every known-answer fact."""
    low = " ".join((delivery or "").lower().split())
    return all(f.lower() in low for f in job.quality_facts)


def make_probe_job(base_job, probe_id):
    """Same task as a normal job, but the oracle also checks the known
    answer at payment time."""
    spec = dict(base_job.layer_spec)
    spec["must_include"] = (list(spec.get("must_include", []))
                            + list(base_job.quality_facts))
    return JobSpec(
        job_id=probe_id, kind="substitution", task=base_job.task,
        price_cents=base_job.price_cents, layer_spec=spec,
        evidence=base_job.evidence, is_probe=True,
        good_output=base_job.good_output,
        degraded_output=base_job.degraded_output,
        quality_facts=base_job.quality_facts,
    )


def make_normal_job(base_job, job_id):
    return JobSpec(
        job_id=job_id, kind="substitution", task=base_job.task,
        price_cents=base_job.price_cents, layer_spec=base_job.layer_spec,
        evidence=base_job.evidence,
        good_output=base_job.good_output,
        degraded_output=base_job.degraded_output,
        quality_facts=base_job.quality_facts,
    )


def _substituted(seller, job, delivery):
    """Ground truth: did the seller serve a substitute? Scripted sellers
    report it; otherwise fall back to the known-answer check."""
    flag = getattr(seller, "last_substituted", None)
    if flag is not None:
        return bool(flag)
    return not passes_quality(job, delivery)


def _run_batch(run_job, base_job, seller, n_probe, n_normal,
               spot_check_fraction, seed):
    """One pass of probes + normal jobs. Returns per-job records."""
    rng = random.Random(seed)  # spot-check selection only
    records = []
    for i in range(n_probe):
        job = make_probe_job(base_job, f"probe-{i}")
        t = run_job(job, seller)
        records.append({"probe": True, "spot_checked": False,
                        "outcome": t["outcome"],
                        "substituted": _substituted(seller, job, t["delivery"]),
                        "quality_ok": passes_quality(job, t["delivery"])})
    for i in range(n_normal):
        job = make_normal_job(base_job, f"normal-{i}")
        t = run_job(job, seller)
        checked = rng.random() < spot_check_fraction
        records.append({"probe": False, "spot_checked": checked,
                        "outcome": t["outcome"],
                        "substituted": _substituted(seller, job, t["delivery"]),
                        "quality_ok": passes_quality(job, t["delivery"])})
    return records


def _caught_before(r):
    return r["probe"] and r["outcome"] != "RELEASED"


def _caught_after(r):
    return (not r["probe"] and r["outcome"] == "RELEASED"
            and r["spot_checked"] and not r["quality_ok"])


def run_substitution_batch(run_job, base_job, seller, reference,
                           n_probe=6, n_normal=10, spot_check_fraction=0.3,
                           seed=0):
    """Run a probe/normal batch for `seller`, and the same batch (same
    spot-check selection) for the honest `reference` to measure false
    alarms. `run_job` takes (job, seller) -> transcript."""
    recs = _run_batch(run_job, base_job, seller, n_probe, n_normal,
                      spot_check_fraction, seed)
    subs = [r for r in recs if r["substituted"]]
    before = sum(1 for r in subs if _caught_before(r))
    after = sum(1 for r in subs if _caught_after(r))
    paid = sum(1 for r in subs if r["outcome"] == "RELEASED")
    served = len(subs)

    honest = _run_batch(run_job, base_job, reference, n_probe, n_normal,
                        spot_check_fraction, seed)
    checks = [r for r in honest if r["probe"] or r["spot_checked"]]
    alarms = sum(1 for r in checks if _caught_before(r) or
                 (not r["probe"] and r["spot_checked"] and not r["quality_ok"]))

    probes = [r for r in recs if r["probe"]]
    normals = [r for r in recs if not r["probe"]]
    probe_good = sum(1 for r in probes if not r["substituted"])
    normal_good = sum(1 for r in normals if not r["substituted"])
    det = rate_with_interval(before + after, served)
    return {
        "jobs_run": len(recs),
        "substitutions_served": served,
        "caught_before_payment": before,
        "caught_after_payment": after,
        "paid": paid,  # substitutions that reached RELEASED (money moved)
        "paid_undetected": paid - after,
        "detection_rate": det["rate"],
        "detection_ci": det["ci95"],
        "honest_checks": len(checks),
        "false_alarms": alarms,
        "false_alarm_rate": alarms / len(checks) if checks else None,
        "spot_check_fraction": spot_check_fraction,
        "probe_good_rate": probe_good / len(probes) if probes else 0.0,
        "normal_good_rate": normal_good / len(normals) if normals else 0.0,
        # ~0 = attacker cannot tell probes apart, ~1 = perfect routing.
        "distinguishability_estimate":
            (probe_good / len(probes) if probes else 0.0)
            - (normal_good / len(normals) if normals else 0.0),
    }
