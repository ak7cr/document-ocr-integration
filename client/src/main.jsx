import { useEffect, useRef, useState } from 'react';
import { createRoot } from 'react-dom/client';
import './styles.css';

const IMAGE_TYPES = new Set(['image/jpeg', 'image/png', 'image/webp', 'image/tiff']);
const labels = { documentType: 'Document type', documentNumber: 'Order number', documentDate: 'Order date', vendorName: 'Vendor / supplier', customerName: 'Customer / buyer', currency: 'Currency', subtotalAmount: 'Subtotal', taxAmount: 'Tax', totalAmount: 'Total' };
const blank = Object.fromEntries(Object.keys(labels).map((key) => [key, '']));
const requiredFormFields = new Set(['documentNumber', 'documentDate', 'vendorName', 'totalAmount']);
const formMapping = Object.fromEntries(Object.keys(labels).map((field) => [field, { sourceField: field, required: requiredFormFields.has(field) }]));

// Fields the dictionary actively tracks (must match server/dictionary.js TRACKED_FIELDS)
const DICT_TRACKED = new Set(['vendorName', 'customerName', 'currency', 'documentType']);

// ─── Dictionary Panel ────────────────────────────────────────────────────────

function DictionaryPanel() {
  const [open, setOpen] = useState(false);
  const [entries, setEntries] = useState([]);
  const [loading, setLoading] = useState(false);
  const [field, setField] = useState('');

  async function load() {
    setLoading(true);
    try {
      const url = field ? `/api/dictionary?field=${encodeURIComponent(field)}` : '/api/dictionary';
      const res = await fetch(url);
      const payload = await res.json();
      setEntries(payload.entries || []);
    } finally { setLoading(false); }
  }

  async function remove(id) {
    await fetch(`/api/dictionary/${id}`, { method: 'DELETE' });
    setEntries((prev) => prev.filter((e) => e.id !== id));
  }

  useEffect(() => { if (open) load(); }, [open, field]); // eslint-disable-line react-hooks/exhaustive-deps

  // Group entries by fieldName for display
  const grouped = entries.reduce((acc, e) => { (acc[e.fieldName] ??= []).push(e); return acc; }, {});

  return (
    <div className="mt-6 rounded-xl border border-slate-700 bg-slate-900/60">
      <button
        id="dict-panel-toggle"
        onClick={() => setOpen((v) => !v)}
        className="flex w-full items-center justify-between px-4 py-3 text-sm font-semibold text-slate-300 hover:text-white transition"
      >
        <span> Field Dictionary {entries.length > 0 && !open ? `(${entries.length} entries)` : ''}</span>
        <span className="text-slate-500">{open ? '▲' : '▼'}</span>
      </button>

      {open && (
        <div className="border-t border-slate-700 px-4 pb-4 pt-3">
          <div className="flex flex-wrap gap-2 items-center mb-3">
            <label className="text-xs text-slate-400 font-medium">Filter by field:</label>
            <select
              value={field}
              onChange={(e) => setField(e.target.value)}
              className="rounded-lg border border-slate-600 bg-slate-950 px-2 py-1 text-xs text-slate-100 outline-none focus:border-cyan-400"
            >
              <option value="">All tracked fields</option>
              {[...DICT_TRACKED].map((f) => <option key={f} value={f}>{labels[f] || f}</option>)}
            </select>
            <button onClick={load} className="ml-auto rounded-lg border border-slate-600 px-3 py-1 text-xs text-slate-300 hover:border-cyan-400 hover:text-cyan-300 transition">
              {loading ? 'Loading…' : '↻ Refresh'}
            </button>
          </div>

          {Object.keys(grouped).length === 0
            ? <p className="text-xs text-slate-500 italic">No entries yet. Submit a document with high confidence or use "Submit Corrections" to build the dictionary.</p>
            : Object.entries(grouped).map(([fieldName, items]) => (
              <div key={fieldName} className="mb-4">
                <h4 className="text-xs font-semibold text-cyan-400 mb-1">{labels[fieldName] || fieldName}</h4>
                <div className="space-y-1">
                  {items.map((entry) => (
                    <div key={entry.id} className="flex items-start justify-between gap-2 rounded-lg bg-slate-950/60 px-3 py-2 text-xs">
                      <div className="min-w-0">
                        <span className="font-medium text-slate-200 break-words">{entry.canonical}</span>
                        {entry.aliases?.length > 0 && (
                          <span className="ml-2 text-slate-500">({entry.aliases.join(', ')})</span>
                        )}
                        <span className="ml-2 rounded-full bg-slate-800 px-1.5 py-0.5 text-slate-400">×{entry.hitCount}</span>
                      </div>
                      <button
                        onClick={() => remove(entry.id)}
                        title="Remove this entry"
                        className="shrink-0 rounded px-1.5 py-0.5 text-rose-400 hover:bg-rose-950 transition text-xs"
                      >✕</button>
                    </div>
                  ))}
                </div>
              </div>
            ))
          }
        </div>
      )}
    </div>
  );
}

