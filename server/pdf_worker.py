
import json
import sys
from concurrent.futures import ThreadPoolExecutor

import numpy as np
import pymupdf as fitz
import pdfplumber
import pytesseract
from pytesseract import Output
from PIL import Image, ImageOps

DEFAULT_PSM = 6       # "Assume a single uniform block" -> reads rows left-to-right
MIN_CONF = 30

# Their OCR engine (invoice-ocr-c++): PaddleOCR is disabled on Python >= 3.13,
# so on this machine the real engine is EasyOCR. We use it as the table OCR
# (Tesseract stays for the header/scalar fields).
_EASYOCR_READER = None


def _get_easyocr_reader():
    global _EASYOCR_READER
    if _EASYOCR_READER is None:
        try:
            import easyocr
            _EASYOCR_READER = easyocr.Reader(["en"], gpu=False)
        except Exception:
            _EASYOCR_READER = None
    return _EASYOCR_READER


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
    """Run Tesseract directly on an image file (JPEG, PNG, WebP, TIFF, etc.).
    Header/scalar fields stay on Tesseract (clean labelled lines); the ITEMS
    table uses the spatial engine fed by EasyOCR/RapidOCR (see table_text)."""
    image = Image.open(path)
    if image.mode not in ("L", "RGB"):
        image = image.convert("RGB")
    return _ocr_multipass(image), 1


def _words_to_full_lines(words):
    """Their full-page reconstruction — faithful port of
    ocr_engine.perform_image_ocr: sort words by (y0, x0) -> cluster into lines
    (h*0.65) -> '  '.join per line. This is the raw text their dashboard shows."""
    if not words:
        return ""
    words = sorted(words, key=lambda w: (w["y0"], w["x0"]))
    lines = []
    curr = [words[0]]
    for w in words[1:]:
        prev_yc = (curr[-1]["y0"] + curr[-1]["y1"]) / 2.0
        w_yc = (w["y0"] + w["y1"]) / 2.0
        h = max(curr[-1]["y1"] - curr[-1]["y0"], 12)
        if abs(w_yc - prev_yc) < (h * 0.65):
            curr.append(w)
        else:
            curr = sorted(curr, key=lambda it: it["x0"])
            lines.append("  ".join(it["text"] for it in curr))
            curr = [w]
    if curr:
        curr = sorted(curr, key=lambda it: it["x0"])
        lines.append("  ".join(it["text"] for it in curr))
    return "\n".join(lines)


# ── spatial cell-grid table engine (ported from invoice-ocr-c++) ──────────────
# Their winning method: OCR word boxes -> detect the table header row by column
# keywords -> compute column x-boundaries (midpoint partition) -> detect product
# row y-intervals (S.NO / HSN / QTY+unit anchors) -> assign each word to its
# (row, col) cell. This resolves qty/rate by geometry, which the header-driven
# text parser often mis-assigns when OCR drops a digit (e.g. GST qty "3").

_RAPID_OCR = None


def _get_rapid_ocr():
    global _RAPID_OCR
    if _RAPID_OCR is None:
        try:
            from rapidocr import RapidOCR
        except Exception:
            from rapidocr_onnxruntime import RapidOCR
        _RAPID_OCR = RapidOCR()
    return _RAPID_OCR


def _rapid_words(image):
    """Run RapidOCR -> list of {text, conf, x0, y0, x1, y1} word boxes."""
    try:
        result = _get_rapid_ocr()(image)
    except Exception:
        return []
    if result is None or getattr(result, "boxes", None) is None:
        return []
    words = []
    for box, txt, score in zip(result.boxes, result.txts, result.scores):
        try:
            pts = np.asarray(box, dtype=float).reshape(-1, 2)
        except Exception:
            continue
        text = str(txt or "").strip()
        if not text:
            continue
        words.append({
            "text": text,
            "conf": float(score or 0),
            "x0": float(pts[:, 0].min()),
            "y0": float(pts[:, 1].min()),
            "x1": float(pts[:, 0].max()),
            "y1": float(pts[:, 1].max()),
        })
    return words


