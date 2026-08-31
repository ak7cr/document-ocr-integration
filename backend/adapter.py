"""Compatibility layer: exposes OUR React frontend's API contract on top of the
ported invoice-ocr-c++ FastAPI backend. The frontend keeps calling the same
endpoints (/api/extractions, /api/templates, /api/extractions/:id/corrections)
it used with the old Express server; this adapter runs their engine and maps
their InvoiceData model into our response shape."""
import io
import uuid
from typing import List, Dict, Any, Optional

from fastapi import APIRouter, UploadFile, File, Form, HTTPException
from starlette.datastructures import UploadFile as StarletteUploadFile
from sqlalchemy.orm import Session

from backend.schemas import InvoiceData
from backend import database as dbmod
from backend.services.validator import validate_invoice

router = APIRouter()


def _session() -> Session:
    """Resolve the session factory lazily — init_db() runs at app startup, after
    this module is imported, so the module-level SessionLocal is None here."""
    if dbmod.SessionLocal is None:
        dbmod.init_db()
    return dbmod.SessionLocal()

MIME = {
    "pdf": "application/pdf", "png": "image/png", "jpg": "image/jpeg",
    "jpeg": "image/jpeg", "webp": "image/webp", "tiff": "image/tiff", "bmp": "image/bmp",
}
ACCEPTED = set(MIME.values())


def fmt(x: Any) -> str:
    """Render a number like their engine: trim trailing zeros, keep ints clean."""
    if x is None:
        return ""
    if isinstance(x, bool):
        return str(x)
    if isinstance(x, float):
        if x == int(x):
            return str(int(x))
        return ("%.2f" % x).rstrip("0").rstrip(".")
    return str(x)


def our_line_items(inv: InvoiceData) -> List[dict]:
    items = []
    for it in inv.line_items:
        items.append({
            "serialNo": str(it.sno or ""),
            "itemName": (it.item_name or it.description or "").strip(),
            "hsnSac": it.hsn_sac or "",
            "quantity": fmt(it.quantity),
            "unit": it.unit or "",
            "rate": fmt(it.unit_price),
            "discount": fmt(it.discount),
            "tax": fmt(it.tax_rate),
            "taxableValue": fmt(it.taxable_value),
            "cgstAmount": "",
            "sgstAmount": "",
            "igstAmount": "",
            "amount": fmt(it.total_amount),
            "grossAmount": "",
        })
    return items


def invoice_to_our_data(inv: InvoiceData) -> dict:
    meta = inv.metadata
    return {
        "documentType": "",
        "documentNumber": meta.invoice_number or "",
        "documentDate": meta.invoice_date or "",
        "vendorName": (inv.seller.name if inv.seller else None) or "",
        "customerName": (inv.buyer.name if inv.buyer else None) or "",
        "currency": meta.currency or "",
        "subtotalAmount": fmt(inv.summary.subtotal if inv.summary else 0),
        "taxAmount": fmt(inv.summary.total_gst if inv.summary else 0),
        "totalAmount": fmt(inv.summary.grand_total if inv.summary else 0),
        "lineItems": our_line_items(inv),
    }


async def _run_their_pipeline(content: bytes, filename: str, mode: str = "hybrid", db: Session = None) -> InvoiceData:
    """Run their full /api/extract-invoice pipeline (template fast-path -> local
    cell-grid -> Claude/Gemini fallback) and return the InvoiceData (already
    logged to their DB with an extraction_log_id)."""
    from backend.main import extract_invoice
    uf = StarletteUploadFile(filename=filename, file=io.BytesIO(content))
    return await extract_invoice(file=uf, pipeline_mode=mode, use_ai_vision=False, api_key=None, db=db)


def _build_attempts(inv: InvoiceData, log: Optional[ExtractionLog], confidence: float) -> List[dict]:
    engine = (inv.engine_used or "")
    attempts: List[dict] = []
    attempts.append({"name": "local_ocr", "status": "completed", "confidence": None,
                     "detail": f"{len(inv.line_items)} items via {engine}" if inv.line_items else engine})
    if log and log.template_id:
        attempts.append({"name": "saved_template", "status": "completed", "confidence": round(confidence, 2),
                         "detail": f"Template #{log.template_id} ({log.match_strategy})"})
    if "Claude" in engine:
        attempts.append({"name": "ai_fallback_claude", "status": "completed", "confidence": round(confidence, 2), "detail": engine})
    elif "Gemini" in engine:
        attempts.append({"name": "ai_fallback_gemini", "status": "completed", "confidence": round(confidence, 2), "detail": engine})
    vs = inv.validation_summary or {}
    attempts.append({"name": "validation", "status": "completed", "confidence": round(confidence, 2),
                     "detail": f"passed={vs.get('passed')}" if "passed" in vs else None})
    return attempts


