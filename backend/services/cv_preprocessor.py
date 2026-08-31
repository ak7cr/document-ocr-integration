import io
import cv2
import numpy as np
from PIL import Image
from typing import Tuple, Dict, Any


def pil_to_cv2(pil_img: Image.Image) -> np.ndarray:
    """Converts PIL Image to OpenCV BGR numpy array."""
    if pil_img.mode == "RGBA":
        pil_img = pil_img.convert("RGB")
    return cv2.cvtColor(np.array(pil_img), cv2.COLOR_RGB2BGR)


def cv2_to_pil(cv_img: np.ndarray) -> Image.Image:
    """Converts OpenCV BGR / Grayscale numpy array to PIL Image."""
    if len(cv_img.shape) == 2:
        return Image.fromarray(cv_img)
    return Image.fromarray(cv2.cvtColor(cv_img, cv2.COLOR_BGR2RGB))


def calculate_blur_score(cv_img: np.ndarray) -> float:
    """Laplacian variance blur score. >100=sharp, 50-100=ok, <50=noisy."""
    gray = cv2.cvtColor(cv_img, cv2.COLOR_BGR2GRAY) if len(cv_img.shape) == 3 else cv_img
    return round(float(cv2.Laplacian(gray, cv2.CV_64F).var()), 2)


