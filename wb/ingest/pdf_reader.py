"""Dependency-free PDF text extraction.

Supports the common born-digital case: FlateDecode content streams, Type1/TrueType
fonts, WinAnsi or Identity-H + ToUnicode CMaps, multi-column reading order,
heading / footnote / page-number heuristics. Not a full PDF engine.
"""
from __future__ import annotations

import re
import zlib
from dataclasses import dataclass, field
from typing import Any

WS = b"\x00\t\n\x0c\r "
DELIM = b"()<>[]{}/%"


class Name(str):
    pass


@dataclass(frozen=True)
class Ref:
    num: int
    gen: int = 0


class Op(str):
    pass


class Lexer:
    def __init__(self, data: bytes, pos: int = 0):
        self.data = data
        self.pos = pos
        self.n = len(data)

    def skip_ws(self) -> None:
        d, n = self.data, self.n
        while self.pos < n:
            c = d[self.pos]
            if c in WS:
                self.pos += 1
            elif c == 0x25:  # %
                while self.pos < n and d[self.pos] not in b"\r\n":
                    self.pos += 1
            else:
                break

    def value(self) -> Any:
        self.skip_ws()
        if self.pos >= self.n:
            raise EOFError
        d = self.data
        c = d[self.pos]
        if c == 0x2F:  # /
            self.pos += 1
            start = self.pos
            while self.pos < self.n and d[self.pos] not in WS and d[self.pos] not in DELIM:
                self.pos += 1
            raw = d[start:self.pos]
            raw = re.sub(rb"#([0-9A-Fa-f]{2})", lambda m: bytes([int(m.group(1), 16)]), raw)
            return Name("/" + raw.decode("latin-1"))
        if c == 0x28:  # (
            return self._string()
        if c == 0x3C:  # <
            if self.pos + 1 < self.n and d[self.pos + 1] == 0x3C:
                return self._dict()
            return self._hex()
        if c == 0x5B:  # [
            self.pos += 1
            arr = []
            while True:
                self.skip_ws()
                if self.pos >= self.n:
                    return arr
                if d[self.pos] == 0x5D:
                    self.pos += 1
                    return arr
                arr.append(self.value())
        if c in b"+-.0123456789":
            num = self._number()
            if isinstance(num, int) and num >= 0:
                save = self.pos
                self.skip_ws()
                m = re.match(rb"(\d+)\s+R\b", self.data[self.pos:self.pos + 32])
                if m:
                    self.pos += m.end()
                    return Ref(num, int(m.group(1)))
                self.pos = save
            return num
        # keyword / operator
        start = self.pos
        while self.pos < self.n and d[self.pos] not in WS and d[self.pos] not in DELIM:
            self.pos += 1
        word = d[start:self.pos].decode("latin-1")
        if word == "true":
            return True
        if word == "false":
            return False
        if word == "null":
            return None
        return Op(word)

    def _number(self):
        d = self.data
        start = self.pos
        self.pos += 1
        while self.pos < self.n and (d[self.pos] in b"0123456789.+-eE"):
            self.pos += 1
        raw = d[start:self.pos].decode("latin-1")
        try:
            return float(raw) if ("." in raw or "e" in raw.lower()) else int(raw)
        except ValueError:
            return 0

    def _string(self) -> bytes:
        d = self.data
        self.pos += 1
        depth = 1
        out = bytearray()
        while self.pos < self.n:
            c = d[self.pos]
            self.pos += 1
            if c == 0x5C:  # backslash
                if self.pos >= self.n:
                    break
                e = d[self.pos]
                self.pos += 1
                mapping = {0x6E: 10, 0x72: 13, 0x74: 9, 0x62: 8, 0x66: 12,
                           0x28: 40, 0x29: 41, 0x5C: 92}
                if e in mapping:
                    out.append(mapping[e])
                elif 0x30 <= e <= 0x37:
                    oct_digits = bytes([e])
                    for _ in range(2):
                        if self.pos < self.n and 0x30 <= d[self.pos] <= 0x37:
                            oct_digits += bytes([d[self.pos]])
                            self.pos += 1
                        else:
                            break
                    out.append(int(oct_digits, 8) & 0xFF)
                elif e in (0x0A, 0x0D):
                    pass
                else:
                    out.append(e)
            elif c == 0x28:
                depth += 1
                out.append(c)
            elif c == 0x29:
                depth -= 1
                if depth == 0:
                    break
                out.append(c)
            else:
                out.append(c)
        return bytes(out)

    def _hex(self) -> bytes:
        d = self.data
        self.pos += 1
        start = self.pos
        while self.pos < self.n and d[self.pos] != 0x3E:
            self.pos += 1
        raw = re.sub(rb"[^0-9A-Fa-f]", b"", d[start:self.pos])
        self.pos += 1
        if len(raw) % 2:
            raw += b"0"
        return bytes.fromhex(raw.decode("ascii"))

    def _dict(self) -> dict:
        self.pos += 2
        out: dict[str, Any] = {}
        d = self.data
        while True:
            self.skip_ws()
            if self.pos >= self.n:
                return out
            if d[self.pos:self.pos + 2] == b">>":
                self.pos += 2
                return out
            key = self.value()
            val = self.value()
            if isinstance(key, Name):
                out[str(key)] = val

    def array_or_value(self) -> Any:
        return self.value()


