# trust_layer — measure what the oracle loses, per attack class

Steps 1–9 of the trust-layer roadmap: freeze the core, one job format,
reproducible tournament, three attack classes (model substitution, fake
approval, judge gaming), real-model lanes, a behavioral monitor with
escalation, and an honest report. Step 10 (publish, open leaderboard,
outreach) is done by hand.
Nothing in this package sends email, contacts anyone, or spends real money.

The core (`agentpay/`) is frozen at tag `v0.1-trust-layer-freeze`.
Nothing here modifies it: the trust layer runs jobs through the real
`PaymentLayer` and adds gates on top.

```bash
python3 run_trust_layer.py --seeds 5        # scripted suite, one command
python3 run_trust_layer.py --models gpt,sonnet,gemini  # + real judge lanes (needs keys)
python3 -m unittest discover tests -v      # full suite incl. trust-layer tests
```

## What is measured vs simulated

- **By construction (harness checks, not findings):** escrow flip rate
  after the trusted gate (zero by design), judge sway and exploit rates
  against the scripted judge (built to be fooled), the monitor's catch of
  the planted "rest assured" phrase, and the distinguishability estimate
  (recovers the simulation input).
- **Measured (against scripted attackers):** honest payout, substitution
  paid / caught before payment / caught after payment, detection rate
  over substitutions actually served, honest false alarms, and the
  monitor's catch rate and honest false flags against a varied attacker
  with no planted phrase.
- **Measured (real models):** only what the `--models` lanes produce.
- **Simulated (stand-ins, documented in code):** scripted sellers and
  judges. Their parameters — substitution distinguishability, judge
  sway — are simulation inputs, not measured properties of real models.
  A number measured against a stand-in measures the harness + the
  stand-in. The real numbers come from step 7 with real adapters,
  reported with Wilson intervals across seeds.

## Layout

```
trust_layer/
  jobs.py          one job spec: task, price, delivery, evidence, verdict
  sellers.py       scripted sellers: honest + one per attack class
  judges.py        naive ScriptedJudge + stripping / blind mitigations
  settlement.py    step-5 gate: trusted (buyer channel) vs vulnerable (payload)
  detectors.py     step-4: probes + spot-checks for substitution
  monitor.py       step-8: behavioral detector + escalation queue
  stats.py         Wilson intervals for LLM-run rates
  scoring.py       headline metrics: attempted / paid / wrongly rejected
  scoreboard.py    self-contained HTML scoreboard
  runner.py        jobs through the real PaymentLayer, transcripts
  real_models.py   step-7: LLM adapters (env keys only) + RealJudge/Attacker
  adaptive.py      step-7: strategy-rotating attacker + exploit curve
  labels.py/.json  step-6: pre-registered ground truth, agreement pending
  report.py        step-9: fill the report template from results
  report_template.md  2-page report skeleton
```

## Real-model lanes

Keys from env only, read at call time, never logged:
`OPENAI_API_KEY`, `ANTHROPIC_API_KEY`, `GEMINI_API_KEY`
(or `GOOGLE_API_KEY` fallback). Model IDs pinned in `real_models.py`
with `AGENTPAY_*_MODEL` overrides — confirm via each provider's model
list at run time. Missing key → lane skipped, never failed.
