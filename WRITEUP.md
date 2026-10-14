# agentpay-wedge: what happens when paying agents misbehave

A buyer agent pays a seller agent per task through a small local payment layer. The agents are deliberately dumb (about 50 lines each). The interesting part is the layer between them, plus the tests that try to break it. Everything uses Python stdlib and SQLite. `python3 run.py` runs 22 tests and a demo in about 5 seconds.

## The invariant

**Money is never created, destroyed, or stuck.** After every test, and after every one of about 4,800 randomized fuzz steps, `check_invariants()` verifies all of the following from the raw journal:

- **Conservation.** Total of all balances = total ever minted. Minting is the only way money enters.
- **Replay.** Every stored balance equals mints + accepted transfers in − accepted transfers out.
- **No negatives.** No balance is ever below zero. A DB `CHECK` constraint backs this up.
- **Escrow accounting.** The escrow account holds exactly the sum of live escrows.
- **Settlement.** Every finished escrow has exactly one release *or* one refund, never both. No live escrow has either.
- **Liveness ("not stuck").** Once the oracle is reachable and deadlines pass, every escrow reaches a terminal state and the escrow account returns to 0.

## The pieces, and the failure each one prevents

| Piece | What it is | Failure it prevents |
|---|---|---|
| **Ledger** (`ledger.py`) | Double-entry journal with integer cents. Every attempt is a row keyed by an idempotency key. | **Double payment on retry.** A replayed key returns the stored row and moves nothing. Reusing a key with a different amount raises an error instead of paying twice. |
| **Escrow** (`layer.py`) | State machine: OPEN → ASSIGNED → SUBMITTED → RELEASED / REFUNDED. Funds lock in the same transaction that creates the task. | **Paying for undelivered work, or losing a buyer's money when the seller vanishes.** Unsubmitted work times out and is refunded. Payout is a compare-and-swap on state, so exactly one settler wins. |
| **Oracle** (`oracle.py`) | Deterministic checks: hash match, JSON schema, summary constraints. Has an optional judge hook that can only tighten a pass. | **"Who was right?" disputes.** Garbage fails, the buyer is refunded, and the seller's reputation drops. An oracle that is down or crashing returns *no verdict*, so no money moves. |
| **Spend caps** (`layer.py`) | Per-agent sliding hourly limits on amount and task count, enforced inside the payment layer's transaction. | **A haywire agent draining its budget.** The agent can't skip the check because the check isn't in the agent. |
| **Serialization** (`db.py`) | Every write uses `BEGIN IMMEDIATE` on one SQLite file in WAL mode, one connection per thread or process. | **Races.** Double-assignment and double-pay under concurrency, including across separate OS processes. |

## Chaos tests (`tests/test_chaos.py`)

1. **Seller killed mid-task.** A real seller *process* picks up the task and is `SIGKILL`ed. Before the deadline, the funds sit in escrow. After it, the buyer is refunded in full and the seller gets 0 and −1 reputation. A late "zombie" submit is rejected.
2. **Retry 10×.** Covered sequentially, with 10 threads behind a barrier, and near a spend cap. Each case produces exactly one transfer row and ten identical responses. Reusing a key with a different amount raises an error.
3. **Overspend.** 100 posts against a 3-task cap: 3 accepted, 97 rejected. The cap window rolls over after an hour. Without caps, the balance never goes negative, and a direct `balance = -1` write is refused by the DB.
4. **Garbage output.** Empty, binary, overlong, missing-keyword, and copy-of-source outputs all fail. The escrow refunds and reputation goes 0 → −1. An honest control run gets paid.
5. **Oracle down.** `settle` returns `oracle_unavailable` repeatedly. Ten hours past the deadline, the sweep still does not refund submitted work. When the oracle comes back, the escrow releases. A *crashing* oracle is treated as down, not as "fail."
6. **Two buyers, one seller.** Run 25 rounds of concurrent assigns against a capacity-1 seller: one assign wins, the other gets `seller_busy`. Two sellers racing for one task: one wins. Twelve concurrent settles: one release, and reputation is bumped once.

**Testing the tests.** I injected 7 bugs into the layer, including no compare-and-swap on settle, timeouts that refund submitted work, crashing-oracle-as-fail, a missing capacity check, and replays re-charged against caps. The suite catches all 7; `python3 mutation_check.py` reproduces this. The last one survived the first version of the suite and turned out to be a real bug: a retried payment near the cap crashed instead of returning its original result. A test now covers it.

**Design choice worth arguing about.** Timeouts refund only work that was *never submitted*. If work was submitted and the oracle is unreachable, the money waits. Refunding would punish a seller who delivered; paying would bypass verification. Parking the funds is the only option that never settles on a guess, and the liveness check proves the parking ends.

## Mapping to Skyfire's KYAPay

Skyfire's open KYAPay protocol has two signed-JWT primitives. A **KYA (Know Your Agent)** token carries verified information about the agent's owner, which a service uses to create account credentials. A **PAY** token carries an authorized spend amount that a merchant can capture.

| This repo | KYAPay analogue |
|---|---|
| `agents` table and who may post or assign | **KYA token.** *Who* is this agent, and which principal is accountable? Here it's a bare string ID; in production it would be a verified, signed identity. |
| Escrow lock + spend cap | **PAY token.** An authorized, bounded amount that is captured only on delivery. |
| Oracle + reputation | The **post-transaction** half: did the agent actually do the work, and who eats the loss if not? |

**The honest gap.** KYA answers *who* before the transaction. The oracle answers *whether* after it. They are complementary, not the same layer. A KYA token tells a seller the buyer is accountable; it doesn't tell the buyer whether the summary it paid for is garbage. A natural next step is to require a verified KYA identity at `post_task` and `assign`, and to attach the oracle's verdict and reputation to that identity. That way, disputes and reputation follow the *principal* across services, not a throwaway agent ID.

**Not done yet.** No signed tokens: identities are plain strings. The LLM judge is a hook, not a wired model. Single node only, with SQLite's write lock as the sole serialization point.
