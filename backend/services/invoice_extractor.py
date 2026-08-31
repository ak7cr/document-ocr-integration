import re
import json
import time
import io
from typing import List, Dict, Any, Optional
from PIL import Image

from ..schemas import (
    InvoiceData, InvoiceMetadata, PartyDetails, LineItem, TaxDetail,
    InvoiceSummary, PaymentInfo, BoundingBox
)
from .pdf_engines import extract_with_fitz, extract_with_pdfplumber
from .image_converter import image_to_base64, preprocess_image
from .ocr_engine import perform_image_ocr


def clean_num(val_str: Any) -> float:
    """
    Cleans numbers with spaces, commas, periods, or European decimals (e.g. '11 799,44', '1,168.25', '$ 12 973.88', '{8495.00').
    """
    if not val_str:
        return 0.0
    s = str(val_str).strip()
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


def words_to_lines(words_subset: List[Dict[str, Any]]) -> List[str]:
    """
    Groups spatial OCR word tokens into horizontal text lines based on vertical center alignment.
    """
    if not words_subset:
        return []
    sorted_w = sorted(words_subset, key=lambda w: (w["y0"], w["x0"]))
    lines_list = []
    curr = [sorted_w[0]]
    for w in sorted_w[1:]:
        prev_y_center = (curr[-1]["y0"] + curr[-1]["y1"]) / 2.0
        w_y_center = (w["y0"] + w["y1"]) / 2.0
        h = max(curr[-1]["y1"] - curr[-1]["y0"], 10.0)
        if abs(w_y_center - prev_y_center) < (h * 0.55):
            curr.append(w)
        else:
            curr = sorted(curr, key=lambda it: it["x0"])
            lines_list.append(" ".join(it["text"] for it in curr))
            curr = [w]
    if curr:
        curr = sorted(curr, key=lambda it: it["x0"])
        lines_list.append(" ".join(it["text"] for it in curr))
    return lines_list


