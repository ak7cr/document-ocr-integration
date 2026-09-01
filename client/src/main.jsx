import { useRef, useState } from 'react';
import { createRoot } from 'react-dom/client';
import './styles.css';

const IMAGE_TYPES = new Set(['image/jpeg', 'image/png', 'image/webp', 'image/tiff']);


const FIELD_SCHEMA = [
  { key: 'documentType', label: 'Invoice type', type: 'select', options: ['tax_invoice', 'invoice'] },
  { key: 'documentNumber', label: 'Invoice number', type: 'text' },
  { key: 'documentDate', label: 'Invoice date', type: 'text' },
  { key: 'vendorName', label: 'Vendor / seller', type: 'text' },
  { key: 'customerName', label: 'Customer / buyer', type: 'text' },
  { key: 'currency', label: 'Currency', type: 'text' },
  { key: 'subtotalAmount', label: 'Subtotal', type: 'number' },
  { key: 'taxAmount', label: 'Tax', type: 'number' },
  { key: 'totalAmount', label: 'Total', type: 'number' },
];
const schemaFor = (key) => FIELD_SCHEMA.find((f) => f.key === key);
const labelFor = (key) => schemaFor(key)?.label || key;
const blank = Object.fromEntries(FIELD_SCHEMA.map((f) => [f.key, '']));
const requiredFormFields = new Set(['documentNumber', 'documentDate', 'vendorName', 'totalAmount']);
const formMapping = Object.fromEntries(FIELD_SCHEMA.map((f) => [f.key, { sourceField: f.key, required: requiredFormFields.has(f.key) }]));
const INVOICE_TYPE_LABELS = { tax_invoice: 'Tax invoice', invoice: 'Invoice' };

// ─── Line items table ────────────────────────────────────────────────────────

const LINE_ITEM_COLUMNS = [
  ['serialNo', '#'],
  ['itemName', 'Item name'],
  ['hsnSac', 'HSN/SAC'],
  ['quantity', 'Qty'],
  ['unit', 'Unit'],
  ['rate', 'Rate'],
  ['discount', 'Discount'],
  ['taxableValue', 'Taxable'],
  ['cgstAmount', 'CGST'],
  ['sgstAmount', 'SGST'],
  ['igstAmount', 'IGST'],
  ['tax', 'Tax'],
  ['amount', 'Amount'],
  ['grossAmount', 'Gross'],
];

