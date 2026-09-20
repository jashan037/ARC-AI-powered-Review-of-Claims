# Prompts for Claude Code

Unzip the project, open a terminal in the folder, run `claude`, then paste **Prompt 1**. Use the later prompts one at a time, in separate sessions or after Prompt 1 is finished.

---

## Prompt 1: get it running on real Azure (paste this first)

```
Read CLAUDE.md completely. It has the full context of this project: a claims-officer assistant on Azure (AI Search + a Foundry agent with function tools), backend only.

Do not change any code yet. First:
1. Create a virtual environment, install requirements.txt (add --pre for azure-ai-projects if needed), copy .env.example to .env if .env does not exist, and run `python -m pytest tests -q`. Tell me the result. 30 tests should pass.
2. Summarise back to me in 10 lines or fewer: what this project does, what is already tested, what has never been run against real Azure, and the order of the roadmap in section 10.

Then guide me through roadmap steps 2 and 3 (Azure wiring) ONE STEP AT A TIME. I am new to Azure, so for anything I must do in a portal give exact click paths and tell me what I should see afterwards, then wait for me to say it worked.

Rules for this session:
- Never ask me to paste an API key into this chat. Tell me which .env line to fill in myself. Do not print, log or commit secrets.
- Ask me before anything destructive or that changes cost (deleting an index or agent, changing tiers, creating new resources). Do not touch the old index rag-1789575754829 or the agent claims-adjudication-agent.
- Scripts that talk to Azure (create_index.py, upload_chunks.py, create_agent.py, and the FoundryAgent in app/agent/runner.py) were written from documentation and never run. If one fails, read the error, check Microsoft Learn for the current API, fix the smallest thing, explain the fix, and re-run the tests.
- Do not loosen the validation in app/tools/registry.py and do not let the model compute numbers. Numbers come from app/tools/claims_engine.py.
- Do not edit expected values in data/sample_claims.json to make anything pass.

Definition of done for this session:
(a) claims-kb-v2 exists with 186 documents and Search explorer returns results;
(b) `RETRIEVER=azure python scripts/eval_retrieval.py --verbose` is at least as good as the local baseline (hit@5 16/16), with every MISS explained;
(c) agent claims-adjudication-agent-v2 exists, and `RETRIEVER=azure AGENT_MODE=foundry python scripts/chat_cli.py --claim TC07` answering "assess this claim" prints the formatted assessment containing ₹1,22,125 and ₹1,01,625;
(d) tests still pass. End with an honest list of what you ran and what you did not.
```

---

## Prompt 2: agent-level evaluation (roadmap 4)

```
Read CLAUDE.md. Build scripts/eval_agent.py that runs through the real Foundry agent (AGENT_MODE=foundry, RETRIEVER=azure): all 12 samples in data/sample_claims.json with the message "Assess this claim", plus about 15 questions in claims-officer language (coverage, waiting period with dates, definitions, documents, what-if, and 3 the policy cannot answer). Check per case: answer_type, recommendation and amounts equal the hand-derived expected values, every citation is real, final_answer was accepted on the first or second try. Include my earlier test: "Is knee replacement covered and what is the waiting period?" (expect: specified list, 24 months, accident exception, pre-existing disease longer period, cited to C.1.b and C.1.a). Print a results table and save it to examples/eval_report.md. Then fix the prompt in app/agent/instructions.py where the agent misbehaves, without weakening validation. Report which cases are still flaky.
```

## Prompt 3: IRDAI non-payable list (roadmap 6)

```
Read CLAUDE.md. I will put the IRDAI standardization guidelines (or master circular) PDF in reference/. Extract its list of non-payable items and items subsumed into room, procedure and treatment charges into data/rules/irdai_non_payable.json (item, group, payable or not, source page). Extend lookup_non_medical_item and the bill analysis so billed items are matched against both HDFC's Annexure B and the IRDAI list, with a clear distinction between "non-payable" and "included in another charge, cannot be billed separately". Update the renderer, add tests with new sample claims, and do not change existing expected numbers.
```

## Prompt 4: 2026 wording support (roadmap 7)

```
Read CLAUDE.md sections 4 and 10. Download or use the newer my:Optima Secure wording (UIN HDFHLIP26058V082526, policies starting 2026-04-02; the URL is in reference/kb_sources_pack/kb_manifest.json). Adapt tools/chunk_policy.py or the generic chunker so it produces clause-level chunks with correct clause IDs, index it next to the old wording, and make retrieval filter by the claim's policy_uin. List the clauses that differ from the older wording and make the engine honour the ones that change money (utilization order, Protect Benefit only if in the schedule, Infinite Benefit). Add tests proving a claim with each UIN only sees its own wording.
```

## Prompt 5: claim intake from PDFs (roadmap 8)

```
Read CLAUDE.md. Add a claim-intake module that takes the PDFs in reference/claims_data_pack/demo_claim/ and produces the claim JSON the engine expects, using Azure AI Content Understanding (or Document Intelligence if that is what my subscription supports; check first and tell me). Show extracted fields for the officer to confirm, score the extraction against reference/claims_data_pack/demo_claim/expected_extraction.json, and add a POST /sessions/{id}/documents endpoint. Do not change the engine's rules.
```