def _easy_words(image):
    """EasyOCR word boxes — their engine's tuned params (catches single digits)."""
    reader = _get_easyocr_reader()
    if reader is None:
        return []
    img_np = np.array(image.convert("RGB"))
    try:
        results = reader.readtext(img_np, batch_size=8, workers=0, min_size=2,
                                  text_threshold=0.35, low_text=0.25,
                                  link_threshold=0.3, mag_ratio=1.0,
                                  contrast_ths=0.1, adjust_contrast=0.5)
    except Exception:
        return []
    words = []
    for bbox, text, prob in results:
        t = str(text).strip()
        if not t:
            continue
        xs = [p[0] for p in bbox]
        ys = [p[1] for p in bbox]
        words.append({
            "text": t,
            "conf": float(prob or 0),
            "x0": float(min(xs)),
            "y0": float(min(ys)),
            "x1": float(max(xs)),
            "y1": float(max(ys)),
        })
    return words


_HEADER_MAP = [
    ("sno",           [r"^s[\.\s]?no", r"^sr[\.\s]?no", r"^sl[\.\s]?no", r"^item\s*#", r"^#$", r"^no\.$", r"^sno"]),
    ("description",   [r"desc", r"particular", r"item", r"goods", r"product", r"service", r"narration"]),
    ("hsn_sac",       [r"hsn", r"sac", r"hsncode"]),
    ("quantity",      [r"^qt[yxa]", r"quant", r"^qnty", r"^nos\.?$", r"^pcs\.?$", r"^units?$"]),
    ("unit",          [r"^unit", r"^uom", r"^per", r"^u\.?m\.?"]),
    ("unit_price",    [r"^rate", r"unit\s*price", r"price", r"mrp", r"net\s*price", r"cost", r"rate\s*\/"]),
    ("discount",      [r"disc"]),
    ("cgst",          [r"^cgst"]),
    ("sgst",          [r"^sgst"]),
    ("igst",          [r"^igst"]),
    ("tax_rate",      [r"tax\s*%", r"gst\s*%", r"igst\s*%", r"cgst\s*%", r"vat\s*%", r"tax\s*rate", r"rate\s*%", r"rate\s*of\s*tax", r"^tax\b"]),
    ("taxable_value", [r"taxable\s*value", r"taxable\s*amt", r"net\s*worth", r"net\s*amount"]),
    ("total_amount",  [r"^amount", r"total\s*amount", r"gross", r"net\s*amount", r"amount\s*\(inc", r"^total$", r"^value$"]),
]

import re as _re


def _match_header_col(word):
    wt = word.strip().upper()
    for col, patterns in _HEADER_MAP:
        for pat in patterns:
            if _re.search(pat, wt, _re.IGNORECASE):
                return col
    return None


def _clean_num(s):
    if not s:
        return 0.0
    t = _re.sub(r"[^\d\.,]", "", str(s)).strip()
    if not t:
        return 0.0
    if "." in t and "," in t:
        t = t.replace(",", "")
    elif "," in t and "." not in t:
        parts = t.split(",")
        if len(parts) == 2 and len(parts[1]) <= 2:
            t = parts[0] + "." + parts[1]
        else:
            t = t.replace(",", "")
    t = t.rstrip(".")
    try:
        return float(t)
    except ValueError:
        m = _re.search(r"(\d+(?:\.\d+)?)", t)
        return float(m.group(1)) if m else 0.0


