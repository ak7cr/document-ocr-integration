"""
Invoice Table Structure Engine — v2
====================================
Replaces fragile x-range keyword matching with a two-phase algorithm:

Phase 1 — Column Topology Discovery
  • Detects column separator x-positions via gap analysis on header words
  • Builds a Voronoi-style column map: each word is owned by its nearest separator

Phase 2 — Row Reconstruction
  • Identifies row boundaries using S.No sequences + right-side numeric presence
  • Slots every word into the correct column bucket using the topology map
  • Stitches multi-line descriptions without contaminating numeric columns
  • Reconciles Qty × Rate = Amount and fills missing fields

PaddleOCR word-level fallback (optional)
  • If EasyOCR word data is sparse (<5 tokens), attempts a PaddleOCR pass
  • PaddleOCR is lazy-loaded and cached so the first call pays the init cost
"""

import re
from decimal import Decimal
from typing import List, Dict, Any, Optional, Tuple
from backend.schemas import LineItem
from backend.services.validator import to_decimal


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------

def _clean_num(v: Any) -> float:
    if not v:
        return 0.0
    s = re.sub(r"[\$€£₹\s]|(?:\b|^)(?:Rs\.?|INR|USD|EUR|GBP)(?:\b|$)", "", str(v), flags=re.IGNORECASE).strip().replace(",", "")
    s = re.sub(r"\.$", "", s)
    try:
        return float(s)
    except ValueError:
        m = re.search(r"(\d+(?:\.\d+)?)", s)
        if m:
            try:
                return float(m.group(1))
            except ValueError:
                return 0.0
        return 0.0


def _is_numeric(text: str) -> bool:
    if not text:
        return False
    s = re.sub(r"[\$€£₹\s,]|(?:\b|^)(?:Rs\.?|INR|USD|EUR|GBP)(?:\b|$)", "", str(text), flags=re.IGNORECASE).strip()
    s = re.sub(r"\.$", "", s)
    try:
        float(s)
        return True
    except ValueError:
        return False


def _is_numeric_or_unit(text: str) -> bool:
    if not text:
        return False
    t = text.strip().lower()
    if t in ["piece", "pcs", "nos", "units", "box", "kg", "mtr", "sqft", "gm", "ml", "-", "na", "n/a", "nil", "₹", "$", "€", "£"]:
        return True
    return _is_numeric(text) or bool(re.search(r"^\d+[\.,]?\d*%$", t))


def group_words_into_lines(
    words: List[Dict[str, Any]],
    y_tolerance: float = 8.0
) -> List[List[Dict[str, Any]]]:
    """
    Groups OCR word tokens into horizontal text lines using y-center proximity.
    Tolerance adapts to font height automatically for both 72dpi PDF and 300dpi image coordinates.
    """
    if not words:
        return []

    sorted_w = sorted(
        words,
        key=lambda w: (((w.get("y0", 0) + w.get("y1", 0)) / 2.0), w.get("x0", 0))
    )

    lines: List[List[Dict[str, Any]]] = []
    for w in sorted_w:
        wy = (w.get("y0", 0) + w.get("y1", 0)) / 2.0
        h = max(w.get("y1", 0) - w.get("y0", 0), 8.0)
        tol = max(y_tolerance, h * 0.65)
        placed = False
        for line in reversed(lines):          # check most recent lines first (faster)
            ly = sum((it.get("y0", 0) + it.get("y1", 0)) / 2.0 for it in line) / len(line)
            if abs(wy - ly) <= tol:
                line.append(w)
                placed = True
                break
        if not placed:
            lines.append([w])

    for line in lines:
        line.sort(key=lambda it: it.get("x0", 0))
    lines.sort(key=lambda line: sum(it.get("y0", 0) for it in line) / len(line))
    return lines


# ---------------------------------------------------------------------------
# Phase 1 — Column topology discovery
# ---------------------------------------------------------------------------

