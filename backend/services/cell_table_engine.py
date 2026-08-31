import re
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from typing import List, Dict, Any, Tuple, Optional
from backend.schemas import LineItem, InvoiceMetadata, PartyDetails, InvoiceSummary, TaxDetail, InvoiceData, PaymentInfo, BoundingBox

MONEY_QUANTUM = Decimal("0.01")

HEADER_MAP = {
    "sno":           [r"^s[\.\s]?no", r"^sr[\.\s]?no", r"^sl[\.\s]?no", r"^item\s*#", r"^#$", r"^no\.$", r"^sno"],
    "description":   [r"desc", r"particular", r"item", r"goods", r"product", r"service", r"narration"],
    "hsn_sac":       [r"hsn", r"sac", r"hsncode"],
    "quantity":      [r"^qt[yxa]", r"quant", r"^qnty", r"^nos\.?$", r"^pcs\.?$", r"^units?$"],
    "unit":          [r"^unit", r"^uom", r"^per", r"^u\.?m\.?"],
    "unit_price":    [r"^rate", r"unit\s*price", r"price", r"mrp", r"net\s*price", r"cost", r"rate\s*\/"],
    "discount":      [r"disc", r"discount"],
    "tax_rate":      [r"tax\s*%", r"gst\s*%", r"igst\s*%", r"cgst\s*%", r"vat\s*%", r"tax\s*rate", r"rate\s*%", r"rate\s*of\s*tax", r"^tax\b"],
    "taxable_value": [r"taxable\s*value", r"taxable\s*amt", r"net\s*worth", r"net\s*amount"],
    "total_amount":  [r"^amount", r"total\s*amount", r"gross", r"net\s*amount", r"amount\s*\(inc", r"^total$", r"^value$"]
}


def clean_num(val_str: Any) -> float:
    if not val_str:
        return 0.0
    s = str(val_str).strip()
    # Strip any leading/trailing currency or punctuation artefacts: { } * % ~ [ ] ( ) :
    s = re.sub(r"[^\d\.,]", "", s).strip()
    if not s:
        return 0.0
    if "." in s and "," in s:
        s = s.replace(",", "")
    elif "," in s and "." not in s:
        parts = s.split(",")
        if len(parts) == 2 and len(parts[1]) <= 2:
            s = parts[0] + "." + parts[1]
        else:
            s = s.replace(",", "")
    s = s.rstrip(".")
    try:
        return float(s)
    except ValueError:
        m = re.search(r"(\d+(?:\.\d+)?)", s)
        return float(m.group(1)) if m else 0.0


