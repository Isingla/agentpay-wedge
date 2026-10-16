"""Planted-bug variants for the tournament (mutation testing).

Each variant is ONE genuine bug, applied as a monkeypatch for the duration
of a round and restored afterwards. Every variant is verified to trip the
layer's own audit: run the fake model against it and check_invariants()
returns non-empty. A variant that cannot be tripped is not shipped.

Why these seven: each removes exactly one of the layer's defenses, and each
is caught by a DIFFERENT audit check, so the scoreboard shows which check
caught which bug.

Deliberately NOT shipped: cap-removal variants. The audit does not check
caps, so a removed cap is uncatchable by design — it would sit at 0 breaks
forever and prove nothing.

Also deliberately NOT shipped: the literal double-release (remove settle's
already_settled early-return AND its in-transaction re-check). It does not
trip the audit: the idempotency key is a THIRD line of defense, and the
second transfer replays the first row instead of moving money again.
And the funds-check removal: the accounts table has
CHECK (balance >= 0), so a missing funds check dies at the DB constraint
instead of tripping the audit. Defense in depth working as intended, twice.
The journal variant (m4) covers the same "money moves when it shouldn't"
family and IS caught.
"""
import contextlib

from agentpay import ledger
from agentpay.layer import ESCROW, PaymentLayer
from agentpay.ledger import TransferResult


@contextlib.contextmanager
def _patch_ledger_transfer(buggy):
    orig = ledger.transfer
    ledger.transfer = buggy
    try:
        yield
    finally:
        ledger.transfer = orig


@contextlib.contextmanager
def _patch_method(cls, name, fn):
    orig = getattr(cls, name)
    setattr(cls, name, fn)
    try:
        yield
    finally:
        setattr(cls, name, orig)


def _noop_for_prefix(orig, prefix):
    """BUG: transfers under `prefix` claim success but move nothing and
    record nothing."""
    def buggy(c, key, src, dst, amount, now):
        if key.startswith(prefix):
            return TransferResult(key, src, dst, amount, "ok", None)
        return orig(c, key, src, dst, amount, now)
    return buggy


def _redirect_prefix(orig, prefix, wrong_dst):
    """BUG: transfers under `prefix` go to the wrong account."""
    def buggy(c, key, src, dst, amount, now):
        if key.startswith(prefix):
            dst = wrong_dst
        return orig(c, key, src, dst, amount, now)
    return buggy


def _m1_settle_skips_release():
    # (a) settle() marks RELEASED but the release transfer never happens.
    # Audit catches: "RELEASED without matching release transfer"
    # (+ escrow account != live escrows). Tripped by any settle that passes.
    orig = ledger.transfer
    return _patch_ledger_transfer(_noop_for_prefix(orig, "release:"))


def _m2_sweep_no_state_update():
    # (b) sweep() refunds but never updates the escrow state: the escrow
    # stays OPEN/ASSIGNED after being paid out.
    # Audit catches: "live escrow already paid out".
    # Tripped by timeout_games (sweep of an expired OPEN escrow).
    def buggy_sweep(self):
        now = self.clock()
        refunded = []
        with self.db.tx() as c:
            rows = c.execute(
                "SELECT * FROM escrows WHERE state IN ('OPEN','ASSIGNED') "
                "AND deadline < ?", (now,)).fetchall()
            for e in rows:
                r = ledger.transfer(c, f"refund:{e['task_id']}", ESCROW,
                                    e["buyer"], e["amount"], now)
                if not r.ok:
                    raise RuntimeError(f"timeout refund failed: {r.reason}")
                # BUG: the state UPDATE is missing — escrow stays live.
                if e["seller"]:
                    c.execute("UPDATE agents SET reputation = reputation - 1 "
                              "WHERE id=?", (e["seller"],))
                refunded.append(e["task_id"])
        return refunded

    return _patch_method(PaymentLayer, "sweep", buggy_sweep)


def _m3_post_task_skips_lock():
    # (c) post_task() creates the escrow row but the lock transfer never
    # happens: the escrow claims funds that never moved.
    # Audit catches: "missing/wrong lock" (+ escrow account != live escrows).
    # Tripped by any post_task.
    orig = ledger.transfer
    return _patch_ledger_transfer(_noop_for_prefix(orig, "lock:"))


