"""Double-entry ledger with idempotency keys.

Amounts are integer cents. Every transfer attempt, accepted or rejected, is a
row keyed by its idempotency key. Replaying a key returns the stored row and
moves nothing. Replaying a key with different parameters is an error, not a
silent second payment.

All functions take an open connection inside a caller's transaction so ledger
moves and escrow state changes commit together or not at all.
"""
from dataclasses import dataclass
from typing import Optional

ESCROW = "__escrow__"


class IdempotencyConflict(Exception):
    """Same idempotency key reused for a different payment."""


@dataclass(frozen=True)
class TransferResult:
    idem_key: str
    src: str
    dst: str
    amount: int
    status: str            # 'ok' | 'rejected'
    reason: Optional[str]

    @property
    def ok(self) -> bool:
        return self.status == "ok"


def _row(r) -> TransferResult:
    return TransferResult(r["idem_key"], r["src"], r["dst"], r["amount"],
                          r["status"], r["reason"])


def open_account(c, acct: str) -> None:
    c.execute("INSERT OR IGNORE INTO accounts(id, balance) VALUES (?, 0)", (acct,))


def mint(c, acct: str, amount: int) -> None:
    """Genesis funding. The only place money is created; recorded so audits can sum it."""
    if not isinstance(amount, int) or amount <= 0:
        raise ValueError("mint amount must be a positive int")
    open_account(c, acct)
    c.execute("UPDATE accounts SET balance = balance + ? WHERE id = ?", (amount, acct))
    c.execute("INSERT INTO mints(account, amount) VALUES (?, ?)", (acct, amount))


def balance(c, acct: str) -> Optional[int]:
    r = c.execute("SELECT balance FROM accounts WHERE id = ?", (acct,)).fetchone()
    return None if r is None else r["balance"]


def lookup(c, key: str, src: str, dst: str, amount: int) -> Optional[TransferResult]:
    """Return the stored result for `key`, or None. Raises on parameter mismatch."""
    r = c.execute("SELECT * FROM transfers WHERE idem_key = ?", (key,)).fetchone()
    if r is None:
        return None
    if (r["src"], r["dst"], r["amount"]) != (src, dst, amount):
        raise IdempotencyConflict(
            f"key {key!r} was used for {r['src']}->{r['dst']} {r['amount']}, "
            f"not {src}->{dst} {amount}")
    return _row(r)


def record_rejection(c, key, src, dst, amount, reason, now) -> TransferResult:
    c.execute("INSERT INTO transfers VALUES (?,?,?,?, 'rejected', ?, ?)",
              (key, src, dst, amount, reason, now))
    return TransferResult(key, src, dst, amount, "rejected", reason)


def transfer(c, key: str, src: str, dst: str, amount: int, now: float) -> TransferResult:
    if not isinstance(amount, int) or amount <= 0:
        raise ValueError("amount must be a positive int (cents)")
    prior = lookup(c, key, src, dst, amount)
    if prior is not None:
        return prior
    src_bal = balance(c, src)
    if src_bal is None or balance(c, dst) is None:
        return record_rejection(c, key, src, dst, amount, "no_such_account", now)
    if src_bal < amount:
        return record_rejection(c, key, src, dst, amount, "insufficient_funds", now)
    c.execute("UPDATE accounts SET balance = balance - ? WHERE id = ?", (amount, src))
    c.execute("UPDATE accounts SET balance = balance + ? WHERE id = ?", (amount, dst))
    c.execute("INSERT INTO transfers VALUES (?,?,?,?, 'ok', NULL, ?)",
              (key, src, dst, amount, now))
    return TransferResult(key, src, dst, amount, "ok", None)