// ─── Main App ────────────────────────────────────────────────────────────────

function App() {
  const input = useRef();
  const [file, setFile] = useState();
  const [result, setResult] = useState();
  const [form, setForm] = useState(blank);
  const [originalForm, setOriginalForm] = useState(blank); // snapshot of extracted values
  const [extractionMethod, setExtractionMethod] = useState('auto');
  const [loading, setLoading] = useState(false);
  const [saving, setSaving] = useState(false);
  const [submitting, setSubmitting] = useState(false);
  const [message, setMessage] = useState('');
  const [error, setError] = useState('');

  const isImage = file && IMAGE_TYPES.has(file.type);
  const onChoose = (next) => { setFile(next); setResult(); setForm(blank); setOriginalForm(blank); setError(''); setMessage(''); };

  // Detect whether the user has changed any field from the extracted values
  const hasChanges = result && Object.keys(labels).some((key) => form[key] !== originalForm[key]);

  async function extract() {
    if (!file) return;
    setLoading(true); setError(''); setMessage('');
    try {
      const body = new FormData(); body.append('file', file); body.append('extractionMethod', extractionMethod);
      const response = await fetch('/api/extractions', { method: 'POST', body }); const payload = await response.json();
      if (!response.ok) throw new Error(payload.error || 'Upload failed.');
      setResult(payload); setForm(payload.data); setOriginalForm(payload.data);
    } catch (err) { setError(err.message); } finally { setLoading(false); }
  }

  async function saveTemplate() {
    if (!result?.proposedTemplate) return;
    setSaving(true); setError(''); setMessage('');
    try {
      const proposal = result.proposedTemplate;
      const response = await fetch('/api/templates', { method: 'POST', headers: { 'content-type': 'application/json' }, body: JSON.stringify({ ...proposal, name: proposal.name || form.vendorName || 'Reusable document template', formMapping, runId: result.id }) });
      const payload = await response.json(); if (!response.ok) throw new Error(payload.error || 'Template could not be saved.');
      setMessage(`Saved "${payload.name}". Matching documents will use these rules first.`);
    } catch (err) { setError(err.message); } finally { setSaving(false); }
  }

  // Submit corrections: save hand-edited values back to the run and teach the dictionary
  async function submitCorrections() {
    if (!result?.id) return;
    setSubmitting(true); setError(''); setMessage('');
    try {
      const response = await fetch(`/api/extractions/${result.id}/corrections`, {
        method: 'PATCH',
        headers: { 'content-type': 'application/json' },
        body: JSON.stringify(form),
      });
      const payload = await response.json();
      if (!response.ok) throw new Error(payload.error || 'Could not save corrections.');
      // Snapshot the submitted values so the "has changes" indicator resets
      setOriginalForm({ ...form });
      setMessage('Corrections saved and learned into the dictionary. Future similar documents will autofill better.');
    } catch (err) { setError(err.message); } finally { setSubmitting(false); }
  }

  return <main className="min-h-screen bg-slate-950 text-slate-100"><div className="mx-auto max-w-6xl px-6 py-14">
    <section className="grid gap-6 lg:grid-cols-[.9fr_1.4fr]">
      <div className="rounded-2xl border border-slate-800 bg-slate-900/70 p-6 shadow-2xl shadow-black/20">
        <h2 className="text-lg font-semibold">1. Upload document</h2>
        <button id="upload-area" onClick={() => input.current.click()} className="mt-5 flex min-h-48 w-full flex-col items-center justify-center rounded-xl border border-dashed border-slate-600 bg-slate-950/40 p-6 text-center transition hover:border-cyan-400">
          <span className="text-3xl">{isImage ? '🖼️' : '⇧'}</span>
          <span className="mt-3 font-medium">{file ? file.name : 'Choose a PDF or image'}</span>
          <span className="mt-1 text-sm text-slate-500">PDF · JPG · PNG · WebP · TIFF — max 20 MB</span>
        </button>
        <input ref={input} className="hidden" type="file" accept="application/pdf,image/jpeg,image/png,image/webp,image/tiff" onChange={(event) => onChoose(event.target.files?.[0])} />

        <label className="mt-4 block text-sm font-medium text-slate-300">Text extraction method
          <select id="extraction-method-select" value={extractionMethod} onChange={(event) => setExtractionMethod(event.target.value)} className="mt-1.5 w-full rounded-lg border border-slate-700 bg-slate-950 px-3 py-2.5 text-slate-100 outline-none focus:border-cyan-400">
            <option value="auto">Automatic (Tesseract → AI fallback)</option>
            <option value="ai">AI-assisted (Claude → Gemini, always)</option>
            {!isImage && <option value="pdfplumber">PDFPlumber only (PDF)</option>}
            <option value="tesseract">Tesseract OCR only</option>
          </select>
        </label>

        <button id="extract-btn" disabled={!file || loading} onClick={extract} className="mt-4 w-full rounded-xl bg-cyan-400 px-4 py-3 font-semibold text-slate-950 transition hover:bg-cyan-300 disabled:cursor-not-allowed disabled:opacity-40">
          {loading ? 'Extracting…' : 'Extract fields'}
        </button>
        {error && <p className="mt-4 rounded-lg border border-rose-900 bg-rose-950/40 p-3 text-sm text-rose-300">{error}</p>}
        {message && <p className="mt-4 rounded-lg border border-emerald-900 bg-emerald-950/40 p-3 text-sm text-emerald-300">{message}</p>}

        <div className="mt-7 border-t border-slate-800 pt-5">
          <h3 className="text-sm font-semibold text-slate-300">Pipeline</h3>
          <ol className="mt-3 space-y-2 text-sm text-slate-400">
            {isImage
              ? <>
                <li>1. Tesseract OCR reads the image</li>
                <li>2. A saved template fills matching documents first</li>
                <li>3. Dictionary normalizes vendor/customer names</li>
                <li>4. Automatic: Claude, then Gemini kick in when confidence &lt; 75%</li>
                <li>5. AI-assisted: Claude → Gemini runs for every upload</li>
                <li>6. Review, correct, then <strong>Submit Corrections</strong> to teach the dictionary</li>
              </>
              : <>
                <li>1. PDFPlumber or Tesseract gets text</li>
                <li>2. A saved template fills matching documents first</li>
                <li>3. Dictionary normalizes vendor/customer names</li>
                <li>4. Automatic: Claude, then Gemini only for low-confidence layouts</li>
                <li>5. AI-assisted: same provider chain for every new layout</li>
                <li>6. Review, correct, then <strong>Submit Corrections</strong> to teach the dictionary</li>
              </>
            }
          </ol>
        </div>

        {/* Dictionary Panel (always visible in sidebar) */}
        <DictionaryPanel />
      </div>

      <div className="rounded-2xl border border-slate-800 bg-white p-6 text-slate-900">
        <div className="flex items-start justify-between gap-4">
          <div>
            <h2 className="text-lg font-semibold">2. Review extracted form</h2>
            <p className="mt-1 text-sm text-slate-500">Edit any field, then submit corrections to improve future autofill.</p>
          </div>
          {result && <span className="rounded-full bg-cyan-50 px-3 py-1 text-xs font-semibold text-cyan-700">{Math.round(result.confidence * 100)}% confidence</span>}
        </div>

        <div className="mt-6 grid gap-4 sm:grid-cols-2">
          {Object.entries(labels).map(([key, label]) => {
            const dictHit = result?.dictionaryHits?.[key];
            return (
              <label key={key} className="text-sm font-medium text-slate-600">
                <span className="flex items-center gap-1.5">
                  {label}
                  {dictHit && (
                    <span
                      title={`Dictionary match: "${dictHit.canonical}" (${Math.round(dictHit.score * 100)}% similarity)`}
                      className="rounded-full bg-violet-100 px-1.5 py-0.5 text-xs font-semibold text-violet-600 cursor-help"
                    > dict</span>
                  )}
                  {DICT_TRACKED.has(key) && !dictHit && result && (
                    <span title="This field is tracked by the dictionary" className="text-slate-300 text-xs">◦</span>
                  )}
                </span>
                <input
                  id={`field-${key}`}
                  value={form[key] || ''}
                  onChange={(event) => setForm({ ...form, [key]: event.target.value })}
                  className={`mt-1.5 w-full rounded-lg border px-3 py-2.5 text-slate-900 outline-none focus:ring-2 ${form[key] !== originalForm[key]
                      ? 'border-amber-400 bg-amber-50 focus:border-amber-500 focus:ring-amber-100'
                      : 'border-slate-200 focus:border-cyan-500 focus:ring-cyan-100'
                    }`}
                  placeholder="Not extracted"
                />
              </label>
            );
          })}
        </div>

        {result && <>
          {/* ── Action row ── */}
          <div className="mt-6 flex flex-wrap gap-3">
            {/* Submit Corrections — always shown, disabled only when nothing changed */}
            <button
              id="submit-corrections-btn"
              onClick={submitCorrections}
              disabled={submitting || !hasChanges}
              title={!hasChanges ? 'Edit one or more fields above to enable this button' : 'Save your corrections and teach the dictionary'}
              className="flex-1 rounded-xl border-2 border-amber-400 bg-amber-50 px-4 py-2.5 text-sm font-semibold text-amber-800 transition hover:bg-amber-100 disabled:cursor-not-allowed disabled:opacity-40"
            >
              {submitting ? 'Saving…' : hasChanges ? ' Submit Corrections' : ' Submit Corrections (no changes)'}
            </button>

            {/* Save as template — only when this run didn't already use a saved template */}
            {!result.templateId && (
              <button
                id="save-template-btn"
                onClick={saveTemplate}
                disabled={saving}
                className="rounded-xl bg-slate-900 px-4 py-2.5 text-sm font-semibold text-white transition hover:bg-slate-700 disabled:opacity-50"
              >
                {saving ? 'Saving…' : 'Save as template'}
              </button>
            )}
          </div>

          {hasChanges && (
            <p className="mt-2 text-xs text-amber-600">
              {Object.keys(labels).filter((k) => form[k] !== originalForm[k]).length} field(s) changed — click <strong>Submit Corrections</strong> to teach the dictionary.
            </p>
          )}

          <div className="mt-5 border-t border-slate-200 pt-5">
            <div className="flex flex-wrap items-center justify-between gap-3">
              <div>
                <h3 className="font-semibold">Extraction path</h3>
                <p className="text-sm text-slate-500">Selected: {result.source.replaceAll('_', ' ')}</p>
              </div>
            </div>
            <div className="mt-3 flex flex-wrap gap-2">{result.attempts.map((attempt) => <span key={attempt.name} title={attempt.detail} className={`rounded-full px-3 py-1 text-xs font-medium ${attempt.status === 'completed' ? 'bg-emerald-50 text-emerald-700' : 'bg-amber-50 text-amber-700'}`}>{attempt.name.replaceAll('_', ' ')} · {attempt.status}</span>)}</div>
          </div>
          <details className="mt-5 text-sm"><summary className="cursor-pointer font-medium text-slate-600">View extracted text preview</summary><pre className="mt-3 max-h-48 overflow-auto rounded-lg bg-slate-950 p-3 whitespace-pre-wrap text-xs text-slate-300">{result.preview || 'No text recovered.'}</pre></details>
        </>}
      </div>
    </section>
  </div></main>;
}

createRoot(document.getElementById('root')).render(<App />);
