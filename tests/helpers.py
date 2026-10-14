import os
import tempfile
import unittest

from agentpay import DB, ESCROW, Oracle, PaymentLayer


class FakeClock:
    def __init__(self, t=1_000_000.0):
        self.t = t

    def __call__(self):
        return self.t

    def advance(self, s):
        self.t += s


SUMMARY_SPEC = {"kind": "summary", "max_words": 25, "must_include": ["ledger"],
                "source": "A ledger records every transfer as a row. Balances are derived "
                          "from it. Auditors replay the rows."}
GOOD = "A ledger records every transfer as a row."


class LedgerTest(unittest.TestCase):
    """Fresh SQLite file per test; invariants asserted after every test."""

    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.path = os.path.join(self.dir.name, "pay.db")
        self.clock = FakeClock()
        self.oracle = Oracle()
        self.layer = PaymentLayer(DB(self.path), self.oracle, self.clock)

    def tearDown(self):
        self.assertInvariants()
        self.dir.cleanup()

    def assertInvariants(self):
        self.assertEqual(self.layer.check_invariants(), [])

    def balances(self, *names):
        return {n: self.layer.balance(n) for n in (*names, ESCROW)}
