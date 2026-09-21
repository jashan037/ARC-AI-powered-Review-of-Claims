# ARC: AI-powered Review of Claims — submission

AI-103 student project. Everything in this repository is synthetic: fictional people, fictional hospitals, fictional amounts.
The policy wording is the public HDFC ERGO my:Optima Secure document (UIN HDFHLIP25041V062425).

---

## 1. The problem

A health-insurance claim is decided by a person reading documents. The customer, meanwhile, knows almost nothing:

- The hospital hands over a bill of twenty-three lines and the customer cannot tell which of them the policy pays for.
- Deductions have names like "proportionate deduction on room rent" and "Annexure B non-medical items", which mean nothing
  to the person paying the bill.
- One missing sheet of paper — a prescription — can hold up a five-figure amount, and nobody says so plainly.
- The customer sends the documents and then waits, without knowing whether anything is wrong with them.

On the other side, the claims team gets a folder of PDFs and starts from zero on every claim: read the schedule, find the
dates, add up the bill, check the waiting periods, check what is missing.

Both sides need the same thing first: **an honest reading of the documents.** Not a decision - a reading.

## 2. What ARC does

1. The customer drops their claim PDFs on a page. `app/intake.py` reads them with pypdf and rules (no model), recognises
   each document, extracts the fields, and cross-checks them: same person, same policy number, same admission and discharge
   dates, and a bill whose lines add up to its own total. Anything that does not add up is reported back in plain words.
2. `app/tools/claims_engine.py` then does the work a person would do, in Python: was the policy in force on the admission
   date; is the patient the insured person; are the three waiting periods served; were the documents sent inside the 30 days
   (E.1.6, and a late filing is a **review flag**, never a rejection - E.1.7 Note iv); which bill lines are reduced by the
   room-rent proportion (B.1.1.1 Note iii); which items are Annexure B extras; what is on hold for a missing prescription;
   and what the estimate therefore is.
3. The customer asks questions in plain language. A Foundry agent (gpt-5-mini) explains the engine's results and looks up
   the policy wording through Azure AI Search. It never computes anything.
4. The insurer's team gets `GET /sessions/{id}/report.pdf`: a four-page report built entirely in code from the same facts,
   with the readiness checks and their evidence, the money worked out line by line, the document register, where every fact
   came from (file and page) and the provisions applied. **No model takes part in producing it.**

## 3. Where ARC stands relative to the decision

```
the customer's documents            ARC                                  the insurer's team
        |                            |                                            |
        |---- reads and checks ----->|                                            |
        |                            |-- explains, in plain words, to the customer |
        |                            |-- hands over a report, with its evidence -->|
        |                            |                                            |-- DECIDES
```

ARC produces **an estimate and a list of what needs a person's judgement.** It has no authority and claims none:

- The wording is always "likely", "appears", "counted so far", "would likely not be payable", "flagged for review". The code
  rejects "approved", "rejected", "settled" and "confirmed", and a reply that opens with a bare "Yes" or "No" to "will I get
  this claim" is rewritten.
- Customer-facing text says **"your insurer's team"**, never a role inside the insurer. A guard rewrites it if the model slips.
- A late filing, a document conflict, an ambiguous item and a policy that was not in force are all kept in a section of the
  report called "Needs a person's judgement", separate from the verified facts.
- Next steps in the report are verbs a reviewer acts on - request, refer, verify - and the last one is always "Decide the
  claim. ARC neither approves nor rejects."

## 4. Architecture

```
                      +-------------------------------------------------------------+
  PDFs -------------->|  app/intake.py       pypdf + rules, no model                |
                      |                      per-field source file and page kept    |
                      +----------------------------+--------------------------------+
                                                   v
                      +-------------------------------------------------------------+
                      |  app/tools/claims_engine.py    every date and rupee, in code |
                      |  + totals.py, facts.py         derived figures, claim facts  |
                      +------+---------------------------------+--------------------+
                             |                                 |
      question --------------v-----------+                     v
  +--------------------------------------+     +-------------------------------------+
  | app/agent/runner.py                  |     | app/report.py                       |
  | assess_claim precomputed in code     |     | the claims team's PDF, code only    |
  | Foundry agent (gpt-5-mini) + 7 tools |     | reportlab + a vendored font         |
  | Azure AI Search over 186 clauses     |     +-------------------------------------+
  +---------------+----------------------+
                  v
  +-------------------------------------------------------------------------------+
  | guards (all in code, one rewrite then repair):                                 |
  |  numbers      every amount, date, percentage and count came from this turn      |
  |  amounts      an amount offered as "what we will pay" is a payment figure       |
  |  verdicts     no sentence contradicts a tool result (in force, waiting, filing, |
  |               non-medical, documents, outlook)                                  |
  |  policy       a policy statement is supported by a passage retrieved this turn  |
  |  focus        the answer leads with the topic that was asked about              |
  |  timing       nobody is told to wait for a treatment that already happened      |
  |  decision     no approval, rejection, "confirmed", or bare yes/no               |
  |  voice        "you" and "your insurer's team", never "the insured" or "officer" |
  |  names        plans, hospitals and documents are the ones in the documents      |
  |  format       length, tables that add up, no headings, no repeated table        |
  +-------------------------------------------------------------------------------+
                  v
            the customer's page: / , /upload , /chat  (one file, three real paths, strict CSP)
```