def _detect_grid_structure(words, doc_w, doc_h):
    if not words:
        return None

    lines_dict = {}
    for w in words:
        yc = (w["y0"] + w["y1"]) / 2.0
        lines_dict.setdefault(round(yc / 14.0) * 14, []).append(w)
    sorted_y_keys = sorted(lines_dict.keys())

    header_idx, header_y0, header_cols = -1, 0.0, {}
    for idx, yk in enumerate(sorted_y_keys):
        line = sorted(lines_dict[yk], key=lambda w: w["x0"])
        matched = {}
        for w in line:
            col = _match_header_col(w["text"])
            if col and col not in matched:
                cx = (w["x0"] + w["x1"]) / 2.0
                matched[col] = {"cx": cx, "x0": float(w["x0"]), "x1": float(w["x1"]), "word": w}
        if len(matched) >= 3 and ("description" in matched or "sno" in matched or "quantity" in matched):
            header_idx = idx
            header_y0 = min(w.get("y0", 0) for w in line)
            header_cols = matched
            break

    if not header_cols:
        return None

    col_centers = {name: info["cx"] for name, info in header_cols.items()}
    if "description" not in col_centers:
        sno_cx = col_centers.get("sno", doc_w * 0.05)
        nxt = min((c for n, c in col_centers.items() if n != "sno" and c > sno_cx), default=doc_w * 0.35)
        col_centers["description"] = (sno_cx + nxt) / 2.0
    if "total_amount" not in col_centers:
        mx = max(col_centers.values(), default=doc_w * 0.80)
        col_centers["total_amount"] = (mx + doc_w) / 2.0

    sorted_items = sorted(col_centers.items(), key=lambda kv: kv[1])
    column_boundaries = {}
    for i, (name, cx) in enumerate(sorted_items):
        left_b = 0.0 if i == 0 else (cx + sorted_items[i - 1][1]) / 2.0
        right_b = doc_w if i == len(sorted_items) - 1 else (cx + sorted_items[i + 1][1]) / 2.0
        column_boundaries[name] = {"x0": left_b, "x1": right_b, "cx": cx}

    if "sno" in column_boundaries:
        sno_cut = min(column_boundaries["sno"]["x1"], doc_w * 0.06)
        column_boundaries["sno"]["x1"] = sno_cut
        if "description" in column_boundaries:
            column_boundaries["description"]["x0"] = sno_cut

    header_y1 = max(w.get("y1", w.get("y0", 0) + 15) for w in lines_dict[sorted_y_keys[header_idx]])
    summary_y0 = doc_h * 0.92
    _SUMMARY = _re.compile(r"sub\s*total|subtotal|total\s*amount|grand\s*total|taxable\s*value|"
                           r"total\s*tax\s*amount|cgst|sgst|summary|terms\s*&?\s*conditions|notes", _re.IGNORECASE)
    for yk in sorted_y_keys[(header_idx + 1):]:
        line = lines_dict[yk]
        txt = " ".join(w["text"] for w in line)
        if _SUMMARY.search(txt):
            min_y = min(w["y0"] for w in line)
            if min_y > header_y1 + 25.0:
                summary_y0 = min_y - 2.0
                break

    body_words = [w for w in words if w.get("y0", 0) >= (header_y1 - 2.0) and w.get("y0", 0) < summary_y0]

    sno_info = column_boundaries.get("sno", {})
    hsn_info = column_boundaries.get("hsn_sac", {})
    qty_info = column_boundaries.get("quantity", {})
    sno_max_x = sno_info.get("x1", doc_w * 0.12) + 15.0 if sno_info else doc_w * 0.12
    hsn_x0 = hsn_info.get("x0", doc_w * 0.30) if hsn_info else doc_w * 0.30
    hsn_x1 = hsn_info.get("x1", doc_w * 0.48) if hsn_info else doc_w * 0.48
    qty_x0 = qty_info.get("x0", doc_w * 0.42) if qty_info else doc_w * 0.42
    qty_x1 = qty_info.get("x1", doc_w * 0.58) if qty_info else doc_w * 0.58

    body_lines = {}
    for w in body_words:
        yk = round(((w["y0"] + w["y1"]) / 2.0) / 8.0) * 8
        body_lines.setdefault(yk, []).append(w)
    sorted_body_y = sorted(body_lines.keys())
    row_starts = []
    for yk in sorted_body_y:
        bline = sorted(body_lines[yk], key=lambda w: w["x0"])
        has_sno = any(
            w["text"].strip().replace(".", "").isdigit()
            and 1 <= int(w["text"].strip().replace(".", "")) <= 200
            and ((w["x0"] + w["x1"]) / 2.0) <= sno_max_x for w in bline
        )
        has_hsn = any(_re.match(r"^\d{4,8}$", w["text"].strip())
                      and hsn_x0 <= ((w["x0"] + w["x1"]) / 2.0) <= hsn_x1 for w in bline)
        has_qty_unit = any(_re.match(r"^(?:piece|pcs|nos|box|kg|mtr|units?)$", w["text"].strip(), _re.I)
                           and qty_x0 <= ((w["x0"] + w["x1"]) / 2.0) <= (qty_x1 + 30.0) for w in bline)
        if has_sno or has_hsn or has_qty_unit:
            min_y = min(w["y0"] for w in bline)
            if not row_starts or (min_y - row_starts[-1]) >= 15.0:
                row_starts.append(min_y)

    row_intervals = []
    if row_starts:
        for idx, ys in enumerate(row_starts):
            y_start = max(header_y1, ys - 4.0)
            y_end = row_starts[idx + 1] - 4.0 if idx + 1 < len(row_starts) else summary_y0
            row_intervals.append((y_start, y_end, str(idx + 1)))
    else:
        cur = header_y1
        for yk in sorted_body_y:
            bline = body_lines[yk]
            has_amount = any(
                _clean_num(w["text"]) > 0 and ((w["x0"] + w["x1"]) / 2.0)
                >= column_boundaries.get("total_amount", {}).get("x0", doc_w * 0.7) for w in bline
            )
            if has_amount:
                y_end = max(w["y1"] for w in bline) + 4.0
                row_intervals.append((cur, y_end, str(len(row_intervals) + 1)))
                cur = y_end

    return {
        "header_cols": header_cols,
        "column_boundaries": column_boundaries,
        "row_intervals": row_intervals,
        "body_words": body_words,
    }


