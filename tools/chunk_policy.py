import re, json, subprocess, sys, os

import argparse
HERE = os.path.dirname(os.path.abspath(__file__))
ap = argparse.ArgumentParser(description="Clause-level chunker for HDFC ERGO my:Optima Secure wordings")
ap.add_argument("pdf")
ap.add_argument("--out", default=os.path.join(HERE, "..", "data", "policy_clauses.jsonl"))
ap.add_argument("--uin", default="HDFHLIP25041V062425", help="UIN printed in the PDF footer")
ap.add_argument("--effective-from", default=None, help="ISO date the wording applies from (policies starting on/after)")
ap.add_argument("--doc-id", default=None, help="short unique id, e.g. optima-secure-v062425")
ap.add_argument("--force", action="store_true", help="run on a wording other than the tuned one (page constants may be wrong)")
args = ap.parse_args()
PDF, OUT, UIN = args.pdf, args.out, args.uin
TUNED_UIN = "HDFHLIP25041V062425"
if UIN != TUNED_UIN and not args.force:
    sys.exit(f"chunk_policy.py is tuned to the {TUNED_UIN} layout (page numbers for annexures and tables are fixed). "
             "Use chunk_generic.py for other wordings, or pass --force after checking the output.")
DOC_ID = args.doc_id or ("optima-secure-" + UIN.lower())

raw = subprocess.run(["pdftotext", "-layout", PDF, "-"], capture_output=True, text=True).stdout
pages = raw.split("\f")

NOISE = re.compile(r"^(HDFC ERGO General Insurance Company Limited\.? ?(IRDAI Reg.*)?|Policy Wording|my: Optima Secure\s*|Floor, Leela Business Park.*|HDFHLIP25041V062425|\s*\d{1,2}\s*)$")

def clean_lines(txt):
    out = []
    for ln in txt.split("\n"):
        s = ln.strip()
        if not s:
            out.append("")
            continue
        if NOISE.match(s) or s.startswith("HDFC ERGO General Insurance Company Limited. IRDAI") or s.startswith("Floor, Leela Business Park"):
            continue
        if re.fullmatch(r"HDFHLIP\S+", s):
            continue
        out.append(ln.rstrip())
    return out

# page -> list of (page_no, line)
stream = []
for pno, ptxt in enumerate(pages, start=1):
    if pno == 1:          # table of contents
        continue
    for ln in clean_lines(ptxt):
        stream.append((pno, ln))

chunks = []
cur = None

def start(chunk_id, section, section_title, clause, title, page, code=None, ctype="clause"):
    global cur
    flush()
    cur = dict(chunk_id=chunk_id, section=section, section_title=section_title, clause=clause, title=title,
               page_start=page, page_end=page, code=code, chunk_type=ctype, lines=[])

def flush():
    global cur
    if cur and "".join(cur["lines"]).strip():
        text = re.sub(r"[ \t]+", " ", "\n".join(cur["lines"]))
        text = re.sub(r"\n{3,}", "\n\n", text).strip()
        cur["text"] = text
        del cur["lines"]
        chunks.append(cur)
    cur = None

def add(pno, ln):
    if cur is not None:
        cur["lines"].append(ln.strip())
        cur["page_end"] = pno

SEC_TITLE = {"P": "Preamble and Operative Clause", "A": "Definitions", "B": "Benefits",
             "C": "Waiting Periods and Exclusions", "D": "General Terms and Clauses", "E": "Other Terms and Clauses",
             "ANX": "Annexures"}
section = "P"
subsec = None            # A1.1 / A1.2 ; C1 / C2 / C3
expected_letter = "a"
started_preamble = False
table_skip = False

RE_SECTION = re.compile(r"^\s*SECTION ([A-E])\.")
RE_DEF = re.compile(r"^\s*Def\.\s*(\d+)\.\s+(.*)")
RE_LETTER = re.compile(r"^\s*([a-q])\.\s+(.*)")
RE_CODE = re.compile(r"Code\s*[–-]?\s*(Excl\d{2})")
RE_ANNEX = re.compile(r"^\s*(Annexure [ABC])\b\s*(.*)")


