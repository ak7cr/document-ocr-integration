# Learned document extraction

The service extracts PDF text with PDFPlumber or Tesseract, then uses saved template fingerprints, dynamic regex rules, and dictionary aliases for recurring layouts. For an unfamiliar low-confidence layout, it can use Claude PDF vision/OCR and structured extraction, then Gemini 3 Pro if Claude is unavailable or fails. Both can propose a reusable template. Templates are only saved after review in the UI.

## Run locally

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
npm install
cp .env.example .env
psql "$DATABASE_URL" -f db/schema.sql
npm run dev
```

Install the Tesseract executable as well (`brew install tesseract` on macOS).

## Extraction flow

1. Choose PDFPlumber, Tesseract, AI-assisted extraction, or automatic extraction with an AI fallback.
2. Match active templates using stable document anchors.
3. Apply the matching template’s scoped regex rules and dictionary aliases.
4. If a template matches, its dynamic regex rules fill the form and no AI call is made. Otherwise, deterministic rules run first; automatic mode invokes AI only at low confidence, while AI-assisted mode tries Claude, then Gemini 3 Pro. Each AI receives both the original PDF for vision/OCR and PDFPlumber/Tesseract text as context. If both fail, the rule result remains.
5. Review the result and save the proposed template. Future matching documents use it before AI.

`DATABASE_URL` is optional for one-off extraction. It is required for saved templates, dictionaries, and extraction history. Configure `ANTHROPIC_API_KEY` for the primary AI provider and `GEMINI_API_KEY` for the Gemini 3 Pro fallback. Without either key, the app still produces a deterministic template proposal for review.
