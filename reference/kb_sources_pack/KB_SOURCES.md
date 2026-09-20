# Knowledge base sources for the claims copilot

Everything here is public. Priority 1 = index before anything else; 3 = optional.

**Retrieval rule:** read the UIN from the policy schedule footer, filter chunks to that `uin`, then rank. Use IRDAI chunks as a fallback or to explain a conflict.

## Tier 1 - Policy wordings (index first)

| P | Document | UIN / effective | Link | Use |
|---|---|---|---|---|
| 1 | my:Optima Secure policy wording (policies starting 02-Apr-2026 onwards) | HDFHLIP26058V082526 / 2026-04-02 | [open](https://customer-portal-assets.hdfcergo.com/documents/PolicyWordings_myOptimaSecure-76673175551.pdf) | index |
| 1 | my:Optima Secure policy wording (earlier version, the file you uploaded) | HDFHLIP25041V062425 | your upload | index |

- `optima-secure-2026`: Adds Optima Secure + plan and Infinite Benefit (B.2.17); Protect Benefit only if stated in schedule; new utilization order (D.1.19). Chunked with the generic chunker until the clause-aware one is adapted to its layout (annexures now start on pp.52, 56, 60).
- `optima-secure-v062425`: Applies to policies incepted before the 2026 wording. Confirm which wording applies from the UIN in the policy schedule footer.

## Tier 2 - Add-on and rider wordings (only if claims include add-ons)

| P | Document | UIN / effective | Link | Use |
|---|---|---|---|---|
| 3 | Serious Illness Booster (add-on) | - | [open](https://customer-portal-assets.hdfcergo.com/documents/Policywordings_SeriousIllnessBooster-941452609852.pdf) | index |
| 3 | ABCD Chronic Care (add-on) | - | [open](https://customer-portal-assets.hdfcergo.com/assets/v2/docs/default-source/downloads/policy-wordings/health/abcd-chronic-care---policy-wordings/abcd-chronic-care---policy-wordings-588300043193.pdf) | index |
| 3 | Limitless (add-on) | - | [open](https://customer-portal-assets.hdfcergo.com//documents/Policywordings_Limitless-727294559278.pdf) | index |
| 3 | Parenthood (add-on) | - | [open](https://customer-portal-assets.hdfcergo.com/assets/v2/docs/default-source/downloads/policy-wordings/health/parenthood/parenthood-613771733087.pdf) | index |
| 3 | Unlimited Restore (add-on) | - | [open](https://customer-portal-assets.hdfcergo.com//documents/PolicyWordings_UnlimitedRestore(Addon)-220558092606.pdf) | index |
| 3 | Optima Wellbeing (add-on, outpatient benefits) | - | [open](https://customer-portal-assets.hdfcergo.com//documents/PolicyWordings_OptimaWellbeing(Add-on)-202041347369.pdf) | index |
| 3 | my:Health Hospital Cash Benefit (add-on) | - | [open](https://customer-portal-assets.hdfcergo.com/documents/myhealth-suraksha-hospital-cash-add-on-pw-681932316570.pdf) | index |
| 3 | my:health Critical Illness (add-on) | - | [open](https://customer-portal-assets.hdfcergo.com/documents/myhealth-critical-illness---pww-593808504989.pdf) | index |
| 3 | Individual Personal Accident Rider (Optima Secure) | - | [open](https://customer-portal-assets.hdfcergo.com/assets/v2/docs/default-source/downloads/policy-wordings/health/ipa-rider---pww/ipa-rider---pww-819611261262.pdf) | index |

- `addon-hospital-cash`: HDFC lists several copies under different parent products. Check the UIN in the footer against the policy schedule.
- `addon-critical-illness`: Several copies exist. Check the UIN.

## Tier 3 - HDFC ERGO disclosures and summaries

| P | Document | UIN / effective | Link | Use |
|---|---|---|---|---|
| 1 | Notes pertaining to regulatory changes issued by IRDAI, 2024 | - | [open](https://customer-portal-assets.hdfcergo.com/assets/docs/default-source/downloads/policy-wordings/notespertainingtoregulatorychanges2024-892382445782.pdf) | index |
| 2 | Note on AYUSH treatments (effective 1 Apr 2024) | - | [open](https://customer-portal-assets.hdfcergo.com/assets/docs/default-source/public-disclosure/ayushtreatments-914243884484.pdf) | index |
| 2 | Product-wise cashless services disclosure | - | [open](https://customer-portal-assets.hdfcergo.com/assets/docs/default-source/default-document-library/disclosuresonwebsiteresponse-962341769599.pdf) | index |
| 2 | Policy on Protection of Policyholders' Interests (v1.9) | - | [open](https://customer-portal-assets.hdfcergo.com/documents/PPHIPolicy-V1.9(1)-14133981369.pdf) | index |
| 1 | Customer Information Sheet (CIS) - pick the my:Optima Secure entry | - | [landing page](https://www.hdfcergo.com/download/cis) (manual) | index |
| 2 | Prospectus - pick the my:Optima Secure entry | - | [landing page](https://www.hdfcergo.com/download/prospectus) (manual) | index |
| 3 | Brochure (optional) | - | [landing page](https://www.hdfcergo.com/download/brochure) (manual) | optional |

- `hdfc-regulatory-changes-2024`: Explains how the 2024 IRDAI rules change the wordings. Index it, and use it to resolve conflicts between wording and circular.
- `hdfc-pphi-policy`: Claim-service commitments and turnaround standards.
- `hdfc-cis-landing`: Landing page only: I could not confirm the direct PDF link. The CIS is the plain-language summary IRDAI requires with every policy.
- `hdfc-prospectus-landing`: Landing page only. Direct PDF link not confirmed.
- `hdfc-brochure-landing`: Marketing material. Index only with authority=secondary so it never outranks the wording.

## Tier 4 - Blank claim forms (field and checklist reference, not RAG text)

| P | Document | UIN / effective | Link | Use |
|---|---|---|---|---|
| 1 | Current health claim form (Part A / Part B) | - | [landing page](https://www.hdfcergo.com/download/claim-form) (manual) | schema |
| 3 | Health Suraksha claim form (blank, older product) | - | [open](https://v.hdfcbank.com/content/dam/hdfc-aem-microsites/common-pdfs/pdf/nonlife/health_suraksha_claim_form.pdf) | schema |
| 3 | Group Mediclaim claim form Part A/B (blank) | - | [open](https://www.safewaytpa.in/documents/HDFC%20ERGO%20CO%20LTD%20CLAIM%20FORM.pdf) | schema |
| 3 | Claim intimation form (blank) | - | [open](https://www.bandhanbank.com/sites/default/files/2021-03/HDFC%20Ergo%20Health%20Claim%20Intimation%20Form.pdf) | schema |
| 3 | KYC form | - | [landing page](https://selfhelp.hdfcergo.com/SelfHelpDF/DigitalClaimForms/KYC_Form_English_Ctc.aspx) (manual) | schema |

- `hdfc-claim-form-landing`: Use for the field list and document checklist, not as RAG text.
- `form-group-mediclaim-claim`: Shows ICD-10 code fields, procedure fields, and the Part A/Part B split.
- `form-kyc`: Required for claims above Rs. 1 lakh (E.1.7.p).

## Tier 5 - UIN and version mapping (parse into a table)

| P | Document | UIN / effective | Link | Use |
|---|---|---|---|---|
| 1 | Revised Product List (25 Aug 2026) | - | [open](https://customer-portal-assets.hdfcergo.com//documents/RevisedProductList_25_08_2026-94109879598.pdf) | table |
| 2 | Withdrawn Product List | - | [open](https://customer-portal-assets.hdfcergo.com/documents/list-of-withdrawn-product-1-411527700526.pdf) | table |

- `hdfc-revised-product-list`: Parse into uin_map.csv (product, UIN, wording version, effective dates). Drives version-aware retrieval.

## Tier 6 - IRDAI regulation

| P | Document | UIN / effective | Link | Use |
|---|---|---|---|---|
| 1 | IRDAI Master Circular on Health Insurance Business (29 May 2024), ref IRDAI/HLT/CIR/PRO/84/5/2024 | - | find on irdai.gov.in | index |
| 2 | IRDAI Guidelines on Standardization in Health Insurance (2020), 155 pages, includes Annexure A list of non-payable and subsumed items | - | [open](https://compfie.aparajitha.com/wp-content/uploads/2020/07/23072020_FCC_04.pdf) | index |
| 3 | Deloitte tax alert: summary of the 2024 health Master Circular | - | [open](https://www2.deloitte.com/content/dam/Deloitte/in/Documents/tax/in-tax-gbt-insurance-regulator-releases-new-guidelines-noexp.pdf) | optional |

- `irdai-master-circular-2024`: Search the reference number on irdai.gov.in. Repealed 55 earlier circulars. Governs claim timelines, moratorium, cashless, standard definitions and exclusions.
- `irdai-standardization-2020`: Index Annexure A as a table (item, group, payable or not). Parts are superseded by the 2024 Master Circular, so tag chunks superseded_in_part=true.
- `deloitte-master-circular-summary`: Secondary. Use only to cross-check the circular; never cite it as the rule.

## Keep out of the RAG index (use as lookup tools)

- [HDFC ERGO cashless hospital network](https://www.hdfcergo.com/locators/cashless-hospitals-networks): Dynamic, changes constantly. Build a network_provider lookup for the demo instead of embedding it.

## Worth adding, but I could not verify a direct link

- IRDAI (Protection of Policyholders' Interests, Operations and Allied Matters of Insurers) Regulations, 2024
- IRDAI (Insurance Products) Regulations, 2024
- Insurance Ombudsman Rules, 2017 (the wording points to them in D.1.17)
- ICD-10 code list (for diagnosis-to-code checks)
- CGHS or PM-JAY package rate lists (benchmark for 'reasonable and customary charges', Def. 39)
- General Insurance Council list of day-care procedures

## Do not use

- Filled policy schedules or claim forms on Scribd and similar sites: they contain real names, addresses and policy numbers.
- The 2013 IRDA Annexure IV non-admissible list on TPA sites: superseded.
- Blog, comparison-site and news pages as RAG sources: fine for reading, but they will contradict the wording.

## Run it

```bash
pip install requests
python download_kb.py            # downloads every auto item into kb/
python ingest_kb.py              # chunks everything into kb/chunks/all_chunks.jsonl
```

Put your existing wording at `kb/01_policy_wordings/optima-secure-HDFHLIP25041V062425.pdf`. For items marked manual, download them into the folder and file name given in `kb_manifest.json`.
