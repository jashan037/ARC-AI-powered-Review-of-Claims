# ARC: 3-4 minute keynote (script + slides)

About 520 spoken words, roughly 3 minutes 40 seconds at a calm keynote pace. Slides: white background, black type,
one cyan accent, one idea per slide, big type (headline 72-96 pt, body at most 28 pt), nothing in a corner.
`[REC]` marks a screen recording to play on that slide (record them with Screen Studio from the live app at 1920x1080).

---

## 1. Opening (0:00-0:25)

**Slide 1.** White. Centre, one line: **"Is my claim going to be paid?"**

> Every year, millions of people in India file a health-insurance claim. And almost every one of them asks the same question:
> is my claim going to be paid? And if not all of it... why?

**Slide 2.** Three words, stacked, huge: **Deductions. Waiting periods. Fine print.**

> The answer is in the policy wording. Sixty pages of it. Room-rent limits, waiting periods, a list of sixty-eight items
> the policy won't pay for. Most people never find out why the number changed until the letter arrives.

## 2. The solution (0:25-0:55)

**Slide 3.** The ARC logo, then **"Know where your claim stands before it's decided."**

> So we built ARC. AI-powered Review of Claims. You upload your claim documents, and ARC reads them, builds your claim,
> flags what's missing, and explains every deduction in plain words.

**Slide 4.** One sentence, the cyan word highlighted: **ARC never decides. It *explains*.**

> One rule shaped everything we built: ARC never decides. Your insurer's team does. ARC tells you what is likely, and why.

## 3. Demo (0:55-2:05)

**Slide 5.** `[REC 1]` Landing page, click "Try with sample documents", the glass loading card, then the chat.

> Here's a real run. These are synthetic documents for an appendix operation: a hospital bill of one lakh, eighty-four
> thousand, five hundred rupees. ARC reads all ten documents in a few seconds.

**Slide 6.** `[REC 2]` The first message, zoom on "One document is still missing".

> The first thing it tells you: the policy was in force, you filed on time, and one document is missing, the prescription
> for the pharmacy bills.

**Slide 7.** `[REC 3]` Click "How much will be paid?", the answer with the table.

> Ask how much will be paid. About one lakh, twenty-two thousand, one hundred and twenty-five rupees. Twenty thousand, five
> hundred is on hold until that prescription arrives. And it shows why: the room was eight thousand a day against a five
> thousand limit, so the room and the related charges are reduced in proportion. And twelve thousand five hundred of
> non-medical extras aren't covered.

**Slide 8.** `[REC 4]` Click "Download report", the PDF's first page.

> Then one click gives a four-page report for the insurer's team, built entirely in code, with every check marked pass,
> review, or missing.

## 4. How it works (2:05-3:10)

**Slide 9.** `docs/architecture.html`, full screen `[REC 5: slow zoom out]`.

> Under the hood, the most important decision is this: the model never does the maths.

**Slide 10.** Two columns. Left, white: **Python: dates and money.** Right, cyan glass: **AI: plain words.**

> A rules engine in Python works out the waiting periods, the room-rent limit, the filing time and every rupee.
> The AI's only job is to explain the result.

**Slide 11.** Three LED-dot numbers from the landing page: **186 · 8 · 68**

> The policy wording is split into one hundred and eighty-six clauses in Azure AI Search, so every answer is grounded in
> the actual text. The agent runs on Microsoft Foundry with gpt-5-mini. And before any reply reaches you, eight checks
> run in code: every figure, every date and every verdict must match the engine. If the model gets something wrong,
> the reply is corrected before you see it.

**Slide 12.** **555 tests. 12 hand-checked claims. 3 real Azure runs.**

> We test it the hard way: over five hundred automated tests, twelve claims whose answers we worked out by hand, and
> accuracy runs on the live agent in Azure.

## 5. Closing (3:10-3:40)

**Slide 13.** Honest slide, small type: **Next: scanned documents, more policies, sign-in, cloud deployment.**

> It's not finished. Next come scanned documents, more policies, and a proper deployment.

**Slide 14.** White. The logo. **ARC. Know where your claim stands.**

> But the idea is simple. Insurance shouldn't be a mystery. With ARC, you know where your claim stands, before it's decided.
> Thank you.

---

## Recording checklist

| Clip | Where | Action | Length |
|---|---|---|---|
| REC 1 | `/` | open fresh, wait for the entrance, click "Try with sample documents" | 12 s |
| REC 2 | `/chat` | hold on the first message, zoom to the missing-document line | 8 s |
| REC 3 | `/chat` | click the "How much will be paid?" chip, wait for the answer, scroll to the table | 15 s |
| REC 4 | `/chat` | "Download report", open the PDF | 8 s |
| REC 5 | `docs/architecture.html` | full screen, slow zoom out (Screen Studio) | 10 s |

Record against the **live** agent (`scripts/run_demo.sh`), so the answer is the real one; if the wording differs from the
script, change the narration, never the figures. Close every other tab and notification first.
