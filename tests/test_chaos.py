"""The six chaos scenarios. Each ends with assertions on the ledger, and
tearDown re-audits every invariant from the raw journal."""
import os
import signal
import subprocess
import sys
import threading
import time

from agentpay import DB, ESCROW, IdempotencyConflict, PaymentLayer
from agentpay.agents import Buyer, Seller
from tests.helpers import GOOD, SUMMARY_SPEC, FakeClock, LedgerTest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


class T1_SellerKilledMidTask(LedgerTest):
    def test_sigkill_seller_process_then_timeout_refunds_buyer(self):
        # Real wall clock here: the seller is a separate OS process.
        self.clock.t = time.time()
        L = self.layer
        L.open_agent("buyer", 1000)
        L.open_agent("seller")
        self.assertTrue(L.post_task("t1", "buyer", 300, SUMMARY_SPEC, ttl=30).ok)
        self.assertTrue(L.assign("t1", "seller").ok)

        proc = subprocess.Popen(
            [sys.executable, "-m", "agentpay.seller_worker", self.path, "seller", "60"],
            cwd=ROOT, stdout=subprocess.PIPE, text=True)
        try:
            line = proc.stdout.readline().strip()
            self.assertEqual(line, "WORKING t1")       # it really picked the task up
            os.kill(proc.pid, signal.SIGKILL)          # ...and dies mid-task
            proc.wait(timeout=10)
        finally:
            if proc.poll() is None:
                proc.kill()
        self.assertEqual(proc.returncode, -signal.SIGKILL)

        # Before the deadline nothing moves: money sits in escrow, not lost.
        self.assertEqual(L.sweep(), [])
        self.assertEqual(self.balances("buyer", "seller"),
                         {"buyer": 700, "seller": 0, ESCROW: 300})
        self.assertInvariants()

        self.clock.advance(31)
        self.assertEqual(L.sweep(), ["t1"])
        self.assertEqual(self.balances("buyer", "seller"),
                         {"buyer": 1000, "seller": 0, ESCROW: 0})
        self.assertEqual(L.task("t1").state, "REFUNDED")
        self.assertEqual(L.reputation("seller"), -1)

        # A zombie seller coming back late can't submit or get paid.
        self.assertFalse(L.submit("t1", "seller", GOOD).ok)
        self.assertEqual(L.settle("t1").state, "REFUNDED")
        self.assertEqual(L.balance("seller"), 0)
        self.assertEqual(L.sweep(), [])                # refund happens once

    def test_in_process_crash_seller_agent(self):
        L = self.layer
        L.open_agent("buyer", 1000)
        L.open_agent("seller")
        buyer, seller = Buyer(L, "buyer", "seller", price=100), Seller(L, "seller", "crash")
        tid = buyer.post("ledger", SUMMARY_SPEC["source"])
        with self.assertRaises(SystemExit):
            seller.work_once()
        self.clock.advance(61)
        self.assertEqual(L.sweep(), [tid])
        self.assertEqual(self.balances("buyer", "seller"),
                         {"buyer": 1000, "seller": 0, ESCROW: 0})


