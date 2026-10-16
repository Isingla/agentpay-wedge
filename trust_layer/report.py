"""Report generator (step 9): fill the template from results.

The generator never invents numbers: every placeholder comes from a
measured results dict. Missing measurements render as 'not measured',
never as zero.

The commit line is honest about the working tree: if there are
uncommitted changes, the commit is suffixed with '+uncommitted', because
then the hash does not pin the code that produced the numbers.
"""
import os
import subprocess
from datetime import date

NM = "not measured"


def _fmt(v, default=NM):
    if v is None:
        return default
    if isinstance(v, float):
        return f"{v:.3f}"
    return str(v)


def _git_commit(repo_dir):
    try:
        commit = subprocess.check_output(
            ["git", "rev-parse", "--short", "HEAD"],
            cwd=repo_dir, text=True, stderr=subprocess.DEVNULL).strip()
    except Exception:
        return "unknown"
    try:
        dirty = subprocess.check_output(
            ["git", "status", "--porcelain", "--untracked-files=normal"],
            cwd=repo_dir, text=True, stderr=subprocess.DEVNULL).strip()
    except Exception:
        dirty = ""
    return commit + ("+uncommitted" if dirty else "")


def _agreement(a):
    if not isinstance(a, dict):
        return str(a)
    if a.get("status") == "measured":
        return (f"Cohen's kappa {a.get('kappa')} over {a.get('n')} labels "
                f"(observed agreement {a.get('observed')})")
    return "pending (second reviewer has not labeled yet)"


def _real_judges(results):
    real = results.get("real_judges") or {}
    skipped = results.get("skipped_lanes") or []
    if not real and not skipped:
        return ("## Real-model judges\n\nNot run. The judge-gaming and "
                "fake-approval rows above are scripted, so they are not yet "
                "claims about any real judge.")
    lines = ["## Real-model judges", "",
             "| Judge | Labeled garbage passed | Good passed | Kind |",
             "|---|---|---|---|"]
    for name, r in sorted(real.items()):
        lines.append(f"| {name} | {r.get('garbage_passed')} of "
                     f"{r.get('garbage_n')} | {r.get('good_passed')} of "
                     f"{r.get('good_n')} | Measured |")
    for name, why in skipped:
        lines.append(f"| {name} | skipped: {why} | | |")
    return "\n".join(lines)


def _status(results):
    if results.get("real_judges"):
        return ("partial real-model run",
                "Rows marked Measured with a real-model lane are claims about "
                "those models. Every scripted row is still a harness check.")
    return ("harness validated, no real-model results yet",
            "These numbers show the harness and fixes are wired correctly "
            "against scripted stand-ins. They are not evidence that the oracle "
            "holds against real attackers or real judges. That needs the "
            "step 7 real-model run.")


def generate(results, out_path, repo="agentpay-wedge", commit=None):
    here = os.path.dirname(os.path.abspath(__file__))
    with open(os.path.join(here, "report_template.md")) as f:
        tpl = f.read()
    if commit is None:
        commit = _git_commit(os.path.dirname(here))
    sub = results.get("substitution", {})
    fa = results.get("fake_approval", {})
    jg = results.get("judge_gaming", {})
    mon_all = results.get("monitor", {})
    monv_all = results.get("monitor_varied", {})
    mon = mon_all.get("marginal_catch_rate", {})
    monv = monv_all.get("marginal_catch_rate", {})
    ff = mon_all.get("honest_false_flags", {})
    ffv = monv_all.get("honest_false_flags", {})
    by_kind = results.get("by_kind", {})
    paid_by_kind = ", ".join(
        f"{k} {d['paid']}/{d['attempted']}" for k, d in sorted(by_kind.items())
        if k != "honest") or NM
    status, status_note = _status(results)
    values = {
        "repo": repo, "commit": commit,
        "date": date.today().isoformat(),
        "seeds": results.get("seeds", "?"),
        "seed0": results.get("seed0", "?"),
        "models": results.get("models", "scripted"),
        "status": status, "status_note": status_note,
        "suite_jobs": results.get("jobs", "?"),
        "attacks_attempted": results.get("attacks_attempted", "?"),
        "attacks_paid": results.get("attacks_paid", "?"),
        "paid_by_kind": paid_by_kind,
        "honest_wrongly_rejected": results.get("honest_wrongly_rejected", "?"),
        "invariant_breaks": results.get("invariant_breaks", "?"),
        "sub_served": _fmt(sub.get("substitutions_served")),
        "sub_paid": _fmt(sub.get("paid")),
        "sub_before": _fmt(sub.get("caught_before_payment")),
        "sub_after": _fmt(sub.get("caught_after_payment")),
        "sub_spot_fraction": _fmt(sub.get("spot_check_fraction")),
        "sub_detection_rate": _fmt(sub.get("detection_rate")),
        "sub_detection_ci": _fmt(sub.get("detection_ci"), ""),
        "sub_false_alarms": _fmt(sub.get("false_alarms")),
        "sub_honest_checks": _fmt(sub.get("honest_checks")),
        "sub_distinguishability": _fmt(sub.get("distinguishability_estimate")),
        "sub_dist_input": _fmt(sub.get("distinguishability_input")),
        "fa_attempted": _fmt(fa.get("attempted")),
        "fa_paid": _fmt(fa.get("paid")),
        "fa_paid_vulnerable": _fmt(fa.get("paid_vulnerable")),
        "fa_judge_shift": _fmt(fa.get("judge_shift")),
        "fa_judge_shift_after": _fmt(fa.get("judge_shift_after")),
        "jg_attempted": _fmt(jg.get("attempted")),
        "jg_paid": _fmt(jg.get("paid")),
        "jg_paid_after": _fmt(jg.get("paid_after_fix")),
        "label_agreement": _agreement(results.get("label_agreement", "pending")),
        "real_judges_section": _real_judges(results),
        "mon_rate": _fmt(mon.get("rate")),
        "mon_caught": _fmt(mon.get("caught_by_monitor")),
        "mon_passed": _fmt(mon.get("passed_attacks")),
        "mon_ff": _fmt(ff.get("flagged")),
        "mon_honest": _fmt(ff.get("honest_jobs")),
        "monv_rate": _fmt(monv.get("rate")),
        "monv_caught": _fmt(monv.get("caught_by_monitor")),
        "monv_passed": _fmt(monv.get("passed_attacks")),
        "monv_ff": _fmt(ffv.get("flagged")),
        "monv_honest": _fmt(ffv.get("honest_jobs")),
    }
    out = tpl
    for k, v in values.items():
        out = out.replace("{{" + k + "}}", str(v))
    with open(out_path, "w") as f:
        f.write(out)
    return out_path