@router.get("/api/health")
def health():
    return {"ok": True}


@router.post("/api/extractions")
async def create_extraction(file: UploadFile = File(...), extractionMethod: str = Form("auto")):
    if file.content_type not in ACCEPTED:
        raise HTTPException(400, "Upload one PDF or image file (JPG, PNG, WebP, TIFF).")
    content = await file.read()
    filename = file.filename or "invoice"
    # Map our frontend's extraction method to their pipeline mode.
    mode = {"ai": "ai", "local": "local"}.get(extractionMethod, "hybrid")

    db = _session()
    try:
        inv = await _run_their_pipeline(content, filename, mode=mode, db=db)
        vs = inv.validation_summary or {}
        confidence = vs.get("overall_confidence")
        if confidence is None:
            confidence = validate_invoice(inv).overall_confidence
        confidence = float(confidence or 0.0)

        log = None
        if inv.extraction_log_id:
            log = db.get(dbmod.ExtractionLog, inv.extraction_log_id)

        template_id = log.template_id if (log and log.template_id) else None
        if "Claude" in (inv.engine_used or ""):
            source = "ai_fallback_claude"
        elif "Gemini" in (inv.engine_used or ""):
            source = "ai_fallback_gemini"
        elif template_id:
            source = "saved_template"
        else:
            source = "local_ocr"

        validation = {"ok": bool(vs.get("passed")), "issues": vs.get("errors", [])} if vs else {}
        return {
            "id": str(inv.extraction_log_id) if inv.extraction_log_id else str(uuid.uuid4()),
            "fileName": filename,
            "mimeType": file.content_type,
            "data": invoice_to_our_data(inv),
            "source": source,
            "templateId": template_id,
            "confidence": round(confidence, 3),
            "attempts": _build_attempts(inv, log, confidence),
            "rawText": inv.raw_text or "",
            "validation": validation,
            "persisted": bool(inv.extraction_log_id),
            "proposedTemplate": None,
            "template": {"id": template_id, "name": log and "Template" or None} if template_id else None,
        }
    finally:
        db.close()


@router.post("/api/templates")
async def save_template(payload: dict):
    name = payload.get("name")
    fp = payload.get("fingerprint") or {}
    anchors = fp.get("anchors") or (fp.get("structure") or {}).get("keyLabels") or []
    if not name or not anchors:
        raise HTTPException(400, "A template name and fingerprint anchors are required.")
    structure = fp.get("structure") or {}
    db = _session()
    try:
        existing = db.query(dbmod.InvoiceTemplate).filter(dbmod.InvoiceTemplate.template_name == name).first()
        if existing:
            db.close()
            return {"id": existing.id, "name": existing.template_name}
        tpl = dbmod.InvoiceTemplate(
            template_name=name,
            format_type="GST_TAX_INVOICE" if "GST" in name.upper() else "STANDARD_INVOICE",
            status="ACTIVE",
            anchor_keywords=list(anchors),
            column_boundaries=structure.get("columnBoundaries"),
            extraction_rules={"mid_x_ratio": 0.48},
            table_columns=structure.get("columnFields") or [],
            known_vendors=[],
            hit_count=1,
            success_count=1,
            failure_count=0,
            is_verified=True,
            verified_by="frontend_save",
            verified_at=__import__("datetime").datetime.utcnow(),
        )
        db.add(tpl)
        db.commit()
        db.refresh(tpl)
        return {"id": tpl.id, "name": tpl.template_name}
    finally:
        db.close()


@router.patch("/api/extractions/{extraction_id}/corrections")
async def submit_corrections(extraction_id: int, payload: dict):
    db = _session()
    try:
        log = db.get(dbmod.ExtractionLog, extraction_id)
        if not log:
            raise HTTPException(404, "Extraction not found.")
        review = dbmod.ExtractionReview(
            extraction_log_id=extraction_id,
            action="CORRECTED",
            corrected_fields=payload,
            reviewed_by="accountant",
        )
        db.add(review)
        db.commit()
        return {"ok": True, "id": review.id}
    finally:
        db.close()