_COL_HEADER_PATTERNS = {
    "sno":          [r"^s[\.\s]?no", r"^sr[\.\s]?no", r"^sl[\.\s]?no", r"^item\s*#", r"^#$", r"^no\.$"],
    "description":  [r"desc", r"particular", r"item", r"goods", r"product", r"service", r"narration"],
    "hsn_sac":      [r"hsn", r"sac", r"hsncode"],
    "quantity":     [r"^qty", r"^quantity", r"^qnty", r"^nos\.?$", r"^pcs\.?$", r"^units?$"],
    "unit":         [r"^unit$", r"^uom$", r"^per$", r"^u\.?m\.?$"],
    "unit_price":   [r"^rate", r"unit\s*price", r"price", r"mrp", r"net\s*price", r"cost", r"rate\s*\/"],
    "discount":     [r"disc", r"discount"],
    "tax_rate":     [r"gst\s*%?", r"tax\s*%?", r"igst\s*%?", r"cgst\s*%?", r"vat\s*%?", r"tax\s*rate", r"gst\s*rate", r"rate\s*%", r"rate\s*of\s*tax"],
    "tax_amount":   [r"tax\s*amt", r"gst\s*amt", r"igst\s*amt", r"cgst\s*amt"],
    "total_amount": [r"^amount", r"total\s*amount", r"gross", r"net\s*amount", r"taxable\s*value", r"taxable\s*amt", r"^total$", r"^value$"],
}


def _match_col_header(text: str) -> Optional[str]:
    t = text.strip().lower()
    # Priority order — specific to generic
    for col, patterns in _COL_HEADER_PATTERNS.items():
        for pat in patterns:
            if re.search(pat, t):
                return col
    return None


def _detect_column_topology(
    header_line: List[Dict[str, Any]],
    body_words: List[Dict[str, Any]],
    doc_width: float,
) -> Dict[str, Dict[str, float]]:
    """
    Builds a column topology map: {col_name: {x0, x1, cx}}.

    Strategy:
    1. Identify which header words map to which column name
    2. Use header word x-ranges as anchor points
    3. Fill gaps between adjacent anchors as column boundaries
    4. If no header found for a slot, infer from numeric x-distributions in body
    """
    named_cols: Dict[str, Dict[str, float]] = {}   # name -> {cx, x0, x1}

    # Step A: Match header words → column names
    for w in header_line:
        t_raw = w.get("text", "").strip()
        clean_t = re.sub(r"\(.*?\)", "", t_raw).strip()
        if not clean_t:
            clean_t = re.sub(r"[()]", "", t_raw).strip()
            if re.search(r"^(?:inc|incl|excl|tax|gst)$", clean_t, re.IGNORECASE):
                continue
        col = _match_col_header(clean_t)
        if col:
            # Do not overwrite already matched primary column anchors
            if col in named_cols:
                continue
            cx = (w.get("x0", 0) + w.get("x1", 0)) / 2.0
            named_cols[col] = {
                "cx": cx,
                "x0": float(w.get("x0", 0)),
                "x1": float(w.get("x1", 0)),
            }

    # Step B: Sort by cx
    ordered = sorted(named_cols.items(), key=lambda kv: kv[1]["cx"])

    # Step C: Assign boundaries — each column owns the space up to halfway to the next column
    result: Dict[str, Dict[str, float]] = {}
    for i, (name, info) in enumerate(ordered):
        prev_cx = ordered[i - 1][1]["cx"] if i > 0 else 0.0
        next_cx = ordered[i + 1][1]["cx"] if i < len(ordered) - 1 else doc_width

        left_boundary = (info["cx"] + prev_cx) / 2.0 if i > 0 else 0.0
        right_boundary = (info["cx"] + next_cx) / 2.0 if i < len(ordered) - 1 else doc_width

        result[name] = {
            "cx": info["cx"],
            "x0": left_boundary,
            "x1": right_boundary,
        }

    # Step D: If description column is missing, claim everything from sno to next column
    if "description" not in result and "sno" in result:
        sno_x1 = result["sno"]["x1"]
        next_x0 = min((v["x0"] for k, v in result.items() if k not in ("sno", "description") and v["x0"] > sno_x1), default=doc_width * 0.55)
        result["description"] = {"cx": (sno_x1 + next_x0) / 2.0, "x0": sno_x1, "x1": next_x0}

    # Step E: Ensure total_amount column covers the right edge
    if "total_amount" in result:
        result["total_amount"]["x1"] = doc_width

    return result