for pno, ln in stream:
    s = ln.strip()
    if not s:
        if cur: cur["lines"].append("")
        continue

    if pno in (46, 47, 48):             # Annexure A: ombudsman addresses (not needed for claim logic)
        if RE_ANNEX.match(s) and "Annexure A" in s:
            start("ANX-A", "ANX", "Annexures", "Annexure A", "Insurance Ombudsman offices", pno)
        add(pno, ln)
        continue
    if pno == 49 and RE_ANNEX.match(s) and "Annexure B" in s:
        flush(); cur = None; section = "ANX"
    if pno >= 49:                        # tables handled elsewhere
        continue

    m = RE_SECTION.match(s)
    if m:
        section = m.group(1); subsec = None; expected_letter = "a"
        flush()
        continue

    if not started_preamble and s.startswith("Preamble"):
        started_preamble = True
        start("P-PREAMBLE", "P", SEC_TITLE["P"], "Preamble", "Preamble", pno)
        continue
    if section == "P":
        if s.startswith("Operating Clause"):
            start("P-OPERATING", "P", SEC_TITLE["P"], "Operating Clause", "Operating Clause", pno)
            continue
        add(pno, ln); continue

    # ---------------- Section A: definitions
    if section == "A":
        if re.match(r"^\s*1\.1\.\s+Standard Definitions", s):
            subsec = "A1.1"; flush(); continue
        if re.match(r"^\s*1\.2\.\s+Specific Definitions", s):
            subsec = "A1.2"; flush(); continue
        m = RE_DEF.match(s)
        if m and subsec:
            n = int(m.group(1)); rest = m.group(2)
            t = re.split(r"\s+(?:means|is|refers|shall)\b", rest)[0].strip()[:80]
            start(f"{subsec}-Def{n}", "A", SEC_TITLE["A"], f"{subsec} Def. {n}", t, pno)
            cur["lines"].append(rest); continue
        add(pno, ln); continue

    # ---------------- Section B: benefits
    if section == "B":
        mt = re.match(r"^\s*([123])\.\s+(Base Coverage|Optional Covers|Renewal Benefit.*)$", s)
        if mt:
            start(f"B{mt.group(1)}", "B", SEC_TITLE["B"], f"B.{mt.group(1)}", mt.group(2).strip(), pno); continue
        md = re.match(r"^\s*(\d{1,2}\.\d{1,2}(?:\.\d{1,2})?)\.?\s+([A-Z].+)$", s)
        if md and len(md.group(2)) <= 95:
            num, title = md.group(1), md.group(2).strip()
            start(f"B{num}", "B", SEC_TITLE["B"], f"B.{num}", title.split(" [")[0], pno); continue
        if cur is not None and cur["chunk_id"] == "B1.1.1" and re.match(r"^\s*iii\.\s+Proportionate deduction on Room Rent", s):
            start("B1.1.1-Note-iii", "B", SEC_TITLE["B"], "B.1.1.1 Note iii", "Proportionate deduction on room rent", pno)
            cur["lines"].append(s); continue
        add(pno, ln); continue

    # ---------------- Section C: waiting periods and exclusions
    if section == "C":
        if re.match(r"^\s*1\.\s+Waiting Periods", s):
            subsec = "C1"; expected_letter = "a"; flush(); continue
        if re.match(r"^\s*2\.\s+Standard Exclusions", s):
            subsec = "C2"; expected_letter = "a"; flush(); continue
        if re.match(r"^\s*3\.\s+Specific Exclusions", s):
            subsec = "C3"; expected_letter = "a"; flush()
            start("C3-INTRO", "C", SEC_TITLE["C"], "C.3", "Specific exclusions (introduction)", pno); continue
        m = RE_LETTER.match(s)
        if m and subsec and m.group(1) == expected_letter and (len(s) - len(s.lstrip())) < 14:
            letter, rest = m.group(1), m.group(2)
            code = None
            mc = RE_CODE.search(rest)
            if mc: code = mc.group(1)
            title = re.split(r":|\s*Code\b", rest)[0].strip()[:90] or rest[:90]
            start(f"{subsec}-{letter}", "C", SEC_TITLE["C"], f"{subsec[0]}.{subsec[1]}.{letter}", title, pno, code=code)
            cur["lines"].append(rest)
            expected_letter = chr(ord(letter) + 1)
            continue
        if pno != 29:
            table_skip = False
        if table_skip:
            continue
        if "List of specific diseases/procedures is provided below" in s:
            add(pno, ln); table_skip = True; continue
        if cur is not None and not cur.get("code"):
            mc = RE_CODE.search(s)
            if mc: cur["code"] = mc.group(1)
        add(pno, ln); continue

    # ---------------- Section D / E
    if section in ("D", "E"):
        m = re.match(r"^\s*(\d{1,2}\.\d{1,2})\.\s*(?=[A-Z])(.+)", s)
        if m and len(m.group(2)) <= 90:
            num, title = m.group(1), m.group(2).strip().rstrip(":")
            start(f"{section}{num}", section, SEC_TITLE[section], f"{section}.{num}", title, pno); continue
        m = re.match(r"^\s*(\d)\.\s+(Standard General Terms & Clauses|Claims Procedure|Contact Us)", s)
        if m:
            start(f"{section}{m.group(1)}", section, SEC_TITLE[section], f"{section}.{m.group(1)}", m.group(2), pno); continue
        if s.startswith("Specific General Terms and Clauses"):
            flush(); continue
        add(pno, ln); continue

