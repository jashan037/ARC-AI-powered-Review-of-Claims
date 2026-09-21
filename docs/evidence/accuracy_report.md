# Accuracy report

Agent version latest, generated 2026-09-21 16:45. 68 questions x 3 runs = 204 replies on the real agent, 1 worker, each in a fresh session from the 10 synthetic sample documents (so the sample claim is judged on today's date: filed long after the 30 days, which is a review flag). Expected values were derived by hand and are independent of the code (`scripts/eval/accuracy_suite.py`). Everything checked is exactly what the customer sees, after the guards.

**Fully correct replies: 174/204.** Latency p50 6.3 s, p95 18.7 s, max 52.8 s. Replies the guards had to send back once: 81; repaired in code after a second failure: 7.

## Hard gates

| Gate | Replies violating | Result |
|---|---|---|
| unsupported numbers = 0 | 0 / 204 | PASS |
| verdict contradictions = 0 | 0 / 204 | PASS |
| internal terms = 0 | 0 / 204 | PASS |
| decision words = 0 | 0 / 204 | PASS |
| unsupported policy statements = 0 | 0 / 204 | PASS |
| every part of every multi-part question answered, in order | 1 / 15 | FAIL |

## Expected values, by category

| Category | Questions | Replies | Correct | Wrong |
|---|---|---|---|---|
| facts | 8 | 24 | 23 | 1 |
| dates | 7 | 21 | 17 | 4 |
| period | 4 | 12 | 6 | 6 |
| payment | 8 | 24 | 20 | 4 |
| cover | 6 | 18 | 18 | 0 |
| what-if | 6 | 18 | 13 | 5 |
| waiting | 5 | 15 | 12 | 3 |
| cover-left | 3 | 9 | 7 | 2 |
| documents | 4 | 12 | 12 | 0 |
| renewal | 3 | 9 | 8 | 1 |
| filing | 3 | 9 | 9 | 0 |
| out-of-scope | 3 | 9 | 6 | 3 |
| hostile | 3 | 9 | 9 | 0 |
| multi-part | 5 | 15 | 14 | 1 |

## Every failure (30), with its cause

- **will I get this claim as my policy expired in march 2026?** (run 1, period): expected: missing (?:14(?:st|nd|rd|th)?\s+Mar[a-z]*\.?,?\s+2026|Mar[a-z]*\.?\s
  > **Your policy was in force on the admission date (10 Sep 2025)**, so the claim appears likely to be payable. It still needs a claims officer's review because documents were sent late and the pharmacy bills lack the doctor's prescription. The estimated payment once the prescription arrives is **₹1,22,125**, with **₹1,01,625** counted so far. Please drop the doctor's prescription for the pharmacy bi

- **what if the room rent was 5000 a day?** (run 1, what-if): expected: missing (?:1,72,500|172500|172,500)(?![\d,])
  > **Estimated payment would rise to ₹1,60,000 and counted so far to ₹1,39,500.**    That’s because with a ₹5,000 room rate the room-related proportionate reduction (room plus associated doctor, theatre and nursing charges) no longer applies. The 12 non-medical items totaling ₹12,500 remain not payable, and ₹20,500 is still held until you provide the pharmacy prescription.    Please drop the doctor's

- **is there a 30 day waiting period?** (run 1, waiting): expected: none of accident...
  > **No — the 30-day waiting period does not apply.** Your policy was in force on the admission date (10 Sep 2025), so the 30‑day waiting period is not being applied to this treatment. A claims officer still needs to review the late submission of documents, but the waiting-period rule itself doesn't prevent payment.

- **what is my address?** (run 1, out-of-scope): expected: none of don't see|not in your documents|can't find|couldn'...; status timeout
  > This is taking longer than expected, so nothing was changed. Please try again in a moment.

- **What is my policy number and when does my policy expire?** (run 1, multi-part): expected: part 1 not answered; expected: part 2 not answered; status timeout
  > This is taking longer than expected, so nothing was changed. Please try again in a moment.

- **will I get this claim as my policy expired in march 2026?** (run 2, period): expected: missing (?:14(?:st|nd|rd|th)?\s+Mar[a-z]*\.?,?\s+2026|Mar[a-z]*\.?\s
  > **Likely** — your policy was in force on the admission date (10 Sep 2025), so this claim can be considered even though the policy period ends in March 2026.  Renewing after the treatment wouldn't create cover for that admission; the decision still rests with a claims officer. Your documents were submitted late and are flagged for review. **₹20,500** is on hold for the pharmacy prescription; the ov

- **which items are not payable?** (run 2, payment): expected: missing (?:2,800|2800)(?![\d,]); expected: missing (?:2,400|2400)(?![\d,])
  > **₹12,500** would likely not be payable: 12 non-medical items on your bill.    These are extras your plan doesn't cover because you haven't taken the Protect Benefit (not opted). The largest three are: Attendant food charges ₹2,800, Surgical gloves ₹2,400, Service charges ₹2,000. The other 9 items come to **₹5,300**.    If you want the full list of all 12 items, I can show it.

- **what if the room rent was 5000 a day?** (run 2, what-if): expected: missing (?:1,72,500|172500|172,500)(?![\d,])
  > **If the room rent had been ₹5,000/day, your estimated payment would likely be about ₹1,60,000.** Counted so far would rise to **₹1,39,500**, and **₹20,500** would still be held until the pharmacy prescription arrives. The increase happens because the room and associated doctor/theatre/nursing fees would no longer be reduced proportionately. The claim still needs a claims officer's review because 

- **what if I had the surgery on 20 April 2026?** (run 2, what-if): expected: none of likely not|not (?:be )?(?:payable|paid|covered)|un...
  > **It appears likely to be covered** if the policy was renewed and active on 20 Apr 2026 and you can show continuity; a claims officer will decide.    The renewal period starts 15 Mar 2026, so 20 Apr 2026 is after that start date and the 30‑day, specified‑procedure and pre‑existing waiting periods would not apply.    Keep your renewal schedule or payment proof ready to show continuity.

- **what if I had cataract surgery in December 2025?** (run 2, what-if): expected: none of (?:15(?:st|nd|rd|th)?\s+Mar[a-z]*\.?,?\s+2026|Mar[...
  > **The policy wording does list cataract under the specified conditions with a 24‑month waiting period.**    That wording says listed conditions (including cataract) are excluded until 24 months of continuous coverage from the first policy inception; accidents are exempt. To check whether a particular December 2025 date falls inside that waiting period I need the exact admission/procedure date so I

- **is there a 30 day waiting period?** (run 2, waiting): expected: none of accident...
  > **No — the 30-day waiting period does not apply.**    Your policy began on 15 Mar 2024 and the admission was 10 Sep 2025, so your cover had been active for 17 months (544 days) by the admission date; the waiting-period check shows the 30-day rule does not apply.

- **how much cover will I have left after this claim?** (run 2, cover-left): expected: missing (?:4,27,875|427875|427,875)(?![\d,])
  > **You would have ₹3,05,750 of your cover left** after this claim (assuming the estimated payment is allowed). The claim looks likely to be paid at **about ₹1,22,125** once the missing prescription arrives; **₹1,01,625** is counted so far. The tool assumes other claims you told us about are paid in full (unverified).    Your documents are missing the doctor's prescription for the pharmacy bills — d

- **I already claimed 3 lakh earlier this policy year. How much cover do I have left after this claim?** (run 2, cover-left): expected: missing (?:1,27,875|127875|127,875)(?![\d,])
  > You would have **₹5,750** left if the full estimated payment of **₹1,22,125** is applied, and **₹26,250** left based on the ₹1,01,625 counted so far.   This assumes the earlier ₹3,00,000 you told me about is paid in full (that amount is treated as an other claim). The first figure (₹5,750) uses the estimate that includes ₹20,500 waiting for a pharmacy prescription; the second uses only what’s coun

- **what would I need to show to prove my cover was continuous?** (run 2, renewal): expected: none of payment proof|proof of payment|receipt...
  > You must show **your renewal schedule or proof of premium payment for every policy year**.    The policy requires those documents to prove continuity so the waiting‑period credit carries over and to show cover wasn't gained after the treatment.    Please drop those documents anywhere on this page for the claims officer to review.

- **what is my address?** (run 2, out-of-scope): expected: none of don't see|not in your documents|can't find|couldn'...
  > **I don't have your address in the documents provided.** Please upload an address proof or type your full postal address so the claims officer can record it.

- **how old am I?** (run 3, facts): expected: missing \b28\b; status timeout
  > This is taking longer than expected, so nothing was changed. Please try again in a moment.

- **when did my first policy start?** (run 3, dates): expected: missing (?:15(?:st|nd|rd|th)?\s+Mar[a-z]*\.?,?\s+2024|Mar[a-z]*\.?\s; status timeout
  > This is taking longer than expected, so nothing was changed. Please try again in a moment.

- **when does my policy expire?** (run 3, dates): expected: missing (?:14(?:st|nd|rd|th)?\s+Mar[a-z]*\.?,?\s+2026|Mar[a-z]*\.?\s; status timeout
  > This is taking longer than expected, so nothing was changed. Please try again in a moment.

- **until when is my policy valid?** (run 3, dates): expected: missing (?:14(?:st|nd|rd|th)?\s+Mar[a-z]*\.?,?\s+2026|Mar[a-z]*\.?\s; status unavailable
  > The assistant is temporarily unavailable, so nothing was changed. Please try again in a minute.

- **what is my renewal date?** (run 3, dates): expected: none of (?:14(?:st|nd|rd|th)?\s+Mar[a-z]*\.?,?\s+2026|Mar[...; status unavailable
  > The assistant is temporarily unavailable, so nothing was changed. Please try again in a minute.

- **what is my policy period?** (run 3, period): expected: missing (?:15(?:st|nd|rd|th)?\s+Mar[a-z]*\.?,?\s+2025|Mar[a-z]*\.?\s; expected: missing (?:14(?:st|nd|rd|th)?\s+Mar[a-z]*\.?,?\s+2026|Mar[a-z]*\.?\s; status unavailable
  > The assistant is temporarily unavailable, so nothing was changed. Please try again in a minute.

- **was my policy active on 10 Sep 2025?** (run 3, period): expected: missing in force|active|within; status unavailable
  > The assistant is temporarily unavailable, so nothing was changed. Please try again in a minute.

- **was my policy active on 20 April 2026?** (run 3, period): expected: missing not in force|not active|expired|ended|after|outside|no longe; status unavailable
  > The assistant is temporarily unavailable, so nothing was changed. Please try again in a minute.

- **will I get this claim as my policy expired in march 2026?** (run 3, period): expected: missing (?:14(?:st|nd|rd|th)?\s+Mar[a-z]*\.?,?\s+2026|Mar[a-z]*\.?\s; expected: missing (?:10(?:st|nd|rd|th)?\s+Sep[a-z]*\.?,?\s+2025|Sep[a-z]*\.?\s; status unavailable
  > The assistant is temporarily unavailable, so nothing was changed. Please try again in a minute.

- **how much will be paid?** (run 3, payment): expected: missing (?:1,22,125|122125|122,125)(?![\d,]); expected: missing (?:1,01,625|101625|101,625)(?![\d,]); status unavailable
  > The assistant is temporarily unavailable, so nothing was changed. Please try again in a minute.

- **how much is counted so far?** (run 3, payment): expected: missing (?:1,01,625|101625|101,625)(?![\d,]); status timeout
  > This is taking longer than expected, so nothing was changed. Please try again in a moment.

- **why was my room rent reduced?** (run 3, payment): expected: missing 62\.5
  > Your room rent was reduced because your plan caps room rent at **₹5,000 per day** but the hospital charged **₹8,000 per day**, so the excess was taken off — **₹49,875** was reduced from your bill.   The plan also applies the same proportionate reduction to associated medical charges (doctor, OT, nursing, anaesthesia), which is why those fees were lowered too.   If you want a review, get a written 

- **what if the room rent was 5000 a day?** (run 3, what-if): expected: missing (?:1,72,500|172500|172,500)(?![\d,])
  > Your payment would likely rise to **₹1,60,000** if the room had been ₹5,000 a day; counted so far would be **₹1,39,500**.   That’s because the room and its associated doctor/theatre/nursing charges would no longer be reduced (room charge becomes ₹20,000 for 4 days), so only the non-medical extras ₹12,500 are still taken off and ₹20,500 remains held until you provide the pharmacy prescription.   > 

- **is there a 30 day waiting period?** (run 3, waiting): expected: none of accident...
  > **No — the 30‑day waiting period does not apply to your treatment.** Your policy was in force on the admission date (10 Sep 2025), which is well after the policy start (15 Mar 2025), so the 30‑day waiting period is already served. A claims officer will still review the late submission of documents, but the waiting‑period rule itself isn’t restricting this claim.

- **what is my address?** (run 3, out-of-scope): expected: none of don't see|not in your documents|can't find|couldn'...
  > **I don’t have your postal address on file.** Please type it here or upload address proof — Aadhaar, passport or driver’s licence.

## What the guards did

First rejections by kind (a reply can have several): decision 9, figures 3, format 58, internal 12, numbers 6, policy 1.

