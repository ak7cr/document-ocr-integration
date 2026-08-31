import re
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from typing import List, Dict, Any, Optional
from backend.schemas import InvoiceData, LineItem, InvoiceSummary

MONEY_QUANTUM = Decimal("0.01")
GSTIN_RE = re.compile(r"^[0-9]{2}[A-Z]{5}[0-9]{4}[A-Z][1-9A-Z]Z[0-9A-Z]$")


@dataclass
class ValidationResult:
    passed: bool
    auto_acceptable: bool
    needs_review: bool
    overall_confidence: float
    errors: List[str] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)
    field_confidence: Dict[str, Any] = field(default_factory=dict)
    detailed_checks: List[Dict[str, Any]] = field(default_factory=list)


def to_decimal(value: Any) -> Decimal:
    """
    Safely and losslessly parses any monetary value into a 2-decimal-place Decimal object.
    Supports both US (1,234.56) and European (1.234,56) number conventions.
    """
    if value is None or value == "":
        return Decimal("0.00")

    if isinstance(value, Decimal):
        return value.quantize(MONEY_QUANTUM, rounding=ROUND_HALF_UP)

    if isinstance(value, int):
        return Decimal(value).quantize(MONEY_QUANTUM, rounding=ROUND_HALF_UP)

    if isinstance(value, float):
        return Decimal(str(value)).quantize(MONEY_QUANTUM, rounding=ROUND_HALF_UP)

    raw = str(value).strip()
    raw = re.sub(r"[₹\$€£\s]", "", raw)

    if not raw:
        return Decimal("0.00")

    if "," in raw and "." in raw:
        if raw.rfind(",") > raw.rfind("."):
            raw = raw.replace(".", "").replace(",", ".")
        else:
            raw = raw.replace(",", "")
    elif "," in raw:
        left, right = raw.rsplit(",", 1)
        raw = f"{left}.{right}" if len(right) in (1, 2) else raw.replace(",", "")

    try:
        return Decimal(raw).quantize(MONEY_QUANTUM, rounding=ROUND_HALF_UP)
    except (InvalidOperation, ValueError, TypeError):
        return Decimal("0.00")


def close_enough(actual: Decimal, expected: Decimal, tolerance: str = "2.50") -> bool:
    """
    Checks if actual and expected are within a specific decimal tolerance.
    """
    return abs(actual - expected) <= Decimal(tolerance)


def normalize_gstin(value: Optional[str]) -> str:
    """
    Strips whitespace and punctuation from GSTIN strings.
    """
    return re.sub(r"[^A-Z0-9]", "", (value or "").upper())


