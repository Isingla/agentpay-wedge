"""One command: `python3 run.py` runs every chaos test, then the demo. Stdlib only."""
import os
import sys
import unittest

ROOT = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, ROOT)
os.chdir(ROOT)

suite = unittest.defaultTestLoader.discover("tests", top_level_dir=ROOT)
result = unittest.TextTestRunner(verbosity=2).run(suite)
if not result.wasSuccessful():
    sys.exit(1)

print("\n=== demo ===")
import demo  # noqa: E402
sys.exit(demo.main())