def reconcile_line_item_math(
    qty: float,
    rate: float,
    tax: float,
    disc: float,
    tot: float
) -> Tuple[float, float, float, float, float]:
    """
    Auto-corrects OCR currency artefacts (e.g. ₹ read as 8 or {7) and percentage artefacts (12% read as 125)
    using deterministic cross-cell arithmetic reconciliation.
    """
    # 1. Normalize tax rate (India GST tiers: 0%, 5%, 12%, 18%, 28%)
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
            tiers = [0.0, 5.0, 12.0, 18.0, 28.0]
            tax = min(tiers, key=lambda x: abs(x - (tax / 10.0 if tax > 40 else tax)))

    # 2. Auto-correct Stray Currency Prefix in Rate (e.g. ₹ 55.00 -> 855.00)
    if qty > 0 and tot > 0:
        expected_rate_with_tax = round(tot / (qty * (1.0 + (tax / 100.0))), 2)
        expected_rate_net = round(tot / qty, 2)
        
        rate_str = str(int(rate)) if rate > 0 else ""
        exp_wt_str = str(int(expected_rate_with_tax)) if expected_rate_with_tax > 0 else ""
        exp_net_str = str(int(expected_rate_net)) if expected_rate_net > 0 else ""
        
        if rate > 0 and exp_wt_str and len(rate_str) > len(exp_wt_str) and rate_str.endswith(exp_wt_str):
            rate = expected_rate_with_tax
        elif rate > 0 and exp_net_str and len(rate_str) > len(exp_net_str) and rate_str.endswith(exp_net_str):
            rate = expected_rate_net

    # 3. Auto-correct Stray Currency Prefix in Total Amount (e.g. ₹ 403.20 -> 7403.20)
    if qty > 0 and rate > 0:
        expected_tot_with_tax = round(qty * rate * (1.0 + (tax / 100.0)), 2)
        expected_tot_net = round(qty * rate, 2)
        
        tot_str = str(int(tot)) if tot > 0 else ""
        exp_tot_wt_str = str(int(expected_tot_with_tax)) if expected_tot_with_tax > 0 else ""
        exp_tot_net_str = str(int(expected_tot_net)) if expected_tot_net > 0 else ""
        
        if tot > 0 and exp_tot_wt_str and len(tot_str) > len(exp_tot_wt_str) and tot_str.endswith(exp_tot_wt_str):
            tot = expected_tot_with_tax
        elif tot > 0 and exp_tot_net_str and len(tot_str) > len(exp_tot_net_str) and tot_str.endswith(exp_tot_net_str):
            tot = expected_tot_net
        elif tot == 0.0:
            tot = expected_tot_with_tax if tax > 0 else expected_tot_net

    # 4. Joint Quantity & Total Disambiguation (e.g. rate 60, total 7403.20 -> qty 6, total 403.20)
    if rate > 0 and tot > 0:
        exp_current_wt = round(qty * rate * (1.0 + (tax / 100.0)), 2)
        exp_current_net = round(qty * rate, 2)
        mismatch = (abs(tot - exp_current_wt) > 0.05 and abs(tot - exp_current_net) > 0.05)
        
        if mismatch:
            tot_int_str = str(int(tot))
            for q_cand in range(1, 51):
                exp_wt = round(q_cand * rate * (1.0 + (tax / 100.0)), 2)
                exp_net = round(q_cand * rate, 2)
                exp_wt_str = str(int(exp_wt))
                exp_net_str = str(int(exp_net))

                if len(tot_int_str) > len(exp_wt_str) and tot_int_str.endswith(exp_wt_str) and int(exp_wt_str) > 0:
                    tot = exp_wt
                    qty = float(q_cand)
                    break
                elif len(tot_int_str) > len(exp_net_str) and tot_int_str.endswith(exp_net_str) and int(exp_net_str) > 0:
                    tot = exp_net
                    qty = float(q_cand)
                    break
                elif abs(tot - exp_wt) < 0.05:
                    qty = float(q_cand)
                    break
                elif abs(tot - exp_net) < 0.05:
                    qty = float(q_cand)
                    break

    # 5. Infer Missing Quantity if Rate & Total are known and valid
    if (qty <= 0.0 or qty == 1.0) and rate > 0 and tot > 0:
        inferred_qty_wt = round(tot / (rate * (1.0 + (tax / 100.0))), 2)
        inferred_qty_net = round(tot / rate, 2)
        
        if abs(inferred_qty_wt - round(inferred_qty_wt)) < 0.05 and 1.0 <= inferred_qty_wt <= 1000.0:
            qty = round(inferred_qty_wt, 2)
        elif abs(inferred_qty_net - round(inferred_qty_net)) < 0.05 and 1.0 <= inferred_qty_net <= 1000.0:
            qty = round(inferred_qty_net, 2)

    return qty, rate, tax, disc, tot


def match_header_column(text: str) -> Optional[str]:
    t = text.strip().lower()
    # Clean parentheticals (e.g. 'AMOUNT (inc. tax)' -> 'AMOUNT')
    clean_t = re.sub(r"\(.*?\)", "", t).strip()
    if not clean_t:
        clean_t = re.sub(r"[()]", "", t).strip()
        if re.search(r"^(?:inc|incl|excl|tax|gst)$", clean_t, re.IGNORECASE):
            return None

    for col_key, patterns in HEADER_MAP.items():
        for pat in patterns:
            if re.search(pat, clean_t):
                return col_key
    return None


