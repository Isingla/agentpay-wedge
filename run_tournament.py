"""Run the agentpay tournament.

    python3 run_tournament.py --rounds 3
    python3 run_tournament.py --rounds 6 --budget 10 --out tournament_results
    python3 run_tournament.py --retry-from tournament_results/results.json \
        --budget 25 --out tournament_results2
    python3 run_tournament.py --rounds 2 --variants all --expect-breaks
    python3 run_tournament.py --rounds 3 --expect-clean

Writes, into --out: results.json, one replay JSON per round, scoreboard.html
(re-rendered after every round, so the page is near-live while rounds run).
Prints a summary table to stdout.
"""
import argparse
import json
import os
import sys

ROOT = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, ROOT)
os.chdir(ROOT)

from tournament import FakeModel  # noqa: E402
from tournament.harness import run_tournament  # noqa: E402


def main():
    p = argparse.ArgumentParser(description="agentpay tournament")
    p.add_argument("--rounds", type=int, default=3)
    p.add_argument("--budget", type=float, default=10.0,
                   help="simulated dollars per model for the whole run")
    p.add_argument("--models", default="fake",
                   help="comma-separated model names; only 'fake' exists yet")
    p.add_argument("--out", default="tournament_results")
    p.add_argument("--seed", type=int, default=0)
    p.add_argument("--step-cap", type=int, default=50)
    p.add_argument("--retry-from", default=None, metavar="RESULTS_JSON",
                   help="resubmit rounds left pending by the dollar budget "
                        "in a previous run (use a bigger --budget)")
    p.add_argument("--variants", default=None, metavar="LIST",
                   help="planted-bug variants to run IN ADDITION to the "
                        "clean layer: 'all', or comma-separated ids "
                        "(m1,m3). Default: clean layer only.")
    p.add_argument("--expect-clean", action="store_true",
                   help="exit non-zero if any clean-layer round breaks "
                        "(CI: the clean layer must hold)")
    p.add_argument("--expect-breaks", action="store_true",
                   help="exit non-zero if any requested variant goes "
                        "uncaught (CI: every planted bug must be caught)")
    args = p.parse_args()

    from tournament.mutations import MUTATIONS, list_mutations  # noqa: E402
    variants = None
    if args.variants:
        if args.variants == "all":
            variants = list_mutations()
        else:
            variants = [v.strip() for v in args.variants.split(",") if v.strip()]
            for v in variants:
                if v not in MUTATIONS:
                    p.error(f"unknown variant {v!r}; known: "
                            f"{', '.join(list_mutations())}")

    retry_rounds = None
    if args.retry_from:
        with open(args.retry_from) as f:
            prev = json.load(f)
        retry_rounds = {}
        for p_ in prev.get("pending_retries", []):
            retry_rounds.setdefault(p_["model"], []).append(p_["round"])
        if not retry_rounds:
            print(f"No pending retries in {args.retry_from}; nothing to run.")
            return 0
        print(f"Retrying pending rounds from {args.retry_from}: {retry_rounds}")

    adapters = []
    for name in args.models.split(","):
        name = name.strip()
        if name == "fake":
            adapters.append(FakeModel(seed=args.seed))
        else:
            p.error(f"unknown model {name!r}: only 'fake' exists. "
                    "Add a real model by implementing ModelAdapter (README).")

    def on_round(summary):
        b = f" BREAK: {'; '.join(summary['break']['violations'])}" \
            if summary["break"] else ""
        vtag = f" [{summary.get('variant')}]" if summary.get("variant") else ""
        print(f"  {summary['model']} round {summary['round']}{vtag} "
              f"({summary.get('strategy')}): {summary['steps']} steps, "
              f"{summary['blocked']} blocked, "
              f"{summary.get('budget_blocked', 0)} budget-blocked, "
              f"diversity {summary['diversity']}, "
              f"furthest {summary['furthest_state']}, "
              f"stop={summary['stop_reason']}, "
              f"spend ${summary['spend']:.4f}{b}")

    print(f"Tournament: models={args.models} rounds={args.rounds} "
          f"budget=${args.budget}/model seed={args.seed} "
          f"variants={variants or 'clean only'}")
    results = run_tournament(adapters, rounds=args.rounds,
                             budget_per_model=args.budget,
                             step_cap=args.step_cap, seed=args.seed,
                             out_dir=args.out, on_round=on_round,
                             retry_rounds=retry_rounds,
                             retry_from=args.retry_from, variants=variants)

    print(f"\n{'model':<10} {'rounds':>6} {'breaks':>6} {'blocked':>7} "
          f"{'budget-blk':>10} {'div':>3} {'closeness':>9} {'spend':>9}")
    for name, m in sorted(results["models"].items()):
        print(f"{name:<10} {m['rounds']:>6} {m['breaks']:>6} {m['blocked']:>7} "
              f"{m.get('budget_blocked', 0):>10} "
              f"{m['diversity']:>3} {m['closeness']:>9} ${m['spend']:>8.4f}")
    if results.get("pending_retries"):
        print(f"\nPending retries (dollar budget cut these off — "
              f"inconclusive, not blocked):")
        for p_ in results["pending_retries"]:
            print(f"  {p_['model']} round {p_['round']} ({p_['reason']})")
        print(f"Resubmit with a bigger budget: "
              f"--retry-from {os.path.join(args.out, 'results.json')} "
              f"--budget <higher> --out <new dir>")
    print(f"\nResults: {os.path.join(args.out, 'results.json')}")
    print(f"Scoreboard: {os.path.join(args.out, 'scoreboard.html')}")
    rc = 0
    if args.expect_clean:
        clean_breaks = sum(1 for r in results["rounds"]
                           if r.get("variant") is None and r["break"])
        if clean_breaks:
            print(f"FAIL --expect-clean: {clean_breaks} clean-layer "
                  f"round(s) broke.")
            rc = 1
        else:
            print("PASS --expect-clean: clean layer held (0 breaks).")
    if args.expect_breaks:
        if not variants:
            print("FAIL --expect-breaks: no variants were run.")
            rc = 1
        else:
            for v in variants:
                caught = any(r.get("variant") == v and r["break"]
                             for r in results["rounds"])
                if caught:
                    print(f"PASS --expect-breaks: variant {v} caught.")
                else:
                    print(f"FAIL --expect-breaks: variant {v} NOT caught.")
                    rc = 1
    return rc


if __name__ == "__main__":
    sys.exit(main())