flush()

# ---------------------------------------------------------------- curated table chunks (pp.29, 49-51)
rules = os.path.join(HERE, "..", "rules")
if not os.path.isdir(rules):
    rules = os.path.join(HERE, "..", "data", "rules")
wp = json.load(open(f"{rules}/waiting_periods.json"))
nm = json.load(open(f"{rules}/non_medical_items.json"))
pc = json.load(open(f"{rules}/plan_config.json"))

def tchunk(cid, section, clause, title, page_start, page_end, text):
    chunks.append(dict(chunk_id=cid, section=section, section_title=SEC_TITLE[section], clause=clause, title=title,
                       page_start=page_start, page_end=page_end, code=None, chunk_type="table", text=text))

tchunk("C1-b-list", "C", "C.1.b.vi", "Specified illnesses and surgical procedures (24-month waiting period, Excl02)", 29, 29,
       "Illnesses:\n" + "\n".join("- " + i for i in wp["specified_illnesses"]) +
       "\n\nSurgical procedures:\n" + "\n".join("- " + i for i in wp["specified_surgical_procedures"]))
tchunk("ANX-B", "ANX", "Annexure B", "Non-medical expenses (items for which coverage is not available)", 49, 50,
       "\n".join(f"{i['sr_no']}. {i['item']}" for i in nm["items"]))
for pname, p in pc["plans"].items():
    room = p["room_rent"]; icu = p["icu"]
    def fmt(x):
        if x["type"] == "at_actuals": return "At actuals"
        if x["type"] == "single_private_room": return "Up to single private room"
        return f"Up to {x['percent']:g}% of base sum insured per day"
    text = (f"Plan: {pname}. Base sum insured options: {', '.join(f'{v:g}' for v in p['base_si_lakh'])} lakh. Geography: {p['geography']}. "
            f"Room rent: {fmt(room)}. ICU: {fmt(icu)}. Pre-hospitalization: {p['pre_hosp_days']} days. "
            f"Post-hospitalization: {p['post_hosp_days']} days. Cumulative bonus: {p['cumulative_bonus']}. "
            f"Protect benefit (non-medical expenses): {p['protect_benefit']}. Plus benefit: {p['plus_benefit']}. "
            f"Secure benefit: {p['secure_benefit']}. Automatic restore: {p['automatic_restore']}. "
            f"Aggregate deductible options: {', '.join(p['aggregate_deductible_options'])}. E-opinion: {p['e_opinion']}.")
    tchunk("ANX-C-" + pname.replace("Optima ", "").replace(" ", "-"), "ANX", "Annexure C", f"Plan chart: {pname}", 50, 51, text)

ex = json.load(open(f"{rules}/exclusions.json"))
title_by_clause = {e["clause"]: e["title"] for e in ex["standard_exclusions"] + ex["specific_exclusions"]}
for c in chunks:
    if c["chunk_id"].startswith(("C2-", "C3-")) and c["clause"] in title_by_clause:
        c["title"] = title_by_clause[c["clause"]]
    if c["chunk_id"].startswith(("C2-", "C3-")):
        c["clause"] = c["clause"].replace("C.2.", "C.2.").replace("C.3.", "C.3.")

notes_lines = []
for pno in (52, 53):
    for ln in clean_lines(pages[pno - 1]):
        notes_lines.append(ln.strip())
tchunk("ANX-C-notes", "ANX", "Annexure C notes", "Plan chart notes and add-on covers", 51, 53,
       re.sub(r"\n{3,}", "\n\n", "\n".join(notes_lines)).strip())

# ---------------------------------------------------------------- write
for c in chunks:
    c["source"] = "HDFC ERGO my:Optima Secure policy wording"
    c["uin"] = UIN
    c["doc_id"] = DOC_ID
    c["effective_from"] = args.effective_from
    c["chunk_key"] = f"{DOC_ID}:{c['chunk_id']}"
    c["char_count"] = len(c["text"])
    c["citation"] = f"Policy {c['clause']}, p.{c['page_start']}"

with open(OUT, "w", encoding="utf-8") as f:
    for c in chunks:
        f.write(json.dumps(c, ensure_ascii=False) + "\n")

print(f"{len(chunks)} chunks -> {OUT}")
