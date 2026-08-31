from typing import List, Optional, Dict, Any
from pydantic import BaseModel, Field


class BoundingBox(BaseModel):
    id: Optional[str] = None
    label: str
    text: str
    x0: float
    y0: float
    x1: float
    y1: float
    page: int = 1
    confidence: Optional[float] = 1.0


class PartyDetails(BaseModel):
    name: Optional[str] = None
    business_name: Optional[str] = None
    gstin: Optional[str] = None
    pan: Optional[str] = None
    vat_id: Optional[str] = None
    address: Optional[str] = None
    city: Optional[str] = None
    state: Optional[str] = None
    pin_code: Optional[str] = None
    country: Optional[str] = None
    email: Optional[str] = None
    phone: Optional[str] = None
    mobile: Optional[str] = None
    website: Optional[str] = None


class LineItem(BaseModel):
    sno: Optional[str] = None
    item_name: Optional[str] = None
    description: Optional[str] = None
    hsn_sac: Optional[str] = None
    quantity: Optional[float] = 1.0
    unit: Optional[str] = None
    unit_price: Optional[float] = 0.0
    discount: Optional[float] = 0.0
    tax_rate: Optional[float] = 0.0
    tax_amount: Optional[float] = 0.0
    taxable_value: Optional[float] = 0.0
    total_amount: float = 0.0
    bbox: Optional[List[float]] = None


class TaxDetail(BaseModel):
    hsn_sac: Optional[str] = None
    taxable_value: Optional[float] = 0.0
    cgst_rate: Optional[float] = 0.0
    cgst_amount: Optional[float] = 0.0
    sgst_rate: Optional[float] = 0.0
    sgst_amount: Optional[float] = 0.0
    igst_rate: Optional[float] = 0.0
    igst_amount: Optional[float] = 0.0
    total_tax_amount: Optional[float] = 0.0


class InvoiceSummary(BaseModel):
    subtotal: float = 0.0
    total_discount: Optional[float] = 0.0
    total_gst: Optional[float] = 0.0
    cgst_total: Optional[float] = 0.0
    sgst_total: Optional[float] = 0.0
    igst_total: Optional[float] = 0.0
    shipping_charges: Optional[float] = 0.0
    packing_charges: Optional[float] = 0.0
    extra_charges: Optional[float] = 0.0
    grand_total: float = 0.0
    total_in_words: Optional[str] = None
    amount_paid: Optional[float] = 0.0
    balance_due: Optional[float] = 0.0


class InvoiceMetadata(BaseModel):
    invoice_type: Optional[str] = "TAX INVOICE"
    invoice_number: Optional[str] = None
    invoice_date: Optional[str] = None
    due_date: Optional[str] = None
    po_number: Optional[str] = None
    order_date: Optional[str] = None
    currency: Optional[str] = "INR"
    currency_symbol: Optional[str] = "₹"
    payment_terms: Optional[str] = None


class PaymentInfo(BaseModel):
    bank_name: Optional[str] = None
    account_holder: Optional[str] = None
    account_number: Optional[str] = None
    ifsc_code: Optional[str] = None
    swift_code: Optional[str] = None
    branch: Optional[str] = None
    upi_id: Optional[str] = None


class InvoiceData(BaseModel):
    metadata: InvoiceMetadata = Field(default_factory=InvoiceMetadata)
    seller: PartyDetails = Field(default_factory=PartyDetails)
    buyer: PartyDetails = Field(default_factory=PartyDetails)
    shipping_address: Optional[PartyDetails] = None
    line_items: List[LineItem] = Field(default_factory=list)
    tax_breakdown: List[TaxDetail] = Field(default_factory=list)
    summary: InvoiceSummary = Field(default_factory=InvoiceSummary)
    payment: PaymentInfo = Field(default_factory=PaymentInfo)
    terms_and_conditions: Optional[str] = None
    notes: Optional[str] = None
    raw_text: Optional[str] = None
    markdown_content: Optional[str] = None
    bounding_boxes: List[BoundingBox] = Field(default_factory=list)
    engine_used: str = "local"
    processing_time_ms: Optional[float] = None
    timing_breakdown: Optional[Dict[str, Any]] = Field(default_factory=dict)
    validation_summary: Optional[Dict[str, Any]] = None
    document_preview_urls: List[str] = Field(default_factory=list)
    extraction_log_id: Optional[int] = None
    status: Optional[str] = "NEEDS_REVIEW"


class SpatialBlock(BaseModel):
    block_id: int
    page: int
    bbox: List[float]
    text: str
    type: str  # heading, table, paragraph, key_value
    lines: List[Dict[str, Any]] = Field(default_factory=list)


class PDFInspectionResult(BaseModel):
    filename: str
    page_count: int
    metadata: Dict[str, Any] = Field(default_factory=dict)
    fitz_text: str
    pdfplumber_text: str
    pypdf_text: str
    tables: List[Dict[str, Any]] = Field(default_factory=list)
    spatial_blocks: List[SpatialBlock] = Field(default_factory=list)
    font_details: List[Dict[str, Any]] = Field(default_factory=list)
    images_found: int = 0
    preview_images: List[str] = Field(default_factory=list)


class ImageConvertRequest(BaseModel):
    image_base64_list: List[str]


class SearchablePdfRequest(BaseModel):
    image_base64: str
    ocr_language: str = "eng"


class ReviewRequest(BaseModel):
    action: str = Field(default="APPROVED", description="Review action: APPROVED, CORRECTED, or REJECTED")
    reviewer_note: Optional[str] = Field(default=None, description="Optional note by human reviewer")
    reviewed_by: Optional[str] = Field(default="accountant", description="Name or role of reviewer")
    corrected_fields: Optional[Dict[str, Any]] = Field(default=None, description="Mapping of corrected fields, e.g. {'grand_total': {'before': 207, 'after': 3394}}")

