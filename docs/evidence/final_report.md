# Final verification report

Agent version latest, which was 29 when this ran (AGENT_VERSION unset), generated 2026-09-21 23:37. **84 questions x 3 runs = 252 replies** on the real agent, 1 worker, each in a fresh session built from one of the three synthetic document sets, with ARC_TODAY=2026-09-21. Expected values were derived by hand and are independent of the code (`scripts/eval/accuracy_suite.py`, same figures as `demo/samples/expected_outcomes.json`). Everything checked is exactly what the customer sees, after the guards. The expectations are re-evaluated from the stored replies when this report is written, so a checker that was too narrow about WORDING can be widened without re-running the agent; no expected figure, date or verdict was ever widened.

**Fully correct replies: 252/252.** Latency p50 7.0 s, p95 15.7 s, max 22.4 s (targets: p50 <= 15 s, p95 <= 30 s). Model calls per turn: median 3, max 5. Tokens per reply: median in 9264, out 424. Turns that needed a 429/5xx retry inside the turn: 0. Replies that needed a platform retry (a timeout or an unavailable service, asked again up to twice): 1; still failing after the retries: 0. Replies the guards sent back once: 117; repaired in code after a second failure: 40.

## Hard gates

| Gate | Replies violating | Result |
|---|---|---|
| unsupported numbers = 0 | 0 / 252 | PASS |
| verdict contradictions = 0 | 0 / 252 | PASS |
| internal terms = 0 | 0 / 252 | PASS |
| decision words = 0 | 0 / 252 | PASS |
| unsupported policy statements = 0 | 0 / 252 | PASS |
| officer in customer text = 0 | 0 / 252 | PASS |
| repeated notes = 0 | 0 / 252 | PASS |
| amounts offered as a payment = 0 | 0 / 252 | PASS |
| every part of every multi-part question answered, in order | 0 / 18 | PASS |

## By document set

| Set | Questions | Replies | Correct | Wrong |
|---|---|---|---|---|
| on_time | 64 | 192 | 192 | 0 |
| late_filing | 12 | 36 | 36 | 0 |
| expired | 8 | 24 | 24 | 0 |

## By category

| Category | Questions | Replies | Correct | Wrong |
|---|---|---|---|---|
| facts | 8 | 24 | 24 | 0 |
| dates | 7 | 21 | 21 | 0 |
| period | 9 | 27 | 27 | 0 |
| payment | 11 | 33 | 33 | 0 |
| cover | 6 | 18 | 18 | 0 |
| what-if | 6 | 18 | 18 | 0 |
| waiting | 6 | 18 | 18 | 0 |
| cover-left | 4 | 12 | 12 | 0 |
| documents | 5 | 15 | 15 | 0 |
| filing | 5 | 15 | 15 | 0 |
| out-of-scope | 3 | 9 | 9 | 0 |
| hostile | 3 | 9 | 9 | 0 |
| multi-part | 6 | 18 | 18 | 0 |
| repeats | 1 | 3 | 3 | 0 |
| renewal | 4 | 12 | 12 | 0 |

## Latency per run

| Run | Replies | p50 | p95 | max |
|---|---|---|---|---|
| 1 | 84 | 6.7 s | 16.8 s | 22.4 s |
| 2 | 84 | 7.1 s | 14.4 s | 18.3 s |
| 3 | 84 | 7.9 s | 16.2 s | 19.7 s |

## Two customers at the same time

12 turns, two sessions in parallel, 57.0 s wall clock: p50 10.3 s, p95 12.5 s, max 16.7 s, failures 0. (`docs/evidence/concurrent_run.json`)

## Every failure (0), with its cause

None.

## What the guards did

First rejections by kind (a reply can have several): amounts 2, decision 12, figures 22, focus 1, format 105, internal 14, names 7, numbers 7, timing 11, verdict 4.

