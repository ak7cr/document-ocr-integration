
import json
import sys

import pymupdf as fitz
import pdfplumber
import pytesseract
from pytesseract import Output
from PIL import Image, ImageOps

DEFAULT_PSM = 6       # "Assume a single uniform block" -> reads rows left-to-right
MIN_CONF = 30

# Local table OCR: RapidOCR PP-Structure (rapid_table + unified rapidocr).
# Optional import so the rest of the worker keeps working without it.
try:
    from rapid_table import RapidTable
    _RAPID_AVAILABLE = True
except Exception:
    _RAPID_AVAILABLE = False

_RAPID_TABLE = None


def embedded_text(path):
    pages = []
    with pdfplumber.open(path) as pdf:
        for page in pdf.pages:
            pages.append(page.extract_text(x_tolerance=2, y_tolerance=3) or "")
    return "\n\n".join(pages), len(pages)


# ── table-aware OCR helpers ──────────────────────────────────────────────────


def _preprocess(image):
    image = image.convert("L")
    return ImageOps.autocontrast(image)


def _collect_words(image, psm=DEFAULT_PSM, min_conf=MIN_CONF):
    """Word boxes: (x, y, w, h, text) tuples, confidence-filtered."""
    data = pytesseract.image_to_data(image, config=f"--psm {psm}", output_type=Output.DICT)
    words = []
    for i in range(len(data["text"])):
        text = (data["text"][i] or "").strip()
        if not text:
            continue
        try:
            conf = float(data["conf"][i])
        except (TypeError, ValueError):
            conf = -1
        if conf < min_conf:
            continue
        words.append((data["left"][i], data["top"][i], data["width"][i], data["height"][i], text))
    return words


def _cluster_rows(words, tol=4):
    """Group words into text rows by vertical overlap."""
    words = sorted(words, key=lambda w: (w[1], w[0]))
    rows = []
    for w in words:
        x, y, ww, hh, text = w
        mid = y + hh / 2
        placed = False
        for row in rows:
            top = min(t[1] for t in row)
            bottom = max(t[1] + t[3] for t in row)
            if top - tol <= mid <= bottom + tol:
                row.append(w)
                placed = True
                break
        if not placed:
            rows.append([w])
    return rows


