import { useRef, useState } from 'react';
import { createRoot } from 'react-dom/client';
import './styles.css';

const IMAGE_TYPES = new Set(['image/jpeg', 'image/png', 'image/webp', 'image/tiff']);
const labels = { documentType: 'Document type', documentNumber: 'Order number', documentDate: 'Order date', vendorName: 'Vendor / supplier', customerName: 'Customer / buyer', currency: 'Currency', subtotalAmount: 'Subtotal', taxAmount: 'Tax', totalAmount: 'Total' };
const blank = Object.fromEntries(Object.keys(labels).map((key) => [key, '']));
const requiredFormFields = new Set(['documentNumber', 'documentDate', 'vendorName', 'totalAmount']);
const formMapping = Object.fromEntries(Object.keys(labels).map((field) => [field, { sourceField: field, required: requiredFormFields.has(field) }]));

function App() {
  const input = useRef();
  const [file, setFile] = useState();
  const [result, setResult] = useState();
  const [form, setForm] = useState(blank);
  const [extractionMethod, setExtractionMethod] = useState('auto');
  const [loading, setLoading] = useState(false);
  const [saving, setSaving] = useState(false);
  const [message, setMessage] = useState('');
  const [error, setError] = useState('');

  const isImage = file && IMAGE_TYPES.has(file.type);
  const onChoose = (next) => { setFile(next); setResult(); setForm(blank); setError(''); setMessage(''); };

  async function extract() {
    if (!file) return;
    setLoading(true); setError(''); setMessage('');
    try {
      const body = new FormData(); body.append('file', file); body.append('extractionMethod', extractionMethod);
      const response = await fetch('/api/extractions', { method: 'POST', body }); const payload = await response.json();
      if (!response.ok) throw new Error(payload.error || 'Upload failed.');
      setResult(payload); setForm(payload.data);
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

  return <main className="min-h-screen bg-slate-950 text-slate-100"><div className="mx-auto max-w-6xl px-6 py-14">
    <section className="grid gap-6 lg:grid-cols-[.9fr_1.4fr]">
      <div className="rounded-2xl border border-slate-800 bg-slate-900/70 p-6 shadow-2xl shadow-black/20">
        <h2 className="text-lg font-semibold">1. Upload document</h2>
        <button onClick={() => input.current.click()} className="mt-5 flex min-h-48 w-full flex-col items-center justify-center rounded-xl border border-dashed border-slate-600 bg-slate-950/40 p-6 text-center transition hover:border-cyan-400">
          <span className="text-3xl">{isImage ? '🖼️' : '⇧'}</span>
          <span className="mt-3 font-medium">{file ? file.name : 'Choose a PDF or image'}</span>
          <span className="mt-1 text-sm text-slate-500">PDF · JPG · PNG · WebP · TIFF — max 20 MB</span>
        </button>
        <input ref={input} className="hidden" type="file" accept="application/pdf,image/jpeg,image/png,image/webp,image/tiff" onChange={(event) => onChoose(event.target.files?.[0])}/>

        <label className="mt-4 block text-sm font-medium text-slate-300">Text extraction method
          <select value={extractionMethod} onChange={(event) => setExtractionMethod(event.target.value)} className="mt-1.5 w-full rounded-lg border border-slate-700 bg-slate-950 px-3 py-2.5 text-slate-100 outline-none focus:border-cyan-400">
            <option value="auto">Automatic (Tesseract → AI fallback)</option>
            <option value="ai">AI-assisted (Claude → Gemini, always)</option>
            {!isImage && <option value="pdfplumber">PDFPlumber only (PDF)</option>}
            <option value="tesseract">Tesseract OCR only</option>
          </select>
        </label>

        <button disabled={!file || loading} onClick={extract} className="mt-4 w-full rounded-xl bg-cyan-400 px-4 py-3 font-semibold text-slate-950 transition hover:bg-cyan-300 disabled:cursor-not-allowed disabled:opacity-40">
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
                  <li>3. Automatic: Claude, then Gemini kick in when confidence &lt; 75% — image is sent natively so AI can read it visually</li>
                  <li>4. AI-assisted: Claude → Gemini runs for every upload regardless of confidence</li>
                  <li>5. Review and save the proposed template for future form filling</li>
                </>
              : <>
                  <li>1. PDFPlumber or Tesseract gets text</li>
                  <li>2. A saved template fills matching documents first</li>
                  <li>3. Automatic: Claude, then Gemini only for low-confidence new layouts</li>
                  <li>4. AI-assisted: same provider chain for every new layout</li>
                  <li>5. Review and save the proposed template for future form filling</li>
                </>
            }
          </ol>
        </div>
      </div>

      <div className="rounded-2xl border border-slate-800 bg-white p-6 text-slate-900">
        <div className="flex items-start justify-between gap-4">
          <div>
            <h2 className="text-lg font-semibold">2. Review extracted form</h2>
            <p className="mt-1 text-sm text-slate-500">Confirm the values before saving a reusable template.</p>
          </div>
          {result && <span className="rounded-full bg-cyan-50 px-3 py-1 text-xs font-semibold text-cyan-700">{Math.round(result.confidence * 100)}% confidence</span>}
        </div>

        <div className="mt-6 grid gap-4 sm:grid-cols-2">{Object.entries(labels).map(([key, label]) => <label key={key} className="text-sm font-medium text-slate-600">{label}<input value={form[key] || ''} onChange={(event) => setForm({ ...form, [key]: event.target.value })} className="mt-1.5 w-full rounded-lg border border-slate-200 px-3 py-2.5 text-slate-900 outline-none focus:border-cyan-500 focus:ring-2 focus:ring-cyan-100" placeholder="Not extracted" /></label>)}</div>

        {result && <><div className="mt-7 border-t border-slate-200 pt-5">
          <div className="flex flex-wrap items-center justify-between gap-3">
            <div>
              <h3 className="font-semibold">Extraction path</h3>
              <p className="text-sm text-slate-500">Selected: {result.source.replaceAll('_', ' ')}</p>
            </div>
            {!result.templateId && <button onClick={saveTemplate} disabled={saving} className="rounded-lg bg-slate-900 px-4 py-2 text-sm font-semibold text-white disabled:opacity-50">{saving ? 'Saving…' : 'Save as reusable template'}</button>}
          </div>
          <div className="mt-3 flex flex-wrap gap-2">{result.attempts.map((attempt) => <span key={attempt.name} title={attempt.detail} className={`rounded-full px-3 py-1 text-xs font-medium ${attempt.status === 'completed' ? 'bg-emerald-50 text-emerald-700' : 'bg-amber-50 text-amber-700'}`}>{attempt.name.replaceAll('_', ' ')} · {attempt.status}</span>)}</div>
        </div>
        <details className="mt-5 text-sm"><summary className="cursor-pointer font-medium text-slate-600">View extracted text preview</summary><pre className="mt-3 max-h-48 overflow-auto rounded-lg bg-slate-950 p-3 whitespace-pre-wrap text-xs text-slate-300">{result.preview || 'No text recovered.'}</pre></details></>}
      </div>
    </section>
  </div></main>;
}

createRoot(document.getElementById('root')).render(<App />);