class T2_RetryStorm(LedgerTest):
    def test_same_payment_retried_10x(self):
        L = self.layer
        L.open_agent("buyer", 1000)
        L.open_agent("seller")
        results = [L.pay("pay-123", "buyer", "seller", 250) for _ in range(10)]
        self.assertEqual(len(set(results)), 1)          # ten identical responses
        self.assertTrue(results[0].ok)
        self.assertEqual(self.balances("buyer", "seller"),
                         {"buyer": 750, "seller": 250, ESCROW: 0})
        n = L.db.conn().execute("SELECT COUNT(*) n FROM transfers").fetchone()["n"]
        self.assertEqual(n, 1)                          # exactly one transfer row

    def test_same_payment_retried_10x_concurrently(self):
        L = self.layer
        L.open_agent("buyer", 1000)
        L.open_agent("seller")
        gate, out = threading.Barrier(10), []

        def hit():
            gate.wait()
            out.append(L.pay("pay-race", "buyer", "seller", 250))
        ts = [threading.Thread(target=hit) for _ in range(10)]
        [t.start() for t in ts]
        [t.join() for t in ts]
        self.assertEqual(len(out), 10)
        self.assertEqual(len(set(out)), 1)
        self.assertEqual(L.balance("seller"), 250)

    def test_rejected_payment_replays_as_rejected(self):
        L = self.layer
        L.open_agent("buyer", 100)
        L.open_agent("seller")
        first = L.pay("big", "buyer", "seller", 500)
        self.assertEqual(first.reason, "insufficient_funds")
        L.open_agent("buyer", 1000)                     # top up
        self.assertEqual(L.pay("big", "buyer", "seller", 500), first)   # key is spent
        self.assertEqual(L.balance("seller"), 0)

    def test_key_reuse_with_different_amount_is_an_error(self):
        L = self.layer
        L.open_agent("buyer", 1000)
        L.open_agent("seller")
        L.pay("k", "buyer", "seller", 100)
        with self.assertRaises(IdempotencyConflict):
            L.pay("k", "buyer", "seller", 900)
        self.assertEqual(L.balance("seller"), 100)

    def test_retry_near_spend_cap_returns_original_not_cap_rejection(self):
        # A replay must not be re-charged against the cap: 300 + 300 > 500 would
        # otherwise turn a successful payment into a "spend_cap" error on retry.
        L = self.layer
        L.open_agent("buyer", 1000)
        L.open_agent("seller")
        L.set_cap("buyer", max_spend_per_hour=500)
        outs = [L.pay("capped", "buyer", "seller", 300) for _ in range(10)]
        self.assertEqual(len(set(outs)), 1)
        self.assertTrue(outs[0].ok)
        posts = [L.post_task("cj", "buyer", 200, SUMMARY_SPEC) for _ in range(10)]
        self.assertTrue(all(p.ok for p in posts))
        self.assertEqual(L.balance("buyer"), 500)

    def test_task_post_retried_10x_locks_once(self):
        L = self.layer
        L.open_agent("buyer", 1000)
        outs = [L.post_task("job", "buyer", 300, SUMMARY_SPEC) for _ in range(10)]
        self.assertEqual(len(set(outs)), 1)
        self.assertEqual(self.balances("buyer"), {"buyer": 700, ESCROW: 300})


class T3_Overspend(LedgerTest):
    def test_haywire_buyer_hits_task_cap(self):
        L = self.layer
        L.open_agent("buyer", 10_000)
        L.set_cap("buyer", max_spend_per_hour=1_000, max_tasks_per_hour=3)
        outs = [L.post_task(f"t{i}", "buyer", 100, SUMMARY_SPEC) for i in range(100)]
        self.assertEqual(sum(o.ok for o in outs), 3)
        self.assertEqual({o.reason for o in outs[3:]}, {"task_cap"})
        self.assertEqual(L.balance("buyer"), 9_700)

        self.clock.advance(3601)                         # window rolls over
        self.assertTrue(L.post_task("next-hour", "buyer", 100, SUMMARY_SPEC).ok)

    def test_spend_cap_covers_direct_payments_and_tasks(self):
        L = self.layer
        L.open_agent("buyer", 10_000)
        L.open_agent("seller")
        L.set_cap("buyer", max_spend_per_hour=500)
        self.assertTrue(L.pay("p1", "buyer", "seller", 300).ok)
        self.assertEqual(L.post_task("t1", "buyer", 300, SUMMARY_SPEC).reason, "spend_cap")
        self.assertTrue(L.post_task("t2", "buyer", 200, SUMMARY_SPEC).ok)
        self.assertEqual(L.pay("p2", "buyer", "seller", 1).reason, "spend_cap")
        self.assertEqual(L.balance("buyer"), 9_500)

    def test_balance_never_negative_without_caps(self):
        L = self.layer
        L.open_agent("buyer", 250)
        L.open_agent("seller")
        seen = []
        for i in range(50):
            L.post_task(f"t{i}", "buyer", 100, SUMMARY_SPEC)
            L.pay(f"p{i}", "buyer", "seller", 7)
            seen.append(L.balance("buyer"))
        self.assertGreaterEqual(min(seen), 0)
        self.assertEqual(L.balance(ESCROW), 200)        # 2 tasks fit, then funds ran out

    def test_db_constraint_is_a_backstop(self):
        import sqlite3
        with self.assertRaises(sqlite3.IntegrityError):
            with self.layer.db.tx() as c:
                c.execute("UPDATE accounts SET balance = -1 WHERE id = ?", (ESCROW,))


