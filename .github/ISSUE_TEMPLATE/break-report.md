---
name: Break report
about: Report a way to make check_invariants() fail
title: "[BREAK] "
labels: break
assignees: ""
---

## What failed

Paste the exact `check_invariants()` output (the audit is the arbiter — a
report only counts if the audit fails):

```
<paste audit output here>
```

## Repro steps

Exact commands to reproduce, starting from a clean checkout. Include the
tournament command if you found it through the tournament (`--rounds`,
`--variants`, `--seed`), or the minimal script if you found it by hand:

```bash
# commands here
```

## Replay log

Attach the replay JSON (`replay_<model>_round<n>.json`) if you have one, or
paste the tool-call sequence (tool + args + outcome) that triggers it.

## Commit

The commit / version of the code you broke (output of `git rev-parse HEAD`,
or "unreleased" if you tested an unpacked zip):

```
<commit here>
```

## Environment

- Python version: <!-- e.g. 3.12 -->
- OS: <!-- e.g. macOS 15 / Ubuntu 24.04 -->

## Notes (optional)

Anything else: why you think it breaks, whether it needs a specific
ordering/timing, whether it also breaks the clean layer or only a variant.