def _m4_transfer_skips_journal():
    # transfer() moves the money but never writes the journal row: the
    # balances disagree with the journal replayed from the transfers table.
    # Audit catches: "journal mismatch" (+ "missing/wrong lock").
    # Tripped by any transfer.
    def buggy(c, key, src, dst, amount, now):
        if not isinstance(amount, int) or amount <= 0:
            raise ValueError("amount must be a positive int (cents)")
        prior = ledger.lookup(c, key, src, dst, amount)
        if prior is not None:
            return prior
        src_bal = ledger.balance(c, src)
        if src_bal is None or ledger.balance(c, dst) is None:
            return ledger.record_rejection(c, key, src, dst, amount,
                                           "no_such_account", now)
        if src_bal < amount:
            return ledger.record_rejection(c, key, src, dst, amount,
                                           "insufficient_funds", now)
        c.execute("UPDATE accounts SET balance = balance - ? WHERE id = ?",
                  (amount, src))
        c.execute("UPDATE accounts SET balance = balance + ? WHERE id = ?",
                  (amount, dst))
        # BUG: the INSERT into transfers is missing — money moves, no row.
        return TransferResult(key, src, dst, amount, "ok", None)

    return _patch_ledger_transfer(buggy)


def _m5_transfer_records_wrong_amount():
    # (e) transfer moves `amount` but records `amount + 1`: the journal
    # disagrees with the balances.
    # Audit catches: "journal mismatch" (+ "missing/wrong lock").
    # Tripped by any transfer.
    def buggy(c, key, src, dst, amount, now):
        if not isinstance(amount, int) or amount <= 0:
            raise ValueError("amount must be a positive int (cents)")
        prior = ledger.lookup(c, key, src, dst, amount)
        if prior is not None:
            return prior
        src_bal = ledger.balance(c, src)
        if src_bal is None or ledger.balance(c, dst) is None:
            return ledger.record_rejection(c, key, src, dst, amount,
                                           "no_such_account", now)
        if src_bal < amount:
            return ledger.record_rejection(c, key, src, dst, amount,
                                           "insufficient_funds", now)
        c.execute("UPDATE accounts SET balance = balance - ? WHERE id = ?",
                  (amount, src))
        c.execute("UPDATE accounts SET balance = balance + ? WHERE id = ?",
                  (amount, dst))
        # BUG: recorded amount is amount + 1, not amount.
        c.execute("INSERT INTO transfers VALUES (?,?,?,?, 'ok', NULL, ?)",
                  (key, src, dst, amount + 1, now))
        return TransferResult(key, src, dst, amount + 1, "ok", None)

    return _patch_ledger_transfer(buggy)


def _m6_refund_wrong_account():
    # (f) refunds go to the seller instead of the buyer.
    # Audit catches: "REFUNDED without matching refund transfer".
    # Tripped by oracle_games (garbage -> refund) or any sweep refund.
    orig = ledger.transfer
    return _patch_ledger_transfer(_redirect_prefix(orig, "refund:", "seller"))


def _m7_release_wrong_account():
    # (g) releases go to the buyer instead of the seller.
    # Audit catches: "RELEASED without matching release transfer".
    # Tripped by any settle that passes.
    orig = ledger.transfer
    return _patch_ledger_transfer(_redirect_prefix(orig, "release:", "buyer"))


MUTATIONS = {
    "m1": {"title": "settle() skips the release transfer",
           "description": "settle() marks the escrow RELEASED but the "
                          "release transfer never happens.",
           "apply": _m1_settle_skips_release},
    "m2": {"title": "sweep() refunds without updating escrow state",
           "description": "sweep() moves the refund but leaves the escrow "
                          "OPEN/ASSIGNED.",
           "apply": _m2_sweep_no_state_update},
    "m3": {"title": "post_task() skips the lock transfer",
           "description": "post_task() creates the escrow row but the lock "
                          "transfer never happens.",
           "apply": _m3_post_task_skips_lock},
    "m4": {"title": "transfer() moves money but doesn't journal it",
           "description": "transfer() updates the balances but never writes "
                          "the journal row; balances disagree with replay.",
           "apply": _m4_transfer_skips_journal},
    "m5": {"title": "transfer() records the wrong amount",
           "description": "transfer() moves `amount` but records "
                          "`amount + 1`; the journal disagrees.",
           "apply": _m5_transfer_records_wrong_amount},
    "m6": {"title": "refund() goes to the wrong account",
           "description": "refunds go to the seller instead of the buyer.",
           "apply": _m6_refund_wrong_account},
    "m7": {"title": "release() goes to the wrong account",
           "description": "releases go to the buyer instead of the seller.",
           "apply": _m7_release_wrong_account},
}


def list_mutations():
    """Mutation ids in order."""
    return sorted(MUTATIONS)


def apply_mutation(mid):
    """Context manager applying mutation `mid` (restores on exit)."""
    if mid not in MUTATIONS:
        raise ValueError(f"unknown mutation {mid!r}; "
                         f"known: {', '.join(list_mutations())}")
    return MUTATIONS[mid]["apply"]()
