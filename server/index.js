import 'dotenv/config';
import cors from 'cors';
import crypto from 'node:crypto';
import express from 'express';
import multer from 'multer';
import os from 'node:os';
import path from 'node:path';
import { promises as fs } from 'node:fs';
import { confidence, emptyOrder, extractByRules, extractWithClaude, extractWithGemini, runLocalExtractor } from './extraction.js';
import { saveRun } from './database.js';

const app = express();
const upload = multer({ storage: multer.memoryStorage(), limits: { fileSize: 20 * 1024 * 1024 } });
app.use(cors());
app.get('/api/health', (_req, res) => res.json({ ok: true }));

app.post('/api/extractions', upload.single('file'), async (req, res, next) => {
  if (!req.file || req.file.mimetype !== 'application/pdf') return res.status(400).json({ error: 'Upload one PDF file.' });
  const id = crypto.randomUUID();
  const dir = await fs.mkdtemp(path.join(os.tmpdir(), 'order-ocr-'));
  const filePath = path.join(dir, 'source.pdf');
  const attempts = [];
  const tryStep = async (name, action) => {
    try { const value = await action(); attempts.push({ name, status: 'completed' }); return value; }
    catch (error) { attempts.push({ name, status: 'failed', detail: error.message }); return null; }
  };
  try {
    await fs.writeFile(filePath, req.file.buffer);
    let text = '';
    let data = emptyOrder();
    const pdf = await tryStep('pdfplumber', () => runLocalExtractor('pdfplumber', filePath));
    if (pdf) { text = pdf.text; data = extractByRules(text); attempts.at(-1).confidence = confidence(data); }
    if (confidence(data) < 0.75) {
      const ocr = await tryStep('tesseract_ocr', () => runLocalExtractor('ocr', filePath));
      if (ocr) { text = ocr.text; data = extractByRules(text); attempts.at(-1).confidence = confidence(data); }
    }
    let source = attempts.findLast((item) => item.status === 'completed')?.name || 'manual_review';
    if (confidence(data) < 0.75) {
      const claude = await tryStep('claude_haiku', () => extractWithClaude(text));
      if (claude) { data = claude; source = 'claude_haiku'; attempts.at(-1).confidence = confidence(data); }
    }
    if (confidence(data) < 0.75) {
      const gemini = await tryStep('gemini_pro', () => extractWithGemini(text));
      if (gemini) { data = gemini; source = 'gemini_pro'; attempts.at(-1).confidence = confidence(data); }
    }
    const run = { id, fileName: req.file.originalname, mimeType: req.file.mimetype, data, source, confidence: confidence(data), attempts };
    const persisted = await saveRun(run).catch((error) => { attempts.push({ name: 'postgres', status: 'failed', detail: error.message }); return false; });
    res.status(201).json({ ...run, persisted, preview: text.slice(0, 1200) });
  } catch (error) { next(error); } finally { await fs.rm(dir, { recursive: true, force: true }); }
});
app.use((error, _req, res, _next) => res.status(500).json({ error: error.message || 'Extraction failed.' }));
app.listen(process.env.PORT || 3001, () => console.log(`API listening on http://localhost:${process.env.PORT || 3001}`));
