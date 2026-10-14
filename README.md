# agentpay-wedge

A minimal payment layer for agent-to-agent work: an idempotent ledger, escrow, a verification oracle, and spend caps. It ships with chaos tests that prove money is never created, destroyed, or stuck.

```bash
python3 run.py            # 22 tests + demo, ~5s, Python 3.10+, no dependencies
python3 mutation_check.py # plants 7 bugs one at a time; tests must catch all 7
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
```

See [WRITEUP.md](WRITEUP.md).
