import { useRef, useState } from 'react';
import { createRoot } from 'react-dom/client';
import './styles.css';

const labels = { documentType: 'Document type', documentNumber: 'Order number', documentDate: 'Order date', vendorName: 'Vendor / supplier', customerName: 'Customer / buyer', currency: 'Currency', subtotalAmount: 'Subtotal', taxAmount: 'Tax', totalAmount: 'Total' };
const blank = Object.fromEntries(Object.keys(labels).map((key) => [key, '']));

function App() {
  const input = useRef();
  const [file, setFile] = useState();
  const [result, setResult] = useState();
  const [form, setForm] = useState(blank);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState('');
  const onChoose = (next) => { setFile(next); setResult(); setForm(blank); setError(''); };
  async function extract() {
    if (!file) return;
    setLoading(true); setError('');
    try {
      const body = new FormData(); body.append('file', file);
      const response = await fetch('/api/extractions', { method: 'POST', body });
      const payload = await response.json();
      if (!response.ok) throw new Error(payload.error || 'Upload failed.');
      setResult(payload); setForm(payload.data);
    } catch (err) { setError(err.message); } finally { setLoading(false); }
  }
  return <main className="min-h-screen bg-slate-950 text-slate-100"><div className="mx-auto max-w-6xl px-6 py-14">

    <section className="grid gap-6 lg:grid-cols-[.9fr_1.4fr]">
      <div className="rounded-2xl border border-slate-800 bg-slate-900/70 p-6 shadow-2xl shadow-black/20"><h2 className="text-lg font-semibold">1. Upload document</h2><button onClick={() => input.current.click()} className="mt-5 flex min-h-48 w-full flex-col items-center justify-center rounded-xl border border-dashed border-slate-600 bg-slate-950/40 p-6 text-center transition hover:border-cyan-400"><span className="text-3xl">⇧</span><span className="mt-3 font-medium">{file ? file.name : 'Choose a PDF'}</span><span className="mt-1 text-sm text-slate-500">Maximum 20 MB</span></button><input ref={input} className="hidden" type="file" accept="application/pdf" onChange={(event) => onChoose(event.target.files?.[0])}/><label className="mt-4 block text-sm font-medium text-slate-300">Extraction method<select value={extractionMethod} onChange={(event) => setExtractionMethod(event.target.value)} className="mt-1.5 w-full rounded-lg border border-slate-700 bg-slate-950 px-3 py-2.5 text-slate-100 outline-none focus:border-cyan-400"><option value="auto">Automatic fallback (recommended)</option><option value="pdfplumber">PDFPlumber only</option><option value="tesseract">Tesseract OCR only</option></select></label><button disabled={!file || loading} onClick={extract} className="mt-4 w-full rounded-xl bg-cyan-400 px-4 py-3 font-semibold text-slate-950 transition hover:bg-cyan-300 disabled:cursor-not-allowed disabled:opacity-40">{loading ? 'Extracting order…' : 'Extract fields'}</button>{error && <p className="mt-4 rounded-lg border border-rose-900 bg-rose-950/40 p-3 text-sm text-rose-300">{error}</p>}
        <div className="mt-7 border-t border-slate-800 pt-5"><h3 className="text-sm font-semibold text-slate-300">Pipeline</h3><ol className="mt-3 space-y-2 text-sm text-slate-400"><li>1. PDFPlumber: embedded text</li><li>2. Tesseract: scanned-page OCR fallback</li><li>3. Deterministic rules: extract order fields</li></ol></div>
      </div>
      <div className="rounded-2xl border border-slate-800 bg-white p-6 text-slate-900"><div className="flex items-start justify-between gap-4"><div><h2 className="text-lg font-semibold">2. Review extracted form</h2><p className="mt-1 text-sm text-slate-500">Fields remain editable—never silently commit OCR output.</p></div>{result && <span className="rounded-full bg-cyan-50 px-3 py-1 text-xs font-semibold text-cyan-700">{Math.round(result.confidence * 100)}% confidence</span>}</div>
        <div className="mt-6 grid gap-4 sm:grid-cols-2">{Object.entries(labels).map(([key, label]) => <label key={key} className="text-sm font-medium text-slate-600">{label}<input value={form[key] || ''} onChange={(event) => setForm({ ...form, [key]: event.target.value })} className="mt-1.5 w-full rounded-lg border border-slate-200 px-3 py-2.5 text-slate-900 outline-none focus:border-cyan-500 focus:ring-2 focus:ring-cyan-100" placeholder="Not extracted" /></label>)}</div>
        {result && <><div className="mt-7 border-t border-slate-200 pt-5"><div className="flex items-center justify-between"><h3 className="font-semibold">Attempt log</h3><span className="text-sm text-slate-500">Selected: {result.source.replaceAll('_', ' ')}</span></div><div className="mt-3 flex flex-wrap gap-2">{result.attempts.map((attempt) => <span key={attempt.name} title={attempt.detail} className={`rounded-full px-3 py-1 text-xs font-medium ${attempt.status === 'completed' ? 'bg-emerald-50 text-emerald-700' : 'bg-amber-50 text-amber-700'}`}>{attempt.name.replaceAll('_', ' ')} · {attempt.status}</span>)}</div></div><details className="mt-5 text-sm"><summary className="cursor-pointer font-medium text-slate-600">View extracted text preview</summary><pre className="mt-3 max-h-48 overflow-auto rounded-lg bg-slate-950 p-3 whitespace-pre-wrap text-xs text-slate-300">{result.preview || 'No text recovered.'}</pre></details></>}
      </div>
    </section>
  </div></main>;
}
createRoot(document.getElementById('root')).render(<App />);
