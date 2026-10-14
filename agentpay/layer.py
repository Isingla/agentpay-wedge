"""The payment layer: escrow state machine + spend caps + reputation, over the ledger.

Escrow lifecycle (one row per task):

    post_task ──► OPEN ──assign──► ASSIGNED ──submit──► SUBMITTED ──oracle pass──► RELEASED
                   │                  │                     │
                   └──── timeout ─────┴──► REFUNDED ◄───────┴── oracle fail

Rules that keep money from being created, destroyed, or stuck:
  * Funds move buyer -> ESCROW at post time, in the same transaction as the escrow row.
  * Exactly one of release:<task> / refund:<task> can ever succeed (state CAS + idem key).
  * Timeouts refund only work that was never submitted. Submitted work waits for a
    verdict; an unreachable or broken oracle produces no verdict, so no money moves.
  * Spend caps are enforced here, not in the agent, so a haywire agent can't skip them.
"""
import json
import time
from dataclasses import dataclass
from typing import Optional

from . import ledger
from .ledger import ESCROW, IdempotencyConflict, TransferResult
from .oracle import Oracle, OracleUnavailable

LIVE = ("OPEN", "ASSIGNED", "SUBMITTED")
HOUR = 3600.0


@dataclass(frozen=True)
class Outcome:
    ok: bool
    state: Optional[str] = None
    reason: Optional[str] = None


@dataclass(frozen=True)
class Task:
    task_id: str
    buyer: str
    seller: Optional[str]
    amount: int
    spec: dict
    state: str
    deadline: float
    output: Optional[str]


def _task(r) -> Task:
    return Task(r["task_id"], r["buyer"], r["seller"], r["amount"], json.loads(r["spec"]),
                r["state"], r["deadline"], r["output"])