# --------------------------------------------------------------------------
# Document object table
# --------------------------------------------------------------------------

class PDFDoc:
    def __init__(self, data: bytes):
        self.data = data
        self.objs: dict[int, Any] = {}
        self.streams: dict[int, bytes] = {}
        self._parse_objects()

    def _parse_objects(self) -> None:
        d = self.data
        pat = re.compile(rb"(?<![0-9])(\d{1,9})\s+(\d{1,5})\s+obj\b")
        pos = 0
        while True:
            m = pat.search(d, pos)
            if not m:
                break
            num = int(m.group(1))
            lx = Lexer(d, m.end())
            try:
                val = lx.value()
            except Exception:
                pos = m.end()
                continue
            lx.skip_ws()
            if d[lx.pos:lx.pos + 6] == b"stream":
                p = lx.pos + 6
                if d[p:p + 2] == b"\r\n":
                    p += 2
                elif p < len(d) and d[p] in b"\n\r":
                    p += 1
                length = val.get("/Length") if isinstance(val, dict) else None
                if isinstance(length, int) and length >= 0 and p + length <= len(d):
                    raw = d[p:p + length]
                    endp = p + length
                else:
                    e = d.find(b"endstream", p)
                    raw = d[p:e] if e >= 0 else b""
                    endp = e + 9 if e >= 0 else len(d)
                self.streams[num] = self._decode(val, raw) if isinstance(val, dict) else raw
                self.objs[num] = val
                pos = max(endp, lx.pos)
                continue
            self.objs[num] = val
            pos = lx.pos

    @staticmethod
    def _decode(dct: dict, raw: bytes) -> bytes:
        filt = dct.get("/Filter")
        if filt is None:
            return raw
        if not isinstance(filt, list):
            filt = [filt]
        out = raw
        for f in filt:
            if f == "/FlateDecode":
                try:
                    out = zlib.decompress(out)
                except Exception:
                    try:
                        out = zlib.decompressobj().decompress(out)
                    except Exception:
                        return b""
            elif f == "/ASCIIHexDecode":
                out = bytes.fromhex(re.sub(rb"[^0-9A-Fa-f]", b"", out).decode())
            elif f == "/ASCII85Decode":
                import base64
                try:
                    out = base64.a85decode(out, adobe=True)
                except Exception:
                    return b""
        return out

    def get(self, v: Any) -> Any:
        seen = 0
        while isinstance(v, Ref) and seen < 20:
            v = self.objs.get(v.num)
            seen += 1
        return v

    def resolve(self, dct: Any, key: str, default=None):
        if not isinstance(dct, dict):
            return default
        v = self.get(dct.get(key, default))
        return default if v is None else v

    def pages(self) -> list[dict]:
        catalog = None
        for num, o in self.objs.items():
            if isinstance(o, dict) and o.get("/Type") == "/Catalog":
                catalog = o
                break
        out: list[dict] = []
        if catalog is not None:
            root = self.get(catalog.get("/Pages"))
            self._walk_pages(root, out, set())
        if not out:
            for num in sorted(self.objs):
                o = self.objs[num]
                if isinstance(o, dict) and o.get("/Type") == "/Page":
                    out.append(o)
        return out

    def _walk_pages(self, node: Any, out: list, seen: set) -> None:
        if not isinstance(node, dict) or id(node) in seen:
            return
        seen.add(id(node))
        kids = self.get(node.get("/Kids"))
        if node.get("/Type") == "/Page":
            out.append(node)
            return
        if isinstance(kids, list):
            for k in kids:
                self._walk_pages(self.get(k), out, seen)

    def page_content(self, page: dict) -> bytes:
        contents = self.get(page.get("/Contents"))
        if isinstance(contents, list):
            parts = [self.streams.get(c.num, b"") for c in contents if isinstance(c, Ref)]
            return b"\n".join(parts)
        if isinstance(page.get("/Contents"), Ref):
            return self.streams.get(page["/Contents"].num, b"")
        return b""

    def page_resources(self, page: dict) -> dict:
        node = page
        for _ in range(12):
            res = self.get(node.get("/Resources")) if isinstance(node, dict) else None
            if isinstance(res, dict):
                return res
            node = self.get(node.get("/Parent")) if isinstance(node, dict) else None
            if not isinstance(node, dict):
                break
        return {}