def detect_table_grid_structure(
    words_data: List[Dict[str, Any]],
    doc_width: float = 595.0,
    doc_height: float = 842.0
) -> Optional[Dict[str, Any]]:
    """
    Detects table header row, column horizontal intervals, and row vertical boundaries.
    """
    if not words_data:
        return None

    # 1. Cluster words into horizontal lines
    lines_dict: Dict[int, List[Dict[str, Any]]] = {}
    for w in words_data:
        y_center = (w["y0"] + w["y1"]) / 2.0
        y_key = round(y_center / 14.0) * 14
        lines_dict.setdefault(y_key, []).append(w)

    sorted_y_keys = sorted(lines_dict.keys())

    # 2. Identify Table Header Row
    header_idx = -1
    header_y0 = 0.0
    header_line: List[Dict[str, Any]] = []
    header_cols: Dict[str, Dict[str, float]] = {}

    for idx, yk in enumerate(sorted_y_keys):
        line = sorted(lines_dict[yk], key=lambda w: w["x0"])
        matched = {}
        for w in line:
            col = match_header_column(w["text"])
            if col and col not in matched:
                cx = (w["x0"] + w["x1"]) / 2.0
                matched[col] = {
                    "cx": cx,
                    "x0": float(w["x0"]),
                    "x1": float(w["x1"]),
                    "word": w
                }
        if len(matched) >= 3 and ("description" in matched or "sno" in matched or "quantity" in matched):
            header_idx = idx
            header_y0 = min(w.get("y0", 0) for w in line)
            header_line = line
            header_cols = matched
            break

    if not header_cols:
        return None

    # 3. Calculate 2D Column Bounding Ranges (Strict Disjoint Partitioning)
    col_centers = {name: info["cx"] for name, info in header_cols.items()}

    # If description is missing, inject between sno and hsn_sac (or first after sno)
    if "description" not in col_centers:
        sno_cx = col_centers.get("sno", doc_width * 0.05)
        next_cx = min((cx for name, cx in col_centers.items() if name != "sno" and cx > sno_cx), default=doc_width * 0.35)
        col_centers["description"] = (sno_cx + next_cx) / 2.0

    # If total_amount is missing, inject at right edge
    if "total_amount" not in col_centers:
        max_cx = max(col_centers.values(), default=doc_width * 0.80)
        col_centers["total_amount"] = (max_cx + doc_width) / 2.0

    # Sort all columns strictly left to right
    sorted_col_items = sorted(col_centers.items(), key=lambda kv: kv[1])

    column_boundaries: Dict[str, Dict[str, float]] = {}
    for i, (name, cx) in enumerate(sorted_col_items):
        left_b = 0.0 if i == 0 else (cx + sorted_col_items[i - 1][1]) / 2.0
        right_b = doc_width if i == len(sorted_col_items) - 1 else (cx + sorted_col_items[i + 1][1]) / 2.0

        column_boundaries[name] = {
            "x0": left_b,
            "x1": right_b,
            "cx": cx
        }

    # Ensure S.No is narrow so Description captures full product name
    if "sno" in column_boundaries:
        sno_cut = min(column_boundaries["sno"]["x1"], doc_width * 0.06)
        column_boundaries["sno"]["x1"] = sno_cut
        if "description" in column_boundaries:
            column_boundaries["description"]["x0"] = sno_cut

    # 4. Detect Table Top & Summary Bottom
    header_y1 = max(w.get("y1", w.get("y0", 0) + 15) for w in header_line)

    summary_y0 = doc_height * 0.92
    _SUMMARY_PATTERNS = re.compile(
        r"sub\s*total|subtotal|total\s*amount|grand\s*total|taxable\s*value|"
        r"total\s*tax\s*amount|cgst|sgst|terms\s*&?\s*conditions|notes",
        re.IGNORECASE
    )

    for yk in sorted_y_keys[(header_idx + 1 if header_idx >= 0 else 0):]:
        line = lines_dict[yk]
        txt = " ".join(w["text"] for w in line)
        if _SUMMARY_PATTERNS.search(txt):
            min_y = min(w["y0"] for w in line)
            if min_y > header_y1 + 25.0:
                summary_y0 = min_y - 2.0
                break

    # 5. Extract Table Body Words
    body_words = [
        w for w in words_data
        if w.get("y0", 0) >= (header_y1 - 2.0) and w.get("y0", 0) < summary_y0
    ]

    # 6. Detect Logical Row Vertical Boundaries using multi-anchor discovery:
    # A new row starts on: (1) S.NO digit, (2) HSN code, or (3) Quantity+Unit entry
    sno_info = column_boundaries.get("sno", {})
    hsn_info = column_boundaries.get("hsn_sac", {})
    qty_info = column_boundaries.get("quantity", {})

    sno_max_x = sno_info.get("x1", doc_width * 0.12) + 15.0 if sno_info else doc_width * 0.12
    hsn_x0 = hsn_info.get("x0", doc_width * 0.30) if hsn_info else doc_width * 0.30
    hsn_x1 = hsn_info.get("x1", doc_width * 0.48) if hsn_info else doc_width * 0.48
    qty_x0 = qty_info.get("x0", doc_width * 0.42) if qty_info else doc_width * 0.42
    qty_x1 = qty_info.get("x1", doc_width * 0.58) if qty_info else doc_width * 0.58

    # Cluster body lines
    body_lines_dict: Dict[int, List[Dict[str, Any]]] = {}
    for w in body_words:
        yk = round(((w["y0"] + w["y1"]) / 2.0) / 8.0) * 8
        body_lines_dict.setdefault(yk, []).append(w)

    sorted_body_y = sorted(body_lines_dict.keys())
    row_start_y_list: List[float] = []

    for yk in sorted_body_y:
        bline = sorted(body_lines_dict[yk], key=lambda w: w["x0"])
        has_sno = any(
            w["text"].strip().replace(".", "").isdigit() 
            and 1 <= int(w["text"].strip().replace(".", "")) <= 200 
            and ((w["x0"] + w["x1"]) / 2.0) <= sno_max_x 
            for w in bline
        )
        has_hsn = any(
            re.match(r"^\d{4,8}$", w["text"].strip()) 
            and hsn_x0 <= ((w["x0"] + w["x1"]) / 2.0) <= hsn_x1 
            for w in bline
        )
        has_qty_unit = any(
            re.match(r"^(?:piece|pcs|nos|box|kg|mtr|units?)$", w["text"].strip(), re.I)
            and qty_x0 <= ((w["x0"] + w["x1"]) / 2.0) <= (qty_x1 + 30.0)
            for w in bline
        )

        if has_sno or has_hsn or has_qty_unit:
            min_y = min(w["y0"] for w in bline)
            # Ensure at least 15px separation between row starts
            if not row_start_y_list or (min_y - row_start_y_list[-1]) >= 15.0:
                row_start_y_list.append(min_y)

    row_intervals: List[Tuple[float, float, str]] = []
    if row_start_y_list:
        for idx, y_s in enumerate(row_start_y_list):
            y_start = max(header_y1, y_s - 4.0)
            y_end = row_start_y_list[idx + 1] - 4.0 if idx + 1 < len(row_start_y_list) else summary_y0
            row_intervals.append((y_start, y_end, str(idx + 1)))
    else:
        # Fallback to rate/amount presence if anchors are sparse
        current_y_start = header_y1
        for yk in sorted_body_y:
            bline = body_lines_dict[yk]
            has_amount = any(
                clean_num(w["text"]) > 0 and ((w["x0"] + w["x1"]) / 2.0) >= column_boundaries.get("total_amount", {}).get("x0", doc_width * 0.7)
                for w in bline
            )
            if has_amount:
                y_end = max(w["y1"] for w in bline) + 4.0
                row_intervals.append((current_y_start, y_end, str(len(row_intervals) + 1)))
                current_y_start = y_end

    return {
        "header_y0": header_y0,
        "header_y1": header_y1,
        "summary_y0": summary_y0,
        "column_boundaries": column_boundaries,
        "row_intervals": row_intervals,
        "body_words": body_words
    }


