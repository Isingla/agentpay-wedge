"""The trust-layer runner: jobs through the REAL PaymentLayer.

One job lifecycle: post -> assign -> submit -> settle. The settle call
goes through the step-5 gate first: jobs that require buyer approval
settle only when the gate says so; otherwise the job is parked and no
money moves. After every job the layer's own audit runs — a non-empty
result is a break, same rule as the tournament harness.

Each run gets a fresh SQLite ledger + fresh PaymentLayer + frozen clock,
so runs are reproducible per seed. Transcripts record every layer call,
the delivery, the evidence hash, and the outcome — the monitor and the
scorer read transcripts only, never layer internals.
"""
import hashlib
import os
import tempfile

from agentpay import DB, Oracle, PaymentLayer

from . import jobs as jobs_mod
from .jobs import JOBS, by_id
from .monitor import escalation_queue, marginal_catch_rate, seller_stats
from .sellers import (FakeApprovalSeller, HonestSeller, JudgeGamingSeller,
                      SubstitutionSeller)
from .settlement import ApprovalChannel, TrustedSettle, VulnerableSettle


class FakeClock:
    def __init__(self, t=1_000_000.0):
        self.t = t

    def __call__(self):
        return self.t

    def advance(self, s):
        self.t += s


class TrustLayer:
    """A fresh layer + channel + judge for one seed."""

    def __init__(self, judge, seed=0):
        self.tmp = tempfile.TemporaryDirectory()
        self.clock = FakeClock()
        self.layer = PaymentLayer(DB(os.path.join(self.tmp.name, "pay.db")),
                                  Oracle(judge=judge), self.clock)
        self.layer.open_agent("buyer", balance=100_000)
        self.layer.open_agent("seller", balance=0, capacity=10)
        self.layer.open_agent("seller2", balance=0, capacity=10)
        self.channel = ApprovalChannel()
        self.seed = seed

    def close(self):
        self.tmp.cleanup()

    def run_job(self, job, seller, settlement=None, buyer_approves=False):
        """Run one job lifecycle. Returns a transcript dict."""
        t = {"job_id": job.job_id, "kind": job.kind,
             "seller": getattr(seller, "name", "unknown"),
             "price_cents": job.price_cents,
             "attack": job.kind != "honest",
             "calls": [], "settle_attempts": 0,
             "delivery": None, "evidence_hash": None,
             "outcome": None}
        L = self.layer
        tid = f"{job.job_id}-s{self.seed}"

        def call(name, fn):
            try:
                out = fn()
                t["calls"].append({"call": name, "ok": bool(getattr(out, "ok", True)),
                                    "reason": getattr(out, "reason", None),
                                    "state": getattr(out, "state", None)})
                return out
            except Exception as exc:
                t["calls"].append({"call": name, "ok": False,
                                    "reason": f"{type(exc).__name__}: {exc}",
                                    "state": None})
                return None

        post = call("post_task", lambda: L.post_task(
            tid, "buyer", job.price_cents, job.layer_spec, ttl=3600.0))
        if not post or not post.ok:
            t["outcome"] = "POST_FAILED"
            t["violations"] = L.check_invariants()
            return t
        call("assign", lambda: L.assign(tid, "seller"))
        delivery = seller.deliver(job)
        t["delivery"] = delivery
        t["evidence_hash"] = hashlib.sha256(
            (delivery or "").encode()).hexdigest()[:16]
        sub = call("submit", lambda: L.submit(tid, "seller", delivery))
        if not sub or not sub.ok:
            t["outcome"] = "SUBMIT_FAILED"
            t["violations"] = L.check_invariants()
            return t
        # Step-5 gate: approval-required jobs settle only via the gate.
        if job.requires_buyer_approval:
            if buyer_approves:
                self.channel.approve(tid)
            gate = settlement or TrustedSettle()
            if not gate.should_settle(tid, delivery, self.channel):
                t["outcome"] = "parked_no_approval"
                t["violations"] = L.check_invariants()
                return t
        t["settle_attempts"] += 1
        settled = call("settle", lambda: L.settle(tid))
        t["outcome"] = settled.state if settled else "SETTLE_ERROR"
        t["violations"] = L.check_invariants()
        return t


def run_suite(seed=0, distinguishability=0.5, judge=None,
              settlement_for_approval=None, judge_for_gaming=None,
              gaming_seller=None):
    """Run the full scenario suite for one seed. Returns transcripts."""
    from .judges import ScriptedJudge
    tl = TrustLayer(judge=judge or ScriptedJudge(), seed=seed)
    try:
        ts = []
        honest = HonestSeller()
        for job in jobs_mod.honest_jobs():
            ts.append(tl.run_job(job, honest))
        # Step 4: substitution batch is run separately (detectors.py);
        # here the single attack job runs once for the transcript log.
        sub_job = by_id("attack-substitution")
        ts.append(tl.run_job(sub_job, SubstitutionSeller(distinguishability)))
        # Step 5: fake approval, trusted gate (no buyer approval -> parked).
        fa_job = by_id("attack-fake-approval")
        ts.append(tl.run_job(fa_job, FakeApprovalSeller(),
                             settlement=settlement_for_approval))
        # Step 6: judge gaming, through the layer judge hook.
        jg_job = by_id("attack-judge-gaming")
        # gaming_seller: a factory taking the seed (e.g. the varied seller
        # that avoids the monitor's planted template signal).
        seller = gaming_seller(seed) if gaming_seller else JudgeGamingSeller()
        ts.append(tl.run_job(jg_job, seller))
        return ts
    finally:
        tl.close()


def run_all(seeds=1, seed0=0, **kwargs):
    """Run the suite across seeds. Returns {seed: transcripts}."""
    return {s: run_suite(seed=s, **kwargs) for s in range(seed0, seed0 + seeds)}
