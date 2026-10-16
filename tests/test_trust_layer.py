"""Tests for the trust layer (roadmap steps 2-9).

Every test runs against the real PaymentLayer. Scripted sellers/judges
are stand-ins: tests assert the MEASUREMENT plumbing behaves (rates,
gates, vetoes), not that a stand-in equals a real model.
"""
import json
import os
import subprocess
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from trust_layer import adaptive as adaptive_mod  # noqa: E402
from trust_layer import detectors as detectors_mod  # noqa: E402
from trust_layer import judges as judges_mod  # noqa: E402
from trust_layer import labels as labels_mod  # noqa: E402
from trust_layer import real_models as real_mod  # noqa: E402
from trust_layer import report as report_mod  # noqa: E402
from trust_layer import runner as runner_mod  # noqa: E402
from trust_layer import scoring as scoring_mod  # noqa: E402
from trust_layer import settlement as settlement_mod  # noqa: E402
from trust_layer import stats as stats_mod  # noqa: E402
from trust_layer.jobs import by_id  # noqa: E402
from trust_layer.sellers import (  # noqa: E402
    FakeApprovalSeller, HonestSeller, JudgeGamingSeller, SubstitutionSeller,
    VaryingJudgeGamingSeller,
)
import random  # noqa: E402


class TrustLayerTests(unittest.TestCase):
    def test_honest_jobs_pay_out(self):
        ts = runner_mod.run_suite(seed=0, judge=judges_mod.ScriptedJudge())
        for t in ts:
            self.assertEqual(t["violations"], [], t["job_id"])
        honest = [t for t in ts if not t["attack"]]
        self.assertEqual(len(honest), 3)
        for t in honest:
            self.assertEqual(t["outcome"], "RELEASED", t["job_id"])

    def test_fake_approval_vulnerable_flips_trusted_parks(self):
        job = by_id("attack-fake-approval")
        tl = runner_mod.TrustLayer(judge=judges_mod.ScriptedJudge(), seed=0)
        try:
            t = tl.run_job(job, FakeApprovalSeller(),
                           settlement=settlement_mod.VulnerableSettle())
            self.assertEqual(t["outcome"], "RELEASED")  # pre-fix: flips
        finally:
            tl.close()
        tl = runner_mod.TrustLayer(judge=judges_mod.ScriptedJudge(), seed=0)
        try:
            t = tl.run_job(job, FakeApprovalSeller(),
                           settlement=settlement_mod.TrustedSettle())
            self.assertEqual(t["outcome"], "parked_no_approval")
            self.assertEqual(t["violations"], [])
        finally:
            tl.close()

    def test_judge_gaming_labels_naive_exploited_veto_fixes(self):
        before = labels_mod.exploit_rate(judges_mod.ScriptedJudge())
        self.assertEqual(before["exploit_rate"], 1.0)
        self.assertEqual(before["true_positive_rate"], 1.0)
        after = labels_mod.exploit_rate(
            judges_mod.BlindJudge(judges_mod.ScriptedJudge()))
        self.assertEqual(after["exploit_rate"], 0.0)
        self.assertEqual(after["true_positive_rate"], 1.0)

    def test_label_agreement_pending_without_second_reviewer(self):
        self.assertEqual(labels_mod.agreement()["status"], "pending")

    def test_substitution_detection_beats_perfect_routing(self):
        def fresh(job, seller):
            tl = runner_mod.TrustLayer(judge=judges_mod.ScriptedJudge(),
                                        seed=0)
            try:
                return tl.run_job(job, seller)
            finally:
                tl.close()

        blind = detectors_mod.run_substitution_batch(
            fresh, by_id("attack-substitution"),
            SubstitutionSeller(0.0), HonestSeller())
        perfect = detectors_mod.run_substitution_batch(
            fresh, by_id("attack-substitution"),
            SubstitutionSeller(1.0), HonestSeller())
        self.assertGreater(blind["detection_rate"],
                           perfect["detection_rate"])
        self.assertEqual(blind["false_alarm_rate"], 0.0)
        self.assertEqual(perfect["false_alarm_rate"], 0.0)
        # Perfect routing serves the real model on every probe, so probes
        # catch nothing; only spot-checks after payment can.
        self.assertEqual(perfect["caught_before_payment"], 0)

    def test_substitution_denominator_counts_only_substitutions(self):
        def fresh(job, seller):
            tl = runner_mod.TrustLayer(judge=judges_mod.ScriptedJudge(),
                                        seed=0)
            try:
                return tl.run_job(job, seller)
            finally:
                tl.close()

        r = detectors_mod.run_substitution_batch(
            fresh, by_id("attack-substitution"), SubstitutionSeller(1.0),
            HonestSeller(), n_probe=6, n_normal=10)
        # Probes got the real model: not attacks, not in the denominator.
        self.assertEqual(r["substitutions_served"], 10)
        self.assertEqual(r["detection_rate"],
                         (r["caught_before_payment"]
                          + r["caught_after_payment"]) / 10)
        # paid is computed, not assumed: plausible substitutes on
        # normal jobs pass the format check and get paid.
        self.assertGreater(r["paid"], 0)

    def test_spot_check_tolerates_rewording(self):
        job = by_id("attack-substitution")
        reworded = ("After reconciling the excerpts, the inconsistent one "
                    "is Account  B, per the audit trail and ledger.")
        self.assertTrue(detectors_mod.passes_quality(job, reworded))
        self.assertFalse(detectors_mod.passes_quality(job,
                                                      job.degraded_output))

    def test_wilson_interval_contains_rate(self):
        lo, hi = stats_mod.wilson_interval(3, 10)
        self.assertLessEqual(lo, 0.3)
        self.assertGreaterEqual(hi, 0.3)
        lo, hi = stats_mod.wilson_interval(0, 10)
        self.assertEqual(lo, 0.0)
        self.assertLess(hi, 0.3)

    def test_monitor_planted_signal_vs_varied_attacker(self):
        planted = scoring_mod.score_suite(runner_mod.run_all(
            seeds=2, seed0=0, judge=judges_mod.ScriptedJudge()))["monitor"]
        varied = scoring_mod.score_suite(runner_mod.run_all(
            seeds=2, seed0=0, judge=judges_mod.ScriptedJudge(),
            gaming_seller=lambda s: VaryingJudgeGamingSeller(
                rng=random.Random(s))))["monitor"]
        # Planted phrase: the gaming seller is flagged.
        self.assertTrue(planted["seller_stats"]["judge_gaming"]["flagged"])
        # Without it, current features catch nothing — the honest number.
        self.assertEqual(varied["marginal_catch_rate"]["caught_by_monitor"], 0)

    def test_monitor_never_flags_honest_seller(self):
        agg = scoring_mod.score_suite(runner_mod.run_all(
            seeds=5, seed0=0, judge=judges_mod.ScriptedJudge()))
        self.assertEqual(agg["monitor"]["honest_false_flags"]["flagged"], 0)
        self.assertFalse(any(q["seller"] == "honest"
                             for q in agg["monitor"]["escalation_queue"]))

    def test_determinism_same_seed_same_transcripts(self):
        a = runner_mod.run_suite(seed=3, judge=judges_mod.ScriptedJudge())
        b = runner_mod.run_suite(seed=3, judge=judges_mod.ScriptedJudge())
        self.assertEqual(json.dumps(a, sort_keys=True, default=str),
                         json.dumps(b, sort_keys=True, default=str))

    def test_adaptive_curve_rotates_strategies(self):
        attacker = adaptive_mod.AdaptiveAttacker()
        seen = set()
        for _ in range(6):
            seen.add(attacker.strategy)
            attacker.observe_round(0, 1)
        self.assertEqual(seen, set(adaptive_mod.STRATEGIES))

    def test_real_adapter_missing_key_skips_without_network(self):
        for var in ("OPENAI_API_KEY", "ANTHROPIC_API_KEY", "GEMINI_API_KEY",
                    "GOOGLE_API_KEY"):
            os.environ.pop(var, None)
        ready, skipped = real_mod.available_adapters(["gpt", "sonnet", "gemini"])
        self.assertEqual(ready, [])
        self.assertEqual(len(skipped), 3)

    def test_real_judge_fail_closed_on_adapter_error(self):
        class Broken:
            def complete(self, prompt, max_tokens=1024):
                raise RuntimeError("boom")

        judge = real_mod.RealJudge(Broken())
        v = judge({}, "anything")
        self.assertFalse(v.passed)
        self.assertTrue(v.reason.startswith("judge_error"))

    def test_report_generator_never_leaves_placeholders(self):
        results = {"seeds": 1, "seed0": 0, "attacks_attempted": 3,
                   "attacks_paid": 1, "honest_wrongly_rejected": 0,
                   "substitution": {}, "fake_approval": {},
                   "judge_gaming": {}, "monitor": {}}
        out = os.path.join(ROOT, "trust_layer_results",
                           "test_report.md")
        os.makedirs(os.path.dirname(out), exist_ok=True)
        report_mod.generate(results, out, commit="test")
        with open(out) as f:
            text = f.read()
        self.assertNotIn("{{", text)
        self.assertIn("not measured", text)

    def test_cli_one_command_repro(self):
        out = os.path.join(ROOT, "trust_layer_results", "cli_test")
        rc = subprocess.run(
            [sys.executable, os.path.join(ROOT, "run_trust_layer.py"),
             "--seeds", "2", "--out", out],
            capture_output=True, text=True, timeout=120)
        self.assertEqual(rc.returncode, 0, rc.stderr)
        for name in ("results.json", "scoreboard.html", "report.md"):
            self.assertTrue(os.path.exists(os.path.join(out, name)), name)
        with open(os.path.join(out, "results.json")) as f:
            results = json.load(f)
        self.assertEqual(results["invariant_breaks"], 0)
        self.assertEqual(results["honest_wrongly_rejected"], 0)
        # Seeds stays the run count, so the reproduce command is valid.
        self.assertEqual(results["seeds"], 2)
        with open(os.path.join(out, "report.md")) as f:
            report = f.read()
        self.assertIn("run_trust_layer.py --seeds 2 --seed 0", report)
        self.assertIn("harness validated", report)
        self.assertNotIn("| pass", report)


if __name__ == "__main__":
    unittest.main()