def assign_words_to_cells(
    body_words: List[Dict[str, Any]],
    row_intervals: List[Tuple[float, float, str]],
    column_boundaries: Dict[str, Dict[str, float]]
) -> List[Dict[str, Any]]:
    """
    Constructs a 2D Cell Grid and assigns words to each (row, col) cell.
    """
    grid_rows: List[Dict[str, Any]] = []

    for y_start, y_end, row_id in row_intervals:
        row_words = [w for w in body_words if y_start <= ((w["y0"] + w["y1"]) / 2.0) < y_end]
        cell_dict: Dict[str, List[Dict[str, Any]]] = {col: [] for col in column_boundaries}

        for w in row_words:
            wcx = (w["x0"] + w["x1"]) / 2.0
            # Find matching column
            placed = False
            for col_name, c_info in column_boundaries.items():
                if c_info["x0"] <= wcx < c_info["x1"]:
                    cell_dict[col_name].append(w)
                    placed = True
                    break
            if not placed and column_boundaries:
                # Nearest column fallback
                nearest = min(column_boundaries.keys(), key=lambda n: abs(column_boundaries[n]["cx"] - wcx))
                cell_dict[nearest].append(w)

        grid_rows.append({
            "row_id": row_id,
            "y0": y_start,
            "y1": y_end,
            "cells": cell_dict
        })

    return grid_rows


