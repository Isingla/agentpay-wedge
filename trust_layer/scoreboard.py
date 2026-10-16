"""Trust-layer scoreboard: self-contained HTML, rendered from results."""
import html
import json


def render(results, path):
    by_kind = results.get("by_kind", {})
    rows = ""
    for kind, d in sorted(by_kind.items()):
        rows += (f"<tr><td>{html.escape(kind)}</td><td>{d['attempted']}</td>"
                 f"<td>{d['paid']}</td><td>{d['rejected']}</td>"
                 f"<td>{d['parked']}</td></tr>\n")
    sub = results.get("substitution", {})
    fa = results.get("fake_approval", {})
    jg = results.get("judge_gaming", {})
    mon = results.get("monitor", {}).get("marginal_catch_rate", {})
    monv = results.get("monitor_varied", {}).get("marginal_catch_rate", {})
    status = ("Partial real-model run" if results.get("real_judges") else
              "Harness validated against scripted stand-ins, no real-model "
              "results yet")
    curve = results.get("exploit_curve", [])
    curve_rows = "".join(
        f"<tr><td>{c['round']}</td><td>{html.escape(str(c['strategy']))}</td>"
        f"<td>{c['paid']}/{c['attempted']}</td>"
        f"<td>{c['rate']:.3f}</td></tr>\n" for c in curve)
    doc = f"""<!DOCTYPE html>
<html><head><meta charset="utf-8"><title>agentpay trust layer — scoreboard</title>
<style>body{{font-family:system-ui;max-width:860px;margin:2em auto;padding:0 1em}}
table{{border-collapse:collapse}}td,th{{border:1px solid #ccc;padding:4px 10px;text-align:left}}
.bad{{color:#b00;font-weight:bold}}.good{{color:#080;font-weight:bold}}</style></head>
<body><h1>agentpay trust layer — scoreboard</h1>
<p>Seeds: {html.escape(str(results.get('seeds')))} · Models: {html.escape(str(results.get('models')))}</p>
<p><b>Status:</b> {html.escape(status)}</p>
<h2>Suite job runs</h2>
<p>Attacks attempted: <b>{results.get('attacks_attempted')}</b> ·
Attacks paid: <span class="{'bad' if results.get('attacks_paid') else 'good'}">{results.get('attacks_paid')}</span> ·
Honest wrongly rejected: <b>{results.get('honest_wrongly_rejected')}</b> ·
Invariant breaks: <b>{results.get('invariant_breaks')}</b></p>
<h2>By kind</h2>
<table><tr><th>kind</th><th>attempted</th><th>paid</th><th>rejected</th><th>parked</th></tr>
{rows}</table>
<h2>Attack classes</h2>
<ul>
<li>Substitution (measured): paid {sub.get('paid')}/{sub.get('substitutions_served')} served · caught before payment {sub.get('caught_before_payment')} · after payment {sub.get('caught_after_payment')} · detection {sub.get('detection_rate')} {sub.get('detection_ci')} · honest false alarms {sub.get('false_alarms')}/{sub.get('honest_checks')}</li>
<li>Fake approval (by construction): escrow flip before {fa.get('flip_before')} · after {fa.get('flip_after')} · judge shift {fa.get('judge_shift')} · after stripping {fa.get('judge_shift_after')}</li>
<li>Judge gaming (by construction): exploit before {jg.get('exploit_before')} · after veto {jg.get('exploit_after')}</li>
<li>Monitor marginal catch, planted phrase (by construction): {mon.get('rate')} ({mon.get('caught_by_monitor')}/{mon.get('passed_attacks')})</li>
<li>Monitor marginal catch, varied attacker (measured): {monv.get('rate')} ({monv.get('caught_by_monitor')}/{monv.get('passed_attacks')})</li>
</ul>
<h2>Exploit curve (adaptive rounds)</h2>
<table><tr><th>round</th><th>strategy</th><th>paid/attempted</th><th>rate</th></tr>
{curve_rows}</table>
<p><small>Scripted attackers are stand-ins; see trust_layer/README.md for what is simulated vs measured.</small></p>
</body></html>"""
    with open(path, "w") as f:
        f.write(doc)
    return path