# --------------------------------------------------------------------------
# Fonts / CMaps
# --------------------------------------------------------------------------

def _parse_cmap(data: bytes) -> dict[int, str]:
    text = data.decode("latin-1", "ignore")
    mapping: dict[int, str] = {}
    for block in re.findall(r"beginbfchar(.*?)endbfchar", text, re.S):
        for src, dst in re.findall(r"<([0-9A-Fa-f]+)>\s*<([0-9A-Fa-f]+)>", block):
            code = int(src, 16)
            mapping[code] = _utf16(dst)
    for block in re.findall(r"beginbfrange(.*?)endbfrange", text, re.S):
        for src, dst, rest in re.findall(r"<([0-9A-Fa-f]+)>\s*<([0-9A-Fa-f]+)>\s*(<[0-9A-Fa-f]+>|\[[^\]]*\])", block):
            lo, hi = int(src, 16), int(dst, 16)
            if rest.startswith("["):
                items = re.findall(r"<([0-9A-Fa-f]+)>", rest)
                for i, hx in enumerate(items):
                    mapping[lo + i] = _utf16(hx)
            else:
                base = rest.strip("<>")
                start_code = int(base, 16)
                for i in range(hi - lo + 1):
                    mapping[lo + i] = chr(start_code + i)
    return mapping


def _utf16(h: str) -> str:
    try:
        b = bytes.fromhex(h if len(h) % 2 == 0 else h + "0")
        return b.decode("utf-16-be", "ignore")
    except Exception:
        return ""


@dataclass
class Font:
    name: str
    two_byte: bool = False
    cmap: dict[int, str] = field(default_factory=dict)

    def decode(self, raw: bytes) -> str:
        if self.cmap:
            out = []
            if self.two_byte:
                for i in range(0, len(raw) - 1, 2):
                    code = (raw[i] << 8) | raw[i + 1]
                    out.append(self.cmap.get(code, ""))
                if len(raw) % 2:
                    out.append(self.cmap.get(raw[-1], ""))
            else:
                for b in raw:
                    out.append(self.cmap.get(b, ""))
            return "".join(out)
        try:
            return raw.decode("cp1252")
        except Exception:
            return raw.decode("latin-1", "ignore")


def _load_fonts(doc: PDFDoc, resources: dict) -> dict[str, Font]:
    fonts: dict[str, Font] = {}
    fdict = doc.get(resources.get("/Font")) if isinstance(resources, dict) else None
    if not isinstance(fdict, dict):
        return fonts
    for name, ref in fdict.items():
        fobj = doc.get(ref)
        if not isinstance(fobj, dict):
            continue
        font = Font(name=str(name))
        enc = fobj.get("/Encoding")
        if enc == "/Identity-H" or enc == "/Identity-V":
            font.two_byte = True
        tu = doc.get(fobj.get("/ToUnicode"))
        if isinstance(tu, dict) and isinstance(fobj.get("/ToUnicode"), Ref):
            raw = doc.streams.get(fobj["/ToUnicode"].num, b"")
            if raw:
                font.cmap = _parse_cmap(raw)
                if not font.cmap:
                    font.two_byte = True
        if fobj.get("/Subtype") == "/Type0":
            font.two_byte = True
        fonts[str(name)] = font
    return fonts


