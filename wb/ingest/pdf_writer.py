"""Minimal PDF writer (tests/fixtures only). Uncompressed, Type1 base fonts."""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


@dataclass
class TextItem:
    x: float
    y: float
    size: float
    text: str
    bold: bool = False


def _esc(text: str) -> bytes:
    raw = text.encode("cp1252", "replace")
    out = bytearray()
    for b in raw:
        if b in (0x28, 0x29, 0x5C):
            out.append(0x5C)
        out.append(b)
    return bytes(out)


class PDFBuilder:
    def __init__(self):
        self.objects: list[bytes] = []

    def add(self, body: bytes) -> int:
        self.objects.append(body)
        return len(self.objects)

    def reserve(self) -> int:
        self.objects.append(b"")
        return len(self.objects)

    def set(self, num: int, body: bytes) -> None:
        self.objects[num - 1] = body

    def build(self, root: int, info: int | None = None) -> bytes:
        out = bytearray(b"%PDF-1.4\n%\xe2\xe3\xcf\xd3\n")
        offsets = [0]
        for i, body in enumerate(self.objects, start=1):
            offsets.append(len(out))
            out += f"{i} 0 obj\n".encode() + body + b"\nendobj\n"
        xref = len(out)
        n = len(self.objects) + 1
        out += f"xref\n0 {n}\n".encode()
        out += b"0000000000 65535 f \n"
        for off in offsets[1:]:
            out += f"{off:010d} 00000 n \n".encode()
        out += f"trailer\n<< /Size {n} /Root {root} 0 R".encode()
        if info:
            out += f" /Info {info} 0 R".encode()
        out += b" >>\n"
        out += f"startxref\n{xref}\n%%EOF\n".encode()
        return bytes(out)


def write_pdf(path: str | Path, pages: list[list[TextItem]], title: str = "",
              author: str = "", page_size: tuple[float, float] = (595, 842)) -> None:
    b = PDFBuilder()
    catalog = b.reserve()
    pages_obj = b.reserve()
    font_regular = b.add(b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica /Encoding /WinAnsiEncoding >>")
    font_bold = b.add(b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica-Bold /Encoding /WinAnsiEncoding >>")
    info = b.add(("<< /Title (" + title + ") /Author (" + author + ") /Producer (WB) >>").encode("latin-1", "replace"))
    page_nums = []
    w, h = page_size
    for items in pages:
        ops = ["BT"]
        for it in items:
            fname = "/F2" if it.bold else "/F1"
            ops.append(f"{fname} {it.size:.2f} Tf")
            ops.append(f"1 0 0 1 {it.x:.2f} {it.y:.2f} Tm")
            ops.append("(" + _esc(it.text).decode("latin-1") + ") Tj")
        ops.append("ET")
        stream = "\n".join(ops).encode("latin-1", "replace")
        content = b.add(b"<< /Length " + str(len(stream)).encode() + b" >>\nstream\n" + stream + b"\nendstream")
        page = b.add(
            f"<< /Type /Page /Parent {pages_obj} 0 R /MediaBox [0 0 {w} {h}] "
            f"/Resources << /Font << /F1 {font_regular} 0 R /F2 {font_bold} 0 R >> >> "
            f"/Contents {content} 0 R >>".encode()
        )
        page_nums.append(page)
    kids = " ".join(f"{p} 0 R" for p in page_nums)
    b.set(pages_obj, f"<< /Type /Pages /Kids [{kids}] /Count {len(page_nums)} >>".encode())
    b.set(catalog, f"<< /Type /Catalog /Pages {pages_obj} 0 R >>".encode())
    Path(path).write_bytes(b.build(catalog, info))
