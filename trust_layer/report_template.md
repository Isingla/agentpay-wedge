# Agentpay trust layer — harness validation report

**Repo:** {{repo}} · **Commit:** {{commit}} · **Seeds:** {{seeds}} (starting at {{seed0}}) · **Attackers/judges:** {{models}} · **Date:** {{date}}

**Status: {{status}}**

{{status_note}}

## What we tested

Three attack classes against the payment layer (ledger, escrow, idempotency, spend caps, oracle). Every job ran through the real `PaymentLayer`. Attackers and judges are scripted stand-ins unless a real-model lane is listed below.

How to read the "Kind" column:
- **By construction:** the number follows from how the stand-in or the fix was written. It proves the harness and the fix are wired correctly. It says nothing about how a real model behaves.
- **Measured:** the number depends on run-time randomness or detector behavior and could have come out differently. It is still measured against a scripted attacker.

## Results per attack class

Each row has its own denominator, stated in the row.

| Attack class | Metric | Result | Kind |
|---|---|---|---|
| Model substitution | Substitutions paid | {{sub_paid}} of {{sub_served}} served | Measured |
| Model substitution | Caught before payment (probes) | {{sub_before}} of {{sub_served}} | Measured |
| Model substitution | Caught after payment (spot-checks, {{sub_spot_fraction}} of normal jobs) | {{sub_after}} of {{sub_served}} | Measured |
| Model substitution | Detection rate (95% CI) | {{sub_detection_rate}} {{sub_detection_ci}} | Measured |
| Model substitution | Honest false alarms | {{sub_false_alarms}} of {{sub_honest_checks}} honest checks | Measured |
| Model substitution | Probe distinguishability | estimate {{sub_distinguishability}} vs input {{sub_dist_input}} | By construction (recovers the simulation input) |
| Fake approval | Escrow releases on injected approval | {{fa_paid_vulnerable}} of {{fa_attempted}} before fix, {{fa_paid}} of {{fa_attempted}} after | By construction (gate reads approval only from the buyer channel) |
| Fake approval | Judge pass-rate shift from injected approval text | {{fa_judge_shift}} before, {{fa_judge_shift_after}} after stripping | By construction (scripted judge is built to be swayed) |
| Judge gaming | Labeled garbage the judge passed | {{jg_paid}} of {{jg_attempted}} naive, {{jg_paid_after}} of {{jg_attempted}} with canary veto | By construction (scripted judge is keyword-only) |

Label agreement for the judge-gaming ground truth: {{label_agreement}}.

{{real_judges_section}}

## Suite job runs

Separately from the per-class scenarios above, the suite runs each job once per seed ({{suite_jobs}} jobs: 3 honest, 3 attack, × {{seeds}} seeds). Fake approval runs behind the trusted gate; judge gaming runs against the naive judge.

- Attack jobs run: **{{attacks_attempted}}**, attack jobs paid: **{{attacks_paid}}** (by kind: {{paid_by_kind}})
- Honest jobs wrongly rejected: **{{honest_wrongly_rejected}}**
- Ledger invariant breaks: **{{invariant_breaks}}**

## Monitor + escalation

Marginal catch rate = attacks the oracle paid that the monitor flagged ÷ attacks the oracle paid.

| Attacker | Marginal catch | Honest jobs flagged | Kind |
|---|---|---|---|
| Judge-gaming seller with the planted "rest assured" phrase | {{mon_rate}} ({{mon_caught}} of {{mon_passed}}) | {{mon_ff}} of {{mon_honest}} | By construction (the monitor looks for that phrase) |
| Judge-gaming seller with varied wording, no planted phrase | {{monv_rate}} ({{monv_caught}} of {{monv_passed}}) | {{monv_ff}} of {{monv_honest}} | Measured |

The second row is the honest number for the current behavioral features.

## What is simulated

Scripted sellers and judges stand in for real models. Their parameters (substitution distinguishability, judge sway, gamed templates) are simulation inputs, documented in `trust_layer/`. Real-model numbers come only from runs with real adapters (`--models`), reported with Wilson intervals across seeds.

## Roadmap, NOT claims

SLA fraud, fake citations, rubric gaming. Future work, no numbers attached.

## Reproduce

`python3 run_trust_layer.py --seeds {{seeds}} --seed {{seed0}}` gives the same numbers for scripted runs. Real-model runs report intervals across seeds.