def _compute_subzones(
    topology: Dict[str, Dict[str, float]],
    doc_width: float
) -> Dict[str, Dict[str, float]]:
    """
    Partitions the table into 4 modular sub-tables / sub-zones:
      1. identity_zone:      [S.No, Description, HSN/SAC, Part #]
      2. quantity_unit_zone: [Quantity, Unit of Measure]
      3. pricing_zone:       [Rate/Unit Price, Discount, Taxable Value]
      4. tax_gross_zone:     [Tax/GST Rate %, Tax Amount, Gross Amount]
    """
    # 1. Identity Zone (S.No, Description, HSN)
    id_cols = [c for c in ["sno", "description", "hsn_sac", "part_number"] if c in topology]
    id_right = max((topology[c]["x1"] for c in id_cols), default=doc_width * 0.45)

    # 2. Quantity & Unit Zone
    qty_cols = [c for c in ["quantity", "unit"] if c in topology]
    qty_left = min((topology[c]["x0"] for c in qty_cols), default=id_right) if qty_cols else id_right
    qty_right = max((topology[c]["x1"] for c in qty_cols), default=qty_left + (doc_width * 0.15)) if qty_cols else id_right

    # 3. Pricing Zone (Rate, Discount, Taxable Value)
    price_cols = [c for c in ["unit_price", "discount", "taxable_value"] if c in topology]
    price_left = min((topology[c]["x0"] for c in price_cols), default=qty_right) if price_cols else qty_right
    price_right = max((topology[c]["x1"] for c in price_cols), default=price_left + (doc_width * 0.18)) if price_cols else qty_right

    # 4. Tax & Gross Amount Zone
    tax_cols = [c for c in ["tax_rate", "tax_amount", "total_amount"] if c in topology]
    tax_left = min((topology[c]["x0"] for c in tax_cols), default=price_right) if tax_cols else price_right
    tax_right = doc_width

    return {
        "identity_zone":      {"x0": 0.0, "x1": id_right, "columns": id_cols},
        "quantity_unit_zone": {"x0": qty_left, "x1": qty_right, "columns": qty_cols},
        "pricing_zone":       {"x0": price_left, "x1": price_right, "columns": price_cols},
        "tax_gross_zone":     {"x0": tax_left, "x1": tax_right, "columns": tax_cols},
    }


def _assign_col(word_cx: float, topology: Dict[str, Dict[str, float]]) -> Optional[str]:
    """
    Returns the column name whose x-range contains word_cx.
    Falls back to nearest column center if no range matches.
    """
    for name, info in topology.items():
        if info["x0"] <= word_cx < info["x1"]:
            return name

    # Fallback: nearest center distance
    if not topology:
        return None
    return min(topology.keys(), key=lambda n: abs(topology[n]["cx"] - word_cx))


# ---------------------------------------------------------------------------
# Phase 2 — Row reconstruction via Sub-Table Division
# ---------------------------------------------------------------------------

_SUMMARY_KEYWORDS = re.compile(
    r"sub\s*total|subtotal|total\s*amount|grand\s*total|taxable\s*value|"
    r"round\s*off|balance\s*due|amount\s*payable|amount\s*chargeable|"
    r"payment\s*details|bank\s*details|notes|terms\s*&?\s*conditions",
    re.IGNORECASE
)

_AMOUNT_RE = re.compile(r"^\d[\d,\s]*\.?\d{0,2}$")




def _locate_table_region(
    words_data: List[Dict[str, Any]],
    doc_height: float
) -> Tuple[float, float, int, List[Dict[str, Any]]]:
    """
    Returns (header_y0, summary_y0, header_line_idx, header_words).
    """
    lines = group_words_into_lines(words_data, y_tolerance=5.0)

    header_idx = -1
    header_y0 = 0.0
    header_line = []

    for idx, line in enumerate(lines):
        txt = " ".join(w.get("text", "") for w in line).upper()
        has_desc = any(k in txt for k in ["DESC", "ITEM", "PARTICULAR", "GOODS", "PRODUCT"])
        has_num  = any(k in txt for k in ["QTY", "QUANTITY", "RATE", "AMOUNT", "TOTAL", "PRICE"])
        if has_desc and has_num:
            header_idx = idx
            header_y0 = min(w.get("y0", 0) for w in line)
            header_line = line
            break

    if header_idx == -1:
        header_y0 = doc_height * 0.35

    summary_y0 = doc_height * 0.90
    for line in lines[(header_idx + 1 if header_idx >= 0 else 0):]:
        txt = " ".join(w.get("text", "") for w in line)
        if _SUMMARY_KEYWORDS.search(txt):
            y = min(w.get("y0", doc_height) for w in line)
            if y > header_y0 + 30:
                summary_y0 = y - 2.0
                break

    return header_y0, summary_y0, header_idx, header_line