def _assign_words_to_cells(body_words, row_intervals, column_boundaries):
    grid = []
    for y_start, y_end, row_id in row_intervals:
        row_words = [w for w in body_words if y_start <= ((w["y0"] + w["y1"]) / 2.0) < y_end]
        cells = {col: [] for col in column_boundaries}
        for w in row_words:
            wcx = (w["x0"] + w["x1"]) / 2.0
            placed = False
            for col_name, c_info in column_boundaries.items():
                if c_info["x0"] <= wcx < c_info["x1"]:
                    cells[col_name].append(w)
                    placed = True
                    break
            if not placed and column_boundaries:
                nearest = min(column_boundaries.keys(), key=lambda n: abs(column_boundaries[n]["cx"] - wcx))
                cells[nearest].append(w)
        grid.append({"row_id": row_id, "cells": cells})
    return grid


# Column name -> header label our spatial grid engine renders in the grid
# output (kept for the tab-grid view / debugging).
_GRID_HEADERS = {
    "sno": "No.", "description": "Item name", "hsn_sac": "HSN/SAC",
    "quantity": "Qty", "unit": "Unit", "unit_price": "Rate",
    "discount": "Discount", "tax_rate": "Tax %",
    "taxable_value": "Taxable", "total_amount": "Amount",
    "cgst": "CGST", "sgst": "SGST", "igst": "IGST",
}