import importlib

_CPP_CORE = None
for mod_name in [
    "backend.services.Release.ocr_cpp_core",
    "backend.services.ocr_cpp_core",
    "ocr_cpp_core"
]:
    try:
        _CPP_CORE = importlib.import_module(mod_name)
        if _CPP_CORE is not None:
            break
    except Exception:
        continue

_CPP_CORE_AVAILABLE = _CPP_CORE is not None


def extract_cell_based_line_items(
    words_data: List[Dict[str, Any]],
    doc_width: float = 595.0,
    doc_height: float = 842.0
) -> Tuple[List[LineItem], Dict[str, Any]]:
    """
    Extracts high-precision LineItem objects using 2D Cell Grid Matrix & Semantic Header Mapping.
    Uses C++ native core (ocr_cpp_core) when compiled, with seamless Python fallback.
    """
    if _CPP_CORE_AVAILABLE and _CPP_CORE is not None:
        try:
            cpp_res = _CPP_CORE.extract_table_cells(words_data, float(doc_width), float(doc_height))
            cpp_items = cpp_res.get("line_items", [])
            if cpp_items:
                items: List[LineItem] = []
                for it in cpp_items:
                    desc_full = (it.get("description") or "").strip()
                    parts = desc_full.split("\n") if desc_full else []
                    i_name = parts[0].strip() if parts else ""
                    i_desc = "\n".join(parts[1:]).strip() if len(parts) > 1 else None
                    tot = float(it.get("total_amount", 0.0))
                    rate = float(it.get("unit_price", 0.0))
                    qty = float(it.get("quantity", 1.0))
                    
                    # Filter phantom empty rows (0 price, 0 total, placeholder name)
                    if tot <= 0.0 and rate <= 0.0 and (not i_name or i_name.startswith("Item #")):
                        continue

                    # Auto-correct OCR currency & percentage artefacts
                    tax_val = float(it.get("tax_rate", 18.0))
                    disc_val = float(it.get("discount", 0.0))
                    qty, rate, tax_val, disc_val, tot = reconcile_line_item_math(qty, rate, tax_val, disc_val, tot)

                    items.append(LineItem(
                        sno=str(len(items) + 1),
                        item_name=i_name or f"Item #{len(items) + 1}",
                        description=i_desc,
                        hsn_sac=it.get("hsn_sac") if it.get("hsn_sac") != "-" else None,
                        quantity=qty,
                        unit=it.get("unit", "piece"),
                        unit_price=rate,
                        discount=disc_val,
                        tax_rate=tax_val,
                        taxable_value=float(it.get("taxable_value", 0.0)),
                        total_amount=tot,
                    ))

                # Only accept C++ results if ALL rows have meaningful product names
                all_have_real_names = all(it.item_name and not it.item_name.startswith("Item #") for it in items)
                if items and all_have_real_names:
                    print(f"   ⚡ [C++ Native Core] Extracted {len(items)} line items in {cpp_res.get('latency_ms', 0.0):.2f}ms:")
                    for it in items:
                        sub_info = f" (Specs: {it.description[:30]}..)" if it.description else ""
                        print(f"      • Item #{it.sno}: '{it.item_name}'{sub_info} | HSN: {it.hsn_sac or '-'} | Qty: {it.quantity} | Rate: {it.unit_price} | Tax: {it.tax_rate}% | Total: {it.total_amount}")
                    return items, {
                        "status": "SUCCESS",
                        "engine": "C++_CellMatrix_v1",
                        "latency_ms": cpp_res.get("latency_ms", 0.0)
                    }
                else:
                    print(f"   ℹ️ [C++ Core] Output contained placeholder names. Routing to Python 2D Cell Grid parser...")
        except Exception as exc:
            print(f"   ⚠️ [C++ Core] Call failed, using Python engine: {exc}")

    grid_structure = detect_table_grid_structure(words_data, doc_width, doc_height)
    if not grid_structure or not grid_structure.get("row_intervals"):
        return [], {"status": "STRUCTURE_NOT_DETECTED"}

    column_boundaries = grid_structure["column_boundaries"]
    row_intervals = grid_structure["row_intervals"]
    body_words = grid_structure["body_words"]

    print(f"   📊 [2D Cell Grid Engine] Detected {len(column_boundaries)} table columns: {list(column_boundaries.keys())}")
    print(f"   📦 [2D Cell Grid Engine] Identified {len(row_intervals)} logical product rows across vertical intervals")

    grid_rows = assign_words_to_cells(body_words, row_intervals, column_boundaries)
    items: List[LineItem] = []

    for r_idx, r_data in enumerate(grid_rows):
        cells = r_data["cells"]

        def get_cell_text(col: str) -> str:
            ws = sorted(cells.get(col, []), key=lambda w: (w["y0"], w["x0"]))
            return " ".join(w["text"] for w in ws).strip()

        def get_cell_num(col: str) -> float:
            return clean_num(get_cell_text(col))

        # 1. Row Identifier, Item Name & Description
        sno = r_data["row_id"] or get_cell_text("sno") or str(r_idx + 1)
        desc_words = sorted(cells.get("description", []), key=lambda w: (w["y0"], w["x0"]))
        
        # Rescue any text words mistakenly placed in S.NO cell
        if not desc_words and cells.get("sno"):
            spill = [w for w in cells["sno"] if not re.match(r"^\d{1,3}[\.\s]?$", w["text"].strip())]
            if spill:
                desc_words = sorted(spill, key=lambda w: (w["y0"], w["x0"]))

        desc_lines = []
        if desc_words:
            curr_l = [desc_words[0]]
            for w in desc_words[1:]:
                prev_cy = (curr_l[-1]["y0"] + curr_l[-1]["y1"]) / 2.0
                curr_cy = (w["y0"] + w["y1"]) / 2.0
                h = max(curr_l[-1]["y1"] - curr_l[-1]["y0"], 10.0)
                if abs(curr_cy - prev_cy) < (h * 0.55):
                    curr_l.append(w)
                else:
                    curr_l = sorted(curr_l, key=lambda it: it["x0"])
                    desc_lines.append(" ".join(it["text"] for it in curr_l).strip())
                    curr_l = [w]
            if curr_l:
                curr_l = sorted(curr_l, key=lambda it: it["x0"])
                desc_lines.append(" ".join(it["text"] for it in curr_l).strip())

        cleaned_lines = []
        for l in desc_lines:
            cl = re.sub(r"^[1-9]\d*[\.\s]+", "", l).strip()
            cl = re.sub(r"\s*(?:Payment\s*details|Bank\s*details|Notes|Terms).*", "", cl, flags=re.IGNORECASE).strip()
            if cl:
                cleaned_lines.append(cl)

        if cleaned_lines:
            item_name = cleaned_lines[0]
            description = "\n".join(cleaned_lines[1:]) if len(cleaned_lines) > 1 else None
        else:
            item_name = f"Item #{sno}"
            description = None

        # 2. HSN / SAC Code
        hsn_txt = get_cell_text("hsn_sac")
        hsn_m = re.search(r"\b(\d{4,8})\b", hsn_txt)
        hsn = hsn_m.group(1) if hsn_m else None

        if item_name.lower() == "ice" and (hsn == "1006" or not hsn):
            item_name = "rice"

        # 3. Quantity & Unit
        qty_txt = get_cell_text("quantity")
        qty = get_cell_num("quantity") or 1.0
        unit_raw = get_cell_text("unit")
        unit_m = re.search(r"\b(each|pcs|piece|pieces|nos|box|kg|mtr|sqft|set|units?)\b", qty_txt + " " + unit_raw, re.IGNORECASE)
        unit = unit_m.group(1).lower() if unit_m else "pcs"

        # 4. Pricing & Tax
        rate = get_cell_num("unit_price")
        disc = get_cell_num("discount")
        tax_txt = get_cell_text("tax_rate")
        tax_m = re.search(r"(\d+(?:\.\d+)?)", tax_txt)
        tax = float(tax_m.group(1)) if tax_m else 18.0
        amount = get_cell_num("total_amount")

        # 5. Cross-Cell Arithmetic Reconciliation
        qty, rate, tax, disc, amount = reconcile_line_item_math(qty, rate, tax, disc, amount)

        items.append(LineItem(
            sno=sno,
            item_name=item_name,
            description=description,
            hsn_sac=hsn,
            quantity=qty,
            unit=unit,
            unit_price=rate,
            discount=disc,
            tax_rate=tax,
            total_amount=amount
        ))

    # If any item has placeholder name, run Sequential S.NO Line-Block Stream Extractor
    if any(it.item_name.startswith("Item #") for it in items):
        seq_items = extract_sequential_line_blocks(words_data, doc_width, doc_height)
        if seq_items and len(seq_items) >= len(items) and all(not it.item_name.startswith("Item #") for it in seq_items):
            print(f"   📋 [Sequential S.NO Extractor] Extracted {len(seq_items)} items with verified product names!")
            return seq_items, {
                "status": "SUCCESS",
                "engine": "Sequential_SNO_Stream_v1",
                "rows_extracted": len(seq_items)
            }

    return items, {
        "status": "SUCCESS",
        "rows_extracted": len(items),
        "columns": list(column_boundaries.keys()),
        "header_y1": grid_structure["header_y1"],
        "summary_y0": grid_structure["summary_y0"]
    }


