import 'dotenv/config';
import cors from 'cors';
import express from 'express';
import multer from 'multer';
import os from 'node:os';
import path from 'node:path';
import { promises as fs } from 'node:fs';
import { confidence, emptyOrder, extractByRules, runLocalExtractor } from './extraction.js';

const app = express();
const upload = multer({ storage: multer.memoryStorage(), limits: { fileSize: 20 * 1024 * 1024 } });
app.use(cors());
app.get('/api/health', (_req, res) => res.json({ ok: true }));

app.post('/api/extractions', upload.single('file'), async (req, res, next) => {
  if (!req.file || req.file.mimetype !== 'application/pdf') return res.status(400).json({ error: 'Upload one PDF file.' });
  const extractionMethod = req.body.extractionMethod || 'auto';
  if (!['auto', 'pdfplumber', 'tesseract'].includes(extractionMethod)) return res.status(400).json({ error: 'Choose PDFPlumber, Tesseract, or automatic fallback.' });
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
    if (extractionMethod !== 'tesseract') {
      const pdf = await tryStep('pdfplumber', () => runLocalExtractor('pdfplumber', filePath));
      if (pdf) { text = pdf.text; data = extractByRules(text); attempts.at(-1).confidence = confidence(data); }
    }
    if (extractionMethod === 'tesseract' || (extractionMethod === 'auto' && confidence(data) < 0.75)) {
      const ocr = await tryStep('tesseract_ocr', () => runLocalExtractor('ocr', filePath));
      if (ocr) { text = ocr.text; data = extractByRules(text); attempts.at(-1).confidence = confidence(data); }
    }
    const source = attempts.findLast((item) => item.status === 'completed')?.name || 'manual_review';
    res.status(201).json({ fileName: req.file.originalname, mimeType: req.file.mimetype, data, source, confidence: confidence(data), attempts, preview: text.slice(0, 1200) });
  } catch (error) { next(error); } finally { await fs.rm(dir, { recursive: true, force: true }); }
});
app.use((error, _req, res, _next) => res.status(500).json({ error: error.message || 'Extraction failed.' }));
app.listen(process.env.PORT || 3001, () => console.log(`API listening on http://localhost:${process.env.PORT || 3001}`));