def _reconcile_line_item_math(qty, rate, tax, disc, tot):
    """Their deterministic cross-cell arithmetic repair (ported from
    cell_table_engine.reconcile_line_item_math): fixes OCR currency-prefix
    artefacts (₹55 -> 855), percentage digit-smash (12% -> 125), GST tax tiers
    (0/5/12/18/28) and joint qty/total disambiguation."""
    # 1. Normalize tax rate to Indian GST tiers.
    if tax > 35.0:
        if 115.0 <= tax <= 135.0:
            tax = 12.0
        elif 175.0 <= tax <= 195.0:
            tax = 18.0
        elif 270.0 <= tax <= 290.0:
            tax = 28.0
        elif 45.0 <= tax <= 55.0:
            tax = 5.0
        else:
            tax = min([0.0, 5.0, 12.0, 18.0, 28.0], key=lambda x: abs(x - (tax / 10.0 if tax > 40 else tax)))

    # 2. Stray currency prefix in Rate (₹ 55.00 -> 855.00).
    if qty > 0 and tot > 0:
        exp_wt = round(tot / (qty * (1.0 + tax / 100.0)), 2)
        exp_net = round(tot / qty, 2)
        rate_s = str(int(rate)) if rate > 0 else ""
        ewt_s = str(int(exp_wt)) if exp_wt > 0 else ""
        enet_s = str(int(exp_net)) if exp_net > 0 else ""
        if rate > 0 and ewt_s and len(rate_s) > len(ewt_s) and rate_s.endswith(ewt_s):
            rate = exp_wt
        elif rate > 0 and enet_s and len(rate_s) > len(enet_s) and rate_s.endswith(enet_s):
            rate = exp_net

    # 3. Stray currency prefix in Total (₹ 403.20 -> 7403.20).
    if qty > 0 and rate > 0:
        exp_wt = round(qty * rate * (1.0 + tax / 100.0), 2)
        exp_net = round(qty * rate, 2)
        tot_s = str(int(tot)) if tot > 0 else ""
        ewt_s = str(int(exp_wt)) if exp_wt > 0 else ""
        enet_s = str(int(exp_net)) if exp_net > 0 else ""
        if tot > 0 and ewt_s and len(tot_s) > len(ewt_s) and tot_s.endswith(ewt_s):
            tot = exp_wt
        elif tot > 0 and enet_s and len(tot_s) > len(enet_s) and tot_s.endswith(enet_s):
            tot = exp_net
        elif tot == 0.0:
            tot = exp_wt if tax > 0 else exp_net

    # 4. Joint Quantity & Total disambiguation.
    if rate > 0 and tot > 0:
        exp_wt = round(qty * rate * (1.0 + tax / 100.0), 2)
        exp_net = round(qty * rate, 2)
        mismatch = abs(tot - exp_wt) > 0.05 and abs(tot - exp_net) > 0.05
        if mismatch:
            tot_int = str(int(tot))
            for q_cand in range(1, 51):
                ewt = round(q_cand * rate * (1.0 + tax / 100.0), 2)
                enet = round(q_cand * rate, 2)
                ewt_s, enet_s = str(int(ewt)), str(int(enet))
                if len(tot_int) > len(ewt_s) and tot_int.endswith(ewt_s) and int(ewt_s) > 0:
                    tot, qty = ewt, float(q_cand)
                    break
                elif len(tot_int) > len(enet_s) and tot_int.endswith(enet_s) and int(enet_s) > 0:
                    tot, qty = enet, float(q_cand)
                    break
                elif abs(tot - ewt) < 0.05:
                    qty = float(q_cand)
                    break
                elif abs(tot - enet) < 0.05:
                    qty = float(q_cand)
                    break

    # 5. Infer missing quantity when rate & total are known.
    if (qty <= 0.0 or qty == 1.0) and rate > 0 and tot > 0:
        inf_wt = round(tot / (rate * (1.0 + tax / 100.0)), 2)
        inf_net = round(tot / rate, 2)
        if abs(inf_wt - round(inf_wt)) < 0.05 and 1.0 <= inf_wt <= 1000.0:
            qty = round(inf_wt, 2)
        elif abs(inf_net - round(inf_net)) < 0.05 and 1.0 <= inf_net <= 1000.0:
            qty = round(inf_net, 2)

    return qty, rate, tax, disc, tot


def _fmt_num(n):
    if not n:
        return ""
    return f"{round(n, 2):.2f}".rstrip("0").rstrip(".")


