# agentpay tournament

[![CI](https://github.com/OWNER/REPO/actions/workflows/ci.yml/badge.svg)](https://github.com/OWNER/REPO/actions/workflows/ci.yml)
<!-- TODO: replace OWNER/REPO with the real GitHub path once this repo is pushed. -->

**Money is never created, destroyed, or stuck.** That is the invariant, and
the layer's own audit (`check_invariants()`) recomputes every balance from
the journal to prove it. **7/7 planted bugs caught** by the fake attacker
(at `--rounds 6`, the full strategy cycle). This count is tournament-specific:
separately, the unit-test suite catches **8/8** planted bugs in
`mutation_check.py` — a different harness (tests vs attacker). Both verified;
don't conflate them.
Run it: `python3 run_tournament.py --rounds 3`

![Scoreboard from a verified 6-round run: clean layer holds, all 7 planted-bug variants caught](docs/scoreboard.png)

Model attackers vs. the payment layer. Each round a model gets the same
system prompt, the same tools, the same budgets, and a fresh ledger. It tries
to break the layer. A **break** counts only when the layer's own audit —
`check_invariants()` — fails. Everything else is a supporting metric.

## Hall of Fame

**First person to make `check_invariants()` fail goes in the hall of fame.**
Open challenge — see [STARTER_ISSUES.md](../STARTER_ISSUES.md) for three
verified places to begin. The escrow drain (starter issue #1) was found and
fixed — the follow-up challenge is finding another path that moves escrow
money outside the state machine.

Triage rule: the repo's own audit is the arbiter. A report counts only if
`check_invariants()` returns non-empty on the clean layer, and it must
include the replay (the full tool-call sequence) so anyone can reproduce it.
File it with the [Break report](.github/ISSUE_TEMPLATE/break-report.md)
template. "The oracle passes gibberish" and "a `ttl=0` task completes" are
real weaknesses, but they do not fail the audit — they are not hall-of-fame
breaks.

## Run it

```bash
python3 run_tournament.py --rounds 3
python3 run_tournament.py --rounds 6 --budget 10 --out tournament_results
python3 run_tournament.py --rounds 6 --variants all --expect-breaks
```

Output (in `--out`, default `tournament_results/`):

- `scoreboard.html` — static standings + per-round rows, re-rendered after
  every round (near-live while rounds run).
- `results.json` — the same numbers in machine-readable form.
- `replay_<model>_round<n>.json` — every tool call, outcome, category, token
  count, and cost. The full audit trail.

## The fake model

No API keys, no network. `FakeModel` (`tournament/adapters.py`) runs six
scripted, seeded, deterministic attack strategies, one per round, cycling by
`(seed + round)`:

| Strategy | What it tries |
|---|---|
| `retry_storm` | Re-settle, re-submit, replay the same idempotency key, reuse a key with a different amount |
| `cap_probe` | Post tasks up to the spend/task caps, then one more; advance the clock a full hour and retry |
| `state_confusion` | Settle before submit, submit before assign, wrong seller, double-assign |
| `timeout_games` | Let OPEN/ASSIGNED escrows expire and sweep them; prove SUBMITTED work never times out |
| `oracle_games` | Submit garbage (expect refund), settle with the oracle down (expect parked escrow), hash mismatch |
| `key_reuse` | Idempotency-key reuse with changed params, zero/negative amounts, an over-balance pay from the zero-balance uncapped seller account (isolates the funds check), unknown account |

It mostly gets blocked — that is the point. If a strategy ever trips a real
invariant failure, the harness records it as a break and stops the round.

**By design, not a bug:** a task rejected for `spend_cap` stays rejected under
the same task id, even after the cap window rolls over — rejections are saved
under their idempotency key, so the original rejection is the final answer
for that key. No money moves (invariants hold); a buyer must use a new task
id. `cap_probe` demonstrates this: reposting `t6` after the hour still
returns `spend_cap`; a fresh id `t7` posts fine. Worth a line in the writeup,
not a bug report.

Same family: a single transfer larger than `max_spend_per_hour` is *always*
`spend_cap`, however empty the window is — the cap check is
`window_spend + amount > cap`, so the cap doubles as a per-transaction
ceiling by design and the balance check never runs. `insufficient_funds` is
reachable only when `amount <= cap` but `balance < amount`; `key_reuse`
isolates it with a pay from the uncapped, near-empty seller account.

## Scoring (`tournament/scoring.py`)

Deterministic, no LLM judge:

- **Breaks** — `check_invariants()` non-empty after any tool call. Headline metric.
- **Blocked** — moves the layer rejected (`ok=False`, or an exception),
  *excluding* budget blocks (below).
- **Budget-blocked** — moves rejected for `spend_cap` / `task_cap` only.
  A budget block is inconclusive, not a failed attack — see
  [Budget feedback](#budget-feedback) — so it never inflates `blocked`.
- **Diversity** — distinct attack categories attempted, tagged by call pattern:
  `retry`, `repeat`, `cap`, `oracle`, `state`, `timeout`, `funds`.
  The tagger classifies the *attempt*: a replayed key is a retry even when the
  layer accepts the replay; advancing the clock is a timeout attempt even
  when it works. Note: `repeat` is a sequential re-settle / re-assign, not a
  race — the harness makes one call at a time, so true simultaneous-call
  races can't be reached yet.
- **Closeness** — furthest escrow state reached
  (`OPEN < ASSIGNED < SUBMITTED < RELEASED/REFUNDED`) plus the count of
  distinct rejection reasons.

## Planted-bug variants (`tournament/mutations.py`)

Seven genuine bugs, each removing exactly one of the layer's defenses, each
caught by a different audit check. Run them in addition to the clean layer:

```bash
python3 run_tournament.py --rounds 6 --variants all --expect-breaks
python3 run_tournament.py --rounds 6 --variants m1,m3   # selected only
```

| Variant | Planted bug | Audit check that catches it |
|---|---|---|
| `m1` | `settle()` marks RELEASED, skips the release transfer | RELEASED without matching release transfer |
| `m2` | `sweep()` refunds without updating escrow state | live escrow already paid out |
| `m3` | `post_task()` creates the escrow row, skips the lock transfer | missing/wrong lock |
| `m4` | `transfer()` moves money but doesn't journal it | journal mismatch |
| `m5` | `transfer()` moves `amount` but records `amount + 1` | journal mismatch |
| `m6` | refunds go to the seller instead of the buyer | REFUNDED without matching refund transfer |
| `m7` | releases go to the buyer instead of the seller | RELEASED without matching release transfer |

The scoreboard has a per-variant table (CAUGHT / NOT CAUGHT). CI asserts
`--expect-breaks`: every variant must be caught, every run. The clean layer
must hold: CI also asserts `--expect-clean` (0 breaks).

Deliberately not shipped: cap-removal variants (the audit does not check
caps, so they are uncatchable by design), the literal double-release (the
idempotency key is a third line of defense — the second transfer replays
instead of moving money), and funds-check removal (the accounts table has
`CHECK (balance >= 0)`, so it dies at the DB constraint). Defense in depth
working as intended — see `tournament/mutations.py`.

## Budgets: two of them

1. **Play-money caps** — the layer's own `set_cap()` (buyer: 5000
   cents/hour, 5 tasks/hour). The tournament dogfoods the product.
2. **Simulated real dollars** — `--budget` per model for the whole run
   (default $10). Each tool call costs its token counts × fixed assumed
   prices ($3.00/M input, $15.00/M output — see `tournament/harness.py`).
   When a model's remaining budget hits zero, its round stops and it plays no
   further rounds. Spend in the scoreboard is **simulated**, not a real API
   bill.

## Budget feedback

A budget block says nothing about whether the attack would have worked, so
it is treated as **inconclusive** and the attack gets resubmitted:

- **Layer cap block** (`spend_cap` / `task_cap`): the harness tells the model
  in its next state that the block was for budget, not failure, and the
  model resubmits the same attack with a **new** task id / key — the rejected
  id stays rejected by design. The harness advances the clock past the cap
  window so the retry is a fair attempt (budget refilled). `FakeModel` does
  this automatically, at most 2 retries per round. These moves count as
  `budget_blocked`, never as `blocked`.
- **Dollar-budget stop**: the round (and any unrun rounds) are recorded in
  `results.json` as `pending_retries` and shown on the scoreboard. When
  funds increase, resubmit them with a bigger budget:

  ```bash
  python3 run_tournament.py --retry-from tournament_results/results.json \
      --budget 25 --out tournament_results2
  ```

  Each retried round runs on a fresh ledger.

## Add a real model later

Implement the `ModelAdapter` interface (`tournament/adapters.py`):

```python
from tournament.adapters import ModelAdapter

class MyAdapter(ModelAdapter):
    name = "my-model"
    def act(self, state) -> dict:
        # state: prompt, balances, tasks, last_outcome, remaining_budget, ...
        # return {"tool": ..., "args": {...},
        #         "input_tokens": int, "output_tokens": int}
        # ... or {"tool": "stop"}
```

Wire it into `run_tournament.py`'s model list. Keys live in environment
variables on the operator's own machine — **never committed, never in this
repo**. The harness passes the same `SYSTEM_PROMPT`
(`tournament/prompts.py`) to every model; a real adapter must forward it
verbatim, not rewrite it.

## Drop it into the repo

Copy `tournament/`, `run_tournament.py`, `.github/`, `LICENSE`, and
`STARTER_ISSUES.md` into the repo root, next to `agentpay/`. Stdlib only —
no new dependencies. The repo's own suite is untouched: `python3 run.py`
still runs the 22 chaos tests + demo.

After pushing to GitHub, replace `OWNER/REPO` in the CI badge at the top
of this README with the real path.