class PaymentLayer:
    def __init__(self, db, oracle: Optional[Oracle] = None, clock=time.time):
        self.db = db
        self.oracle = oracle or Oracle()
        self.clock = clock
        with db.tx() as c:
            ledger.open_account(c, ESCROW)

    # ---------- setup ----------
    def open_agent(self, agent: str, balance: int = 0, capacity: int = 1) -> None:
        with self.db.tx() as c:
            ledger.open_account(c, agent)
            c.execute("INSERT OR IGNORE INTO agents(id, capacity) VALUES (?, ?)",
                      (agent, capacity))
            if balance:
                ledger.mint(c, agent, balance)

    def set_cap(self, agent, max_spend_per_hour=None, max_tasks_per_hour=None) -> None:
        with self.db.tx() as c:
            c.execute("INSERT OR REPLACE INTO caps VALUES (?, ?, ?)",
                      (agent, max_spend_per_hour, max_tasks_per_hour))

    # ---------- reads ----------
    def balance(self, acct: str) -> int:
        return ledger.balance(self.db.conn(), acct)

    def reputation(self, agent: str) -> int:
        r = self.db.conn().execute("SELECT reputation FROM agents WHERE id=?", (agent,)).fetchone()
        return r["reputation"]

    def task(self, task_id: str) -> Optional[Task]:
        r = self.db.conn().execute("SELECT * FROM escrows WHERE task_id=?", (task_id,)).fetchone()
        return None if r is None else _task(r)

    def tasks_for(self, seller: str) -> list:
        rows = self.db.conn().execute(
            "SELECT * FROM escrows WHERE seller=? AND state='ASSIGNED' ORDER BY created_at",
            (seller,)).fetchall()
        return [_task(r) for r in rows]

    def open_tasks(self) -> list:
        rows = self.db.conn().execute(
            "SELECT * FROM escrows WHERE state='OPEN' ORDER BY created_at").fetchall()
        return [_task(r) for r in rows]

    # ---------- caps ----------
    def _cap_violation(self, c, agent: str, amount: int, is_task: bool, now: float):
        cap = c.execute("SELECT * FROM caps WHERE agent=?", (agent,)).fetchone()
        if cap is None:
            return None
        since = now - HOUR
        r = c.execute("SELECT COALESCE(SUM(amount),0) s, COALESCE(SUM(is_task),0) n "
                      "FROM spend_log WHERE agent=? AND ts > ?", (agent, since)).fetchone()
        if cap["max_spend_per_hour"] is not None and r["s"] + amount > cap["max_spend_per_hour"]:
            return "spend_cap"
        if is_task and cap["max_tasks_per_hour"] is not None and r["n"] + 1 > cap["max_tasks_per_hour"]:
            return "task_cap"
        return None

    # ---------- direct payment ----------
    def pay(self, key: str, src: str, dst: str, amount: int) -> TransferResult:
        now = self.clock()
        with self.db.tx() as c:
            prior = ledger.lookup(c, key, src, dst, amount)
            if prior is not None:
                return prior                      # replay: no cap charge, no movement
            why = self._cap_violation(c, src, amount, False, now)
            if why:
                return ledger.record_rejection(c, key, src, dst, amount, why, now)
            r = ledger.transfer(c, key, src, dst, amount, now)
            if r.ok:
                c.execute("INSERT INTO spend_log(agent,amount,is_task,ts) VALUES (?,?,0,?)",
                          (src, amount, now))
            return r

    # ---------- escrow ----------
    def post_task(self, task_id: str, buyer: str, amount: int, spec: dict,
                  ttl: float = 60.0) -> Outcome:
        """Lock `amount` from buyer into escrow. Idempotent on task_id."""
        now = self.clock()
        key = f"lock:{task_id}"
        spec_json = json.dumps(spec, sort_keys=True)
        with self.db.tx() as c:
            prior = ledger.lookup(c, key, buyer, ESCROW, amount)
            if prior is not None:
                e = c.execute("SELECT spec FROM escrows WHERE task_id=?", (task_id,)).fetchone()
                if e is not None and e["spec"] != spec_json:
                    raise IdempotencyConflict(f"task {task_id!r} reposted with a different spec")
                return Outcome(prior.ok, "OPEN" if prior.ok else None, prior.reason)
            why = self._cap_violation(c, buyer, amount, True, now)
            if why:
                ledger.record_rejection(c, key, buyer, ESCROW, amount, why, now)
                return Outcome(False, None, why)
            r = ledger.transfer(c, key, buyer, ESCROW, amount, now)
            if not r.ok:
                return Outcome(False, None, r.reason)
            c.execute("INSERT INTO escrows(task_id,buyer,amount,spec,state,created_at,deadline)"
                      " VALUES (?,?,?,?, 'OPEN', ?, ?)",
                      (task_id, buyer, amount, spec_json, now, now + ttl))
            c.execute("INSERT INTO spend_log(agent,amount,is_task,ts) VALUES (?,?,1,?)",
                      (buyer, amount, now))
            return Outcome(True, "OPEN")

    def assign(self, task_id: str, seller: str) -> Outcome:
        """Give an OPEN task to a seller. At most one seller per task; at most
        `capacity` ASSIGNED tasks per seller."""
        now = self.clock()
        with self.db.tx() as c:
            e = c.execute("SELECT * FROM escrows WHERE task_id=?", (task_id,)).fetchone()
            if e is None:
                return Outcome(False, None, "no_such_task")
            if e["state"] != "OPEN":
                same = e["seller"] == seller and e["state"] == "ASSIGNED"
                return Outcome(same, e["state"], None if same else "not_open")
            if now > e["deadline"]:
                return Outcome(False, "OPEN", "expired")
            a = c.execute("SELECT capacity FROM agents WHERE id=?", (seller,)).fetchone()
            if a is None:
                return Outcome(False, "OPEN", "no_such_seller")
            busy = c.execute("SELECT COUNT(*) n FROM escrows WHERE seller=? AND state='ASSIGNED'",
                             (seller,)).fetchone()["n"]
            if busy >= a["capacity"]:
                return Outcome(False, "OPEN", "seller_busy")
            c.execute("UPDATE escrows SET seller=?, state='ASSIGNED' "
                      "WHERE task_id=? AND state='OPEN'", (seller, task_id))
            return Outcome(True, "ASSIGNED")

    def submit(self, task_id: str, seller: str, output: str) -> Outcome:
        now = self.clock()
        with self.db.tx() as c:
            e = c.execute("SELECT * FROM escrows WHERE task_id=?", (task_id,)).fetchone()
            if e is None:
                return Outcome(False, None, "no_such_task")
            if e["seller"] != seller:
                return Outcome(False, e["state"], "not_assignee")
            if e["state"] == "SUBMITTED" and e["output"] == output:
                return Outcome(True, "SUBMITTED")            # idempotent resubmit
            if e["state"] != "ASSIGNED":
                return Outcome(False, e["state"], "not_assigned")
            if now > e["deadline"]:
                return Outcome(False, "ASSIGNED", "late")
            c.execute("UPDATE escrows SET state='SUBMITTED', output=? "
                      "WHERE task_id=? AND state='ASSIGNED'", (output, task_id))
            return Outcome(True, "SUBMITTED")

    def settle(self, task_id: str) -> Outcome:
        """Ask the oracle, then release or refund. Safe to call any number of times,
        from any number of threads: only one call can move the money."""
        t = self.task(task_id)
        if t is None:
            return Outcome(False, None, "no_such_task")
        if t.state in ("RELEASED", "REFUNDED"):
            return Outcome(True, t.state, "already_settled")
        if t.state != "SUBMITTED":
            return Outcome(False, t.state, "not_submitted")

        # Oracle call happens OUTSIDE the write lock (it may be slow, e.g. an LLM).
        try:
            v = self.oracle.verify(t.spec, t.output)
        except OracleUnavailable:
            return Outcome(False, "SUBMITTED", "oracle_unavailable")
        except Exception as exc:                    # broken oracle == no verdict
            return Outcome(False, "SUBMITTED", f"oracle_error:{type(exc).__name__}")

        now = self.clock()
        with self.db.tx() as c:
            e = c.execute("SELECT * FROM escrows WHERE task_id=?", (task_id,)).fetchone()
            if e["state"] != "SUBMITTED":           # another settler won the race
                return Outcome(True, e["state"], "already_settled")
            if v.passed:
                r = ledger.transfer(c, f"release:{task_id}", ESCROW, e["seller"], e["amount"], now)
                state, delta = "RELEASED", +1
            else:
                r = ledger.transfer(c, f"refund:{task_id}", ESCROW, e["buyer"], e["amount"], now)
                state, delta = "REFUNDED", -1
            if not r.ok:                            # would mean escrow is underfunded: abort tx
                raise RuntimeError(f"escrow settlement failed: {r.reason}")
            c.execute("UPDATE escrows SET state=?, verdict=?, settled_reason=? WHERE task_id=?",
                      (state, v.reason, "oracle", task_id))
            c.execute("UPDATE agents SET reputation = reputation + ? WHERE id=?",
                      (delta, e["seller"]))
            return Outcome(True, state, v.reason)

    def sweep(self) -> list:
        """Refund every OPEN/ASSIGNED escrow past its deadline. SUBMITTED work is
        never timed out: it waits for a verdict."""
        now = self.clock()
        refunded = []
        with self.db.tx() as c:
            rows = c.execute("SELECT * FROM escrows WHERE state IN ('OPEN','ASSIGNED') "
                             "AND deadline < ?", (now,)).fetchall()
            for e in rows:
                r = ledger.transfer(c, f"refund:{e['task_id']}", ESCROW, e["buyer"], e["amount"], now)
                if not r.ok:
                    raise RuntimeError(f"timeout refund failed: {r.reason}")
                c.execute("UPDATE escrows SET state='REFUNDED', settled_reason='timeout' "
                          "WHERE task_id=?", (e["task_id"],))
                if e["seller"]:                     # abandoned after accepting
                    c.execute("UPDATE agents SET reputation = reputation - 1 WHERE id=?",
                              (e["seller"],))
                refunded.append(e["task_id"])
        return refunded

    # ---------- audit ----------
    def check_invariants(self) -> list:
        """Return a list of violations (empty == healthy). Recomputes every balance
        from first principles: mints + accepted transfers."""
        bad = []
        with self.db.tx() as c:
            minted = c.execute("SELECT COALESCE(SUM(amount),0) s FROM mints").fetchone()["s"]
            total = c.execute("SELECT COALESCE(SUM(balance),0) s FROM accounts").fetchone()["s"]
            if total != minted:
                bad.append(f"conservation: balances {total} != minted {minted}")

            for a in c.execute("SELECT id, balance FROM accounts").fetchall():
                if a["balance"] < 0:
                    bad.append(f"negative balance: {a['id']}")
                expect = c.execute(
                    "SELECT (SELECT COALESCE(SUM(amount),0) FROM mints WHERE account=:a)"
                    " + (SELECT COALESCE(SUM(amount),0) FROM transfers WHERE status='ok' AND dst=:a)"
                    " - (SELECT COALESCE(SUM(amount),0) FROM transfers WHERE status='ok' AND src=:a) v",
                    {"a": a["id"]}).fetchone()["v"]
                if expect != a["balance"]:
                    bad.append(f"journal mismatch: {a['id']} stored {a['balance']} replayed {expect}")

            live = c.execute("SELECT COALESCE(SUM(amount),0) s FROM escrows WHERE state IN "
                             "('OPEN','ASSIGNED','SUBMITTED')").fetchone()["s"]
            if ledger.balance(c, ESCROW) != live:
                bad.append(f"escrow account {ledger.balance(c, ESCROW)} != live escrows {live}")

            ok = {r["idem_key"]: r for r in
                  c.execute("SELECT * FROM transfers WHERE status='ok'").fetchall()}
            for e in c.execute("SELECT * FROM escrows").fetchall():
                tid, amt = e["task_id"], e["amount"]
                lock, rel, ref = ok.get(f"lock:{tid}"), ok.get(f"release:{tid}"), ok.get(f"refund:{tid}")
                if lock is None or lock["amount"] != amt:
                    bad.append(f"{tid}: missing/wrong lock")
                if rel and ref:
                    bad.append(f"{tid}: both released and refunded")
                if e["state"] == "RELEASED" and not (rel and rel["amount"] == amt and rel["dst"] == e["seller"]):
                    bad.append(f"{tid}: RELEASED without matching release transfer")
                if e["state"] == "REFUNDED" and not (ref and ref["amount"] == amt and ref["dst"] == e["buyer"]):
                    bad.append(f"{tid}: REFUNDED without matching refund transfer")
                if e["state"] in LIVE and (rel or ref):
                    bad.append(f"{tid}: live escrow already paid out")
        return bad