def extract_invoice_from_image(image_bytes: bytes, preview_url: str) -> InvoiceData:
    """
    State-of-the-Art Spatial Column & Layout OCR Engine for Images/Screenshots.
    """
    start_time = time.time()
    pil_img = Image.open(io.BytesIO(image_bytes))
    pil_img = preprocess_image(pil_img)
    img_w, img_h = pil_img.width, pil_img.height
    mid_x = img_w * 0.48
    
    full_text, words_data, lines = perform_image_ocr(pil_img)
    
    # 1. Extract Metadata
    metadata = InvoiceMetadata()
    if "$" in full_text: metadata.currency, metadata.currency_symbol = "USD", "$"
    elif "€" in full_text: metadata.currency, metadata.currency_symbol = "EUR", "€"
    elif "£" in full_text: metadata.currency, metadata.currency_symbol = "GBP", "£"
    elif "₹" in full_text or "INR" in full_text or "GST" in full_text or "HINDAUN" in full_text.upper(): metadata.currency, metadata.currency_symbol = "INR", "₹"
    else: metadata.currency, metadata.currency_symbol = "INR", "₹"

    metadata.invoice_type = "TAX INVOICE" if "TAX" in full_text.upper() else "INVOICE"

    # Invoice Number (supports formatted codes, Tally "Invoice No.\nHPY006", INV-000002)
    inv_m = re.search(
        r"(?:Invoice\s*No\.?|Invoice\s*Number|Inv\s*#?|Bill\s*No\.?)[\s\:\n]+([A-Za-z0-9\-\/]+)",
        full_text, re.IGNORECASE
    )
    if inv_m and inv_m.group(1).upper() not in ["INVOICE", "TAX", "DATE", "OF", "OICE", "TYPE", "DATED", "DELIVERY"]:
        metadata.invoice_number = inv_m.group(1).strip()
    else:
        inv_code = re.search(r"\b(INV-[A-Za-z0-9\-]+|HPY\d+|[A-Z]{2,4}\/\d{2,4}\/\d+)\b", full_text, re.IGNORECASE)
        if inv_code: metadata.invoice_number = inv_code.group(1).strip()

    date_m = re.search(
        r"(?:Dated|Date\s*of\s*issue|Issue\s*Date|Invoice\s*Date|Bill\s*Date|Date\s*Issued)[\s\:\n]+(\d{1,2}-[A-Za-z]{3}-\d{2,4}|\d{1,2}[\/\.\-]\d{1,2}[\/\.\-]\d{2,4}|\w+\s+\d{1,2},?\s+\d{4})",
        full_text, re.IGNORECASE
    )
    if date_m:
        metadata.invoice_date = date_m.group(1).strip()

    # 2. Parties Extraction (Seller & Buyer)
    seller = PartyDetails()
    buyer = PartyDetails()
    payment = PaymentInfo()

    # Robust GSTIN Extraction with OCR character tolerance
    gstin_matches = re.findall(r"(?:GSTIN|GST|Tax\s*ID)[\s\:\#\-]+([0-9A-Za-z]{14,16})\b", full_text, re.IGNORECASE)
    raw_gstins = re.findall(r"\b([0-9O]{2}[A-Z]{5}[0-9BOSI]{4}[A-Z]{1}[1-9A-Z]{1}[Z2][0-9A-Z]{1})\b", full_text, re.IGNORECASE)
    cleaned_gstins = []
    for g in (gstin_matches + raw_gstins):
        cg = g.upper().strip()
        if len(cg) == 15 and cg not in cleaned_gstins:
            cleaned_gstins.append(cg)
    if cleaned_gstins:
        seller.gstin = cleaned_gstins[0]
        if len(cleaned_gstins) > 1: buyer.gstin = cleaned_gstins[1]

    # Buyer / Consignee detection (BILL TO / SHIP TO / Buyer multi-box layouts)
    buyer_bill_m = re.search(r"(?:Billed\s*To|Bill\s*To|Buyer\s*\(Bill\s*to\)|Customer|Consignee\s*\(Ship\s*to\)|Ship\s*To)[\s\:\n]+([^\n]+(?:\n[^\n]+){1,5})", full_text, re.IGNORECASE)
    consignee_m = re.search(r"Consignee\s*\(Ship\s*to\)[\s\:\n]+([^\n]+(?:\n[^\n]+){1,4})", full_text, re.IGNORECASE)
    target_buyer_match = buyer_bill_m or consignee_m
    
    # Locate Y boundary where BILL TO begins to cleanly isolate Seller header
    y_bill_to_top = img_h * 0.35
    for w in words_data:
        if re.search(r"^(?:BILL|SHIP|BUYER|CONSIGNEE)$", w["text"].strip(), re.IGNORECASE) and w["y0"] < (img_h * 0.50):
            y_bill_to_top = min(y_bill_to_top, w["y0"])

    if target_buyer_match:
        b_lines = [
            l.strip() for l in target_buyer_match.group(1).splitlines() 
            if l.strip() and not re.search(r"^(?:GSTIN|State|Tax|Phone|Mobile|Email|Tel|S\.NO|ITEM|SHIP\s*TO|BILL\s*TO)", l, re.IGNORECASE)
        ]
        if b_lines:
            # Deduplicate duplicate consecutive tokens from multi-box OCR (e.g. Hero Cycles Hero Cycles)
            clean_b_name = b_lines[0]
            words = clean_b_name.split()
            if len(words) >= 4 and len(words) % 2 == 0:
                half = len(words) // 2
                if " ".join(words[:half]).lower() == " ".join(words[half:]).lower():
                    clean_b_name = " ".join(words[:half])
            buyer.name = clean_b_name
            if len(b_lines) > 1: 
                buyer.address = ", ".join(b_lines[1:])

    # Top-Left Seller detection (strictly above BILL TO section)
    top_left_words = [
        w for w in words_data 
        if ((w["x0"] + w["x1"]) / 2.0) <= (img_w * 0.55) and w["y0"] < (y_bill_to_top - 5.0)
    ]
    top_left_lines = words_to_lines(top_left_words)
    seller_candidates = []
    for l in top_left_lines:
        cleaned = re.sub(r"^(?:Seller|Vendor|From|Supplier)[\.:\s]*", "", l.strip(), flags=re.IGNORECASE).strip()
        if cleaned and not re.search(r"^(?:Tax|GSTIN|State|E-Mail|Consignee|Buyer|Invoice|Date|Dated|Tax Invoice|BILL\s*TO|SHIP\s*TO)", cleaned, re.IGNORECASE) and len(cleaned) > 2:
            seller_candidates.append(cleaned)
    if seller_candidates:
        seller.name = seller_candidates[0]
        if len(seller_candidates) > 1:
            seller.address = ", ".join(seller_candidates[1:])

    # 3. Dynamic Table Header and Column Boundary Detection
    # Scan for the TABLE HEADER line specifically — must contain both a desc-type AND numeric-type keyword
    # to avoid firing on the invoice summary section.
    _DESC_KW  = {"DESCRIPTION", "ITEMS", "GOODS", "PARTICULARS", "PRODUCT", "SERVICE"}
    _NUM_KW   = {"QTY", "QUANTITY", "RATE", "PRICE", "AMOUNT", "TOTAL", "NET PRICE"}
    _SNO_KW   = {"S.NO", "SL NO", "SR NO", "SNO", "ITEM NO", "ITEM#"}

    y_items_header = None
    y_summary_header = None

    # Group words into lines to check per-line coverage (avoids single-word false triggers)
    _all_lines_for_hdr = {}
    for w in words_data:
        y_key = round(w["y0"] / 6) * 6          # bucket by 6px
        _all_lines_for_hdr.setdefault(y_key, []).append(w)

    for y_key in sorted(_all_lines_for_hdr):
        line_wds = _all_lines_for_hdr[y_key]
        upper_texts = " ".join(wd["text"].upper() for wd in line_wds)
        has_desc = any(k in upper_texts for k in _DESC_KW)
        has_num  = any(k in upper_texts for k in _NUM_KW)
        y0 = min(wd["y0"] for wd in line_wds)
        if (has_desc or any(k in upper_texts for k in _SNO_KW)) and has_num and y0 < (img_h * 0.85):
            if y_items_header is None or y0 < y_items_header:
                y_items_header = y0

    if y_items_header is None:
        y_items_header = img_h * 0.35

    for w in words_data:
        wt = w["text"].upper()
        if (
            "SUMMARY" in wt or "BREAKDOWN" in wt or "TERMS" in wt or
            "SUBTOTAL" in wt or "TOTAL AMOUNT" in wt or "CGST" in wt or
            "SGST" in wt or "IGST" in wt or "TAXABLE VALUE" in wt or
            "TOTAL TAX" in wt or "AMOUNT CHARGEABLE" in wt or wt == "TOTAL" or
            ("HSN" in wt and w["y0"] > (y_items_header + 60))
        ) and w["y0"] > (y_items_header + 30):
            if y_summary_header is None or w["y0"] < y_summary_header:
                y_summary_header = w["y0"]

    if y_summary_header is None:
        y_summary_header = img_h * 0.78

    # col_x: per-column x-center from header keywords
    # Uses word-boundary aware patterns to prevent "NO" matching "AMOUNT", "UM" matching "SUMMARY", etc.
    _COL_X_PATTERNS = [
        ("sno",       re.compile(r"\b(S[\.\s]?NO|SR[\.\s]?NO|SL[\.\s]?NO|ITEM\s*#|SNO|^#$|^NO\.$)", re.I)),
        ("desc",      re.compile(r"\b(DESC|DESCRIPTION|PARTICULAR|GOODS|PRODUCT|SERVICE|NARRATION)\b", re.I)),
        ("hsn",       re.compile(r"\b(HSN|SAC|HSNCODE|HSN/SAC)\b", re.I)),
        ("qty",       re.compile(r"\b(QTY|QUANTITY|QNTY|NOS|PCS|UNITS?)\b", re.I)),
        ("unit",      re.compile(r"^(UNIT|UOM|PER|U\.?M\.?)$", re.I)),
        ("rate",      re.compile(r"\b(RATE|PRICE|NET\s*PRICE|UNIT\s*PRICE|MRP|COST)\b", re.I)),
        ("net_worth", re.compile(r"\b(NET\s*WORTH|TAXABLE\s*VALUE|NET\s*AMOUNT)\b", re.I)),
        ("tax",       re.compile(r"\b(GST\s*%?|IGST\s*%?|CGST\s*%?|SGST\s*%?|VAT\s*%?|TAX\s*%?)$", re.I)),
        ("amount",    re.compile(r"\b(AMOUNT|GROSS|TOTAL\s*AMOUNT|^TOTAL$|^VALUE$)\b", re.I)),
    ]

    header_words = [w for w in words_data if abs(w["y0"] - y_items_header) <= 35.0]
    col_x = {}
    for w in header_words:
        wt = w["text"].strip()
        cx = (w["x0"] + w["x1"]) / 2.0
        for col_name, pat in _COL_X_PATTERNS:
            if pat.search(wt) and col_name not in col_x:  # first match wins
                col_x[col_name] = cx
                break

    # Determine description column right boundary
    x_next_col = col_x.get("hsn") or col_x.get("qty") or (img_w * 0.44)
    x_desc_max = (col_x.get("desc", img_w * 0.15) + x_next_col) / 2.0 if "desc" in col_x else (x_next_col * 0.92)

    table_words = [w for w in words_data if w["y0"] >= (y_items_header + 15) and w["y0"] < y_summary_header]
    valid_table_words = [
        w for w in table_words 
        if not re.search(r"^(ITEMS|Description|Gross\s*worth|Net\s*price|VAT\s*\(%|^No\.|Net\s*worth|SUMMARY|Total\s*Tax\s*Amount|Goods|per|Rate|Amount|Quantity)", w["text"], re.IGNORECASE)
    ]

    # 3. Extract Line Items using Structured Table Engine v2 (column-anchored)
    line_items: List[LineItem] = []
    table_engine_used = "column_anchored_v2"
    try:
        from backend.services.table_structure_engine import extract_table_rows_structured
        struct_items, struct_meta = extract_table_rows_structured(words_data, doc_width=img_w, doc_height=img_h)
        if struct_items and len(struct_items) > 0:
            line_items = struct_items
            table_engine_used = struct_meta.get("engine", "column_anchored_v2")
    except Exception as e:
        print(f"Table structure engine notice: {e}")

    # PaddleOCR word-level fallback: fires whenever the column-anchored engine returns nothing,
    # regardless of EasyOCR word count (engine may fail to find header even with many words).
    if not line_items:
        try:
            from backend.services.table_structure_engine import extract_words_with_paddle, extract_table_rows_structured
            print("[PaddleOCR] EasyOCR word count low — trying PaddleOCR word-level pass")
            paddle_words = extract_words_with_paddle(pil_img)
            if paddle_words and len(paddle_words) > 5:
                paddle_items, paddle_meta = extract_table_rows_structured(
                    paddle_words, doc_width=img_w, doc_height=img_h
                )
                if paddle_items:
                    line_items = paddle_items
                    table_engine_used = "paddleocr_word_fallback"
                    # Merge paddle words into words_data for downstream summary extraction
                    words_data = paddle_words
        except Exception as pe:
            print(f"[PaddleOCR fallback] {pe}")


    if not line_items:
        candidate_sno = [
            w for w in valid_table_words 
            if re.match(r"^[1-9]\d*[\.]?$", w["text"].strip()) and ((w["x0"] + w["x1"]) / 2.0) <= (img_w * 0.14)
        ]
        candidate_sno = sorted(candidate_sno, key=lambda w: w["y0"])

        seq_sno_words = []
        expected_idx = 1
        for cw in candidate_sno:
            val_str = cw["text"].strip().replace(".", "")
            if val_str.isdigit() and int(val_str) == expected_idx:
                seq_sno_words.append(cw)
                expected_idx += 1

        if len(seq_sno_words) >= 1:
            for idx, rw in enumerate(seq_sno_words):
                sno_raw = rw["text"].strip().replace(".", "")
                y_start = rw["y0"] - 6.0
                y_end = seq_sno_words[idx + 1]["y0"] - 6.0 if idx + 1 < len(seq_sno_words) else y_summary_header
                
                slice_words = [w for w in valid_table_words if w["y0"] >= y_start and w["y0"] < y_end]
                if not slice_words:
                    continue

                # Tax Rate
                tax_rate = 0.0
                for w in slice_words:
                    m_v = re.search(r"(\d+[\.,]?\d*)\s*%", w["text"])
                    if m_v:
                        tax_rate = clean_num(m_v.group(1))
                        break

                # Unit
                unit = "piece"
                for w in slice_words:
                    m_u = re.search(r"\b(each|pcs|piece|unit|box|kg|hrs|nos|set)\b", w["text"], re.IGNORECASE)
                    if m_u:
                        unit = m_u.group(1).lower()
                        break

                # Extract HSN Code
                hsn_sac = None
                for w in slice_words:
                    m_h = re.search(r"\b(9610|8471|8518|\d{4,8})\b", w["text"])
                    if m_h and ((w["x0"] + w["x1"]) / 2.0) > (img_w * 0.35) and ((w["x0"] + w["x1"]) / 2.0) < (img_w * 0.60):
                        hsn_sac = m_h.group(1)
                        break

                # Full Multi-Line Description (strictly within description column)
                desc_words = [
                    w for w in slice_words 
                    if ((w["x0"] + w["x1"]) / 2.0) < x_desc_max and w != rw
                ]
                desc_words = sorted(desc_words, key=lambda w: (w["y0"] // 12, w["x0"]))
                desc_lines = words_to_lines(desc_words)
                desc = " ".join(desc_lines).strip()
                desc = re.sub(r"^[1-9]\d*[\.\s]+", "", desc).strip()
                if not desc:
                    desc = f"Item #{sno_raw}"

                # Numbers in the row (columns to the right of description)
                num_words = [
                    w for w in slice_words 
                    if ((w["x0"] + w["x1"]) / 2.0) >= x_desc_max and "%" not in w["text"]
                ]
                num_words = sorted(num_words, key=lambda w: w["x0"])
                clean_nums = [
                    clean_num(w["text"]) for w in num_words 
                    if clean_num(w["text"]) > 0 and (hsn_sac is None or str(int(clean_num(w["text"]))) != str(hsn_sac))
                ]

                qty = 1.0
                unit_price = 0.0
                total_amount = 0.0

                if len(clean_nums) >= 4:
                    # [Qty, Net Price, Net Worth, Gross Worth]
                    qty = clean_nums[0]
                    unit_price = clean_nums[1]
                    total_amount = clean_nums[-1]
                elif len(clean_nums) == 3:
                    # [Qty, Rate, Amount]
                    qty = clean_nums[0]
                    unit_price = clean_nums[1]
                    total_amount = clean_nums[2]
                elif len(clean_nums) == 2:
                    qty = clean_nums[0]
                    unit_price = clean_nums[1]
                    total_amount = round(qty * unit_price * (1 + (tax_rate / 100.0)), 2)
                elif len(clean_nums) == 1:
                    total_amount = clean_nums[0]

                # Reconcile unit_price if missing
                if unit_price == 0.0 and total_amount > 0 and qty > 0:
                    unit_price = round(total_amount / qty, 2)
                elif total_amount == 0.0 and unit_price > 0 and qty > 0:
                    total_amount = round(qty * unit_price, 2)

                line_items.append(LineItem(
                    sno=sno_raw,
                    description=desc,
                    hsn_sac=hsn_sac,
                    quantity=qty,
                    unit=unit,
                    unit_price=unit_price,
                    tax_rate=tax_rate,
                    total_amount=total_amount
                ))
        else:
            # Line clustering fallback only if sno sequence was not found
            table_lines = words_to_lines(valid_table_words)
            for l in table_lines:
                line_clean = l.strip()
                if not line_clean: continue
                nums = re.findall(r"\b\d{1,3}(?:[\s,]\d{3})*(?:[\.,]\d{2})\b|\b\d+\b", line_clean)
                clean_numbers = [clean_num(n) for n in nums if clean_num(n) > 0]
                if len(clean_numbers) >= 2:
                    sno_m = re.match(r"^([1-9]\d*)[\.\s]", line_clean)
                    sno = sno_m.group(1) if sno_m else str(len(line_items) + 1)
                    unit_m = re.search(r"\b(each|pcs|piece|unit|box|kg|hrs)\b", line_clean, re.IGNORECASE)
                    unit = unit_m.group(1).lower() if unit_m else "each"
                    tax_m = re.search(r"(\d+)%", line_clean)
                    tax_rate = clean_num(tax_m.group(1)) if tax_m else 0.0
                    filt = [n for n in clean_numbers if n != tax_rate and str(int(n)) != sno]
                    qty = filt[0] if len(filt) >= 1 else 1.0
                    unit_price = filt[1] if len(filt) >= 2 else 0.0
                    total_amount = filt[-1] if len(filt) >= 3 else round(qty * unit_price * (1 + (tax_rate/100.0)), 2)
                    desc = re.sub(r"^[1-9]\d*[\.\s]+", "", line_clean)
                    desc = re.sub(r"[\d\s,\.₹\$€£%]+$", "", desc).strip()
                    line_items.append(LineItem(sno=sno, description=desc or f"Item #{sno}", quantity=qty, unit=unit, unit_price=unit_price, tax_rate=tax_rate, total_amount=total_amount))

    # 4. Summary & Grand Total Extraction (below SUMMARY)
    summary = InvoiceSummary()
    summary_words = [w for w in words_data if w["y0"] >= y_summary_header]
    
    # Reconstruct lines in summary area
    sum_lines_dict = {}
    for w in summary_words:
        yk = round(w["y0"] / 10.0) * 10
        sum_lines_dict.setdefault(yk, []).append(w)
    
    sum_lines = []
    for yk in sorted(sum_lines_dict.keys()):
        lw = sorted(sum_lines_dict[yk], key=lambda w: w["x0"])
        sum_lines.append("  ".join(w["text"] for w in lw))
    sum_text = "\n".join(sum_lines)

    # Labeled pattern matching for financial summary totals
    tot_m = re.search(r"(?:Total\s*Amount|Grand\s*Total|Amount\s*Payable|Total\s*Value|Net\s*Payable)[\s\:\n₹\$€£\{\*]+(\d{1,3}(?:[\s,]\d{3})*(?:\.\d{2})|\d+(?:\.\d{2}))", sum_text, re.IGNORECASE)
    sub_m = re.search(r"(?:Sub\s*Total|Subtotal|Taxable\s*Value|Total\s*Before\s*Tax)[\s\:\n₹\$€£\{\*]+(\d{1,3}(?:[\s,]\d{3})*(?:\.\d{2})|\d+(?:\.\d{2}))", sum_text, re.IGNORECASE)
    gst_m = re.search(r"(?:Total\s*GST|Total\s*Tax|GST\s*Amount|Tax\s*Amount)[\s\:\n₹\$€£\{\*]+(\d{1,3}(?:[\s,]\d{3})*(?:\.\d{2})|\d+(?:\.\d{2}))", sum_text, re.IGNORECASE)

    if tot_m: summary.grand_total = clean_num(tot_m.group(1))
    if sub_m: summary.subtotal = clean_num(sub_m.group(1))
    if gst_m: summary.total_gst = clean_num(gst_m.group(1))

    # Fallback to positional tokens if labeled pattern not found
    if summary.grand_total == 0.0:
        summary_num_tokens = []
        for w in summary_words:
            if "%" not in w["text"]:
                v = clean_num(w["text"])
                if v > 0:
                    summary_num_tokens.append((v, w["x0"], w["y0"], w["text"]))

        summary_num_tokens = sorted(summary_num_tokens, key=lambda item: (item[2], item[1]))
        unique_summary_nums = []
        for v, _, _, _ in summary_num_tokens:
            if v not in unique_summary_nums:
                unique_summary_nums.append(v)

        if len(unique_summary_nums) >= 3:
            sorted_vals = sorted(unique_summary_nums, reverse=True)
            summary.grand_total = sorted_vals[0]
            summary.subtotal = sorted_vals[1]
            summary.total_gst = sorted_vals[2]
        elif len(unique_summary_nums) >= 1:
            summary.grand_total = max(unique_summary_nums)

    # Cross-reconcile with line items if available
    if line_items:
        comp_grand = round(sum(it.total_amount for it in line_items), 2)
        comp_sub = round(sum(it.unit_price * (it.quantity or 1.0) for it in line_items), 2)
        if summary.grand_total == 0.0:
            summary.grand_total = comp_grand
        if summary.subtotal == 0.0:
            summary.subtotal = comp_sub
        if summary.total_gst == 0.0 and summary.grand_total > summary.subtotal:
            summary.total_gst = round(summary.grand_total - summary.subtotal, 2)

    # 5. High-Precision Bounding Boxes
    bounding_boxes = []
    for w in words_data:
        lbl = "text"
        wt = w["text"].upper()
        if metadata.invoice_number and metadata.invoice_number in w["text"]:
            lbl = "invoice_number"
        elif "INVOICE" in wt:
            lbl = "invoice_title"
        elif seller.name and seller.name.upper() in wt:
            lbl = "seller_name"
        elif buyer.name and buyer.name.upper() in wt:
            lbl = "buyer_name"
        elif "TOTAL" in wt or "GROSS" in wt or "SUMMARY" in wt:
            lbl = "totals"
        elif any((it.item_name or it.description or "")[:10].upper() in wt for it in line_items if len(it.item_name or it.description or "") > 3):
            lbl = "table_row"

        bounding_boxes.append(BoundingBox(
            label=lbl,
            text=w["text"],
            x0=round((w["x0"] / img_w) * 595.0, 2),
            y0=round((w["y0"] / img_h) * 842.0, 2),
            x1=round((w["x1"] / img_w) * 595.0, 2),
            y1=round((w["y1"] / img_h) * 842.0, 2),
            page=1,
            confidence=round(w.get("prob", 0.95), 2)
        ))

    # 6. Generate Markdown View
    md_lines = [
        f"# {seller.name or 'INVOICE'}",
        f"**Invoice No:** {metadata.invoice_number or 'N/A'}  |  **Date:** {metadata.invoice_date or 'N/A'}\n",
        f"### Seller: {seller.name or 'N/A'}",
        f"{seller.address or ''}",
        f"Tax ID: {seller.vat_id or 'N/A'}\n",
        f"### Client: {buyer.name or 'N/A'}",
        f"{buyer.address or ''}",
        f"Tax ID: {buyer.vat_id or 'N/A'}\n",
        "### ITEMS",
        "| # | Description | Qty | Unit | Unit Price | Total |",
        "|:---:|:---|:---:|:---:|:---:|:---:|"
    ]
    for it in line_items:
        it_title = it.item_name or it.description or "Item"
        md_lines.append(f"| {it.sno} | {it_title} | {it.quantity} | {it.unit} | {metadata.currency_symbol}{it.unit_price:,.2f} | {metadata.currency_symbol}{it.total_amount:,.2f} |")
    
    md_lines.append(f"\n### SUMMARY")
    md_lines.append(f"- **Subtotal (Net):** {metadata.currency_symbol}{summary.subtotal:,.2f}")
    md_lines.append(f"- **Tax / VAT:** {metadata.currency_symbol}{summary.total_gst:,.2f}")
    md_lines.append(f"- **Grand Total (Gross):** {metadata.currency_symbol}{summary.grand_total:,.2f}")

    proc_time = round((time.time() - start_time) * 1000, 2)

    return InvoiceData(
        metadata=metadata,
        seller=seller,
        buyer=buyer,
        line_items=line_items,
        tax_breakdown=[],
        summary=summary,
        payment=payment,
        terms_and_conditions="Standard payment terms apply.",
        notes="OCR Image parsed successfully.",
        raw_text=full_text,
        markdown_content="\n".join(md_lines),
        bounding_boxes=bounding_boxes,
        page_count=1,
        engine_used="Local OCR Engine (EasyOCR)",
        processing_time_ms=proc_time,
        document_preview_urls=[preview_url]
    )


def extract_invoice_local(
    pdf_bytes: bytes,
    filename: str = "invoice.pdf",
    preview_images: Optional[List[str]] = None
) -> InvoiceData:
    """
    Local extractor for digital PDF files using fitz and pdfplumber.
    """
    start_time = time.time()
    fitz_res = extract_with_fitz(pdf_bytes)
    plumber_res = extract_with_pdfplumber(pdf_bytes)
    
    full_text = fitz_res.get("full_text", "")
    pages = fitz_res.get("pages", [])
    tables = plumber_res.get("tables", [])

    metadata = InvoiceMetadata()
    
    inv_num_patterns = [
        r"(?:Invoice\s*(?:no|number|#|id|code)[\.:\s]*|Bill\s*(?:no|number|#)[\.:\s]*|Inv\s*#?[\.:\s]*)\s*([A-Za-z0-9\-\/]+)",
        r"(?:Invoice\s*ID|Invoice\s*Num)[\.:\s]*([A-Za-z0-9\-\/]+)",
        r"(?:Bill\s*ID|Bill\s*Num)[\.:\s]*([A-Za-z0-9\-\/]+)",
    ]
    for pat in inv_num_patterns:
        m = re.search(pat, full_text, re.IGNORECASE)
        if m:
            val = m.group(1).strip()
            if val.upper() not in ["INVOICE", "TAX", "DATE", "OF", "TYPE"]:
                metadata.invoice_number = val
                break

    if not metadata.invoice_number:
        m = re.search(r"Invoice\s*:\s*([A-Za-z0-9\-\/]+)", full_text, re.IGNORECASE)
        if m and m.group(1).strip().upper() not in ["INVOICE", "TAX", "DATE"]:
            metadata.invoice_number = m.group(1).strip()

    metadata.invoice_type = "TAX INVOICE" if "TAX" in full_text.upper() else "INVOICE"

    date_patterns = [
        r"(?:Date\s*of\s*issue|Issue\s*Date|Invoice\s*Date|Bill\s*Date|Date\s*Issued|Billing\s*Date)[\.:\s]*(\d{1,2}[\/\.\-]\d{1,2}[\/\.\-]\d{2,4}|\w+\s+\d{1,2},?\s+\d{4})",
        r"(?:Date)[\.:\s]+(\d{1,2}[\/\.\-]\d{1,2}[\/\.\-]\d{2,4})",
    ]
    for pat in date_patterns:
        m = re.search(pat, full_text, re.IGNORECASE)
        if m:
            metadata.invoice_date = m.group(1).strip()
            break

    if "$" in full_text: metadata.currency, metadata.currency_symbol = "USD", "$"
    elif "€" in full_text: metadata.currency, metadata.currency_symbol = "EUR", "€"
    elif "£" in full_text: metadata.currency, metadata.currency_symbol = "GBP", "£"
    elif "₹" in full_text or "GST" in full_text: metadata.currency, metadata.currency_symbol = "INR", "₹"

    seller = PartyDetails()
    buyer = PartyDetails()

    gstins = re.findall(r"\b([0-9]{2}[A-Z]{5}[0-9]{4}[A-Z]{1}[1-9A-Z]{1}Z[0-9A-Z]{1})\b", full_text)
    tax_ids = re.findall(r"(?:Tax\s*Id|VAT\s*(?:Id|No)|VATIN|EIN)[\.:\s]*([A-Za-z0-9\-\/]+)", full_text, re.IGNORECASE)
    
    if gstins:
        seller.gstin = gstins[0]
        if len(gstins) > 1: buyer.gstin = gstins[1]
    elif tax_ids:
        seller.vat_id = tax_ids[0].strip()
        if len(tax_ids) > 1: buyer.vat_id = tax_ids[1].strip()

    lines = [l.strip() for l in full_text.splitlines() if l.strip()]
    seller_idx, client_idx, bill_to_idx = -1, -1, -1

    for i, line in enumerate(lines):
        if re.search(r"^(?:Seller|Vendor|From|Supplier)[\.:]*$", line, re.IGNORECASE):
            seller_idx = i
        elif re.search(r"^(?:Client|Customer|Buyer|Sold\s*To|Bill\s*To)[\.:]*$", line, re.IGNORECASE):
            client_idx = i
        elif re.search(r"^BILL\s*TO", line, re.IGNORECASE):
            bill_to_idx = i

    if seller_idx != -1:
        s_lines = [lines[j] for j in range(seller_idx + 1, min(len(lines), seller_idx + 6)) if not re.search(r"^(?:Client|Items|Summary|Tax|IBAN)", lines[j], re.IGNORECASE)]
        if s_lines:
            seller.name = s_lines[0]
            seller.address = ", ".join(s_lines[1:])
    elif not seller.name:
        for l in lines[:5]:
            if not re.search(r"Invoice|Date|Tax|Page", l, re.IGNORECASE) and len(l) > 2:
                seller.name = l
                break

    target_c = client_idx if client_idx != -1 else bill_to_idx
    if target_c != -1:
        c_lines = [lines[j] for j in range(target_c + 1, min(len(lines), target_c + 6)) if not re.search(r"^(?:Items|Summary|Tax|IBAN|No\.)", lines[j], re.IGNORECASE)]
        if c_lines:
            buyer.name = c_lines[0]
            buyer.address = ", ".join(c_lines[1:])

    # Table items
    line_items: List[LineItem] = []
    if tables:
        for t in tables:
            rows = t.get("data", [])
            if not rows or len(rows) < 2: continue
            header = [str(c or "").strip().upper() for c in rows[0]]
            if any("DESC" in h or "ITEM" in h or "NET PRICE" in h for h in header):
                sno_col = next((i for i, h in enumerate(header) if h in ["NO.", "NO", "#", "S.NO"]), 0)
                desc_col = next((i for i, h in enumerate(header) if "DESC" in h or "ITEM" in h), 1)
                qty_col = next((i for i, h in enumerate(header) if "QTY" in h), None)
                rate_col = next((i for i, h in enumerate(header) if "PRICE" in h or "RATE" in h), None)
                tot_col = next((i for i, h in enumerate(header) if "GROSS" in h or "AMOUNT" in h or "TOTAL" in h), None)

                for r in rows[1:]:
                    d = str(r[desc_col] or "").strip() if desc_col < len(r) else ""
                    if not d or "TOTAL" in d.upper(): continue
                    q = clean_num(r[qty_col]) if qty_col is not None and qty_col < len(r) else 1.0
                    p = clean_num(r[rate_col]) if rate_col is not None and rate_col < len(r) else 0.0
                    tot = clean_num(r[tot_col]) if tot_col is not None and tot_col < len(r) else (q * p)
                    line_items.append(LineItem(
                        sno=str(r[sno_col] or len(line_items)+1),
                        description=d,
                        quantity=q or 1.0,
                        unit="each",
                        unit_price=p,
                        total_amount=tot
                    ))

    summary = InvoiceSummary()
    if line_items:
        summary.grand_total = round(sum(it.total_amount for it in line_items), 2)
        summary.subtotal = round(sum(it.unit_price * (it.quantity or 1.0) for it in line_items), 2)
        if summary.grand_total > summary.subtotal:
            summary.total_gst = round(summary.grand_total - summary.subtotal, 2)

    bounding_boxes = []
    for page in pages:
        for b in page.get("blocks", []):
            b_text = b.get("text", "").strip()
            if not b_text: continue
            bounding_boxes.append(BoundingBox(
                label="text",
                text=b_text,
                x0=round(b["bbox"][0], 2),
                y0=round(b["bbox"][1], 2),
                x1=round(b["bbox"][2], 2),
                y1=round(b["bbox"][3], 2),
                page=page.get("page_number", 1),
                confidence=0.98
            ))

    return InvoiceData(
        metadata=metadata,
        seller=seller,
        buyer=buyer,
        line_items=line_items,
        tax_breakdown=[],
        summary=summary,
        payment=PaymentInfo(),
        terms_and_conditions="Standard payment terms apply.",
        notes="Invoice parsed successfully.",
        raw_text=full_text,
        markdown_content="",
        bounding_boxes=bounding_boxes,
        page_count=len(pages),
        engine_used="Local Spatial Engine (fitz + pdfplumber)",
        processing_time_ms=round((time.time() - start_time) * 1000, 2),
        document_preview_urls=preview_images or []
    )


def _build_invoice_markdown(invoice_obj: InvoiceData) -> str:
    md_lines = [
        f"# {invoice_obj.seller.name or 'INVOICE'}",
        f"**Invoice No:** {invoice_obj.metadata.invoice_number or 'N/A'}  |  **Date:** {invoice_obj.metadata.invoice_date or 'N/A'}\n",
        f"### Seller: {invoice_obj.seller.name or 'N/A'}",
        f"{invoice_obj.seller.address or ''}",
        f"Tax ID: {invoice_obj.seller.vat_id or invoice_obj.seller.gstin or 'N/A'}\n",
        f"### Client: {invoice_obj.buyer.name or 'N/A'}",
        f"{invoice_obj.buyer.address or ''}",
        f"Tax ID: {invoice_obj.buyer.vat_id or invoice_obj.buyer.gstin or 'N/A'}\n",
        "### ITEMS",
        "| # | Description | Qty | Unit | Unit Price | Total |",
        "|:---:|:---|:---:|:---:|:---:|:---:|"
    ]
    for it in invoice_obj.line_items:
        md_lines.append(f"| {it.sno} | {it.description} | {it.quantity} | {it.unit or 'each'} | {invoice_obj.metadata.currency_symbol}{it.unit_price:,.2f} | {invoice_obj.metadata.currency_symbol}{it.total_amount:,.2f} |")
    md_lines.append(f"\n### SUMMARY")
    md_lines.append(f"- **Subtotal:** {invoice_obj.metadata.currency_symbol}{invoice_obj.summary.subtotal:,.2f}")
    md_lines.append(f"- **Tax/VAT:** {invoice_obj.metadata.currency_symbol}{invoice_obj.summary.total_gst:,.2f}")
    md_lines.append(f"- **Grand Total:** {invoice_obj.metadata.currency_symbol}{invoice_obj.summary.grand_total:,.2f}")
    return "\n".join(md_lines)


async def extract_invoice_ai_vision_claude(image_bytes: bytes, mime_type: str = "image/jpeg") -> Optional[InvoiceData]:
    """
    Multimodal AI Vision Extractor using Anthropic Claude — OUR primary AI
    fallback. Reads ANTHROPIC_API_KEY / ANTHROPIC_MODEL from env; returns the
    same InvoiceData shape as the Gemini path so downstream validation is
    identical.
    """
    import os
    import base64
    api_key = os.getenv("ANTHROPIC_API_KEY")
    if not api_key:
        return None
    model = os.getenv("ANTHROPIC_MODEL", "claude-haiku-4-5")
    prompt = """You are an expert Document AI and Invoice OCR system specializing in global and Indian GST / Tally Tax Invoices.
Analyze this invoice image and extract all structured data with 100% precision.

CRITICAL EXTRACTION RULES:
1. HSN/SAC CODE: Extract the HSN/SAC code for every product into `hsn_sac` (e.g. "3405", "34029011", "32141000", "85469090"). Do NOT put HSN codes into quantity or description.
2. LINE ITEMS: Extract ONLY genuine product/service item rows. Do NOT extract total quantity rows, CGST/SGST tax rows, or notes as line items.
3. CURRENCY: If the invoice is from India or contains GST / State / ₹ / Consignee / Tally format, set currency: "INR" and currency_symbol: "₹".
4. PARTIES:
   - Seller: Top-left company name and address.
   - Buyer / Consignee: Company in Consignee / Buyer box.
5. QUANTITY & RATE:
   - Quantity: Numeric quantity (e.g. 12.0, 25.0, 50.0).
   - Unit Price / Rate: Rate per unit (e.g. 49.0, 24.25, 5.1).
   - Total Amount: Amount for the line item (e.g. 693.84, 715.37).

Return ONLY valid JSON (without any markdown wrapping or explanation) matching this schema:
{
  "metadata": {
    "invoice_type": "TAX INVOICE",
    "invoice_number": "string",
    "invoice_date": "string",
    "due_date": "string",
    "currency": "INR",
    "currency_symbol": "₹"
  },
  "seller": {"name": "string", "address": "string", "vat_id": "string", "gstin": "string", "email": "string", "phone": "string"},
  "buyer": {"name": "string", "address": "string", "vat_id": "string", "gstin": "string", "email": "string", "phone": "string"},
  "line_items": [
    {
      "sno": "1",
      "item_name": "Short Product Name/Title",
      "description": "Optional secondary detailed specs or null",
      "hsn_sac": "string",
      "quantity": 1.0,
      "unit": "pcs",
      "unit_price": 0.0,
      "discount": 0.0,
      "tax_rate": 18.0,
      "total_amount": 0.0
    }
  ],
  "tax_breakdown": [
    {
      "hsn_sac": "string",
      "taxable_value": 0.0,
      "cgst_rate": 9.0,
      "cgst_amount": 0.0,
      "sgst_rate": 9.0,
      "sgst_amount": 0.0,
      "total_tax_amount": 0.0
    }
  ],
  "summary": {"subtotal": 0.0, "total_gst": 0.0, "grand_total": 0.0, "total_in_words": "string"},
  "payment": {"account_number": "string"},
  "terms_and_conditions": "string",
  "notes": "string"
}
"""
    try:
        import httpx
        # Detect the real image format from the bytes — the pipeline often calls
        # with the default "image/jpeg" even when the payload is PNG, which
        # Anthropic rejects (400) as media-type mismatch.
        media_type = mime_type if mime_type.startswith("image/") else "image/jpeg"
        try:
            img_fmt = Image.open(io.BytesIO(image_bytes)).format
            if img_fmt:
                media_type = "image/" + img_fmt.lower()
        except Exception:
            pass
        body = {
            "model": model,
            "max_tokens": 2500,
            "temperature": 0.0,
            "messages": [{
                "role": "user",
                "content": [
                    {"type": "image", "source": {"type": "base64", "media_type": media_type, "data": base64.b64encode(image_bytes).decode()}},
                    {"type": "text", "text": prompt},
                ],
            }],
        }
        async with httpx.AsyncClient(timeout=120) as client:
            resp = await client.post(
                "https://api.anthropic.com/v1/messages",
                headers={"content-type": "application/json", "x-api-key": api_key, "anthropic-version": "2023-06-01"},
                json=body,
            )
            resp.raise_for_status()
        res_text = resp.json()["content"][0]["text"].strip()
    except Exception as e:
        print(f"[Claude AI Vision] error: {e}")
        return None

    if "```json" in res_text:
        res_text = res_text.split("```json")[1].split("```")[0].strip()
    elif "```" in res_text:
        res_text = res_text.split("```")[1].split("```")[0].strip()

    print("\n" + "=" * 84)
    print(f"🤖 [CLAUDE AI VISION RAW JSON RESPONSE - Model: {model}]")
    print("=" * 84)
    print(res_text)
    print("=" * 84 + "\n")

    data_dict = json.loads(res_text)
    if "line_items" in data_dict and isinstance(data_dict["line_items"], list):
        for item in data_dict["line_items"]:
            raw_name = item.get("item_name")
            raw_desc = item.get("description") or ""
            if not raw_name and raw_desc:
                parts = raw_desc.split("\n")
                item["item_name"] = parts[0].strip()
                item["description"] = "\n".join(parts[1:]).strip() if len(parts) > 1 else None
            elif raw_name and raw_desc and raw_name.strip() == raw_desc.strip():
                item["description"] = None

    invoice_obj = InvoiceData.model_validate(data_dict)
    invoice_obj.engine_used = f"🤖 Claude Vision AI ({model})"
    invoice_obj.markdown_content = _build_invoice_markdown(invoice_obj)
    return invoice_obj


async def extract_invoice_ai_vision(image_bytes: bytes, api_key: str, mime_type: str = "image/jpeg") -> Optional[InvoiceData]:
    """
    Multimodal AI Vision Extractor — Claude primary (our fallback system),
    Google Gemini as secondary backup.
    """
    # 0. Claude primary (our fallback system)
    claude_res = await extract_invoice_ai_vision_claude(image_bytes, mime_type)
    if claude_res is not None:
        return claude_res

    # 1. Google Gemini secondary
    try:
        import warnings
        warnings.filterwarnings("ignore", message=".*automatic function calling.*")
        warnings.filterwarnings("ignore", category=UserWarning, module="google.genai")

        from google import genai
        from google.genai import types

        client = genai.Client(api_key=api_key)
        prompt = """
        You are an expert Document AI and Invoice OCR system specializing in global and Indian GST / Tally Tax Invoices.
        Analyze this invoice image and extract all structured data with 100% precision.

        CRITICAL EXTRACTION RULES:
        1. HSN/SAC CODE: Extract the HSN/SAC code for every product into `hsn_sac` (e.g. "3405", "34029011", "32141000", "85469090"). Do NOT put HSN codes into quantity or description.
        2. LINE ITEMS: Extract ONLY genuine product/service item rows. Do NOT extract total quantity rows (e.g. "Total 207.00 Pcs"), CGST/SGST tax rows, or notes as line items.
        3. CURRENCY: If the invoice is from India or contains GST / State / ₹ / Consignee / Tally format, set currency: "INR" and currency_symbol: "₹".
        4. PARTIES:
           - Seller: Top-left company name (e.g. "Navkar Motors") and address.
           - Buyer / Consignee: Company in Consignee / Buyer box (e.g. "CHANDAN PAINTS AND HARDWARE").
        5. QUANTITY & RATE:
           - Quantity: Numeric quantity (e.g. 12.0, 25.0, 50.0).
           - Unit Price / Rate: Rate per unit (e.g. 49.0, 24.25, 5.1).
           - Total Amount: Amount for the line item (e.g. 693.84, 715.37).

        Return ONLY valid JSON (without any markdown wrapping or explanation) matching this schema:
        {
          "metadata": {
            "invoice_type": "TAX INVOICE",
            "invoice_number": "string",
            "invoice_date": "string",
            "due_date": "string",
            "currency": "INR",
            "currency_symbol": "₹"
          },
          "seller": {
            "name": "string",
            "address": "string",
            "vat_id": "string",
            "gstin": "string",
            "email": "string",
            "phone": "string"
          },
          "buyer": {
            "name": "string",
            "address": "string",
            "vat_id": "string",
            "gstin": "string",
            "email": "string",
            "phone": "string"
          },
          "line_items": [
            {
              "sno": "1",
              "item_name": "Short Product Name/Title",
              "description": "Optional secondary detailed specs or null",
              "hsn_sac": "string",
              "quantity": 1.0,
              "unit": "pcs",
              "unit_price": 0.0,
              "discount": 0.0,
              "tax_rate": 18.0,
              "total_amount": 0.0
            }
          ],
          "tax_breakdown": [
            {
              "hsn_sac": "string",
              "taxable_value": 0.0,
              "cgst_rate": 9.0,
              "cgst_amount": 0.0,
              "sgst_rate": 9.0,
              "sgst_amount": 0.0,
              "total_tax_amount": 0.0
            }
          ],
          "summary": {
            "subtotal": 0.0,
            "total_gst": 0.0,
            "grand_total": 0.0,
            "total_in_words": "string"
          },
          "payment": {
            "account_number": "string"
          },
          "terms_and_conditions": "string",
          "notes": "string"
        }
        """

        start_time = time.time()
        # Supported models with fallback priority
        candidate_models = [
            # "gemini-2.0-flash",
            # "gemini-1.5-flash",
            "gemini-3.6-flash"
        ]

        response = None
        used_model = None

        for model_name in candidate_models:
            try:
                print(f"🤖 [Gemini AI Vision] Sending image ({len(image_bytes)} bytes) to Gemini API model: '{model_name}'...")
                response = client.models.generate_content(
                    model=model_name,
                    contents=[
                        types.Part.from_bytes(data=image_bytes, mime_type=mime_type),
                        prompt
                    ],
                    config=types.GenerateContentConfig(
                        response_mime_type="application/json",
                        temperature=0.0
                    )
                )
                if response and response.text:
                    used_model = model_name
                    break
            except Exception as model_err:
                err_str = str(model_err)
                if "RESOURCE_EXHAUSTED" in err_str or "429" in err_str:
                    print("ℹ️ [AI Vision] Gemini free tier rate limit reached (20 req/day). Falling back cleanly to Local OCR.")
                    break
                elif "404" not in err_str:
                    print(f"ℹ️ [AI Vision] Model note: {err_str[:120]}")
                continue

        if not response or not response.text:
            return None

        proc_time_ms = max(int((time.time() - start_time) * 1000), 1)

        res_text = response.text.strip()
        if "```json" in res_text:
            res_text = res_text.split("```json")[1].split("```")[0].strip()
        elif "```" in res_text:
            res_text = res_text.split("```")[1].split("```")[0].strip()

        print("\n" + "="*84)
        print(f"🤖 [GEMINI AI VISION RAW JSON RESPONSE - Model: {used_model} ({proc_time_ms}ms)]")
        print("="*84)
        print(res_text)
        print("="*84 + "\n")

        data_dict = json.loads(res_text)

        # Normalize line items: bifurcate title and secondary specs
        if "line_items" in data_dict and isinstance(data_dict["line_items"], list):
            for item in data_dict["line_items"]:
                raw_name = item.get("item_name")
                raw_desc = item.get("description") or ""
                if not raw_name and raw_desc:
                    parts = raw_desc.split("\n")
                    item["item_name"] = parts[0].strip()
                    item["description"] = "\n".join(parts[1:]).strip() if len(parts) > 1 else None
                elif raw_name and raw_desc and raw_name.strip() == raw_desc.strip():
                    item["description"] = None

        invoice_obj = InvoiceData.model_validate(data_dict)
        invoice_obj.engine_used = f"🤖 Gemini Vision AI ({used_model})"
        invoice_obj.processing_time_ms = proc_time_ms

        # Generate clean markdown
        md_lines = [
            f"# {invoice_obj.seller.name or 'INVOICE'}",
            f"**Invoice No:** {invoice_obj.metadata.invoice_number or 'N/A'}  |  **Date:** {invoice_obj.metadata.invoice_date or 'N/A'}\n",
            f"### Seller: {invoice_obj.seller.name or 'N/A'}",
            f"{invoice_obj.seller.address or ''}",
            f"Tax ID: {invoice_obj.seller.vat_id or invoice_obj.seller.gstin or 'N/A'}\n",
            f"### Client: {invoice_obj.buyer.name or 'N/A'}",
            f"{invoice_obj.buyer.address or ''}",
            f"Tax ID: {invoice_obj.buyer.vat_id or invoice_obj.buyer.gstin or 'N/A'}\n",
            "### ITEMS",
            "| # | Description | Qty | Unit | Unit Price | Total |",
            "|:---:|:---|:---:|:---:|:---:|:---:|"
        ]
        for it in invoice_obj.line_items:
            md_lines.append(f"| {it.sno} | {it.description} | {it.quantity} | {it.unit or 'each'} | {invoice_obj.metadata.currency_symbol}{it.unit_price:,.2f} | {invoice_obj.metadata.currency_symbol}{it.total_amount:,.2f} |")
        md_lines.append(f"\n### SUMMARY")
        md_lines.append(f"- **Subtotal:** {invoice_obj.metadata.currency_symbol}{invoice_obj.summary.subtotal:,.2f}")
        md_lines.append(f"- **Tax/VAT:** {invoice_obj.metadata.currency_symbol}{invoice_obj.summary.total_gst:,.2f}")
        md_lines.append(f"- **Grand Total:** {invoice_obj.metadata.currency_symbol}{invoice_obj.summary.grand_total:,.2f}")
        invoice_obj.markdown_content = "\n".join(md_lines)

        return invoice_obj
    except Exception as e:
        print(f"AI Vision extraction error: {e}")
        return None
