# agentpay-wedge

[![CI](https://github.com/OWNER/REPO/actions/workflows/ci.yml/badge.svg)](https://github.com/OWNER/REPO/actions/workflows/ci.yml)
<!-- TODO: replace OWNER/REPO with the real GitHub path once this repo is pushed. -->

**Money is never created, destroyed, or stuck.** That is the invariant, and
the layer's own audit (`check_invariants()`) recomputes every balance from
the journal to prove it. **7/7 planted bugs caught** by the fake attacker in
the [model tournament](tournament/README.md) (at `--rounds 6`, the full
strategy cycle) — and separately, **8/8 planted bugs caught** by the unit
suite in `mutation_check.py` (different harness: tests vs attacker — both
verified, don't conflate them). Try to break it yourself:
[starter issues](STARTER_ISSUES.md) · [hall of fame](tournament/README.md#hall-of-fame).

A minimal payment layer for agent-to-agent work: an idempotent ledger, escrow, a verification oracle, and spend caps. It ships with chaos tests that prove money is never created, destroyed, or stuck.

```bash
python3 run.py            # 27 tests + demo, ~5s, Python 3.10+, no dependencies
python3 mutation_check.py # plants 8 bugs one at a time; tests must catch all 8
```

```
agentpay/
  db.py            SQLite schema + BEGIN IMMEDIATE transactions
  ledger.py        idempotent double-entry transfers
  layer.py         escrow state machine, spend caps, reputation, invariant audit
  oracle.py        deterministic verifier (hash / schema / summary) + judge hook
  agents.py        toy buyer + seller (honest / garbage / crash modes)
  seller_worker.py seller as a separate process, so tests can SIGKILL it
tests/
  test_chaos.py       the six chaos scenarios
  test_invariants.py  randomized fuzz: ~4,800 ops, invariants audited after each
demo.py
WRITEUP.md         one-page design + Skyfire KYAPay mapping
mutation_check.py  plants 8 bugs one at a time; tests must catch all 8
tournament/        model attackers vs. the layer (see tournament/README.md)
run_tournament.py  run the tournament
STARTER_ISSUES.md  three places to begin attacking (one fixed, two open)
trust_layer/       per-attack-class measurement suite (see trust_layer/README.md)
run_trust_layer.py run the trust-layer suite
```

See [WRITEUP.md](WRITEUP.md).

## Threat model (trust layer, v0.1 freeze — tag `v0.1-trust-layer-freeze`)

The attackers are a seller agent optimizing for payout, a buyer agent overspending or replaying messages, and the verification oracle itself being gamed; what they want is payment without delivery, double payment, free work, or reputation without quality; and "the oracle loses" means one of three things — it pays for garbage (money lost), it rejects honest work (seller churn), or it freezes funds in limbo (trust lost). The trust layer in `trust_layer/` measures exactly these three losses per attack class. See [trust_layer/README.md](trust_layer/README.md).
