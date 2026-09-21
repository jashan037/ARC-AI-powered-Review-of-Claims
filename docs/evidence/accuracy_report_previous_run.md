# Accuracy report

Agent version 28 (latest), generated 2026-09-21 17:42. 68 questions x 3 runs = 204 replies on the real agent, 1 worker, each in a fresh session from the 10 synthetic sample documents (so the sample claim is judged on today's date: filed long after the 30 days, which is a review flag). Expected values were derived by hand and are independent of the code (`scripts/eval/accuracy_suite.py`). Everything checked is exactly what the customer sees, after the guards.

**Fully correct replies: 195/204.** Latency p50 6.8 s, p95 15.5 s, max 18.9 s. Replies that needed a platform retry (a timeout or an unavailable service, asked again up to twice): 0; still failing after the retries: 0. Replies the guards had to send back once: 89; repaired in code after a second failure: 11.

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
| period | 4 | 12 | 9 | 3 |
| payment | 8 | 24 | 24 | 0 |
| cover | 6 | 18 | 18 | 0 |
| what-if | 6 | 18 | 17 | 1 |
| waiting | 5 | 15 | 14 | 1 |
| cover-left | 3 | 9 | 9 | 0 |
| documents | 4 | 12 | 12 | 0 |
| renewal | 3 | 9 | 9 | 0 |
| filing | 3 | 9 | 8 | 1 |
| out-of-scope | 3 | 9 | 6 | 3 |
| hostile | 3 | 9 | 9 | 0 |
| multi-part | 5 | 15 | 15 | 0 |

## Every failure (9), with its cause

- **will I get this claim as my policy expired in march 2026?** (run 1, period): expected: missing (?:10(?:st|nd|rd|th)?\s+Sep[a-z]*\.?,?\s+2025|Sep[a-z]*\.?\s
  > **Your treatment was during the policy period, so the claim appears likely to be payable for about ₹1,22,125 once the missing document arrives; ₹1,01,625 is counted so far.** A claims officer still needs to review the late submission (documents sent 372 days after discharge) and the pharmacy bills are waiting for the doctor's prescription, which is why part of the amount is on hold. > Please uploa

- **is there a 30 day waiting period?** (run 1, waiting): expected: missing 30
  > Your policy was not in force on that date (15 Mar 2024): it is before the start of the policy period 15 Mar 2025 to 14 Mar 2026. Accidents are always exempt from waiting periods, too.   If you like, I can also check the other waiting‑period rules listed for your policy or explain how this affects the estimated payment.

- **what is my address?** (run 1, out-of-scope): expected: none of don't see|not in your documents|can't find|couldn'...
  > **Your address isn't in the documents provided.** Please upload an ID or proof of address and I'll check.

- **will I get this claim as my policy expired in march 2026?** (run 2, period): expected: missing (?:10(?:st|nd|rd|th)?\s+Sep[a-z]*\.?,?\s+2025|Sep[a-z]*\.?\s
  > **About ₹1,22,125** looks likely to be payable of your ₹1,84,500 bill once the missing pharmacy prescription arrives; **₹1,01,625** is counted so far. Your policy was in force on the admission date and the policy period ends on **14 Mar 2026**, so the treatment falls inside the policy period. The late submission is flagged for a claims officer review.    Please drop the doctor's prescription for t

- **what if the room was 5000 a day and I had the Protect Benefit?** (run 2, what-if): expected: missing (?<![\d,])(?:1,52,000|152000|152,000)(?!\d|,\d)
  > **About ₹1,72,500** would be the estimated payment if the room had been ₹5,000/day and you had the Protect Benefit.    This is because the room would be within your plan limit so room-related charges wouldn't be reduced, and Protect Benefit would cover the 12 non-medical extras (₹12,500). ₹20,500 is still held until you provide the doctor's prescription for the pharmacy bills, and your late submis

- **is my claim late?** (run 2, filing): expected: missing \b30 days\b
  > **Your documents were sent 372 days after discharge,** which is past the policy's 30‑day time limit (by 14 Oct 2025), so the claim is flagged as late and needs a claims officer's review. The delay isn't an automatic rejection — a late submission can be accepted if it was beyond your control. Please upload the missing doctor's prescription for the pharmacy bills anywhere on this page so the officer

- **what is my address?** (run 2, out-of-scope): expected: none of don't see|not in your documents|can't find|couldn'...
  > **I don't have your address in the documents.** The KYC form was received but the address wasn't extracted. Please type the address to add it.

- **will I get this claim as my policy expired in march 2026?** (run 3, period): expected: missing (?:10(?:st|nd|rd|th)?\s+Sep[a-z]*\.?,?\s+2025|Sep[a-z]*\.?\s
  > Your policy was not in force on that date (15 Mar 2026): it is after the end of the policy period 15 Mar 2025 to 14 Mar 2026. A late submission is flagged for review and ₹20,500 is held until you provide the doctor’s prescription for the pharmacy bills. **₹1,01,625** is counted so far and the estimate would be **₹1,22,125** once that prescription arrives. Please drop the prescription anywhere on t

- **what is my address?** (run 3, out-of-scope): expected: none of don't see|not in your documents|can't find|couldn'...
  > **Your address isn't on the documents we have.** Please type it here or upload the document with your address.

## What the guards did

First rejections by kind (a reply can have several): decision 7, figures 18, format 60, internal 10, numbers 6, verdict 1, voice 1.

