"""Extract embedded PDF text; render pages and OCR only when requested.
Also supports direct OCR of image files (JPEG, PNG, WebP, TIFF)."""
import json
import sys

import pymupdf as fitz
import pdfplumber
import pytesseract
from PIL import Image


def embedded_text(path):
    pages = []
    with pdfplumber.open(path) as pdf:
        for page in pdf.pages:
            pages.append(page.extract_text(x_tolerance=2, y_tolerance=3) or "")
    return "\n\n".join(pages), len(pages)


def ocr_text(path):
    document = fitz.open(path)
    pages = []
    for page in document:
        pixmap = page.get_pixmap(matrix=fitz.Matrix(2, 2), alpha=False)
        image = Image.frombytes("RGB", (pixmap.width, pixmap.height), pixmap.samples)
        pages.append(pytesseract.image_to_string(image))
    count = len(document)
    document.close()
    return "\n\n".join(pages), count


def image_ocr(path):
    """Run Tesseract directly on an image file (JPEG, PNG, WebP, TIFF, etc.)."""
    image = Image.open(path).convert("RGB")
    text = pytesseract.image_to_string(image)
    return text, 1


if __name__ == "__main__":
    try:
        mode, path = sys.argv[1], sys.argv[2]
        if mode == "pdfplumber":
            text, pages = embedded_text(path)
        elif mode == "image_ocr":
            text, pages = image_ocr(path)
        else:
            text, pages = ocr_text(path)
        print(json.dumps({"ok": True, "text": text, "pages": pages}))
    except Exception as error:
        print(json.dumps({"ok": False, "error": str(error)}))
