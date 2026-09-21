ARC demo documents (all synthetic)

Three sets of the same 10 PDFs for one demo customer, Rohan Verma, policy SYN-2805-0000-0001.
Only the dates and the age differ; the names, amounts, bill lines and layout are identical.
Regenerate them with:  python demo/make_sample_sets.py

  on_time/      policy 15/03/2026 to 14/03/2027, admitted 10/09/2026 - in force, filed on time
  late_filing/  policy 15/03/2025 to 14/03/2026, admitted 10/09/2025 - in force, filed long after the 30 days (a review flag)
  expired/      policy 15/03/2025 to 14/03/2026, admitted 20/04/2026 - the policy had ended (likely not covered)

In the app:  "Try with sample documents" loads on_time;  /?sample=late  and  /?sample=expired  load the other two.
Every set leaves the doctor's prescription out on purpose, so the demo shows a real gap.

The outcome each set must produce, derived by hand from the documents and the policy wording, is in
expected_outcomes.json. expected_extraction.json is the field-level ground truth for the late_filing set.