def estimate_and_correct_skew(cv_img: np.ndarray) -> Tuple[np.ndarray, float]:
    """
    Estimates document skew angle using minAreaRect on edge contours and corrects it.
    Clamps correction angle to [-45.0, 45.0] degrees.
    """
    gray = cv2.cvtColor(cv_img, cv2.COLOR_BGR2GRAY) if len(cv_img.shape) == 3 else cv_img
    blurred = cv2.GaussianBlur(gray, (5, 5), 0)
    thresh = cv2.threshold(blurred, 0, 255, cv2.THRESH_BINARY_INV + cv2.THRESH_OTSU)[1]

    # Find all coordinates of non-zero pixels
    coords = np.column_stack(np.where(thresh > 0))
    if len(coords) < 100:
        return cv_img, 0.0

    rect = cv2.minAreaRect(coords)
    angle = rect[-1]

    # OpenCV minAreaRect returns angles in [-90, 0)
    if angle < -45:
        angle = -(90 + angle)
    else:
        angle = -angle

    # Only rotate if skew is noticeable (> 0.5 degrees and < 45 degrees)
    if abs(angle) > 0.5 and abs(angle) < 45.0:
        (h, w) = cv_img.shape[:2]
        center = (w // 2, h // 2)
        M = cv2.getRotationMatrix2D(center, angle, 1.0)
        rotated = cv2.warpAffine(
            cv_img, M, (w, h),
            flags=cv2.INTER_CUBIC,
            borderMode=cv2.BORDER_REPLICATE
        )
        return rotated, round(float(angle), 2)

    return cv_img, 0.0


def enhance_contrast_and_denoise(cv_img: np.ndarray) -> np.ndarray:
    """
    Applies CLAHE (Contrast Limited Adaptive Histogram Equalization) and subtle denoising.
    """
    gray = cv2.cvtColor(cv_img, cv2.COLOR_BGR2GRAY) if len(cv_img.shape) == 3 else cv_img
    
    # Adaptive Histogram Equalization
    clahe = cv2.createCLAHE(clipLimit=2.0, tileGridSize=(8, 8))
    enhanced_gray = clahe.apply(gray)
    
    # Return 3-channel image for OCR compatibility
    return cv2.cvtColor(enhanced_gray, cv2.COLOR_GRAY2BGR)


def detect_table_grid_lines(cv_img: np.ndarray) -> Dict[str, Any]:
    """
    Uses morphological kernels to detect horizontal and vertical table grid lines.
    Returns detected line masks and bounding boxes of potential table regions.
    """
    gray = cv2.cvtColor(cv_img, cv2.COLOR_BGR2GRAY) if len(cv_img.shape) == 3 else cv_img
    thresh = cv2.adaptiveThreshold(
        ~gray, 255, cv2.ADAPTIVE_THRESH_MEAN_C, cv2.THRESH_BINARY, 15, -2
    )

    h, w = gray.shape[:2]

    # Horizontal lines kernel
    h_kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (w // 30, 1))
    h_lines = cv2.morphologyEx(thresh, cv2.MORPH_OPEN, h_kernel)

    # Vertical lines kernel
    v_kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (1, h // 30))
    v_lines = cv2.morphologyEx(thresh, cv2.MORPH_OPEN, v_kernel)

    # Combined table grid mask
    table_mask = cv2.add(h_lines, v_lines)
    
    # Find table bounding boxes
    contours, _ = cv2.findContours(table_mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    table_boxes = []
    for cnt in contours:
        x, y, bw, bh = cv2.boundingRect(cnt)
        if bw > (w * 0.40) and bh > (h * 0.10):  # Must cover at least 40% width and 10% height
            table_boxes.append({"x0": x, "y0": y, "x1": x + bw, "y1": y + bh, "w": bw, "h": bh})

    return {
        "has_grid": len(table_boxes) > 0,
        "table_boxes": table_boxes,
        "horizontal_lines_count": len(cv2.findContours(h_lines, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)[0]),
        "vertical_lines_count": len(cv2.findContours(v_lines, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)[0])
    }


def _maybe_denoise(cv_img: np.ndarray, blur_score: float) -> Tuple[np.ndarray, bool]:
    """Bilateral filter only on noisy scans (blur_score < 80)."""
    if blur_score < 80.0:
        return cv2.bilateralFilter(cv_img, d=5, sigmaColor=30, sigmaSpace=30), True
    return cv_img, False


def _maybe_binarize(gray_clahe: np.ndarray) -> Tuple[np.ndarray, bool]:
    """Adaptive Gaussian threshold only on low-contrast / faded scans."""
    mean_val = float(np.mean(gray_clahe))
    std_val = float(np.std(gray_clahe))
    if (std_val < 55.0) or (mean_val < 160.0):
        binary = cv2.adaptiveThreshold(
            gray_clahe, 255,
            cv2.ADAPTIVE_THRESH_GAUSSIAN_C,
            cv2.THRESH_BINARY,
            blockSize=21, C=8
        )
        return binary, True
    return gray_clahe, False


def _sharpen(gray: np.ndarray) -> np.ndarray:
    """Unsharp mask — boosts thin strokes without halation."""
    blurred = cv2.GaussianBlur(gray, (0, 0), sigmaX=1.5)
    return cv2.addWeighted(gray, 1.6, blurred, -0.6, 0)


def preprocess_invoice_image_cv(pil_img: Image.Image) -> Tuple[Image.Image, Dict[str, Any]]:
    """
    Six-stage OpenCV preprocessing pipeline for invoice OCR.
    Each heavy stage is adaptive — clean digital images skip it.

      1. Deskew     — minAreaRect + affine rotation
      2. Denoise    — bilateral filter (only noisy scans, blur_score < 80)
      3. CLAHE      — adaptive histogram equalization
      4. Binarize   — adaptive Gaussian threshold (only low-contrast / faded)
      5. Sharpen    — unsharp mask (only non-binarized path)
      6. Grid detect — morphological table structure analysis

    Returns (out_pil, meta) where meta is a quality dict for timing_breakdown logging.
    """
    cv_img = pil_to_cv2(pil_img)

    # Stage 1: Deskew
    blur_before = calculate_blur_score(cv_img)
    deskewed, skew_angle = estimate_and_correct_skew(cv_img)

    # Stage 2: Bilateral denoise (adaptive)
    denoised, denoise_applied = _maybe_denoise(deskewed, blur_before)

    # Stage 3: CLAHE on grayscale
    gray = cv2.cvtColor(denoised, cv2.COLOR_BGR2GRAY)
    clahe = cv2.createCLAHE(clipLimit=2.0, tileGridSize=(8, 8))
    gray_clahe = clahe.apply(gray)

    # Stage 4: Adaptive binarization
    gray_out, binarized = _maybe_binarize(gray_clahe)

    # Stage 5: Sharpen (continuous-tone path only)
    if not binarized:
        gray_out = _sharpen(gray_out)

    # Stage 6: Table grid analysis (on deskewed, pre-binarize image)
    grid_meta = detect_table_grid_lines(deskewed)

    # Build output: grayscale -> 3ch BGR -> PIL RGB
    out_bgr = cv2.cvtColor(gray_out, cv2.COLOR_GRAY2BGR)
    out_pil = cv2_to_pil(out_bgr)
    blur_after = calculate_blur_score(out_bgr)

    meta = {
        "blur_score_before": blur_before,
        "blur_score_after": blur_after,
        "is_sharp": (blur_before > 60.0),
        "skew_angle_deg": skew_angle,
        "skew_corrected": (abs(skew_angle) > 0.5),
        "denoise_applied": denoise_applied,
        "binarized": binarized,
        "ocr_mode": "ADAPTIVE_BINARY" if binarized else "CLAHE_SHARPEN",
        "table_grid": grid_meta,
    }
    return out_pil, meta
