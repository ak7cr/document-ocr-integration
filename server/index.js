import 'dotenv/config';
import cors from 'cors';
import crypto from 'node:crypto';
import express from 'express';
import multer from 'multer';
import os from 'node:os';
import path from 'node:path';
import { promises as fs } from 'node:fs';
import { applyTemplate, buildTemplateProposal, confidence, emptyOrder, extractByRules, extractLayoutStructure, extractTableWithClaude, extractTableWithGemini, extractWithClaude, extractWithGemini, lineItemQuality, matchTemplate, normalizeLineItems, reconcileInvoice, requiredMappingStatus, runImageExtractor, runLocalExtractor, runLocalTableExtractor, structureScore, summarizeLineItems } from './extraction.js';
import { getActiveTemplates, getRunById, recordTemplateOutcome, saveCorrections, saveRun, saveTemplate } from './database.js';
import { applyDictionary, learnFromData, listDictionary, removeDictionaryEntry } from './dictionary.js';

const ACCEPTED_MIME_TYPES = new Set(['application/pdf', 'image/jpeg', 'image/png', 'image/webp', 'image/tiff']);
const MIME_TO_EXT = { 'application/pdf': '.pdf', 'image/jpeg': '.jpg', 'image/png': '.png', 'image/webp': '.webp', 'image/tiff': '.tiff' };

const app = express();
const upload = multer({ storage: multer.memoryStorage(), limits: { fileSize: 20 * 1024 * 1024 } });
app.use(cors());
app.use(express.json({ limit: '1mb' }));
app.get('/api/health', (_req, res) => res.json({ ok: true }));

