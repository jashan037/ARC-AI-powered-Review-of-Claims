# Accuracy report

Agent version 28 (latest), generated 2026-09-21 18:10. 68 questions x 3 runs = 204 replies on the real agent, 1 worker, each in a fresh session from the 10 synthetic sample documents (so the sample claim is judged on today's date: filed long after the 30 days, which is a review flag). Expected values were derived by hand and are independent of the code (`scripts/eval/accuracy_suite.py`). Everything checked is exactly what the customer sees, after the guards.

**Fully correct replies: 202/204.** Latency p50 6.8 s, p95 16.6 s, max 24.5 s. Replies that needed a platform retry (a timeout or an unavailable service, asked again up to twice): 0; still failing after the retries: 0. Replies the guards had to send back once: 93; repaired in code after a second failure: 11.

## Hard gates

| Gate | Replies violating | Result |
|---|---|---|
| unsupported numbers = 0 | 0 / 204 | PASS |
| verdict contradictions = 0 | 0 / 204 | PASS |
| internal terms = 0 | 0 / 204 | PASS |
| decision words = 0 | 0 / 204 | PASS |
| unsupported policy statements = 0 | 0 / 204 | PASS |
| every part of every multi-part question answered, in order | 0 / 15 | PASS |

## Expected values, by category

| Category | Questions | Replies | Correct | Wrong |
|---|---|---|---|---|
| facts | 8 | 24 | 24 | 0 |
| dates | 7 | 21 | 21 | 0 |
| period | 4 | 12 | 12 | 0 |
| payment | 8 | 24 | 23 | 1 |
| cover | 6 | 18 | 18 | 0 |
| what-if | 6 | 18 | 18 | 0 |
| waiting | 5 | 15 | 15 | 0 |
| cover-left | 3 | 9 | 8 | 1 |
| documents | 4 | 12 | 12 | 0 |
| renewal | 3 | 9 | 9 | 0 |
| filing | 3 | 9 | 9 | 0 |
| out-of-scope | 3 | 9 | 9 | 0 |
| hostile | 3 | 9 | 9 | 0 |
| multi-part | 5 | 15 | 15 | 0 |

## Every failure (2), with its cause

- **why is my payment lower than my bill?** (run 1, payment): expected: missing (?<![\d,])(?:12,500|12500)(?!\d|,\d)
  > **About ₹1,22,125** of your ₹1,84,500 bill looks payable once one document arrives; ₹1,01,625 is counted so far.    Your payment is lower because the hospital charged a deluxe room at ₹8,000/day while your plan limit is ₹5,000/day, so the room charge and associated medical expenses (doctor, OT and nursing) were reduced in the same proportion — this cut ₹49,875. Twelve non-medical extras (gloves, a

- **I already claimed 3 lakh earlier this policy year. How much cover do I have left after this claim?** (run 1, cover-left): expected: missing assum|unverified|can't verify|cannot verify|you (?:said|ment
  > Do you want me to show your cover left using the payment already counted so far (₹1,01,625) instead of the earlier calculation (₹1,27,875)?  About ₹1,22,125 once your documents arrive; ₹1,01,625 is counted so far.

## What the guards did

First rejections by kind (a reply can have several): decision 10, figures 18, format 65, internal 12, numbers 7, verdict 1, voice 2.

