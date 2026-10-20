"""Extract text from a PDF rendered by Chrome/Skia, no third-party libraries.

Subset fonts mean the raw bytes are glyph ids, so the text is decoded through
the /ToUnicode CMaps embedded in the file. Enough for documents printed from
Google Docs or a browser, which is what reference material arrives as here.

Usage: python3 research/pdftext.py input.pdf [output.txt]
"""
import re
import sys
import zlib


def extract(path):
    raw = open(path, "rb").read()

    cmaps = []
    for m in re.finditer(rb"(\d+)\s+0\s+obj(.*?)endobj", raw, re.S):
        s = re.search(rb"stream\r?\n(.*?)\r?\nendstream", m.group(2), re.S)
        if not s:
            continue
        try:
            d = zlib.decompress(s.group(1))
        except Exception:
            continue
        if b"beginbfchar" in d or b"beginbfrange" in d:
            cmaps.append(d)

    mapping = {}
    for data in cmaps:
        for blk in re.findall(rb"beginbfchar(.*?)endbfchar", data, re.S):
            for src, dst in re.findall(rb"<([0-9A-Fa-f]+)>\s*<([0-9A-Fa-f]+)>", blk):
                try:
                    mapping.setdefault(
                        int(src, 16),
                        bytes.fromhex(dst.decode()).decode("utf-16-be", "ignore"))
                except Exception:
                    pass
        for blk in re.findall(rb"beginbfrange(.*?)endbfrange", data, re.S):
            for lo, hi, dst in re.findall(
                    rb"<([0-9A-Fa-f]+)>\s*<([0-9A-Fa-f]+)>\s*<([0-9A-Fa-f]+)>", blk):
                try:
                    a, b, base = int(lo, 16), int(hi, 16), int(dst, 16)
                    for k in range(a, min(b, a + 512) + 1):
                        mapping.setdefault(k, chr(base + (k - a)))
                except Exception:
                    pass

    pages = []
    for m in re.finditer(rb"stream\r?\n(.*?)\r?\nendstream", raw, re.S):
        try:
            d = zlib.decompress(m.group(1))
        except Exception:
            continue
        if b"Tj" not in d and b"TJ" not in d:
            continue
        buf = []
        for hs in re.finditer(rb"<([0-9A-Fa-f]+)>", d):
            hx = hs.group(1)
            if len(hx) % 4:
                continue
            for i in range(0, len(hx), 4):
                buf.append(mapping.get(int(hx[i:i + 4], 16), ""))
        pages.append("".join(buf))
    return "\n".join(pages), len(mapping)


if __name__ == "__main__":
    if len(sys.argv) < 2:
        raise SystemExit("usage: pdftext.py input.pdf [output.txt]")
    text, n = extract(sys.argv[1])
    out = sys.argv[2] if len(sys.argv) > 2 else sys.argv[1].rsplit(".", 1)[0] + ".txt"
    open(out, "w", encoding="utf-8").write(text)
    print("символов: %d | символов в таблице: %d | файл: %s" % (len(text), n, out))
