"""Reproduce the "tests catch planted bugs" claim.

Copies the repo to a temp dir, plants one bug at a time in agentpay/layer.py,
runs the test suite, and reports whether the tests caught it.
usage: python3 mutation_check.py      (exit 0 only if every planted bug is caught)
"""
import os
import shutil
import subprocess
import sys
import tempfile

ROOT = os.path.dirname(os.path.abspath(__file__))

MUTANTS = {
    "settle without state compare-and-swap":
        ('if e["state"] != "SUBMITTED":           # another', 'if False:           # another'),
    "timeout sweep also refunds submitted work":
        ("state IN ('OPEN','ASSIGNED') \"", "state IN ('OPEN','ASSIGNED','SUBMITTED') \""),
    "crashing oracle treated as a fail verdict":
        ('return Outcome(False, "SUBMITTED", f"oracle_error',
         'v = __import__("agentpay").Verdict(False, "x")#'),
    "seller capacity not checked":
        ('if busy >= a["capacity"]:', "if False:"),
    "spend cap not enforced":
        ("if why:\n                ledger.record_rejection", "if False:\n                ledger.record_rejection"),
    "payment replay re-checked against cap":
        ("            if prior is not None:\n                return prior                      # replay",
         "            if False:\n                return prior                      # replay"),
    "pay() lets money in/out of escrow":
        ("if ESCROW in (src, dst):", "if False:"),
    "assign ignores task state":
        ('if e["state"] != "OPEN":\n                same', "if False:\n                same"),
}


def run(name, old, new):
    with tempfile.TemporaryDirectory() as d:
        dst = os.path.join(d, "repo")
        shutil.copytree(ROOT, dst, ignore=shutil.ignore_patterns(".git", "__pycache__"))
        path = os.path.join(dst, "agentpay", "layer.py")
        src = open(path).read()
        if old not in src:
            return "NOT APPLIED"
        open(path, "w").write(src.replace(old, new, 1))
        r = subprocess.run([sys.executable, "-m", "unittest", "discover", "tests"],
                           cwd=dst, capture_output=True, text=True, timeout=600)
        return "caught" if r.returncode != 0 else "SURVIVED"


def main():
    results = {n: run(n, o, w) for n, (o, w) in MUTANTS.items()}
    for n, r in results.items():
        print(f"  {r:<12} {n}")
    caught = sum(r == "caught" for r in results.values())
    print(f"\n{caught}/{len(results)} planted bugs caught")
    return 0 if caught == len(results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
