# Facts fix: what the customer sees

Agent `claims-adjudication-agent-v2` (latest version), model `gpt-5-mini`, generated 2026-09-21 11:39. One chat session, the 8 questions in this order, 1 worker, each asked once. The customer sees the reply as Markdown and, under it, a collapsed **Sources** line when a policy section was used. Latency and model calls are for the whole turn (a model call is a conversation create or a response).

**ARC (first message, built in code, no model):**

> Thanks, I've read your documents. This is the claim for **Rohan Verma** (Optima Lite): Laparoscopic appendectomy for Acute appendicitis at Riverside Multispeciality Hospital (DEMO), 10 Sep 2025 to 14 Sep 2025, with a hospital bill of ₹1,84,500. Still needed: The doctor's prescription for your pharmacy bills. Ask me anything about your claim.

## 1. when does my policy expire

_4.8 s · 2 model calls · tools: assess_claim · status ok_

**Customer:** when does my policy expire

**ARC:**

> Your policy expires on 14 Mar 2026.

## 2. when did my policy start

_4.1 s · 2 model calls · tools: assess_claim · status ok_

**Customer:** when did my policy start

**ARC:**

> Your policy (current policy period) started on 15 Mar 2025.

## 3. what is my policy number

_3.4 s · 2 model calls · tools: assess_claim · status ok_

**Customer:** what is my policy number

**ARC:**

> Your policy number is SYN-2805-0000-0001.

## 4. what is my sum insured

_3.2 s · 2 model calls · tools: assess_claim · status ok_

**Customer:** what is my sum insured

**ARC:**

> Your sum insured available on this policy is ₹5,50,000. 
>
> That is made up of a base sum insured of ₹5,00,000 plus a cumulative bonus of ₹50,000.

## 5. do I have a co-pay or deductible

_4.4 s · 2 model calls · tools: assess_claim · status ok_

**Customer:** do I have a co-pay or deductible

**ARC:**

> You do not have a co-pay and there is no deductible on your policy — both are shown as Nil in your policy details. 
>
> (For completeness: any reductions on your claim come from the room‑rent limit, associated medical fees and non‑medical items, not from a co‑pay or deductible.)

## 6. what is my room rent limit

_3.8 s · 2 model calls · tools: assess_claim · status ok_

**Customer:** what is my room rent limit

**ARC:**

> Your room rent limit under Optima Lite is ₹5,000 per day. This is 1% of your base sum insured (1% of ₹5,00,000 = ₹5,000/day).

## 7. was my policy active on 10 Sep 2025

_3.7 s · 2 model calls · tools: assess_claim · status ok_

**Customer:** was my policy active on 10 Sep 2025

**ARC:**

> Your policy was in force on 10 Sep 2025. It falls within your current policy period, which runs from 15 Mar 2025 to 14 Mar 2026.

## 8. will I get this claim as my policy expired in march 2026

_5.2 s · 2 model calls · tools: assess_claim · status ok_

**Customer:** will I get this claim as my policy expired in march 2026

**ARC:**

> Likely — your claim appears eligible because your policy was in force on the admission date (10 Sep 2025), but a claims officer decides the final outcome.
>
> Your policy period is 15 Mar 2025 to 14 Mar 2026, so the 14 Mar 2026 expiry does not stop this claim (admission was 10 Sep 2025). The assessment shows ₹1,01,625 is confirmed today and ₹20,500 is held until you submit the missing pharmacy prescriptions; once those arrive the estimated payment becomes ₹1,22,125. The final decision and payment timing will be made by a claims officer.

## Summary

Latency: median 4.0 s, mean 4.1 s, max 5.2 s. Model calls per question: mean 2.00 (0 answered in code with no model call, 8 with one response, 0 with more).

Before an upload the chat answers with the fixed message: “Please upload your documents first. Once they have been checked, I can answer your questions about your claim.”