function LineItemsTable({ items }) {
  const rows = Array.isArray(items) ? items.filter((it) => it && (it.itemName || it.quantity || it.amount || it.serialNo)) : [];
  if (!rows.length) {
    return <p className="text-sm text-slate-400 italic">No line-items table detected in this document.</p>;
  }
  const cols = LINE_ITEM_COLUMNS.filter(([key]) => rows.some((it) => it[key]));
  return (
    <div className="overflow-x-auto rounded-lg border border-slate-200">
      <table className="min-w-full table-auto text-left text-sm">
        <thead>
          <tr className="bg-slate-50 text-slate-500">
            {cols.map(([key, label]) => <th key={label} className="whitespace-nowrap px-3 py-2 font-semibold">{label}</th>)}
          </tr>
        </thead>
        <tbody>
          {rows.map((it, i) => (
            <tr key={i} className="border-t border-slate-100 align-top odd:bg-white even:bg-slate-50/60">
              {cols.map(([key]) => (
                <td key={key} className="whitespace-nowrap px-3 py-2 text-slate-700">{it[key] || '—'}</td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
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

  // Dynamic field set: schema fields + any extra scalar keys the API returns.
  const fieldKeys = result
    ? [...new Set([...FIELD_SCHEMA.map((f) => f.key), ...Object.keys(result.data || {}).filter((k) => k !== 'lineItems' && typeof result.data[k] !== 'object')])]
    : FIELD_SCHEMA.map((f) => f.key);

  // Detect whether the user has changed any field from the extracted values
  const hasChanges = result && fieldKeys.some((key) => form[key] !== originalForm[key]);

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
        body: JSON.stringify(Object.fromEntries(fieldKeys.map((k) => [k, form[k] || '']))),
      });
      const payload = await response.json();
      if (!response.ok) throw new Error(payload.error || 'Could not save corrections.');
      // Snapshot the submitted values so the "has changes" indicator resets
      setOriginalForm({ ...form });
      setMessage('Corrections saved and learned into the dictionary. Future similar documents will autofill better.');
    } catch (err) { setError(err.message); } finally { setSubmitting(false); }
  }

  return <main className="min-h-screen bg-slate-950 text-slate-100"><div className="mx-auto max-w-5xl px-6 py-14">
    <section className="grid gap-6 lg:grid-cols-[340px_minmax(0,1fr)]">
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
            <option value="auto">Automatic (EasyOCR → AI fallback)</option>
            <option value="ai">AI-assisted (Claude → Gemini, always)</option>
            {!isImage && <option value="pdfplumber">PDFPlumber only (PDF)</option>}
            <option value="local">Local OCR only (EasyOCR, no AI)</option>
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
                <li>1. EasyOCR OCR reads the image (PaddleOCR fallback)</li>
                <li>2. A saved template fills matching documents first</li>
                <li>3. Dictionary normalizes vendor/customer names</li>
                <li>4. Automatic: Claude, then Gemini kick in when confidence &lt; 75%</li>
                <li>5. AI-assisted: Claude → Gemini runs for every upload</li>
                <li>6. Review, correct, then <strong>Submit Corrections</strong> to teach the dictionary</li>
              </>
              : <>
                <li>1. PDFPlumber (PDF) or EasyOCR reads the document</li>
                <li>2. A saved template fills matching documents first</li>
                <li>3. Dictionary normalizes vendor/customer names</li>
                <li>4. Automatic: Claude, then Gemini only for low-confidence layouts</li>
                <li>5. AI-assisted: same provider chain for every new layout</li>
                <li>6. Review, correct, then <strong>Submit Corrections</strong> to teach the dictionary</li>
              </>
            }
          </ol>
        </div>
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
          {fieldKeys.map((key) => {
            const schema = schemaFor(key);
            const type = schema?.type || 'text';
            const changed = form[key] !== originalForm[key];
            const inputClass = `mt-1.5 w-full rounded-lg border px-3 py-2.5 text-slate-900 outline-none focus:ring-2 ${changed
              ? 'border-amber-400 bg-amber-50 focus:border-amber-500 focus:ring-amber-100'
              : 'border-slate-200 focus:border-cyan-500 focus:ring-cyan-100'
            }`;
            const onChange = (event) => setForm({ ...form, [key]: event.target.value });
            return (
              <label key={key} className="text-sm font-medium text-slate-600">
                <span>{labelFor(key)}</span>
                {type === 'select' ? (
                  <select id={`field-${key}`} value={form[key] || ''} onChange={onChange} className={inputClass}>
                    <option value="">Not extracted</option>
                    {(schema?.options || []).map((opt) => (
                      <option key={opt} value={opt}>{INVOICE_TYPE_LABELS[opt] || opt}</option>
                    ))}
                  </select>
                ) : (
                  <input
                    id={`field-${key}`}
                    type={type}
                    value={form[key] || ''}
                    onChange={onChange}
                    className={inputClass}
                    placeholder="Not extracted"
                  />
                )}
              </label>
            );
          })}
        </div>

        {result && <>
          {/* ── Table contents ── */}
          <div className="mt-6 border-t border-slate-200 pt-5">
            <div className="flex items-center gap-2">
              <h3 className="font-semibold">Table contents</h3>
              {result.data?.lineItems?.length > 0 && (
                <span className="rounded-full bg-slate-100 px-2 py-0.5 text-xs font-semibold text-slate-500">{result.data.lineItems.length} item{result.data.lineItems.length === 1 ? '' : 's'}</span>
              )}
            </div>
            <p className="mb-3 text-sm text-slate-500">Line items extracted from the document table (serial no., item, quantity, rate, discount, tax, amount).</p>
            <LineItemsTable items={result.data?.lineItems} />
          </div>

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
              {fieldKeys.filter((k) => form[k] !== originalForm[k]).length} field(s) changed — click <strong>Submit Corrections</strong> to teach the dictionary.
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
          <details className="mt-5 text-sm"><summary className="cursor-pointer font-medium text-slate-600">View extracted text preview</summary><pre className="mt-3 max-h-48 overflow-auto rounded-lg bg-slate-950 p-3 whitespace-pre-wrap text-xs text-slate-300">{result.rawText || result.preview || 'No text recovered.'}</pre></details>
        </>}
      </div>
    </section>
  </div></main>;
}

createRoot(document.getElementById('root')).render(<App />);
