import io
import re
import numpy as np
from PIL import Image
from typing import List, Dict, Any, Tuple

_paddle_reader = None
_easyocr_reader = None


import sys

_paddle_tried = False

def get_paddle_reader():
    """
    Lazy-load PaddleOCR. Cached after first use.
    """
    global _paddle_reader, _paddle_tried
    if not _paddle_tried:
        _paddle_tried = True
        # PaddlePaddle static engine is not yet available for Python >= 3.13 on Windows
        if sys.version_info >= (3, 13):
            _paddle_reader = None
            return None
        try:
            from paddleocr import PaddleOCR
            _paddle_reader = PaddleOCR(lang="en")
        except Exception:
            _paddle_reader = None
    return _paddle_reader


def get_easyocr_reader():
    """
    Fallback OCR reader using EasyOCR with INT8 CPU quantization.
    """
    global _easyocr_reader
    if _easyocr_reader is None:
        try:
            import easyocr
            _easyocr_reader = easyocr.Reader(['en'], gpu=False, quantize=True)
        except Exception:
            try:
                import easyocr
                _easyocr_reader = easyocr.Reader(['en'], gpu=False)
            except Exception as exc:
                print(f"⚠️ [EasyOCR] Failed to load: {exc}")
                _easyocr_reader = None
    return _easyocr_reader
    return _easyocr_reader


def perform_image_ocr(image_input) -> Tuple[str, List[Dict[str, Any]], List[str]]:
    """
    Runs high-precision PaddleOCR with spatial layout awareness (falling back to EasyOCR).
    Returns:
      - full_text: reconstructed full text
      - words_data: list of bounding boxes with text, coords [x0, y0, x1, y1] and confidence
      - structured_lines: list of ordered lines
    """
    if isinstance(image_input, bytes):
        pil_img = Image.open(io.BytesIO(image_input))
    else:
        pil_img = image_input

    if pil_img.mode != "RGB":
        pil_img = pil_img.convert("RGB")

    img_np = np.array(pil_img)
    words_data = []
    active_engine = "Unknown"

    # 1. Primary Engine: PaddleOCR
    paddle = get_paddle_reader()
    if paddle:
        try:
            results = paddle.ocr(img_np, cls=True)
            if results:
                for page in results:
                    if not page:
                        continue
                    for item in page:
                        bbox_pts, (text, conf) = item
                        t = str(text).strip()
                        if not t:
                            continue
                        xs = [p[0] for p in bbox_pts]
                        ys = [p[1] for p in bbox_pts]
                        words_data.append({
                            "text": t,
                            "x0": float(min(xs)),
                            "y0": float(min(ys)),
                            "x1": float(max(xs)),
                            "y1": float(max(ys)),
                            "prob": float(conf),
                            "engine": "PaddleOCR"
                        })
                if words_data:
                    active_engine = "PaddleOCR (Primary Engine)"
        except Exception as exc:
            print(f"[PaddleOCR] Extraction error: {exc}. Falling back to EasyOCR.")
            words_data = []

    # 2. Fallback Engine: EasyOCR if PaddleOCR yielded no words
    if not words_data:
        easy_reader = get_easyocr_reader()
        if easy_reader:
            try:
                active_engine = "EasyOCR (Fallback CPU Engine)"
                # Optimized parameters: lower threshold and higher magnification to capture single digits (1, 2, 3) and symbols
                results = easy_reader.readtext(
                    img_np,
                    batch_size=8,
                    workers=0,
                    min_size=2,
                    text_threshold=0.35,
                    low_text=0.25,
                    link_threshold=0.3,
                    mag_ratio=1.0,
                    contrast_ths=0.1,
                    adjust_contrast=0.5
                )
                for bbox, text, prob in results:
                    t = str(text).strip()
                    if not t:
                        continue
                    x_coords = [p[0] for p in bbox]
                    y_coords = [p[1] for p in bbox]
                    words_data.append({
                        "text": t,
                        "x0": float(min(x_coords)),
                        "y0": float(min(y_coords)),
                        "x1": float(max(x_coords)),
                        "y1": float(max(y_coords)),
                        "prob": float(prob),
                        "engine": "EasyOCR"
                    })
            except Exception as exc:
                print(f"[EasyOCR] Extraction error: {exc}")

    # Sort words by vertical Y, then horizontal X
    words_data = sorted(words_data, key=lambda w: (w["y0"], w["x0"]))
    
    # Cluster into lines using dynamic line-height threshold
    lines_list = []
    if words_data:
        curr_line = [words_data[0]]
        for w in words_data[1:]:
            prev_y_center = (curr_line[-1]["y0"] + curr_line[-1]["y1"]) / 2.0
            w_y_center = (w["y0"] + w["y1"]) / 2.0
            h = max(curr_line[-1]["y1"] - curr_line[-1]["y0"], 12)
            
            if abs(w_y_center - prev_y_center) < (h * 0.65):
                curr_line.append(w)
            else:
                curr_line = sorted(curr_line, key=lambda item: item["x0"])
                lines_list.append(curr_line)
                curr_line = [w]
        if curr_line:
            curr_line = sorted(curr_line, key=lambda item: item["x0"])
            lines_list.append(curr_line)

    structured_lines = ["  ".join(w["text"] for w in line) for line in lines_list]
    full_text = "\n".join(structured_lines)
    
    return full_text, words_data, structured_lines
