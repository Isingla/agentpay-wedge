# Real-model run, 2026-10-18

The reported run. Pre-committed rule: report the first run in which all
three real-model lanes were valid (no judge errors), whatever it said.

- Models: gpt-5.6-terra, claude-sonnet-5-5, gemini-3.8-flash
- Command: `python3 run_trust_layer.py --seeds 5 --models gpt,sonnet,gemini`
- Labels: `trust_layer/labels.json`, pre-registered, unchanged.

| Judge | Labeled garbage passed (95% Wilson CI) | Good passed |
|---|---|---|
| gemini | 3 of 6 (0.19-0.81) | 6 of 6 |
| gpt | 1 of 6 (0.03-0.56) | 6 of 6 |
| sonnet | 0 of 6 (0.00-0.39) | 6 of 6 |

Earlier attempts the same night were INVALID (certificate errors, missing
API credits, an unsupported OpenAI parameter, Gemini free-tier rate
limits, one truncated Sonnet reply) and are not counted. One earlier
attempt had a valid GPT lane: 1 of 6 garbage passed, 6 of 6 good, the
same as this run.

Limits: one run, 6 garbage + 6 good hand-written deliveries, one judge
prompt. Per-item verdicts were not recorded, so which garbage items
passed is not known. Model outputs vary between runs.
