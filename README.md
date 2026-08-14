# Learned document extraction

The service extracts PDF text with PDFPlumber or Tesseract, then uses saved template fingerprints, dynamic regex rules, and dictionary aliases for recurring layouts. For an unfamiliar low-confidence layout, it can optionally ask Claude to extract fields and propose a reusable template. Templates are only saved after review in the UI.

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
4. If no template matches, run deterministic rules. Automatic mode invokes Claude only at low confidence; AI-assisted mode always attempts Claude and falls back to the rule/template result if it fails.
5. Review the result and save the proposed template. Future matching documents use it before AI.

`DATABASE_URL` is optional for one-off extraction. It is required for saved templates, dictionaries, and extraction history. `ANTHROPIC_API_KEY` is optional; without it the app still produces a deterministic template proposal for review.
