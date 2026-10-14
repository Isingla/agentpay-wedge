"""End-to-end demo: one buyer, an honest seller, a garbage seller, a seller that
vanishes, and an oracle outage. Prints the ledger at the end."""
import os
import tempfile

from agentpay import DB, ESCROW, Oracle, PaymentLayer
from agentpay.agents import DOCS, Buyer, Seller


class Clock:
    t = 0.0

    def __call__(self):
        return self.t


def main():
    clock, oracle = Clock(), Oracle()
    with tempfile.TemporaryDirectory() as d:
        L = PaymentLayer(DB(os.path.join(d, "demo.db")), oracle, clock)
        L.open_agent("buyer", 1_000)
        L.set_cap("buyer", max_spend_per_hour=900, max_tasks_per_hour=8)
        for s in ("honest", "garbage", "ghost"):
            L.open_agent(s)

        sellers = {s: Seller(L, s, m) for s, m in
                   [("honest", "honest"), ("garbage", "garbage"), ("ghost", "honest")]}
        submitted = []
        for name in ("honest", "garbage", "ghost"):
            b = Buyer(L, "buyer", name, price=150, ttl=60)
            for title, text in DOCS[:2]:
                tid = b.post(title, text)
                if tid and name != "ghost":         # ghost accepts work and disappears
                    sellers[name].work_once()
                    submitted.append(tid)
            for tid, status, reason in b.log:
                print(f"  post {tid:<22} {status:<10} {reason or ''}")

        oracle.up = False
        print("\noracle DOWN  ->", {t: L.settle(t).reason for t in submitted[:1]})
        oracle.up = True
        for t in submitted:
            L.settle(t)
        clock.t += 61
        print("timeout sweep ->", L.sweep())

        print("\nbalances:", {a: L.balance(a) for a in ("buyer", "honest", "garbage", "ghost", ESCROW)})
        print("reputation:", {s: L.reputation(s) for s in sellers})
        bad = L.check_invariants()
        print("invariants:", "OK (money conserved, none stuck)" if not bad else bad)
        return 0 if not bad else 1


if __name__ == "__main__":
    raise SystemExit(main())