class T4_GarbageOutput(LedgerTest):
    def _run(self, mode):
        L = self.layer
        L.open_agent("buyer", 1000)
        L.open_agent("seller")
        buyer, seller = Buyer(L, "buyer", "seller", price=200), Seller(L, "seller", mode)
        tid = buyer.post("ledger", SUMMARY_SPEC["source"])
        seller.work_once()
        return tid, L.settle(tid)

    def test_garbage_is_failed_refunded_and_reputation_drops(self):
        tid, out = self._run("garbage")
        self.assertEqual(out.state, "REFUNDED")
        self.assertEqual(self.balances("buyer", "seller"),
                         {"buyer": 1000, "seller": 0, ESCROW: 0})
        self.assertEqual(self.layer.reputation("seller"), -1)

    def test_honest_control_is_paid(self):
        tid, out = self._run("honest")
        self.assertEqual(out.state, "RELEASED")
        self.assertEqual(self.balances("buyer", "seller"),
                         {"buyer": 800, "seller": 200, ESCROW: 0})
        self.assertEqual(self.layer.reputation("seller"), 1)

    def test_oracle_rejects_each_kind_of_garbage(self):
        o = self.oracle
        bad = ["", "   ", "\x00\x01\x02 ledger", "word " * 40 + "ledger",
               "no keyword here", SUMMARY_SPEC["source"], None, 12345]
        for out in bad:
            self.assertFalse(o.verify(SUMMARY_SPEC, out).passed, repr(out)[:40])
        self.assertTrue(o.verify(SUMMARY_SPEC, GOOD).passed)
        h = {"kind": "sha256", "sha256": __import__("hashlib").sha256(b"abc").hexdigest()}
        self.assertTrue(o.verify(h, "abc").passed)
        self.assertFalse(o.verify(h, "abd").passed)
        s = {"kind": "json_schema", "required": {"title": "str", "score": "float"}}
        self.assertTrue(o.verify(s, '{"title": "x", "score": 0.5}').passed)
        self.assertFalse(o.verify(s, '{"title": "x"}').passed)
        self.assertFalse(o.verify(s, "not json").passed)


class T5_OracleDown(LedgerTest):
    def _submitted(self):
        L = self.layer
        L.open_agent("buyer", 1000)
        L.open_agent("seller")
        L.post_task("t", "buyer", 400, SUMMARY_SPEC, ttl=60)
        L.assign("t", "seller")
        L.submit("t", "seller", GOOD)

    def test_no_release_no_refund_until_oracle_returns(self):
        L = self.layer
        self._submitted()
        self.oracle.up = False
        for _ in range(5):
            self.assertEqual(L.settle("t").reason, "oracle_unavailable")
        self.clock.advance(10 * 3600)                   # way past the deadline
        self.assertEqual(L.sweep(), [])                 # timeout must NOT refund submitted work
        self.assertEqual(self.balances("buyer", "seller"),
                         {"buyer": 600, "seller": 0, ESCROW: 400})
        self.assertInvariants()                         # money is parked, not lost

        self.oracle.up = True
        self.assertEqual(L.settle("t").state, "RELEASED")
        self.assertEqual(self.balances("buyer", "seller"),
                         {"buyer": 600, "seller": 400, ESCROW: 0})

    def test_crashing_oracle_is_treated_as_no_verdict(self):
        L = self.layer
        self._submitted()

        def boom(spec, output):
            raise ZeroDivisionError
        self.oracle.verify = boom
        self.assertTrue(L.settle("t").reason.startswith("oracle_error"))
        self.assertEqual(L.task("t").state, "SUBMITTED")
        self.assertEqual(L.balance(ESCROW), 400)


