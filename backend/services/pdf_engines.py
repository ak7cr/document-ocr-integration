import io
import pymupdf as fitz
import pdfplumber
import pypdf
from PIL import Image
from typing import Dict, Any, List, Tuple
import base64


def render_pdf_to_images(pdf_bytes: bytes, dpi: int = 200) -> List[Tuple[Image.Image, str]]:
    """
    Renders each page of a PDF as a PIL Image and base64 PNG data URL.
    """
    doc = fitz.open(stream=pdf_bytes, filetype="pdf")
    results = []
    zoom = dpi / 72.0
    matrix = fitz.Matrix(zoom, zoom)

    for page in doc:
        pix = page.get_pixmap(matrix=matrix, alpha=False)
        img = Image.frombytes("RGB", [pix.width, pix.height], pix.samples)
        
        buffered = io.BytesIO()
        img.save(buffered, format="JPEG", quality=90)
        img_b64 = "data:image/jpeg;base64," + base64.b64encode(buffered.getvalue()).decode("utf-8")
        results.append((img, img_b64))

    doc.close()
    return results


def extract_with_fitz(pdf_bytes: bytes) -> Dict[str, Any]:
    """
    Extracts text, detailed blocks, spans, fonts, and word bounding boxes using PyMuPDF (fitz).
    """
    doc = fitz.open(stream=pdf_bytes, filetype="pdf")
    pages_data = []
    full_text_list = []

    for page_idx, page in enumerate(doc):
        # Extract text as blocks with coordinates
        page_dict = page.get_text("dict")
        page_text = page.get_text("text")
        full_text_list.append(page_text)
        
        # Word-level coordinates: (x0, y0, x1, y1, word, block_no, line_no, word_no)
        raw_words = page.get_text("words")
        words_data = [
            {
                "x0": w[0],
                "y0": w[1],
                "x1": w[2],
                "y1": w[3],
                "text": w[4],
                "block": w[5],
                "line": w[6],
                "page": page_idx + 1
            }
            for w in raw_words
        ]

        blocks_data = []
        for b_idx, block in enumerate(page_dict.get("blocks", [])):
            if "lines" in block:
                block_text = ""
                lines_data = []
                for line in block["lines"]:
                    line_text = "".join(span["text"] for span in line.get("spans", []))
                    block_text += line_text + "\n"
                    lines_data.append({
                        "bbox": line["bbox"],
                        "text": line_text,
                        "spans": [
                            {
                                "text": s["text"],
                                "font": s.get("font", ""),
                                "size": s.get("size", 0),
                                "color": s.get("color", 0),
                                "flags": s.get("flags", 0)
                            }
                            for s in line.get("spans", [])
                        ]
                    })
                blocks_data.append({
                    "block_id": b_idx,
                    "bbox": block["bbox"],
                    "text": block_text.strip(),
                    "lines": lines_data,
                    "page": page_idx + 1
                })

        pages_data.append({
            "page_number": page_idx + 1,
            "width": page.rect.width,
            "height": page.rect.height,
            "text": page_text,
            "blocks": blocks_data,
            "words": words_data
        })

    doc.close()
    return {
        "engine": "fitz (PyMuPDF)",
        "full_text": "\n\n--- PAGE BREAK ---\n\n".join(full_text_list),
        "pages": pages_data,
        "page_count": len(pages_data)
    }


def extract_with_pdfplumber(pdf_bytes: bytes) -> Dict[str, Any]:
    """
    Extracts text, table structures, and character layout using pdfplumber.
    """
    pages_data = []
    tables_found = []
    full_text_list = []

    with pdfplumber.open(io.BytesIO(pdf_bytes)) as pdf:
        for page_idx, page in enumerate(pdf.pages):
            page_text = page.extract_text(layout=True) or ""
            full_text_list.append(page_text)
            
            # Extract tables with exact cell bounding boxes
            extracted_tables = page.extract_tables()
            table_objects = page.find_tables()
            
            for t_idx, t_obj in enumerate(table_objects):
                tables_found.append({
                    "page": page_idx + 1,
                    "table_index": t_idx + 1,
                    "bbox": t_obj.bbox,
                    "data": t_obj.extract()
                })

            pages_data.append({
                "page_number": page_idx + 1,
                "width": page.width,
                "height": page.height,
                "text": page_text,
                "table_count": len(table_objects)
            })

    return {
        "engine": "pdfplumber",
        "full_text": "\n\n--- PAGE BREAK ---\n\n".join(full_text_list),
        "pages": pages_data,
        "tables": tables_found,
        "page_count": len(pages_data)
    }


def extract_with_pypdf(pdf_bytes: bytes) -> Dict[str, Any]:
    """
    Extracts raw text streams and document metadata using pypdf.
    """
    reader = pypdf.PdfReader(io.BytesIO(pdf_bytes))
    full_text_list = []
    pages_data = []

    for page_idx, page in enumerate(reader.pages):
        text = page.extract_text() or ""
        full_text_list.append(text)
        pages_data.append({
            "page_number": page_idx + 1,
            "text": text
        })

    meta = {}
    if reader.metadata:
        for k, v in reader.metadata.items():
            meta[str(k).replace("/", "")] = str(v)

    return {
        "engine": "pypdf",
        "full_text": "\n\n--- PAGE BREAK ---\n\n".join(full_text_list),
        "pages": pages_data,
        "metadata": meta,
        "page_count": len(reader.pages)
    }
