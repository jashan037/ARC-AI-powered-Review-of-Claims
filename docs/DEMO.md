# Demo script: ARC in three minutes

**One sentence:** a customer uploads their claim documents, ARC reads them and explains in plain words what is likely to be
paid and why, and the insurer's team gets a ready-made report - while every number, date and rule result comes from code, not
from the model.

Everything below is synthetic. Wording varies a little between runs; **the figures, dates and verdicts do not.**

## Before you start

```bash
az login                                                   # if your session expired
source .venv/bin/activate
bash scripts/run_demo.sh                                    # refuses to start unless .env selects the real agent
```

Open **http://127.0.0.1:8765/**. The demo assumes today is around 21 Sep 2026, which is what the three sets were built for.
If the machine's date is different, the filing-time lines change (that is the point of the sets, not a bug); you can pin it
with `ARC_TODAY=2026-09-21` in the shell that starts the server.

## Minute 1: the on-time claim (set A)

Press **Try with sample documents** on the landing page. ARC reads ten PDFs and moves to the chat.

| # | You type | What the audience should see |
|---|---|---|
| 1 | *(nothing - the first message)* | Rohan Verma, laparoscopic appendectomy for acute appendicitis, 10 Sep 2026 to 14 Sep 2026, bill **₹1,84,500**, policy 15 Mar 2026 to 14 Mar 2027. Then: the policy **was in force** on the admission date; the documents are **7 days** after discharge, inside the 30 days (by 14 Oct 2026); **one document is still missing (9 of 10 received)**, the doctor's prescription; and a report you can download. |
| 2 | `how much will be paid?` | **About ₹1,22,125** of the ₹1,84,500 bill looks payable, **₹1,01,625** counted so far, **₹20,500** waiting for the prescription. Never "approved". |
| 3 | `why is my payment lower than my bill?` | The room was ₹8,000 a day against the plan's ₹5,000, so the room and the doctor, theatre and nursing charges are reduced in the same proportion: **−₹49,875**. The extras the plan doesn't cover: **−₹12,500**. A short table that adds up to ₹1,22,125. |
| 4 | `which items are not payable?` | Leads with **₹12,500** across **12 items**, then the three largest (attendant food ₹2,800, surgical gloves ₹2,400, service charges ₹2,000) and "the other 9 come to ₹5,300". |
| 5 | `what if the room rent was 5000 a day?` | Opens with the change: **from ₹1,22,125 to ₹1,60,000**, bill ₹1,72,500, ₹1,39,500 counted so far, and half a sentence of assumption ("only the room charge changes"). |
| 6 | `how much cover will I have left after this claim?` | **₹4,27,875** of the ₹5,50,000 cover. Then try `I already claimed 3 lakh earlier this policy year` - **₹1,27,875**, labelled as assumed paid in full and unverified. |

Then press **Download report** (top right). Page 1 is the whole story for a reviewer: the readiness checks with the dates
behind each, the money summary, what needs a person's judgement, and suggested next steps. Pages 2 to 4 have every bill line
with its reason and clause, the document register, where each fact came from (file and page) and the provisions applied.
**No model wrote any of it** and nothing from the chat is in it.

## Minute 2: the late filing (set B)

Open **http://127.0.0.1:8765/?sample=late** and press **Try with sample documents**.

| # | You type | What the audience should see |
|---|---|---|
| 7 | *(the first message)* | Same claim, 2025 dates, and: the documents are **372 days** after discharge, the policy asks for 30 (by 14 Oct 2025). **Flagged for your insurer's team to review, not rejected** - a delay can be accepted when it was beyond your control. |
| 8 | `will a late claim be rejected?` | No: not automatically. It is considered on merit. ARC does not decide either way. |
| 9 | `what if I had cataract surgery in December 2025?` | The 24-month wait from 15 Mar 2024 ends **15 Mar 2026**, so a December 2025 treatment falls before it and would likely not be payable then. It must **not** tell the customer to "wait until March" for a date that has already passed. |
| 10 | `will I get this claim as my policy expired in march 2026?` | It corrects the premise from the documents first: the policy period ends **14 Mar 2026** and the admission was 10 Sep 2025, inside it. Never a bare yes or no. |

## Minute 3: the policy that had ended (set C)

Open **http://127.0.0.1:8765/?sample=expired** and press **Try with sample documents**.

| # | You type | What the audience should see |
|---|---|---|
| 11 | *(the first message)* | Leads with it: the policy was **not in force** on the admission date (20 Apr 2026) - it is after the end of the period 15 Mar 2025 to 14 Mar 2026 - and that matters more than anything else unless a renewal was in force. |
| 12 | `how much will be paid?` | Leads with the same thing, not with an amount. Nothing appears payable, and it says what could change that. |
| 13 | `I renewed my policy, does that cover this hospital stay?` | A renewal only covers treatment on or after the day it starts; to show continuity, the customer provides the renewal schedule or proof of premium payment. |

Download the report here too: the readiness check says **Not met** in words (no colour needed), and the money summary ends
with "Nothing appears payable".

## What to say at the end (20 seconds)

Python does every date and every rupee. The model only explains, and eight guards check what it wrote against the tool
results of that turn: numbers, verdicts, policy statements, the topic asked about, dates that have already passed, decision
words, the voice, and the format. If a guard still finds a problem after one rewrite, the code repairs the text - it never
asks the model again. And the report the claims team reads has no model in it at all.

## If something goes wrong on stage

- **"This is taking longer than expected"** or **"temporarily unavailable"**: the 60-second turn deadline or a 429 from the
  shared model quota. Nothing was changed; ask again.
- **A sentence is worded differently from this script.** Expected. Check the figures, the dates and the verdict.
- **No internet or an Azure problem.** Show `docs/evidence/final_transcripts.md` (exactly what the customer saw on the last
  verified run), the three sample reports in `docs/evidence/`, and the screenshots in `docs/screenshots/final/`.
- **You need the numbers without the app**: `demo/samples/expected_outcomes.json` has every figure, derived by hand.

## Known rough edges (say them before someone asks)

- Only wording HDFHLIP25041V062425 is indexed; non-medical items come from HDFC's 68-item Annexure B, not IRDAI's longer list.
- Intake reads text PDFs in the sample layout. A scan or a photo is refused politely, never guessed at.
- Sessions live in memory: restarting the server ends the visit.
- There is no login. The demo runs on your laptop for that reason.
- An estimate is not a decision, and ARC says so in every payment answer.
