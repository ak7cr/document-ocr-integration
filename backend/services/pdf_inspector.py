from typing import Dict, Any, List
from .pdf_engines import extract_with_fitz, extract_with_pdfplumber, extract_with_pypdf, render_pdf_to_images
from ..schemas import PDFInspectionResult, SpatialBlock


def inspect_pdf_document(pdf_bytes: bytes, filename: str = "document.pdf") -> PDFInspectionResult:
    """
    Runs multi-engine analysis across PyMuPDF, pdfplumber, and pypdf,
    extracting font hierarchies, spatial blocks, tables, and comparisons.
    """
    fitz_res = extract_with_fitz(pdf_bytes)
    plumber_res = extract_with_pdfplumber(pdf_bytes)
    pypdf_res = extract_with_pypdf(pdf_bytes)
    preview_images = render_pdf_to_images(pdf_bytes)

    # Spatial blocks from fitz
    spatial_blocks = []
    font_details = []
    seen_fonts = set()
    block_counter = 1

    for page in fitz_res.get("pages", []):
        for block in page.get("blocks", []):
            text = block.get("text", "").strip()
            if not text:
                continue

            # Determine type (heading, table, paragraph, key_value)
            b_type = "paragraph"
            lines = block.get("lines", [])
            if lines and lines[0]["spans"]:
                span0 = lines[0]["spans"][0]
                font_name = span0.get("font", "")
                font_size = span0.get("size", 10)
                font_key = f"{font_name}_{font_size}"
                if font_key not in seen_fonts:
                    seen_fonts.add(font_key)
                    font_details.append({
                        "font": font_name,
                        "size": round(font_size, 1),
                        "sample": text[:30]
                    })

                if font_size > 14 or "bold" in font_name.lower():
                    b_type = "heading"
                elif ":" in text and len(text.splitlines()) <= 2:
                    b_type = "key_value"

            spatial_blocks.append(SpatialBlock(
                block_id=block_counter,
                page=page["page_number"],
                bbox=[round(v, 2) for v in block["bbox"]],
                text=text,
                type=b_type,
                lines=lines
            ))
            block_counter += 1

    return PDFInspectionResult(
        filename=filename,
        page_count=fitz_res["page_count"],
        metadata=pypdf_res.get("metadata", {}),
        fitz_text=fitz_res["full_text"],
        pdfplumber_text=plumber_res["full_text"],
        pypdf_text=pypdf_res["full_text"],
        tables=plumber_res.get("tables", []),
        spatial_blocks=spatial_blocks,
        font_details=font_details,
        images_found=len(preview_images),
        preview_images=[img_b64 for _, img_b64 in preview_images]
    )
