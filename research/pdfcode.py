"""Extract text from a Chrome/Skia PDF keeping the line breaks.

research/pdftext.py concatenates every glyph run on a page into one string, so a
100-line source file arrives as one 4000-character line. This variant reads the
text-positioning operators (Tm / Td / TD / T*) alongside the glyph runs and
starts a new line whenever the baseline moves, which is what makes the output
readable as code.

Usage: python3 research/pdfcode.py input.pdf [output.txt]
"""
import re
import sys
import zlib

TOKEN = re.compile(
    rb"(-?[\d.]+)\s+(-?[\d.]+)\s+(-?[\d.]+)\s+(-?[\d.]+)\s+(-?[\d.]+)\s+(-?[\d.]+)\s+Tm"
    rb"|(-?[\d.]+)\s+(-?[\d.]+)\s+(?:Td|TD)"
    rb"|(T\*)"
    rb"|<([0-9A-Fa-f]+)>")


def _mapping(raw):
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
    return mapping


def extract(path):
    raw = open(path, "rb").read()
    mapping = _mapping(raw)
    pages = []
    for m in re.finditer(rb"stream\r?\n(.*?)\r?\nendstream", raw, re.S):
        try:
            data = zlib.decompress(m.group(1))
        except Exception:
            continue
        if b"Tj" not in data and b"TJ" not in data:
            continue
        lines = []
        buf = []
        base_y = 0.0
        dy_acc = 0.0
        cur_y = 0.0
        last_y = None
        for t in TOKEN.finditer(data):
            if t.group(6) is not None:                    # Tm: new base matrix
                base_y = float(t.group(6))
                dy_acc = 0.0
                cur_y = base_y
                continue
            if t.group(8) is not None:                    # Td / TD: relative move
                dy_acc += float(t.group(8))
                cur_y = base_y + dy_acc
                continue
            if t.group(9) is not None:                    # T*: next line
                dy_acc -= 1000.0
                cur_y = base_y + dy_acc
                continue
            hx = t.group(10)
            if not hx:
                continue
            # Compare baselines only when a glyph actually lands, so the Tm
            # reset at the start of every BT block cannot fake a line break.
            if last_y is not None and abs(cur_y - last_y) > 0.5:
                lines.append("".join(buf))
                buf = []
            last_y = cur_y
            if len(hx) % 4:
                continue
            for i in range(0, len(hx), 4):
                buf.append(mapping.get(int(hx[i:i + 4], 16), ""))
        lines.append("".join(buf))
        pages.append("\n".join(lines))
    return "\n".join(pages), len(mapping)


if __name__ == "__main__":
    if len(sys.argv) < 2:
        raise SystemExit("usage: pdfcode.py input.pdf [output.txt]")
    text, n = extract(sys.argv[1])
    out = sys.argv[2] if len(sys.argv) > 2 else sys.argv[1].rsplit(".", 1)[0] + ".txt"
    open(out, "w", encoding="utf-8").write(text)
    print("символов: %d | строк: %d | таблица: %d | файл: %s" % (
        len(text), text.count("\n") + 1, n, out))