# ---------------------------------------------------------------------------
# Public API — Modular Sub-Table Extraction Architecture
# ---------------------------------------------------------------------------

def extract_table_rows_structured(
    words_data: List[Dict[str, Any]],
    doc_width: float = 595.0,
    doc_height: float = 842.0,
) -> Tuple[List[LineItem], Dict[str, Any]]:
    """
    Multi-Zone Sub-Table Extractor.

    Deconstructs complex invoice tables into 4 dedicated sub-tables:
      1. Item Identity Sub-Table: S.No, Description, HSN/SAC, Part Numbers
      2. Quantity & Unit Sub-Table: Quantity, Measurement Units (pcs, nos, kg)
      3. Pricing Sub-Table: Unit Rate, Discount, Pre-tax Base
      4. Taxation & Gross Sub-Table: GST Rate %, Tax Amount, Line Gross Total (inc. tax)
    """
    if not words_data:
        return [], {"status": "NO_WORDS"}

    # Attempt 2D Cell Grid Matrix & Semantic Header Extraction First
    try:
        from backend.services.cell_table_engine import extract_cell_based_line_items
        cell_items, cell_meta = extract_cell_based_line_items(words_data, doc_width, doc_height)
        if cell_items and len(cell_items) >= 1:
            return cell_items, cell_meta
    except Exception as ce_err:
        print(f"ℹ️ [Cell Table Engine] Notice: {ce_err}")

    header_y0, summary_y0, header_idx, header_line = _locate_table_region(words_data, doc_height)

    if not header_line:
        return [], {"status": "HEADER_NOT_FOUND"}

    topology = _detect_column_topology(header_line, words_data, doc_width)
    if not topology:
        return [], {"status": "NO_TOPOLOGY"}

    subzones = _compute_subzones(topology, doc_width)
    header_y1 = max(w.get("y1", header_y0 + 15) for w in header_line)

    # Filter body words (between header bottom and summary top)
    body_words = [
        w for w in words_data
        if w.get("y0", 0) > header_y1 - 2.0 and w.get("y0", 0) < summary_y0
    ]
    body_lines = group_words_into_lines(body_words, y_tolerance=5.0)

    # --- Row boundary detection across sub-tables ---
    right_threshold = topology.get("total_amount", {}).get("x0", doc_width * 0.65)
    rate_threshold = topology.get("unit_price", {}).get("x0", doc_width * 0.50)

    rows: List[Dict[str, List[Dict]]] = []      # each element = {col_name: [words]}
    current_row: Optional[Dict[str, List[Dict]]] = None
    expected_sno = 1

    for line in body_lines:
        if not line:
            continue
        txt = " ".join(w.get("text", "") for w in line).strip()
        if not txt:
            continue

        # Skip pure header repetitions
        upper = txt.upper()
        if any(k in upper for k in ["DESCRIPTION", "S.NO", "S NO", "ITEMS", "PARTICULARS"]):
            if _match_col_header(line[0].get("text", "")):
                continue

    # Check if this document has an explicit S.NO numbering column
    sno_max_x = (topology.get("sno", {}).get("x1", doc_width * 0.10) + (doc_width * 0.03)) if "sno" in topology else (doc_width * 0.12)
    has_sno_in_table = any(
        any(
            w.get("text", "").strip().replace(".", "").isdigit() 
            and int(w.get("text", "").strip().replace(".", "")) == 1 
            and ((w.get("x0", 0) + w.get("x1", 0)) / 2.0) <= sno_max_x 
            for w in l
        )
        for l in body_lines
    )

    for line in body_lines:
        if not line:
            continue
        txt = " ".join(w.get("text", "") for w in line).strip()
        if not txt:
            continue

        # Skip pure header repetitions
        upper = txt.upper()
        if any(k in upper for k in ["DESCRIPTION", "S.NO", "S NO", "ITEMS", "PARTICULARS"]):
            if _match_col_header(line[0].get("text", "")):
                continue

        # Detect S.NO. in the line
        is_sno = False
        sno_val = None
        for w in line:
            wcx = (w.get("x0", 0) + w.get("x1", 0)) / 2.0
            wtxt = w.get("text", "").strip().replace(".", "")
            if wcx <= sno_max_x and wtxt.isdigit() and 1 <= int(wtxt) <= 500:
                v = int(wtxt)
                if v == expected_sno or (expected_sno == 1 and v in (1, 2)):
                    is_sno = True
                    sno_val = v
                    expected_sno = v + 1
                    break

        has_existing_amount = bool(
            current_row and any(_is_numeric(w.get("text", "")) for w in current_row.get("total_amount", []))
        )
        has_existing_rate = bool(
            current_row and any(_is_numeric(w.get("text", "")) for w in current_row.get("unit_price", []))
        )
        has_line_amount = any(
            _is_numeric(w.get("text", ""))
            and (w.get("x0", 0) + w.get("x1", 0)) / 2.0 >= right_threshold
            for w in line
        )
        rate_x0 = topology.get("unit_price", {}).get("x0", rate_threshold)
        has_line_rate = any(
            _is_numeric(w.get("text", ""))
            and rate_x0 <= (w.get("x0", 0) + w.get("x1", 0)) / 2.0 < right_threshold
            for w in line
        ) if "unit_price" in topology else False

        if has_sno_in_table:
            starts_new_row = is_sno
        else:
            starts_new_row = is_sno or (has_line_amount and (current_row is None or has_existing_amount)) or (has_line_rate and (current_row is None or has_existing_rate))

        if starts_new_row:
            if current_row:
                rows.append(current_row)
            current_row = {col: [] for col in topology}

        if current_row is None:
            if has_line_amount or has_line_rate or is_sno:
                current_row = {col: [] for col in topology}
            else:
                continue

        # Slot words into column buckets
        for w in line:
            wcx = (w.get("x0", 0) + w.get("x1", 0)) / 2.0
            col = _assign_col(wcx, topology)
            if col:
                # Guard: Do not place non-numeric words into purely numeric columns
                if col in ("quantity", "unit_price", "tax_rate", "total_amount", "discount") and not _is_numeric_or_unit(w.get("text", "")):
                    if "description" in current_row:
                        current_row["description"].append(w)
                    continue
                current_row[col].append(w)

    if current_row:
        rows.append(current_row)

    # --- Build LineItems by extracting from each sub-table zone ---
    items: List[LineItem] = []
    for row_idx, row in enumerate(rows):
        def col_text(name: str) -> str:
            ws = sorted(row.get(name, []), key=lambda w: (w.get("y0", 0), w.get("x0", 0)))
            return " ".join(w.get("text", "") for w in ws).strip()

        def col_num(name: str) -> float:
            return _clean_num(col_text(name))

        # 1. Sub-Table: Identity Zone
        sno_raw  = col_text("sno") or str(row_idx + 1)
        desc     = col_text("description").strip()
        hsn_txt  = col_text("hsn_sac")
        
        desc = re.sub(r"^[1-9]\d*[\.\s]+", "", desc).strip()
        desc = re.sub(r"\s*(?:Payment\s*details|Bank\s*details|Notes|Terms).*", "", desc, flags=re.IGNORECASE).strip()
        if not desc:
            desc = f"Item #{sno_raw}"

        hsn_m = re.search(r"\b(\d{4,8})\b", hsn_txt)
        hsn = hsn_m.group(1) if hsn_m else None

        # 2. Sub-Table: Quantity & Units Zone
        qty_txt = col_text("quantity")
        qty = col_num("quantity") or 1.0
        unit_m = re.search(r"\b(each|pcs|piece|pieces|nos|box|kg|mtr|sqft|set|units?)\b", qty_txt + " " + col_text("unit"), re.IGNORECASE)
        unit = unit_m.group(1).lower() if unit_m else "pcs"

        # 3. Sub-Table: Pricing Zone
        rate = col_num("unit_price")
        disc = col_num("discount")

        # 4. Sub-Table: Tax & Gross Amount Zone
        tax_txt = col_text("tax_rate")
        tax_m = re.search(r"(\d+(?:\.\d+)?)", tax_txt)
        tax = float(tax_m.group(1)) if tax_m else 0.0
        amt = col_num("total_amount")

        # Cross-Subtable Spatial & Numeric Reconciliation
        if rate == 0.0 or amt == 0.0:
            row_all_words = sorted([w for ws in row.values() for w in ws], key=lambda w: w.get("x0", 0))
            row_nums = [_clean_num(w.get("text", "")) for w in row_all_words if _is_numeric(w.get("text", ""))]
            row_nums = [n for n in row_nums if n > 0.0]
            
            num_cands = [n for n in row_nums if not (hsn and str(int(n)) == hsn)]
            if len(num_cands) >= 3:
                if qty == 1.0 and num_cands[0] <= 1000.0:
                    qty = num_cands[0]
                if rate == 0.0:
                    rate = num_cands[-2] if num_cands[-1] > num_cands[-2] else num_cands[1]
                if amt == 0.0:
                    amt = num_cands[-1]
            elif len(num_cands) == 2:
                if rate == 0.0: rate = min(num_cands)
                if amt == 0.0: amt = max(num_cands)

        # Cross-Subtable Mathematical Harmonization (Pre-tax Base vs Gross Incl. Tax)
        expected_base = round(qty * rate, 2)
        if expected_base > 0:
            if amt == 0.0:
                if tax > 0:
                    amt = round(expected_base * (1.0 + (tax / 100.0)), 2)
                else:
                    amt = expected_base
            elif rate == 0.0 and amt > 0 and qty > 0:
                if tax > 0 and abs(amt - round(amt / (1.0 + (tax / 100.0)), 2)) > 1.0:
                    rate = round((amt / (1.0 + (tax / 100.0))) / qty, 2)
                else:
                    rate = round(amt / qty, 2)

        items.append(LineItem(
            sno=sno_raw,
            description=desc,
            hsn_sac=hsn,
            quantity=qty,
            unit=unit,
            unit_price=rate,
            discount=disc,
            tax_rate=tax,
            total_amount=amt,
        ))

    return items, {
        "status": "SUCCESS" if items else "NO_ROWS",
        "header_cols": {k: {"cx": v["cx"], "x0": v["x0"], "x1": v["x1"]} for k, v in topology.items()},
        "subzones": subzones,
        "rows_count": len(items),
        "header_y0": header_y0,
        "header_y1": header_y1,
        "table_end_y": summary_y0,
        "engine": "modular_subzones_v2",
    }


