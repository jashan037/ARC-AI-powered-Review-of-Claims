"""Tiny PDF builder for tests: text PDFs (one text line per line), blank pages (a 'scan'), and edits of the sample documents."""
import io

from pypdf import PdfReader


def _esc(s: str) -> str:
    return s.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")


def make_pdf(lines, pages: int = 1) -> bytes:
    """A valid PDF whose text layer is `lines` (empty list = a page with no text at all, like a scan)."""
    stream = "BT /F1 10 Tf 12 TL 40 800 Td\n" + "\n".join(f"({_esc(l)}) Tj T*" for l in lines) + "\nET" if lines else ""
    objs = ["<< /Type /Catalog /Pages 2 0 R >>", "<< /Type /Pages /Kids [" + " ".join(f"{3 + i} 0 R" for i in range(pages)) + f"] /Count {pages} >>"]
    for i in range(pages):
        objs.append(f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 595 842] /Contents {3 + pages + i} 0 R /Resources << /Font << /F1 {3 + 2 * pages} 0 R >> >> >>")
    objs += [f"<< /Length {len(stream)} >>\nstream\n{stream}\nendstream"] * pages + ["<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>"]
    out, offsets = io.BytesIO(), []
    out.write(b"%PDF-1.4\n")
    for n, body in enumerate(objs, 1):
        offsets.append(out.tell())
        out.write(f"{n} 0 obj\n{body}\nendobj\n".encode("latin-1", "replace"))
    xref = out.tell()
    out.write(f"xref\n0 {len(objs) + 1}\n0000000000 65535 f \n".encode())
    for off in offsets:
        out.write(f"{off:010d} 00000 n \n".encode())
    out.write(f"trailer\n<< /Size {len(objs) + 1} /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF\n".encode())
    return out.getvalue()


def text_lines(pdf: bytes) -> list[str]:
    return [l for p in PdfReader(io.BytesIO(pdf)).pages for l in (p.extract_text() or "").split("\n")]


def edit(pdf: bytes, replace: dict[str, str] | None = None, drop: list[str] | None = None) -> bytes:
    """The same document with some text changed (for example another patient name) or some lines removed."""
    lines = text_lines(pdf)
    lines = [l for l in lines if l not in (drop or [])]
    for old, new in (replace or {}).items():
        lines = [l.replace(old, new) for l in lines]
    return make_pdf(lines)