def validate_invoice(invoice: InvoiceData) -> ValidationResult:
    """
    Performs rigorous multi-point business, GST, and arithmetic quality checks.
    Returns a comprehensive ValidationResult with auto_acceptable & needs_review flags.
    """
    errors: List[str] = []
    warnings: List[str] = []
    detailed_checks: List[Dict[str, Any]] = []
    field_conf: Dict[str, float] = {}

    items = invoice.line_items or []
    summary = invoice.summary or InvoiceSummary()

    # 1. Structural Sanity Checks
    if not items:
        err_msg = "NO_LINE_ITEMS"
        errors.append(err_msg)
        detailed_checks.append({"code": err_msg, "severity": "ERROR", "message": "No line items detected in invoice table."})
        field_conf["line_items"] = 0.20
    else:
        field_conf["line_items"] = 0.96

    grand_total = to_decimal(getattr(summary, "grand_total", None))
    subtotal = to_decimal(getattr(summary, "subtotal", None))
    total_tax = to_decimal(getattr(summary, "total_gst", None))

    if grand_total <= Decimal("0.00"):
        err_msg = "MISSING_OR_INVALID_GRAND_TOTAL"
        errors.append(err_msg)
        detailed_checks.append({"code": err_msg, "severity": "ERROR", "message": "Grand total is missing or zero."})
        field_conf["grand_total"] = 0.30
    else:
        field_conf["grand_total"] = 0.96

    # 2. Strict Line Items Summation & Total Reconciliation
    line_total = sum((to_decimal(getattr(item, "total_amount", None)) for item in items), Decimal("0.00"))

    if items and grand_total > Decimal("0.00"):
        gross_match = close_enough(line_total, grand_total, "2.50")
        subtotal_match = close_enough(line_total, subtotal, "2.50") if subtotal > Decimal("0.00") else False
        
        # Guard: Tax cannot exceed 35% of line total (standard GST rate max is 28%)
        effective_tax_rate = (total_tax / line_total) if line_total > Decimal("0.00") else Decimal("0.00")
        net_match = close_enough(line_total + total_tax, grand_total, "2.50") and (effective_tax_rate <= Decimal("0.35"))

        if gross_match or subtotal_match or net_match:
            field_conf["line_items_sum"] = 0.98
            detailed_checks.append({
                "code": "LINE_TOTAL_MATCHED",
                "severity": "INFO",
                "mode": "GROSS" if gross_match else ("SUBTOTAL" if subtotal_match else "NET"),
                "items_sum": str(line_total),
                "grand_total": str(grand_total)
            })
        else:
            err_msg = f"LINE_TOTAL_MISMATCH: items_sum={line_total}, tax={total_tax}, grand_total={grand_total}"
            errors.append(err_msg)
            detailed_checks.append({
                "code": "LINE_TOTAL_MISMATCH",
                "severity": "ERROR",
                "expected": str(grand_total),
                "actual": str(line_total),
                "message": err_msg
            })
            field_conf["line_items_sum"] = 0.40

    # 3. GST & Tax Reconciliation Check
    if subtotal > Decimal("0.00") and total_tax >= Decimal("0.00") and grand_total > Decimal("0.00"):
        if not close_enough(subtotal + total_tax, grand_total, "2.50"):
            warn_msg = f"GST_RECONCILIATION_MISMATCH: subtotal={subtotal}, tax={total_tax}, grand_total={grand_total}"
            warnings.append(warn_msg)
            detailed_checks.append({
                "code": "GST_RECONCILIATION_MISMATCH",
                "severity": "WARNING",
                "expected": str(subtotal + total_tax),
                "actual": str(grand_total),
                "message": warn_msg
            })
            field_conf["tax_reconciliation"] = 0.65
        else:
            field_conf["tax_reconciliation"] = 0.99

    # 4. Individual Row Consistency & Interpretation Tracking
    row_modes = []
    for row_idx, item in enumerate(items, start=1):
        qty = to_decimal(getattr(item, "quantity", None))
        rate = to_decimal(getattr(item, "unit_price", None))
        amt = to_decimal(getattr(item, "total_amount", None))
        tax_rate = to_decimal(getattr(item, "tax_rate", None))
        unit = (getattr(item, "unit", "") or "").lower()

        # Unrealistic Quantity Check (prevents prices/HSN codes from masquerading as quantities)
        if qty > Decimal("10000.00") and unit not in ["gm", "ml", "mtr", "kg", "mg"]:
            err_msg = f"UNREALISTIC_QUANTITY: row={row_idx}, qty={qty}"
            errors.append(err_msg)
            detailed_checks.append({"code": "UNREALISTIC_QUANTITY", "severity": "ERROR", "row": row_idx, "message": err_msg})

        # Suspicious Qty-Rate Pair Check (wholesale alerts)
        if qty > Decimal("1000.00") and rate > Decimal("1000.00"):
            warn_msg = f"SUSPICIOUS_QTY_RATE_PAIR: row={row_idx}, qty={qty}, rate={rate}"
            warnings.append(warn_msg)
            detailed_checks.append({"code": "SUSPICIOUS_QTY_RATE_PAIR", "severity": "WARNING", "row": row_idx, "message": warn_msg})

        if rate <= Decimal("0.00"):
            warn_msg = f"MISSING_RATE: row={row_idx}"
            warnings.append(warn_msg)
            detailed_checks.append({"code": "MISSING_RATE", "severity": "WARNING", "row": row_idx, "message": warn_msg})

        # Line item multiplication check: Qty * Rate vs Amount (NET / GROSS / SLABS)
        if qty > Decimal("0.00") and rate > Decimal("0.00") and amt > Decimal("0.00"):
            expected_net = (qty * rate).quantize(MONEY_QUANTUM, rounding=ROUND_HALF_UP)
            gross_with_row_tax = (expected_net * (Decimal("1.00") + (tax_rate / Decimal("100.00")))).quantize(MONEY_QUANTUM, rounding=ROUND_HALF_UP)
            
            # Check standard GST slabs (5%, 12%, 18%, 28%) and implied invoice tax rate
            implied_gst_slabs = [Decimal("5.00"), Decimal("12.00"), Decimal("18.00"), Decimal("28.00")]
            if subtotal > Decimal("0.00") and total_tax > Decimal("0.00"):
                implied_gst_slabs.append(((total_tax / subtotal) * Decimal("100.00")).quantize(MONEY_QUANTUM, rounding=ROUND_HALF_UP))
            
            if close_enough(amt, expected_net, "3.00"):
                row_mode = "NET"
            elif close_enough(amt, gross_with_row_tax, "3.00"):
                row_mode = "ROW_TAX_GROSS"
            elif any(close_enough(amt, (expected_net * (Decimal("1.00") + (slab / Decimal("100.00")))).quantize(MONEY_QUANTUM, rounding=ROUND_HALF_UP), "3.00") for slab in implied_gst_slabs):
                row_mode = "SLAB_GROSS"
            else:
                row_mode = "MISMATCH"

            row_modes.append(row_mode)

            if row_mode == "MISMATCH":
                err_msg = f"LINE_AMOUNT_MISMATCH: row={row_idx}, expected={expected_net}, actual={amt}"
                errors.append(err_msg)
                detailed_checks.append({"code": "LINE_AMOUNT_MISMATCH", "severity": "ERROR", "row": row_idx, "message": err_msg})
            else:
                detailed_checks.append({"code": "LINE_AMOUNT_VALID", "severity": "INFO", "row": row_idx, "mode": row_mode})

    # 5. Required Business Metadata
    inv_num = getattr(invoice.metadata, "invoice_number", None) if invoice.metadata else None
    seller_name = getattr(invoice.seller, "name", None) if invoice.seller else None
    
    # Missing Invoice Number blocks automatic acceptance
    if not inv_num:
        err_msg = "MISSING_INVOICE_NUMBER"
        errors.append(err_msg)
        detailed_checks.append({"code": err_msg, "severity": "ERROR", "message": "Invoice number is required for automatic template acceptance."})
        field_conf["invoice_number"] = 0.20
    else:
        field_conf["invoice_number"] = 0.96

    if not seller_name:
        warn_msg = "MISSING_SELLER_NAME"
        warnings.append(warn_msg)
        detailed_checks.append({"code": warn_msg, "severity": "WARNING", "message": "Seller name is missing."})
        field_conf["seller_name"] = 0.40
    else:
        field_conf["seller_name"] = 0.96

    # 6. Syntax-Validated GSTIN Verification
    seller_gstin = normalize_gstin(getattr(invoice.seller, "gstin", None) if invoice.seller else None)
    buyer_gstin = normalize_gstin(getattr(invoice.buyer, "gstin", None) if invoice.buyer else None)

    if seller_gstin:
        if GSTIN_RE.fullmatch(seller_gstin):
            field_conf["seller_gstin"] = 0.96
        else:
            warn_msg = "INVALID_SELLER_GSTIN_FORMAT"
            warnings.append(warn_msg)
            detailed_checks.append({"code": warn_msg, "severity": "WARNING", "gstin": seller_gstin})
            field_conf["seller_gstin"] = 0.25
    else:
        field_conf["seller_gstin"] = 0.60

    if buyer_gstin:
        if GSTIN_RE.fullmatch(buyer_gstin):
            field_conf["buyer_gstin"] = 0.96
        else:
            warn_msg = "INVALID_BUYER_GSTIN_FORMAT"
            warnings.append(warn_msg)
            detailed_checks.append({"code": warn_msg, "severity": "WARNING", "gstin": buyer_gstin})
            field_conf["buyer_gstin"] = 0.25
    else:
        field_conf["buyer_gstin"] = 0.60

    # 7. Calibrated Multi-Factor Evidence Confidence Model
    # S_ocr: Token recognition confidence
    s_ocr = 0.99 if any(eng in getattr(invoice, "engine_used", "") for eng in ["Gemini", "PyMuPDF", "Spatial", "fitz", "PostgreSQL", "Format", "Template"]) else 0.90

    # S_header: Presence and detection of required table headers
    has_headers = len(items) > 0 and any((getattr(it, "item_name", None) or getattr(it, "description", None)) for it in items)
    s_header = 0.98 if has_headers else 0.20

    # S_table: Row continuity, valid descriptions/item names, and numeric sanity
    valid_rows_count = sum(1 for it in items if ((getattr(it, "item_name", None) or getattr(it, "description", None)) and to_decimal(getattr(it, "total_amount", None)) > Decimal("0.00")))
    s_table = round(valid_rows_count / float(len(items)), 3) if items else 0.0

    # S_fields: Invoice Number, Date, and GSTIN validity
    fields_valid_points = 0.0
    total_field_points = 3.0
    if inv_num and len(inv_num) >= 2: fields_valid_points += 1.0
    if getattr(invoice.metadata, "invoice_date", None): fields_valid_points += 1.0
    if seller_gstin and GSTIN_RE.fullmatch(seller_gstin): fields_valid_points += 1.0
    elif not seller_gstin: fields_valid_points += 0.5  # Neutral if non-GST international
    s_fields = round(fields_valid_points / total_field_points, 3)

    # S_line_recon: Arithmetic agreement for rows (Qty * Rate == Total)
    valid_calc_rows = sum(1 for mode in row_modes if mode in ("NET", "ROW_TAX_GROSS", "SLAB_GROSS"))
    s_line_recon = round(valid_calc_rows / float(len(items)), 3) if items else 0.0

    # S_summary_recon: Summary agreement with line sums (<= ₹1.00 tolerance)
    if items and grand_total > Decimal("0.00"):
        if close_enough(line_total, grand_total, "1.00") or close_enough(line_total + total_tax, grand_total, "1.00") or (subtotal > Decimal("0.00") and close_enough(subtotal + total_tax, grand_total, "1.00")):
            s_summary_recon = 1.00
        elif close_enough(line_total, grand_total, "2.50") or close_enough(line_total + total_tax, grand_total, "2.50"):
            s_summary_recon = 0.85
        else:
            s_summary_recon = 0.20
    else:
        s_summary_recon = 0.0

    # Composite Evidence Confidence Score
    if errors:
        overall_confidence = min(0.40, round(0.5 * (s_fields + s_summary_recon), 3))
    else:
        overall_confidence = round(
            (0.20 * s_ocr) +
            (0.15 * s_header) +
            (0.20 * s_table) +
            (0.15 * s_fields) +
            (0.15 * s_line_recon) +
            (0.15 * s_summary_recon),
            3
        )

    field_conf["evidence_factors"] = {
        "s_ocr": s_ocr,
        "s_header": s_header,
        "s_table": s_table,
        "s_fields": s_fields,
        "s_line_recon": s_line_recon,
        "s_summary_recon": s_summary_recon
    }

    is_passed = (len(errors) == 0 and grand_total > Decimal("0.00") and len(items) > 0)
    # Auto-approval requires passing all checks, zero errors, and calibrated confidence >= 0.98
    is_auto_acceptable = (is_passed and len(warnings) == 0 and overall_confidence >= 0.98)
    is_needs_review = (not is_auto_acceptable)

    return ValidationResult(
        passed=is_passed,
        auto_acceptable=is_auto_acceptable,
        needs_review=is_needs_review,
        overall_confidence=overall_confidence,
        errors=errors,
        warnings=warnings,
        field_confidence=field_conf,
        detailed_checks=detailed_checks
    )