# ---------------------------------------------------------------------------
# PaddleOCR word-level fallback extractor
# ---------------------------------------------------------------------------

_paddle_reader = None
_paddle_tried = False


def _get_paddle_reader():
    """
    Lazy-load PaddleOCR. Cached after first use so subsequent calls
    are free of init overhead.
    """
    global _paddle_reader, _paddle_tried
    if not _paddle_tried:
        _paddle_tried = True
        import sys
        if sys.version_info >= (3, 13):
            _paddle_reader = None
            return None
        try:
            from paddleocr import PaddleOCR
            _paddle_reader = PaddleOCR(lang="en")
        except Exception:
            _paddle_reader = None
    return _paddle_reader


def extract_words_with_paddle(pil_img) -> List[Dict[str, Any]]:
    """
    Runs PaddleOCR on a PIL image and returns words_data compatible with
    the rest of the pipeline (same dict shape as EasyOCR output).
    """
    reader = _get_paddle_reader()
    if reader is None:
        return []

    import numpy as np
    img_np = np.array(pil_img.convert("RGB"))
    try:
        results = reader.ocr(img_np, cls=True)
    except Exception as exc:
        print(f"[PaddleOCR] OCR failed: {exc}")
        return []

    words: List[Dict[str, Any]] = []
    if not results:
        return words

    for page in results:
        if not page:
            continue
        for item in page:
            bbox_pts, (text, conf) = item
            xs = [p[0] for p in bbox_pts]
            ys = [p[1] for p in bbox_pts]
            if text.strip():
                words.append({
                    "text": text.strip(),
                    "x0": float(min(xs)),
                    "y0": float(min(ys)),
                    "x1": float(max(xs)),
                    "y1": float(max(ys)),
                    "confidence": float(conf),
                })
    return words


def detect_table_column_boundaries(
    header_line: List[Dict[str, Any]],
    doc_width: float = 595.0,
) -> Dict[str, Dict[str, float]]:
    """
    Legacy compat: delegate to new topology engine.
    Used by template_engine.py which imports this name.
    """
    topo = _detect_column_topology(header_line, [], doc_width)
    return {k: {"x0": v["x0"], "x1": v["x1"]} for k, v in topo.items()}