# --------------------------------------------------------------------------
# Text runs
# --------------------------------------------------------------------------

@dataclass
class Run:
    x: float
    y: float
    size: float
    text: str
    page: int


def _content_runs(doc: PDFDoc, page: dict, page_no: int) -> list[Run]:
    content = doc.page_content(page)
    if not content:
        return []
    fonts = _load_fonts(doc, doc.page_resources(page))
    lx = Lexer(content)
    operands: list[Any] = []
    runs: list[Run] = []
    tm = [1, 0, 0, 1, 0, 0]
    tlm = list(tm)
    ctm = [1, 0, 0, 1, 0, 0]
    leading = 0.0
    font = Font("")
    size = 10.0
    stack: list[Any] = []

    def emit(text: str) -> None:
        if not text:
            return
        x = tm[4] + ctm[4]
        y = tm[5] + ctm[5]
        s = abs(size * (tm[0] or 1))
        runs.append(Run(x, y, s if s > 0 else 10.0, text, page_no))

    while True:
        try:
            v = lx.value()
        except EOFError:
            break
        except Exception:
            break
        if isinstance(v, Op):
            op = str(v)
            try:
                if op == "BT":
                    tm = [1, 0, 0, 1, 0, 0]
                    tlm = list(tm)
                elif op == "Tf" and len(operands) >= 2:
                    fname = str(operands[-2])
                    size = float(operands[-1])
                    font = fonts.get(fname, Font(fname))
                elif op == "Tm" and len(operands) >= 6:
                    tm = [float(x) for x in operands[-6:]]
                    tlm = list(tm)
                elif op == "Td" and len(operands) >= 2:
                    tx, ty = float(operands[-2]), float(operands[-1])
                    tlm = [tlm[0], tlm[1], tlm[2], tlm[3], tlm[4] + tx, tlm[5] + ty]
                    tm = list(tlm)
                elif op == "TD" and len(operands) >= 2:
                    tx, ty = float(operands[-2]), float(operands[-1])
                    leading = -ty
                    tlm = [tlm[0], tlm[1], tlm[2], tlm[3], tlm[4] + tx, tlm[5] + ty]
                    tm = list(tlm)
                elif op == "T*":
                    tlm = [tlm[0], tlm[1], tlm[2], tlm[3], tlm[4], tlm[5] - leading]
                    tm = list(tlm)
                elif op == "TL" and operands:
                    leading = float(operands[-1])
                elif op == "Tj" and operands:
                    raw = operands[-1]
                    if isinstance(raw, bytes):
                        emit(font.decode(raw))
                elif op == "TJ" and operands:
                    arr = operands[-1]
                    if isinstance(arr, list):
                        buf = []
                        for item in arr:
                            if isinstance(item, bytes):
                                buf.append(font.decode(item))
                            elif isinstance(item, (int, float)) and item < -120:
                                buf.append(" ")
                        emit("".join(buf))
                elif op in ("'", '"') and operands:
                    tlm = [tlm[0], tlm[1], tlm[2], tlm[3], tlm[4], tlm[5] - leading]
                    tm = list(tlm)
                    raw = operands[-1]
                    if isinstance(raw, bytes):
                        emit(font.decode(raw))
                elif op == "cm" and len(operands) >= 6:
                    m = [float(x) for x in operands[-6:]]
                    ctm = [
                        ctm[0] * m[0] + ctm[1] * m[2], ctm[0] * m[1] + ctm[1] * m[3],
                        ctm[2] * m[0] + ctm[3] * m[2], ctm[2] * m[1] + ctm[3] * m[3],
                        ctm[4] * m[0] + ctm[5] * m[2] + m[4], ctm[4] * m[1] + ctm[5] * m[3] + m[5],
                    ]
                elif op == "q":
                    stack.append((list(ctm),))
                elif op == "Q":
                    if stack:
                        ctm = stack.pop()[0]
            except Exception:
                pass
            operands = []
        else:
            operands.append(v)
    return runs
