# Document extraction

Fallback chain:

1. **PDFPlumber** gets embedded PDF text and deterministic rules extract fields.
2. If the result is incomplete, **Tesseract OCR** reads rendered PDF pages and the rules run again.
3. If still incomplete, **Claude Haiku** returns the validated JSON shape.
4. **Gemini Pro** is the final extraction fallback.

The page displays each attempted provider, the selected result, confidence, and an editable review form. A fallback is triggered by extraction completeness (< 75% of order number, date, vendor, and total), not simply because a PDF library returned text.

## Run locally

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
npm install
cp .env.example .env
npm run dev
```

