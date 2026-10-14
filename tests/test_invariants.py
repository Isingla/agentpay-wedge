"""Randomized chaos: thousands of interleaved operations with failures injected.
After every single step the full audit must pass; at the end, with the oracle
back and time advanced, every escrow must reach a terminal state (nothing stuck)."""
import random

from agentpay import ESCROW, IdempotencyConflict
from agentpay.agents import DOCS, Seller
from tests.helpers import LedgerTest

BUYERS = ["b1", "b2", "b3"]
SELLERS = {"honest": "honest", "junk": "garbage", "flaky": "honest"}


class FuzzInvariants(LedgerTest):
    def run_seed(self, seed, steps=600):
        rng = random.Random(seed)
        L = self.layer
        for b in BUYERS:
            L.open_agent(b, rng.randint(500, 5000))
            if rng.random() < 0.5:
                L.set_cap(b, max_spend_per_hour=rng.randint(300, 3000),
                          max_tasks_per_hour=rng.randint(2, 20))
        sellers = {}
        for name, mode in SELLERS.items():
            L.open_agent(name, capacity=rng.randint(1, 3))
            sellers[name] = Seller(L, name, mode, seed=seed)
        posted = []

        for step in range(steps):
            op = rng.random()
            if op < 0.25:
                title, text = rng.choice(DOCS)
                tid = f"s{seed}-{step}"
                spec = {"kind": "summary", "max_words": 25, "must_include": [title], "source": text}
                if L.post_task(tid, rng.choice(BUYERS), rng.randint(1, 400), spec,
                               ttl=rng.uniform(10, 300)).ok:
                    posted.append(tid)
            elif op < 0.33 and posted:                          # retry an old post
                t = L.task(rng.choice(posted))
                L.post_task(t.task_id, t.buyer, t.amount, t.spec)
            elif op < 0.50 and posted:
                L.assign(rng.choice(posted), rng.choice(list(SELLERS)))
            elif op < 0.65:
                name = rng.choice(list(SELLERS))
                if name == "flaky" and rng.random() < 0.5:
                    continue                                    # flaky seller just never shows up
                sellers[name].work_once()
            elif op < 0.78 and posted:
                L.settle(rng.choice(posted))
            elif op < 0.84:
                L.sweep()
            elif op < 0.88:
                self.oracle.up = not self.oracle.up             # oracle flaps
            elif op < 0.95:
                src, dst = rng.sample(BUYERS + list(SELLERS) + [ESCROW], 2)
                key = f"pay-{rng.randint(0, 30)}"               # deliberate key collisions
                try:
                    L.pay(key, src, dst, rng.randint(1, 300))
                except IdempotencyConflict:
                    pass
            else:
                self.clock.advance(rng.uniform(0, 120))

            bad = L.check_invariants()
            self.assertEqual(bad, [], f"seed={seed} step={step}")

        # Liveness: once the oracle is back and time passes, nothing stays stuck.
        self.oracle.up = True
        for t in posted:
            L.settle(t)
        self.clock.advance(10_000)
        L.sweep()
        live = L.db.conn().execute(
            "SELECT COUNT(*) n FROM escrows WHERE state IN ('OPEN','ASSIGNED','SUBMITTED')"
        ).fetchone()["n"]
        self.assertEqual(live, 0, f"seed={seed}: escrows stuck")
        self.assertEqual(L.balance(ESCROW), 0)
        return len(posted)

    def test_fuzz_many_seeds(self):
        total = 0
        for seed in range(8):
            with self.subTest(seed=seed):
                self.tearDown()
                self.setUp()
                total += self.run_seed(seed)
        self.assertGreater(total, 100)                          # it actually exercised escrow