def _row_to_line(row):
    """Left->right words; single spaces between words, TAB at column gaps.

    Column gaps (whitespace wider than ~0.9x the median word width) become tab
    separators, so table cells stay aligned with their row and side-by-side
    header blocks (e.g. Seller | Client) remain distinguishable. Normal
    inter-word spacing (well under half a word width) stays a single space.
    """
    row = sorted(row, key=lambda w: w[0])
    if not row:
        return ""
    widths = sorted(w[2] for w in row)
    median = widths[len(widths) // 2]
    tab_gap = max(median * 0.9, 12)
    parts, prev_right = [], None
    for x, y, ww, hh, text in row:
        if prev_right is not None:
            parts.append("\t" if x - prev_right > tab_gap else " ")
        parts.append(text)
        prev_right = x + ww
    return "".join(parts)


def _ocr_structured(image):
    """OCR an image with table sense (row-ordered, column-aligned text)."""
    image = _preprocess(image)
    words = _collect_words(image)
    if not words:
        # Empty detection fallback for pathological images.
        return pytesseract.image_to_string(image, config="--psm 6").strip()
    rows = _cluster_rows(words)
    return "\n".join(_row_to_line(row) for row in rows)


def _ocr_multipass(image):
    """Run two Tesseract passes and keep the best one.

    - structured (PSM 6 + row/column reconstruction): table-aware, the primary.
    - plain (PSM 3): Tesseract's default segmentation; it keeps low-confidence
      words the structured pass may drop.

    Pure per-word confidence does not discriminate between the passes (all PSM
    modes score roughly equally), so selection is by word coverage: prefer the
    structured pass unless it is empty or clearly lost most of the content.
    """
    structured = plain = ""
    try:
        structured = _ocr_structured(image)
    except Exception:
        structured = ""
    try:
        plain = pytesseract.image_to_string(_preprocess(image), config="--psm 3").strip()
    except Exception:
        plain = ""

    count_s = len(structured.split())
    count_p = len(plain.split())
    # Healthy docs have identical word counts between passes, so any drop below
    # 90% coverage means the structured pass lost real content -> fall back.
    if not structured or (count_p and count_s < count_p * 0.9):
        return plain or structured
    return structured




def _merge_runs(items, gap=3):
    if not items:
        return []
    runs = [[items[0], items[0]]]
    for x in items[1:]:
        if x - runs[-1][1] <= gap:
            runs[-1][1] = x
        else:
            runs.append([x, x])
    return [(a + b) // 2 for a, b in runs]


def _table_lines(image, ink=200, min_ratio=0.15):
    """Return (vlines, hlines) pixel positions of long ink runs (grid lines).

    ``ink`` must sit above the grid-line color (~110-180) but below the row
    shading (~244-246) so only the ruled lines are treated as table borders.
    ``min_ratio`` is relative to the page so small tables are still found.
    """
    g = image.convert("L")
    w, h = g.size
    px = g.load()
    min_v = max(20, int(h * min_ratio))
    min_h = max(20, int(w * min_ratio))

    col_max = [0] * w
    for x in range(w):
        run = best = 0
        for y in range(h):
            if px[x, y] < ink:
                run += 1
                best = run if run > best else best
            else:
                run = 0
        col_max[x] = best
    row_max = [0] * h
    for y in range(h):
        run = best = 0
        for x in range(w):
            if px[x, y] < ink:
                run += 1
                best = run if run > best else best
            else:
                run = 0
        row_max[y] = best

    vlines = _merge_runs([x for x in range(w) if col_max[x] >= min_v])
    hlines = _merge_runs([y for y in range(h) if row_max[y] >= min_h])
    return vlines, hlines


def _ocr_cell(image, box, psm=6):
    x0, y0, x1, y1 = box
    if x1 - x0 < 3 or y1 - y0 < 3:
        return ""
    crop = image.crop((x0, y0, x1, y1))
    if crop.width < 140:
        scale = max(2, min(4, 140 // max(1, crop.width)))
        crop = crop.resize((crop.width * scale, crop.height * scale), Image.LANCZOS)
    crop = ImageOps.autocontrast(crop.convert("L"))
    return pytesseract.image_to_string(crop, config=f"--psm {psm}").strip()


def _extract_tables(image):
    """Grid-based table extraction -> list of tables (rows of cell strings).

    Consecutive grid lines whose active columns overlap are grouped into one
    table, so an items table (many columns) and a narrower tax table do not
    bleed into each other. The dominant/widest table is the line-items one.
    """
    g = image.convert("L")
    vlines, hlines = _table_lines(g)
    if len(vlines) < 2 or len(hlines) < 2:
        return []
    px = g.load()

    def active(y):
        return set(v for v in vlines if px[v, y] < 200)

    groups = []
    for i, y in enumerate(hlines):
        cols = active(y)
        if not cols:
            continue
        if groups:
            prev = groups[-1]["cols"]
            overlap = len(cols & prev) / max(1, len(prev))
            if overlap >= 0.6:
                groups[-1]["rows"].append(i)
                groups[-1]["cols"] |= cols
                continue
        groups.append({"rows": [i], "cols": set(cols)})

    tables = []
    for ginfo in groups:
        idxs = ginfo["rows"]
        if len(idxs) < 2:
            continue
        cols = sorted(ginfo["cols"])
        if len(cols) < 2:
            continue
        rows = []
        for ri in range(len(idxs) - 1):
            y0, y1 = hlines[idxs[ri]], hlines[idxs[ri + 1]]
            row = []
            for ci in range(len(cols) - 1):
                row.append(_ocr_cell(image, (cols[ci], y0, cols[ci + 1], y1)))
            rows.append(row)
        tables.append(rows)
    return tables


def ocr_text(path):
    document = fitz.open(path)
    pages = []
    for page in document:
        # Render at 2x: higher upscaling makes low-confidence cells (amounts)
        # drop out of Tesseract's word detection entirely.
        pixmap = page.get_pixmap(matrix=fitz.Matrix(2, 2), alpha=False)
        image = Image.frombytes("RGB", (pixmap.width, pixmap.height), pixmap.samples)
        pages.append(_ocr_multipass(image))
    count = len(document)
    document.close()
    return "\n\n".join(pages), count


def image_ocr(path):
    """Run Tesseract directly on an image file (JPEG, PNG, WebP, TIFF, etc.)."""
    image = Image.open(path)
    if image.mode not in ("L", "RGB"):
        image = image.convert("RGB")
    text = _ocr_multipass(image)
    return text, 1





def _get_rapid_table():
    global _RAPID_TABLE
    if _RAPID_TABLE is None:
        _RAPID_TABLE = RapidTable()
    return _RAPID_TABLE


def _html_table_to_grid(html):
   
    from html.parser import HTMLParser

    class TableParser(HTMLParser):
        def __init__(self):
            super().__init__()
            self.rows = []
            self.cur_row = None
            self.cur_cell = None

        def handle_starttag(self, tag, attrs):
            attrs = dict(attrs)
            if tag == "tr":
                self.cur_row = []
            elif tag in ("td", "th"):
                self.cur_cell = {
                    "text": "",
                    "colspan": int(attrs.get("colspan", "1") or 1),
                }

        def handle_endtag(self, tag):
            if tag in ("td", "th") and self.cur_cell is not None:
                if self.cur_row is not None:
                    self.cur_row.append(self.cur_cell)
                self.cur_cell = None
            elif tag == "tr" and self.cur_row is not None:
                self.rows.append(self.cur_row)
                self.cur_row = None

        def handle_data(self, data):
            if self.cur_cell is not None:
                self.cur_cell["text"] += data

    parser = TableParser()
    parser.feed(html)

    width = 0
    for row in parser.rows:
        width = max(width, sum(c["colspan"] for c in row))

    lines = []
    for row in parser.rows:
        cells = []
        for cell in row:
            text = " ".join(cell["text"].split())
            cells.extend([text] + [""] * (cell["colspan"] - 1))
        cells += [""] * (width - len(cells))
        lines.append("\t".join(cells))
    return "\n".join(lines)


def table_text(path):
    
    if not _RAPID_AVAILABLE:
        return "", 0
    if path.lower().endswith(".pdf"):
        document = fitz.open(path)
        images = []
        for page in document:
            pixmap = page.get_pixmap(matrix=fitz.Matrix(2, 2), alpha=False)
            images.append(Image.frombytes("RGB", (pixmap.width, pixmap.height), pixmap.samples))
        document.close()
    else:
        image = Image.open(path)
        if image.mode not in ("L", "RGB"):
            image = image.convert("RGB")
        images = [image]

    try:
        result = _get_rapid_table()(images)
    except Exception:
        return "", 0
    grids = [_html_table_to_grid(html) for html in result.pred_htmls]
    text = "\n\n".join(g for g in grids if g.strip())
    return text, len(images)


if __name__ == "__main__":
    try:
        mode, path = sys.argv[1], sys.argv[2]
        if mode == "pdfplumber":
            text, pages = embedded_text(path)
        elif mode == "image_ocr":
            text, pages = image_ocr(path)
        elif mode == "table":
            text, pages = table_text(path)
        else:
            text, pages = ocr_text(path)
        print(json.dumps({"ok": True, "text": text, "pages": pages}))
    except Exception as error:
        print(json.dumps({"ok": False, "error": str(error)}))
