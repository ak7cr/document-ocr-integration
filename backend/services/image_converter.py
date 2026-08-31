import io
import pymupdf as fitz
from PIL import Image, ImageEnhance, ImageOps, ImageFilter
from typing import List, Tuple, Dict, Any, Optional
import base64


def preprocess_image(img: Image.Image) -> Image.Image:
    """
    Backward-compatible: applies full OpenCV pipeline and returns the PIL image only.
    Use preprocess_image_full() when you also need the quality metadata.
    """
    out, _ = preprocess_image_full(img)
    return out


def preprocess_image_full(img: Image.Image):
    """
    Runs the full 6-stage OpenCV pipeline and returns (pil_img, cv_meta).
    cv_meta contains blur scores, skew angle, binarization flags, and grid detection results.
    Falls back to PIL-only enhancement if OpenCV fails.
    """
    try:
        from backend.services.cv_preprocessor import preprocess_invoice_image_cv
        return preprocess_invoice_image_cv(img)
    except Exception as exc:
        print(f"[cv_preprocessor] OpenCV pipeline failed ({exc}), falling back to PIL")
        if img.mode != "RGB":
            img = img.convert("RGB")
        enhancer = ImageEnhance.Contrast(img)
        img_c = enhancer.enhance(1.4)
        img_s = ImageEnhance.Sharpness(img_c).enhance(1.3)
        fallback_meta = {
            "blur_score_before": None, "blur_score_after": None, "is_sharp": None,
            "skew_angle_deg": 0.0, "skew_corrected": False,
            "denoise_applied": False, "binarized": False,
            "ocr_mode": "PIL_FALLBACK",
            "table_grid": {"has_grid": False, "table_boxes": [], "horizontal_lines_count": 0, "vertical_lines_count": 0},
        }
        return img_s, fallback_meta


def convert_images_to_pdf(images_bytes_list: List[bytes]) -> bytes:
    """
    Converts a list of image byte arrays into a single standardized PDF document.
    """
    doc = fitz.open()

    for img_bytes in images_bytes_list:
        img = Image.open(io.BytesIO(img_bytes))
        img = preprocess_image(img)
        
        buffered = io.BytesIO()
        img.save(buffered, format="JPEG", quality=95)
        clean_bytes = buffered.getvalue()

        # Create a PDF page with exact image dimensions
        img_rect = fitz.Rect(0, 0, img.width, img.height)
        page = doc.new_page(width=img.width, height=img.height)
        page.insert_image(img_rect, stream=clean_bytes)

    pdf_bytes = doc.tobytes()
    doc.close()
    return pdf_bytes


def create_searchable_pdf(image_bytes: bytes, recognized_words: List[Dict[str, Any]]) -> bytes:
    """
    Creates a searchable PDF with visible image and invisible OCR text layer.
    """
    img = Image.open(io.BytesIO(image_bytes))
    doc = fitz.open()
    page = doc.new_page(width=img.width, height=img.height)
    
    # Insert background image
    page.insert_image(fitz.Rect(0, 0, img.width, img.height), stream=image_bytes)

    # Insert invisible text blocks for searchability
    for word in recognized_words:
        text = word.get("text", "").strip()
        if not text:
            continue
        x0 = float(word.get("x0", 0))
        y0 = float(word.get("y0", 0))
        x1 = float(word.get("x1", x0 + 20))
        y1 = float(word.get("y1", y0 + 12))
        
        fontsize = max(8.0, y1 - y0)
        # Render transparent / white text at exact position
        try:
            page.insert_text(
                fitz.Point(x0, y1),
                text,
                fontsize=fontsize,
                color=(0, 0, 0),
                render_mode=3  # 3 = invisible text
            )
        except Exception:
            pass

    pdf_bytes = doc.tobytes()
    doc.close()
    return pdf_bytes


def image_to_base64(img: Image.Image) -> str:
    buffered = io.BytesIO()
    img.save(buffered, format="JPEG", quality=90)
    return "data:image/jpeg;base64," + base64.b64encode(buffered.getvalue()).decode("utf-8")