app.post('/api/extractions', upload.single('file'), async (req, res, next) => {
  if (!req.file || !ACCEPTED_MIME_TYPES.has(req.file.mimetype)) {
    return res.status(400).json({ error: 'Upload one PDF or image file (JPG, PNG, WebP, TIFF).' });
  }
  const mimeType = req.file.mimetype;
  const isPdf = mimeType === 'application/pdf';
  const extractionMethod = req.body.extractionMethod || 'auto';
  if (!['auto', 'pdfplumber', 'tesseract', 'ai', 'local'].includes(extractionMethod)) {
    return res.status(400).json({ error: 'Choose automatic, PDFPlumber (PDF only), Tesseract, Local OCR, or AI-assisted extraction.' });
  }
  const id = crypto.randomUUID();
  const ext = MIME_TO_EXT[mimeType] || '.bin';
  const dir = await fs.mkdtemp(path.join(os.tmpdir(), 'order-ocr-'));
  const filePath = path.join(dir, `source${ext}`);
  const attempts = [];
  const tryStep = async (name, action) => {
    try { const value = await action(); attempts.push({ name, status: 'completed' }); return value; }
    catch (error) { attempts.push({ name, status: 'failed', detail: error.message }); return null; }
  };
  try {
    await fs.writeFile(filePath, req.file.buffer);
    let text = '';
    let localEarly = null;

    if (isPdf) {
      // ── PDF pipeline ────────────────────────────────────────────────────────
      if (extractionMethod === 'local') {
        // Their ported local engine (EasyOCR full-page + spatial table) only.
        localEarly = await tryStep('local_ocr', () => runLocalTableExtractor(filePath));
        if (localEarly) text = localEarly.fullText || localEarly.text || '';
      } else {
        if (extractionMethod !== 'tesseract') {
          const pdf = await tryStep('pdfplumber', () => runLocalExtractor('pdfplumber', filePath));
          if (pdf) text = pdf.text;
        }
        if (extractionMethod === 'tesseract' || ((extractionMethod === 'auto' || extractionMethod === 'ai') && !text.trim())) {
          const ocr = await tryStep('tesseract_ocr', () => runLocalExtractor('ocr', filePath));
          if (ocr) text = ocr.text;
        }
      }
    } else {
      // ── Image pipeline ───────────────────────────────────────────────────────
      // Header/summary source stays Tesseract (clean labelled lines -> correct
      // totals; full-EasyOCR headers regressed totals). Their EasyOCR full-page
      // engine feeds the raw-text preview + the spatial table engine (below),
      // so the UI raw text matches invoice-ocr-c++. The 'local' mode runs their
      // ported engine only — no Tesseract, no AI.
      if (extractionMethod !== 'ai') {
        if (extractionMethod === 'local') {
          localEarly = await tryStep('local_ocr', () => runLocalTableExtractor(filePath));
          if (localEarly) text = localEarly.fullText || localEarly.text || '';
        } else {
          const ocr = await tryStep('tesseract_ocr', () => runImageExtractor(filePath));
          if (ocr) text = ocr.text;
        }
      }
    }
    let rawPreview = text;

    const templates = await getActiveTemplates();
    const match = matchTemplate(text, templates);
    let data = emptyOrder();
    let source = attempts.findLast((item) => item.status === 'completed')?.name || 'manual_review';
    let templateId = null;
    let proposedTemplate;

    if (match) {
      data = applyTemplate(text, match.template);
      source = 'saved_template';
      templateId = match.template.id;
      const mappingStatus = requiredMappingStatus(data, match.template.formMapping);
      const requiredDetail = mappingStatus.required.length ? `; ${mappingStatus.required.length - mappingStatus.missing.length}/${mappingStatus.required.length} required form fields filled` : '';
      attempts.push({ name: 'saved_template', status: 'completed', confidence: confidence(data), detail: `${match.template.name} (${Math.round(match.score * 100)}% match)${requiredDetail}` });
      // Reliability/lifecycle: counts this match as a success only when the
      // template actually filled the form well (drives DRAFT->ACTIVE and
      // ACTIVE->QUARANTINED).
      await recordTemplateOutcome(templateId, { success: confidence(data) >= 0.75, confidence: confidence(data) });
    } else {
      data = extractByRules(text);
      attempts.push({ name: 'dictionary_rules', status: 'completed', confidence: confidence(data) });
    }

   
    const looksTabular = extractionMethod === 'local' || data.lineItems.length > 0 || /\n[^\n]*\t[^\n]*\n/.test(text);
    const currentQuality = data.lineItems.length ? lineItemQuality(data.lineItems) : 0;
    let localTableStrong = false;
    // Local table OCR (their ported spatial cell-grid engine) runs for every
    // tabular document and is preferred whenever its parse is at least as
    // coherent as the deterministic/template parse — so geometry resolves
    // qty/rate that text OCR mangles (e.g. GST qty "3"). In 'local' mode the
    // engine already ran (localEarly) and is reused here — no double run.
    if (looksTabular && extractionMethod !== 'tesseract') {
      const local = localEarly || await (async () => {
        try { const v = await runLocalTableExtractor(filePath); attempts.push({ name: 'table_ocr', status: 'completed' }); return v; }
        catch (error) { attempts.push({ name: 'table_ocr', status: 'failed', detail: error.message }); return null; }
      })();
      // Their EasyOCR full-page raw text rides along on the table pass (same
      // word boxes) — makes the UI raw-text preview match invoice-ocr-c++.
      if (local?.fullText) rawPreview = local.fullText;
      if (local && local.text) {
        // The worker's table mode returns structured line items straight from
        // their spatial cell-grid engine (EasyOCR + RapidOCR, pick-best).
        const localItems = Array.isArray(local.lineItems) && local.lineItems.length
          ? normalizeLineItems(local.lineItems)
          : [];
        const localQuality = lineItemQuality(localItems);
        if (localItems.length && localQuality >= 0.6 && localQuality >= currentQuality) {
          data.lineItems = localItems;
          localTableStrong = true;
          attempts.at(-1).confidence = localQuality;
          attempts.at(-1).detail = `${localItems.length} items read locally (spatial)`;
          // Fill any totals that were not labelled on the document (derivable
          // from the parsed table); labelled summary lines keep priority.
          const computed = summarizeLineItems(data.lineItems);
          for (const [k, v] of Object.entries(computed)) {
            if (v && !data[k]) data[k] = v;
          }
        } else if (localItems.length) {
          attempts.at(-1).status = 'failed';
          attempts.at(-1).detail = localQuality < 0.6
            ? `quality ${localQuality.toFixed(2)} < 0.6 — keeping deterministic`
            : `spatial quality ${localQuality.toFixed(2)} ≤ deterministic ${currentQuality.toFixed(2)} — keeping deterministic`;
        } else {
          attempts.at(-1).status = 'failed';
          attempts.at(-1).detail = 'no usable table structure';
        }
      } else if (local) {
        attempts.at(-1).status = 'failed';
        attempts.at(-1).detail = 'no table text returned';
      }
    }

    // AI fallback — only for fields OCR could not read (headers), and never to
    // re-do the table when the local table OCR already succeeded.
    const shouldUseAi = !match && (extractionMethod === 'ai' || (extractionMethod === 'auto' && confidence(data) < 0.75));
    if (shouldUseAi) {
      for (const provider of [
        { name: 'anthropic_claude', extract: () => extractWithClaude(text, req.file.buffer, mimeType) },
        { name: 'gemini_3_pro',     extract: () => extractWithGemini(text, req.file.buffer, mimeType) },
      ]) {
        const ai = await tryStep(provider.name, provider.extract);
        if (!ai) continue;
        data = localTableStrong ? { ...ai.data, lineItems: data.lineItems } : ai.data;
        proposedTemplate = ai.template; source = provider.name;
        attempts.at(-1).confidence = confidence(data);
        break;
      }
    }

    // ── Dictionary lookup: normalize extracted values against known canonicals ──
    const { data: dictData, dictionaryHits } = await applyDictionary(data);
    data = dictData;
    if (Object.keys(dictionaryHits).length) {
      attempts.push({ name: 'dictionary_lookup', status: 'completed', confidence: confidence(data), detail: `Normalized: ${Object.keys(dictionaryHits).join(', ')}` });
    }

   
    const mainAiRan = ['anthropic_claude', 'gemini_3_pro'].includes(source);
    const needsAiTable = looksTabular && extractionMethod !== 'tesseract' && extractionMethod !== 'local' && !mainAiRan && !localTableStrong
      && (!data.lineItems.length || lineItemQuality(data.lineItems) < 0.6);
    if (needsAiTable) {
      for (const provider of [
        { name: 'ai_table_claude', extract: () => extractTableWithClaude(text, req.file.buffer, mimeType) },
        { name: 'ai_table_gemini', extract: () => extractTableWithGemini(text, req.file.buffer, mimeType) },
      ]) {
        const table = await tryStep(provider.name, provider.extract);
        if (!table || !table.length) continue;
        data.lineItems = table;
        attempts.at(-1).detail = `${table.length} items read from the table (${provider.name})`;
        break;
      }
    }

    // ── Auto-learn: when confidence is high, feed canonicals to dictionary ──
    if (confidence(data) >= 0.75) {
      learnFromData(data).catch(() => {});
    }

    // ── Arithmetic audit (informational): line items + taxes vs totals ──
    const validation = reconcileInvoice(data);

    // ── Auto-learn a DRAFT template for a genuinely new, well-understood
    //    layout so repeat documents resolve deterministically WITHOUT AI.
    //    The lifecycle promotes it to ACTIVE after 2 successful matches. ──
    if (!match && !templateId && confidence(data) >= 0.75) {
      const struct = extractLayoutStructure(text);
      const alreadyCovered = templates.some((t) => {
        const ts = t.fingerprint?.structure;
        return ts && (ts.columnFields?.length || ts.columnHeaders?.length || ts.keyLabels?.length) && structureScore(ts, struct) >= 0.85;
      });
      if (!alreadyCovered) {
        const proposal = buildTemplateProposal(text, data);
        const draftId = await saveTemplate({
          name: `${data.vendorName || 'Layout'} (auto)`,
          fingerprint: proposal.fingerprint,
          fieldRules: proposal.fieldRules,
          formMapping: {},
          status: 'draft',
        }).catch(() => null);
        if (draftId) attempts.push({ name: 'auto_template', status: 'completed', detail: 'saved DRAFT template for this layout' });
      }
    }

    proposedTemplate ??= buildTemplateProposal(text, data);
    const matchedTemplate = match ? {
      name: match.template.name,
      status: match.template.status,
      timesMatched: match.template.timesMatched || 0,
      successCount: match.template.successCount || 0,
      anchorScore: match.anchorScore,
      structureScore: match.structureScore,
    } : null;
    const run = { id, fileName: req.file.originalname, mimeType, data, source, templateId, confidence: confidence(data), attempts, text, validation };
    const persisted = await saveRun(run).catch((error) => { attempts.push({ name: 'postgres', status: 'failed', detail: error.message }); return false; });
    res.status(201).json({ ...run, text: undefined, persisted, proposedTemplate, template: matchedTemplate, dictionaryHits, preview: rawPreview.slice(0, 1200), rawText: rawPreview });
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


app.patch('/api/extractions/:id/corrections', async (req, res, next) => {
  try {
    const { id } = req.params;
    const correctedData = req.body;
    if (!correctedData || typeof correctedData !== 'object') {
      return res.status(400).json({ error: 'Provide the corrected field values as a JSON object.' });
    }

    const run = await getRunById(id);
    let templateSaved = false;

    if (run && run.sourceText) {
      const proposal = buildTemplateProposal(run.sourceText, correctedData, correctedData.vendorName || run.originalFilename);
      if (Object.keys(proposal.fieldRules).length > 0) {
        const formMapping = Object.fromEntries(
          Object.keys(proposal.fieldRules).map((field) => [field, { sourceField: field, required: true }])
        );
        await saveTemplate({
          id: run.templateId || crypto.randomUUID(),
          name: proposal.name,
          fingerprint: proposal.fingerprint,
          fieldRules: proposal.fieldRules,
          formMapping,
          runId: id,
        });
        templateSaved = true;
      }
    }

    const [persisted] = await Promise.all([
      saveCorrections(id, correctedData),
      learnFromData(correctedData),
    ]);

    res.json({ ok: true, persisted, learned: true, templateSaved });
  } catch (error) { next(error); }
});

// ── GET /api/dictionary ───────────────────────────────────────────────────────
app.get('/api/dictionary', async (req, res, next) => {
  try {
    const entries = await listDictionary(req.query.field || null);
    res.json({ entries });
  } catch (error) { next(error); }
});

// ── DELETE /api/dictionary/:id ────────────────────────────────────────────────
app.delete('/api/dictionary/:id', async (req, res, next) => {
  try {
    await removeDictionaryEntry(req.params.id);
    res.json({ ok: true });
  } catch (error) { next(error); }
});

app.use((error, _req, res, _next) => res.status(500).json({ error: error.message || 'Extraction failed.' }));
app.listen(process.env.PORT || 3001, () => console.log(`API listening on http://localhost:${process.env.PORT || 3001}`));