def _cells_to_line_items(grid, col_order):
    """Build our line-item schema dicts straight from the spatial cells using
    their full assembly: description line-clustering + cleanup, HSN regex, unit
    detection, and cross-cell arithmetic reconciliation."""
    items = []
    for r_idx, row in enumerate(grid):
        cells = row["cells"]

        def ct(col):
            ws = sorted(cells.get(col, []), key=lambda w: (w["y0"], w["x0"]))
            return " ".join(w["text"] for w in ws).strip()

        sno = row["row_id"] or ct("sno") or str(r_idx + 1)

        # Description: cluster words into lines, strip leading serial and
        # trailing payment/terms noise (their assembly).
        desc_words = sorted(cells.get("description", []), key=lambda w: (w["y0"], w["x0"]))
        if not desc_words and cells.get("sno"):
            spill = [w for w in cells["sno"] if not _re.match(r"^\d{1,3}[\.\s]?$", w["text"].strip())]
            if spill:
                desc_words = sorted(spill, key=lambda w: (w["y0"], w["x0"]))
        desc_lines = []
        if desc_words:
            curr = [desc_words[0]]
            for w in desc_words[1:]:
                prev_cy = (curr[-1]["y0"] + curr[-1]["y1"]) / 2.0
                cur_cy = (w["y0"] + w["y1"]) / 2.0
                h = max(curr[-1]["y1"] - curr[-1]["y0"], 10.0)
                if abs(cur_cy - prev_cy) < h * 0.55:
                    curr.append(w)
                else:
                    curr = sorted(curr, key=lambda it: it["x0"])
                    desc_lines.append(" ".join(it["text"] for it in curr).strip())
                    curr = [w]
            if curr:
                curr = sorted(curr, key=lambda it: it["x0"])
                desc_lines.append(" ".join(it["text"] for it in curr).strip())
        cleaned = []
        for l in desc_lines:
            cl = _re.sub(r"^[1-9]\d*[\.\s]+", "", l).strip()
            cl = _re.sub(r"\s*(?:Payment\s*details|Bank\s*details|Notes|Terms).*", "", cl, flags=_re.IGNORECASE).strip()
            if cl:
                cleaned.append(cl)
        item_name = " ".join(cleaned) if cleaned else f"Item #{sno}"

        hsn_m = _re.search(r"\b(\d{4,8})\b", ct("hsn_sac"))
        hsn = hsn_m.group(1) if hsn_m else ""

        qty_txt = ct("quantity")
        qty = _clean_num(qty_txt) or 1.0
        unit_raw = ct("unit")
        um = _re.search(r"\b(each|pcs|piece|pieces|nos|box|kg|mtr|sqft|set|units?)\b",
                        qty_txt + " " + unit_raw, _re.I)
        unit = um.group(1).lower() if um else ""

        rate = _clean_num(ct("unit_price"))
        disc = _clean_num(ct("discount"))
        tm = _re.search(r"(\d+(?:\.\d+)?)", ct("tax_rate"))
        tax = float(tm.group(1)) if tm else 18.0
        amount = _clean_num(ct("total_amount"))

        # Their cross-cell arithmetic reconciliation.
        qty, rate, tax, disc, amount = _reconcile_line_item_math(qty, rate, tax, disc, amount)

        item = {
            "serialNo": sno,
            "itemName": item_name,
            "hsnSac": hsn,
            "quantity": _fmt_num(qty),
            "unit": unit,
            "rate": _fmt_num(rate),
            "discount": f"{_fmt_num(disc)}%" if disc else "",
            "tax": f"{_fmt_num(tax)}%" if tax else "",
            "taxableValue": ct("taxable_value"),
            "cgstAmount": ct("cgst"),
            "sgstAmount": ct("sgst"),
            "igstAmount": ct("igst"),
            "amount": _fmt_num(amount),
            "grossAmount": "",
        }
        # Drop phantom placeholder-only rows.
        if item["itemName"].startswith("Item #") and not (item["rate"] or item["amount"] or item["hsnSac"]):
            continue
        if item["itemName"] or item["rate"] or item["amount"] or item["hsnSac"]:
            items.append(item)
    return items