Design decisions worth naming:

- **The model never does arithmetic.** It is not asked to be careful with numbers; it is not given the chance. Tools return
  final figures and the number guard rejects anything else.
- **The engine runs before the first model call**, so most questions need one model call (two service calls) and the answer
  cannot depend on the model deciding to check.
- **Guards repair in code, not by asking again.** One rewrite, then the sentence is dropped or replaced by a sentence built
  from the tool result. A model that keeps lying cannot outlast the loop.
- **The report has no model in it**, so it is reproducible: the same documents give the same bytes, apart from the timestamp.
- **Three document sets, not one.** The same claim on time, filed 372 days late, and after the policy ended - so the demo
  shows the three outcomes that matter instead of one happy path.

## 5. Evidence index

| What | Where |
|---|---|
| The final pass: what was built, what was verified, what was not | `docs/FINAL_REPORT.md` |
| Accuracy on the real agent: questions across all three sets, three runs, hard gates, latency | `docs/evidence/final_report.md` |
| Exactly what the customer saw, reply by reply | `docs/evidence/final_transcripts.md` |
| The three claims-team reports | `docs/evidence/sample_report_on_time.pdf`, `sample_report_late.pdf`, `sample_report_expired.pdf` |
| The outcome each document set must produce, derived by hand | `demo/samples/expected_outcomes.json` |
| Screenshots of all three views at 1280x800 and 390x844, plus each report's first page | `docs/screenshots/final/` |
| The offline test suite | `python -m pytest tests -q` |
| Adversarial tests (a model that lies about figures, verdicts, policy, dates, names) | `tests/test_adversarial.py` |
| Security: what the code enforces, the key-rotation checklist, the roles for keyless mode | `docs/SECURITY.md`, `tests/test_security.py` |
| A single-instance deployment path (written out, not executed) | `docs/DEPLOY.md` |
| The audit that started this work, and the repository cleanup | `docs/SYSTEM_REPORT.md`, `docs/CLEANUP_REPORT.md` |
| Earlier evidence kept for comparison | `docs/evidence/accuracy_report.md` (agent v28), `quality_report*.md`, `format_before_after.md` |

## 6. Honest limitations

1. **One policy version.** Only HDFHLIP25041V062425 is chunked and indexed. The 2026 wording is not.
2. **HDFC's 68-item Annexure B only.** IRDAI's longer non-payable list is not modelled, so an item ARC calls payable may still
   be disallowed by the insurer.
3. **Text PDFs in the sample layout.** Intake is a rule-based reader, not OCR and not document understanding. A scan, a photo
   or an unfamiliar layout is reported back to the customer, never guessed at.
4. **In-memory sessions, one instance.** A restart ends every visit; a second worker would lose half of them.
5. **No authentication.** The demo is meant to run on a laptop. Anything public needs a login in front of it first.
6. **Not modelled:** sub-limits, restore and cumulative-bonus mechanics beyond the amount printed on the schedule, cashless
   pre-authorisation, network-hospital lookup, other claims in the policy year (an amount the customer states is taken as
   stated and labelled unverified), and the Plus Benefit's amount (the schedule prints only whether it was opted).
7. **Estimates are not decisions.** Every figure ARC prints is an estimate from the documents in front of it. The insurer's
   team decides, and may see documents or history ARC never had.
8. **The model still writes the prose.** The guards check the figures, the verdicts, the topic, the wording and the format -
   they cannot prove a sentence is well-judged. `docs/evidence/final_report.md` lists every reply that failed a check.