class T6_TwoBuyersOneSeller(LedgerTest):
    def test_concurrent_assign_respects_seller_capacity(self):
        L = self.layer
        L.open_agent("seller", capacity=1)
        for b in ("b1", "b2"):
            L.open_agent(b, 10_000)
        for rnd in range(25):
            ids = [f"{b}-{rnd}" for b in ("b1", "b2")]
            for tid, b in zip(ids, ("b1", "b2")):
                L.post_task(tid, b, 100, SUMMARY_SPEC)
            gate, out = threading.Barrier(2), {}

            def go(tid):
                gate.wait()
                out[tid] = L.assign(tid, "seller")
            ts = [threading.Thread(target=go, args=(t,)) for t in ids]
            [t.start() for t in ts]
            [t.join() for t in ts]
            winners = [t for t in ids if out[t].ok]
            self.assertEqual(len(winners), 1, out)
            loser = next(t for t in ids if t not in winners)
            self.assertEqual(out[loser].reason, "seller_busy")
            # seller finishes, loser can now be served; both paid exactly once
            L.submit(winners[0], "seller", GOOD)
            self.assertTrue(L.assign(loser, "seller").ok)
            L.submit(loser, "seller", GOOD)
            L.settle(winners[0])
            L.settle(loser)
        self.assertEqual(L.balance("seller"), 25 * 2 * 100)
        self.assertEqual(L.balance("b1") + L.balance("b2"), 20_000 - 5_000)

    def test_two_sellers_race_for_one_task(self):
        L = self.layer
        L.open_agent("buyer", 1000)
        L.open_agent("s1")
        L.open_agent("s2")
        L.post_task("t", "buyer", 100, SUMMARY_SPEC)
        gate, out = threading.Barrier(2), {}

        def go(s):
            gate.wait()
            out[s] = L.assign("t", s)
        ts = [threading.Thread(target=go, args=(s,)) for s in ("s1", "s2")]
        [t.start() for t in ts]
        [t.join() for t in ts]
        self.assertEqual(sum(o.ok for o in out.values()), 1)
        winner = L.task("t").seller
        loser = "s2" if winner == "s1" else "s1"
        self.assertFalse(L.submit("t", loser, GOOD).ok)  # loser can't sneak in work

    def test_concurrent_settle_pays_once(self):
        L = self.layer
        L.open_agent("buyer", 1000)
        L.open_agent("seller")
        L.post_task("t", "buyer", 300, SUMMARY_SPEC)
        L.assign("t", "seller")
        L.submit("t", "seller", GOOD)
        gate, out = threading.Barrier(12), []

        def go():
            gate.wait()
            out.append(L.settle("t"))
        ts = [threading.Thread(target=go) for _ in range(12)]
        [t.start() for t in ts]
        [t.join() for t in ts]
        self.assertTrue(all(o.state == "RELEASED" for o in out))
        self.assertEqual(L.balance("seller"), 300)
        n = L.db.conn().execute(
            "SELECT COUNT(*) n FROM transfers WHERE idem_key LIKE 'release:%'").fetchone()["n"]
        self.assertEqual(n, 1)
        self.assertEqual(L.reputation("seller"), 1)     # rep bumped once, not 12 times

    def test_two_processes_share_one_ledger(self):
        """Second process, same file: a separate PaymentLayer can't double-assign either."""
        other = PaymentLayer(DB(self.path), self.oracle, self.clock)
        L = self.layer
        L.open_agent("buyer", 1000)
        L.open_agent("seller")
        L.post_task("t", "buyer", 100, SUMMARY_SPEC)
        self.assertTrue(other.assign("t", "seller").ok)
        self.assertEqual(L.assign("t", "someone-else").reason, "not_open")
