import 'dotenv/config';
import cors from 'cors';
import crypto from 'node:crypto';
import express from 'express';
import multer from 'multer';
import os from 'node:os';
import path from 'node:path';
import { promises as fs } from 'node:fs';
import { applyTemplate, buildTemplateProposal, confidence, emptyOrder, extractByRules, extractWithClaude, extractWithGemini, matchTemplate, requiredMappingStatus, runLocalExtractor } from './extraction.js';
import { getActiveTemplates, getDictionary, markTemplateMatched, saveRun, saveTemplate } from './database.js';

const app = express();
const upload = multer({ storage: multer.memoryStorage(), limits: { fileSize: 20 * 1024 * 1024 } });
app.use(cors());
app.use(express.json({ limit: '1mb' }));
app.get('/api/health', (_req, res) => res.json({ ok: true }));

app.post('/api/extractions', upload.single('file'), async (req, res, next) => {
  if (!req.file || req.file.mimetype !== 'application/pdf') return res.status(400).json({ error: 'Upload one PDF file.' });
  const extractionMethod = req.body.extractionMethod || 'auto';
  if (!['auto', 'pdfplumber', 'tesseract', 'ai'].includes(extractionMethod)) return res.status(400).json({ error: 'Choose automatic, PDFPlumber, Tesseract, or AI-assisted extraction.' });
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
    if (extractionMethod !== 'tesseract') {
      const pdf = await tryStep('pdfplumber', () => runLocalExtractor('pdfplumber', filePath));
      if (pdf) text = pdf.text;
    }
    if (extractionMethod === 'tesseract' || ((extractionMethod === 'auto' || extractionMethod === 'ai') && !text.trim())) {
      const ocr = await tryStep('tesseract_ocr', () => runLocalExtractor('ocr', filePath));
      if (ocr) text = ocr.text;
    }
    const [templates, dictionary] = await Promise.all([getActiveTemplates(), getDictionary()]);
    const match = matchTemplate(text, templates);
    let data = emptyOrder();
    let source = attempts.findLast((item) => item.status === 'completed')?.name || 'manual_review';
    let templateId = null;
    let proposedTemplate;
    if (match) {
      data = applyTemplate(text, match.template, dictionary);
      source = 'saved_template'; templateId = match.template.id;
      const mappingStatus = requiredMappingStatus(data, match.template.formMapping);
      const requiredDetail = mappingStatus.required.length ? `; ${mappingStatus.required.length - mappingStatus.missing.length}/${mappingStatus.required.length} required form fields filled` : '';
      attempts.push({ name: 'saved_template', status: 'completed', confidence: confidence(data), detail: `${match.template.name} (${Math.round(match.score * 100)}% fingerprint match)${requiredDetail}` });
      await markTemplateMatched(templateId);
    } else {
      data = extractByRules(text, dictionary);
      attempts.push({ name: 'dictionary_rules', status: 'completed', confidence: confidence(data) });
    }
    const shouldUseAi = !match && (extractionMethod === 'ai' || (extractionMethod === 'auto' && confidence(data) < 0.75));
    if (shouldUseAi) {
      for (const provider of [
        { name: 'anthropic_claude', extract: () => extractWithClaude(text, req.file.buffer) },
        { name: 'gemini_3_pro', extract: () => extractWithGemini(text, req.file.buffer) },
      ]) {
        const ai = await tryStep(provider.name, provider.extract);
        if (!ai) continue;
        data = ai.data; proposedTemplate = ai.template; source = provider.name;
        attempts.at(-1).confidence = confidence(data);
        break;
      }
    }
    proposedTemplate ??= buildTemplateProposal(text, data);
    const run = { id, fileName: req.file.originalname, mimeType: req.file.mimetype, data, source, templateId, confidence: confidence(data), attempts, text };
    const persisted = await saveRun(run).catch((error) => { attempts.push({ name: 'postgres', status: 'failed', detail: error.message }); return false; });
    res.status(201).json({ ...run, text: undefined, persisted, proposedTemplate, preview: text.slice(0, 1200) });
  } catch (error) { next(error); } finally { await fs.rm(dir, { recursive: true, force: true }); }
});

app.post('/api/templates', async (req, res, next) => {
  try {
    const { name, fingerprint, fieldRules, formMapping, runId } = req.body;
    const mappingIsValid = formMapping && typeof formMapping === 'object' && Object.entries(formMapping).every(([formField, mapping]) => formField && typeof mapping?.sourceField === 'string' && typeof mapping?.required === 'boolean');
    if (!name || !Array.isArray(fingerprint?.anchors) || !fingerprint.anchors.length || !fieldRules || typeof fieldRules !== 'object' || !mappingIsValid) return res.status(400).json({ error: 'A template name, fingerprint anchors, field rules, and form mapping are required.' });
    const id = crypto.randomUUID();
    await saveTemplate({ id, name, fingerprint, fieldRules, formMapping, runId });
    res.status(201).json({ id, name });
  } catch (error) { next(error); }
});

app.use((error, _req, res, _next) => res.status(500).json({ error: error.message || 'Extraction failed.' }));
app.listen(process.env.PORT || 3001, () => console.log(`API listening on http://localhost:${process.env.PORT || 3001}`));
