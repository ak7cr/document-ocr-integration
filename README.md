# Document extraction 

1. **PDFPlumber** reads embedded PDF text and deterministic rules extract fields.
2. When the result is incomplete, **Tesseract OCR** reads rendered PDF pages and the rules run again.

The page displays each attempted stage, the selected result, confidence, and an editable review form. The OCR fallback is triggered by extraction completeness (< 75% of order number, date, vendor, and total), not merely because PDFPlumber returned text.

## Run locally

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
npm install
npm run dev
```

Tesseract itself must also be installed and available on `PATH` (`brew install tesseract` on macOS).