def _spatial_extract(words, doc_w, doc_h):
    """Run the spatial cell-grid engine -> (tab-grid text, structured line items)."""
    structure = _detect_grid_structure(words, doc_w, doc_h)
    if not structure or not structure["row_intervals"]:
        return "", []
    col_order = list(structure["column_boundaries"].keys())
    grid = _assign_words_to_cells(structure["body_words"], structure["row_intervals"], structure["column_boundaries"])

    def cell_text(cells, col):
        return " ".join(w["text"] for w in sorted(cells.get(col, []), key=lambda w: (w["y0"], w["x0"]))).strip()

    header_line = "\t".join(_GRID_HEADERS.get(c, c) for c in col_order)
    lines = [header_line]
    for row in grid:
        lines.append("\t".join(cell_text(row["cells"], c) for c in col_order))
    return "\n".join(lines), _cells_to_line_items(grid, col_order)


def _items_score(items):
    """(numItems, cleanNumericItems) — pick the read with the most rows and
    the cleanest qty/rate/amount numbers."""
    clean = 0
    for it in items:
        q = _clean_num(it.get("quantity", ""))
        r = _clean_num(it.get("rate", ""))
        a = _clean_num(it.get("amount", ""))
        if q > 0 and r > 0 and a > 0:
            clean += 1
        elif r > 0 and a > 0:
            clean += 1
    return (len(items), clean)


def table_text(path):
    """Local table OCR -> (tab-separated grid, structured line items, EasyOCR
    full-page text). Their full ported system: spatial cell-grid slicing fed by
    EasyOCR (their engine) and RapidOCR (second opinion); we keep whichever
    read the table most completely and cleanly. The EasyOCR full-page text is a
    free byproduct (reuses the same word boxes) so the UI's raw-text preview
    matches invoice-ocr-c++."""
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

    for img in images:
        easy_words = _easy_words(img)
        full_text = _words_to_full_lines(easy_words)
        candidates = []  # (engine, line_items, grid_text, score)
        # Run both OCR engines concurrently (independent models/runtimes).
        with ThreadPoolExecutor(max_workers=2) as ex:
            f_easy = ex.submit(lambda: _spatial_extract(easy_words, img.width, img.height))
            f_rapid = ex.submit(lambda: _spatial_extract(_rapid_words(img), img.width, img.height))
            easy_text, easy_items = f_easy.result()
            rapid_text, rapid_items = f_rapid.result()
        if easy_items:
            candidates.append(("easyocr", easy_items, easy_text, _items_score(easy_items)))
        if rapid_items:
            candidates.append(("rapidocr", rapid_items, rapid_text, _items_score(rapid_items)))
        if candidates:
            # Most complete + cleanest read wins; on a tie prefer EasyOCR.
            candidates.sort(key=lambda c: (c[3][0], c[3][1], c[0] == "easyocr"))
            _, items, text, _ = candidates[-1]
            return text, len(images), items, full_text

    # No table detected by either engine.
    return "", 0, [], ""


if __name__ == "__main__":
    try:
        mode, path = sys.argv[1], sys.argv[2]
        if mode == "pdfplumber":
            text, pages = embedded_text(path)
            print(json.dumps({"ok": True, "text": text, "pages": pages}))
        elif mode == "image_ocr":
            text, pages = image_ocr(path)
            print(json.dumps({"ok": True, "text": text, "pages": pages}))
        elif mode == "table":
            text, pages, line_items, full_text = table_text(path)
            print(json.dumps({"ok": True, "text": text, "pages": pages, "lineItems": line_items, "fullText": full_text}))
        else:
            text, pages = ocr_text(path)
            print(json.dumps({"ok": True, "text": text, "pages": pages}))
    except Exception as error:
        print(json.dumps({"ok": False, "error": str(error)}))