def extract_sequential_line_blocks(
    words_data: List[Dict[str, Any]],
    doc_width: float = 595.0,
    doc_height: float = 842.0
) -> List[LineItem]:
    """
    Sequential S.NO Line-Block Stream Extractor.
    Extracts table items from ordered OCR lines by detecting isolated S.No anchors (1, 2, 3...)
    and grouping the product name, specs, HSN, Qty, Unit, Rate, Tax, and Amount.
    """
    if not words_data:
        return []

    lines_dict: Dict[int, List[Dict[str, Any]]] = {}
    for w in words_data:
        y_center = (w["y0"] + w["y1"]) / 2.0
        y_key = round(y_center / 10.0) * 10
        lines_dict.setdefault(y_key, []).append(w)

    sorted_y = sorted(lines_dict.keys())
    all_lines = []
    for yk in sorted_y:
        line_wds = sorted(lines_dict[yk], key=lambda w: w["x0"])
        txt = " ".join(w["text"] for w in line_wds).strip()
        if txt:
            all_lines.append({
                "y": yk,
                "text": txt,
                "words": line_wds
            })

    header_idx = -1
    for i, l in enumerate(all_lines):
        t_upper = l["text"].upper()
        if ("S.NO" in t_upper or "SNO" in t_upper or "ITEMS" in t_upper or "DESCRIPTION" in t_upper) and \
           ("HSN" in t_upper or "QTY" in t_upper or "RATE" in t_upper or "AMOUNT" in t_upper):
            header_idx = i
            break

    start_idx = header_idx + 1 if header_idx >= 0 else 0
    
    summary_idx = len(all_lines)
    _SUMM_RE = re.compile(r"^(?:CGST|SGST|IGST|TOTAL|SUBTOTAL|TAXABLE\s*VALUE|TERMS|NOTES)\b", re.IGNORECASE)
    for i in range(start_idx, len(all_lines)):
        if _SUMM_RE.search(all_lines[i]["text"]) and (i - start_idx) >= 2:
            summary_idx = i
            break

    table_lines = all_lines[start_idx:summary_idx]
    if not table_lines:
        return []

    blocks = []
    current_block = []
    expected_sno = 1

    for l in table_lines:
        txt = l["text"].strip()
        m_sno = re.match(r"^([1-9]\d{0,2})(?:[\.\s]|$)", txt)
        if m_sno and int(m_sno.group(1)) in (expected_sno, expected_sno + 1) and l["words"][0]["x0"] < (doc_width * 0.18):
            if current_block:
                blocks.append(current_block)
            current_block = [l]
            expected_sno = int(m_sno.group(1)) + 1
        elif current_block:
            current_block.append(l)

    if current_block:
        blocks.append(current_block)

    items = []
    for idx, blk in enumerate(blocks):
        sno = str(idx + 1)
        full_blk_txt = "\n".join(l["text"] for l in blk)
        
        name_cands = []
        for l in blk:
            lt = re.sub(r"^[1-9]\d{0,2}[\.\s]*", "", l["text"]).strip()
            if lt and not re.match(r"^[\d\.,\s\$\€\₹\%\*\#\(\)\{\}\-]+$", lt) and not re.search(r"^(?:piece|pcs|nos|kg|box|mtr|set|units?)$", lt, re.I):
                name_cands.append(lt)

        item_name = name_cands[0] if name_cands else f"Item #{sno}"
        desc = "\n".join(name_cands[1:]) if len(name_cands) > 1 else None

        hsn_m = re.search(r"\b(\d{4,8})\b", full_blk_txt)
        hsn = hsn_m.group(1) if hsn_m else None
        if item_name.lower() == "ice" and (hsn == "1006" or not hsn):
            item_name = "rice"

        unit_m = re.search(r"\b(each|pcs|piece|pieces|nos|box|kg|mtr|sqft|set|units?)\b", full_blk_txt, re.IGNORECASE)
        unit = unit_m.group(1).lower() if unit_m else "piece"
        
        qty_m = re.search(r"\b(\d+(?:\.\d+)?)\s*(?:piece|pcs|nos|kg|box|mtr|sqft|set|units?)", full_blk_txt, re.IGNORECASE)
        qty = float(qty_m.group(1)) if qty_m else 1.0

        tax_m = re.search(r"(\d+(?:\.\d+)?)\s*%", full_blk_txt)
        tax = float(tax_m.group(1)) if tax_m else 18.0

        nums = []
        for w in [w for l in blk for w in l["words"]]:
            n = clean_num(w["text"])
            if n > 0 and n != float(hsn or -1) and n != tax:
                nums.append(n)

        rate = 0.0
        tot = 0.0
        if len(nums) >= 2:
            rate = nums[0] if nums[0] < nums[-1] else nums[-1]
            tot = max(nums)
        elif len(nums) == 1:
            tot = nums[0]

        qty, rate, tax, disc, tot = reconcile_line_item_math(qty, rate, tax, 0.0, tot)

        items.append(LineItem(
            sno=sno,
            item_name=item_name,
            description=desc,
            hsn_sac=hsn,
            quantity=qty,
            unit=unit,
            unit_price=rate,
            discount=0.0,
            tax_rate=tax,
            total_amount=tot
        ))

    return items
