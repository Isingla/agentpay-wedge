# Starter issues — where to begin attacking

These are genuine open attack surfaces, verified reproducible against the
clean layer. None of them is a planted bug — the planted bugs live in
`tournament/mutations.py` (tournament, 7/7 caught) and `mutation_check.py`
(unit suite, 8/8 caught). Issue #1 failed the audit before it was fixed; it
is now marked [FIXED]. Issues #2–#3 are real weaknesses in the verification
story that the audit cannot see.

---

## 1. [FIXED] `pay()` accepted the escrow account — escrow drain

**Status: fixed in commit `f187b2b`** (`Fix: pay() could drain or inject funds
into the escrow account`). The escrow account is now reserved: `pay()` rejects it as
either side (`reserved_account`), `post_task` rejects it as buyer
(`reserved_account`), `open_agent` raises `ValueError` for it, and
self-transfers are rejected (`self_transfer`). Covered by 5 regression tests
in `tests/test_chaos.py`, and the hole is the 8th planted bug in
`mutation_check.py`. The follow-up challenge is finding *another* path that
moves escrow money outside the state machine — if you find one, it is a
hall-of-fame break.

**The original attack.** `pay(key, src, dst, amount)` never checked that
`src` or `dst` was a real agent. Passing `src="__escrow__"` moved money
straight out of the escrow account, bypassing the escrow state machine
entirely. The escrow row still said OPEN, but the money was gone — the audit
failed. The reverse direction broke it too: `pay("in1", "buyer",
"__escrow__", 500)` moved money *into* escrow with no escrow row to back it
(`escrow account 500 != live escrows 0`). And a task posted with escrow as
the buyer was only stopped by the insufficient-funds check — it would have
broken the audit whenever escrow was holding money.

**Repro** (from the repo root — all three now return rejected, audit clean):

```python
from agentpay import DB, Oracle, PaymentLayer
import tempfile, os

tmp = tempfile.TemporaryDirectory()
layer = PaymentLayer(DB(os.path.join(tmp.name, "pay.db")), Oracle(), lambda: 1_000_000.0)
layer.open_agent("buyer", balance=100_000)
layer.open_agent("seller", balance=0, capacity=2)
spec = {"kind": "summary", "max_words": 25, "must_include": ["ledger"],
        "source": "A ledger records every transfer as a row. Balances are derived from it."}
layer.post_task("t1", "buyer", 1000, spec, ttl=600)
print(layer.pay("drain1", "__escrow__", "seller", 1000))  # rejected, reason='reserved_account'
print(layer.pay("in1", "buyer", "__escrow__", 500))       # rejected, reason='reserved_account'
print(layer.post_task("phantom", "__escrow__", 500, spec))  # rejected, reason='reserved_account'
print(layer.check_invariants())  # [] — the audit is clean
```

**The fix:** escrow is a reserved account. Only `post_task`, `settle`, and
`sweep` move escrow funds; `pay()` and `post_task` reject it as a party.

---

## 2. Oracle gaming — gibberish passes the summary check

**The attack.** The summary oracle is a shallow syntactic check: non-empty,
printable, word count, must-include terms, shorter than the source. It never
asks whether the output *means* anything. Submit gibberish that contains the
required term and the oracle passes it — the seller gets paid for garbage.

**Repro:**

```python
from agentpay import DB, Oracle, PaymentLayer
import tempfile, os

tmp = tempfile.TemporaryDirectory()
layer = PaymentLayer(DB(os.path.join(tmp.name, "pay.db")), Oracle(), lambda: 1_000_000.0)
layer.open_agent("buyer", balance=100_000)
layer.open_agent("seller", balance=0, capacity=2)
spec = {"kind": "summary", "max_words": 25, "must_include": ["ledger"],
        "source": "A ledger records every transfer as a row. Balances are derived from it. Auditors replay the rows."}
layer.post_task("t1", "buyer", 1000, spec, ttl=600)
layer.assign("t1", "seller")
layer.submit("t1", "seller", "ledger ledger ledger")  # gibberish
print(layer.settle("t1"))  # Outcome(ok=True, state='RELEASED', reason='summary_ok')
print(layer.check_invariants())  # [] — the audit is clean
```

**What counts as a break:** a settle that RELEASES for output a human would
call garbage. This does **not** fail `check_invariants()` — the money moved
exactly as the layer's rules allow — so it is not a hall-of-fame break. It
is a break of the *verification story*: the oracle is the layer's stand-in
for "the work was actually done," and a must-include list is not verification.
The interesting follow-up is finding the strongest oracle check that stays
deterministic (a stricter spec kind? a judge with teeth?) — or showing that
no deterministic check can do it.

---

## 3. Zero-TTL tasks complete — the deadline is a no-op at equality

**The attack.** Every deadline check is `now > deadline`, so a task whose
deadline equals the current time is *not* expired. Post with `ttl=0` and the
whole lifecycle — assign, submit, settle — succeeds, because the deadline is
exactly `now`.

**Repro:**

```python
from agentpay import DB, Oracle, PaymentLayer
import tempfile, os

tmp = tempfile.TemporaryDirectory()
layer = PaymentLayer(DB(os.path.join(tmp.name, "pay.db")), Oracle(), lambda: 1_000_000.0)
layer.open_agent("buyer", balance=100_000)
layer.open_agent("seller", balance=0, capacity=2)
spec = {"kind": "summary", "max_words": 25, "must_include": ["ledger"],
        "source": "A ledger records every transfer as a row."}
print(layer.post_task("t1", "buyer", 1000, spec, ttl=0))   # OPEN
print(layer.assign("t1", "seller"))                        # ASSIGNED, not expired
print(layer.submit("t1", "seller", "A ledger records every transfer."))
print(layer.settle("t1"))                                  # RELEASED
print(layer.check_invariants())  # [] — the audit is clean
```

**What counts as a break:** a `ttl=0` task that completes. This does
**not** fail `check_invariants()` — again the money moved per the rules. It
is a boundary-semantics question: should `deadline == now` count as expired
(`>=`) or live (`>`)? The follow-up is finding a case where the choice
actually costs someone money — e.g. a sweep that refunds a task the seller
already submitted, or a settle that pays for work delivered after the buyer
believed the task dead.
