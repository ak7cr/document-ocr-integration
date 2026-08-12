import { execFile } from 'node:child_process';
import { existsSync } from 'node:fs';
import path from 'node:path';
import { promisify } from 'node:util';

const execFileAsync = promisify(execFile);
const requiredFields = ['documentNumber', 'documentDate', 'vendorName', 'totalAmount'];

export function emptyOrder() {
  return { documentType: '', documentNumber: '', documentDate: '', vendorName: '', customerName: '',
    currency: '', subtotalAmount: '', taxAmount: '', totalAmount: '', lineItems: [] };
}

function clean(value) { return value?.replace(/\s+/g, ' ').trim() || ''; }
function amount(value) { return value ? value.replace(/[^0-9.,-]/g, '').replace(/,/g, '') : ''; }

export function extractByRules(text) {
  const data = emptyOrder();
  const lines = text.split(/\r?\n/).map(clean).filter(Boolean);
  const match = (patterns) => {
    for (const pattern of patterns) { const result = text.match(pattern); if (result?.[1]) return clean(result[1]); }
    return '';
  };
  data.documentType = /purchase\s+order|\bP\.?O\.?\b/i.test(text) ? 'purchase_order'
    : /sales\s+order|\bS\.?O\.?\b/i.test(text) ? 'sales_order' : '';
  data.documentNumber = match([/\border\s*(?:no\.?|number|#|:)\s*([A-Z0-9][A-Z0-9\-/]+)/i, /\b(?:PO|SO)[\s#:-]*([A-Z0-9\-/]+)/i]);
  data.documentDate = match([/(?:order\s*)?date\s*[:#-]?\s*([0-9]{1,4}[\/.\-][A-Z0-9]{1,3}[\/.\-][0-9]{2,4})/i]);
  data.vendorName = match([/(?:vendor|supplier|sold\s+by)\s*[:#-]?\s*([^\n]+)/i]);
  data.customerName = match([/(?:customer|buyer|bill\s+to|ship\s+to)\s*[:#-]?\s*([^\n]+)/i]);
  const currencyMark = text.match(/(?:₹|\$|€|£|\bINR\b|\bUSD\b|\bEUR\b|\bGBP\b)/i)?.[0] || '';
  data.currency = ({ '₹': 'INR', '$': 'USD', '€': 'EUR', '£': 'GBP' })[currencyMark] || currencyMark.toUpperCase();
  data.subtotalAmount = amount(match([/(?:sub\s*total)\s*[:#-]?\s*((?:₹|\$|€|£|INR|USD|EUR|GBP)?\s*[\d,.-]+)/i]));
  data.taxAmount = amount(match([/(?:tax|gst|vat)\s*(?:amount)?\s*[:#-]?\s*((?:₹|\$|€|£|INR|USD|EUR|GBP)?\s*[\d,.-]+)/i]));
  data.totalAmount = amount(match([/(?:grand\s*)?total\s*(?:amount)?\s*[:#-]?\s*((?:₹|\$|€|£|INR|USD|EUR|GBP)?\s*[\d,.-]+)/i]));
  if (!data.vendorName && lines.length) data.vendorName = lines.find((line) => !/order|date|invoice|page/i.test(line) && /[A-Za-z]{3}/.test(line)) || '';
  return data;
}

export function confidence(data) {
  const filled = requiredFields.filter((key) => Boolean(data[key])).length;
  return Number((filled / requiredFields.length).toFixed(2));
}

export async function runLocalExtractor(mode, filePath) {
  const virtualenvPython = path.join(process.cwd(), '.venv', 'bin', 'python');
  const python = process.env.PYTHON_BIN || (existsSync(virtualenvPython) ? virtualenvPython : 'python3');
  const { stdout } = await execFileAsync(python, ['server/pdf_worker.py', mode, filePath], { timeout: 90_000, maxBuffer: 8_000_000 });
  const result = JSON.parse(stdout);
  if (!result.ok) throw new Error(result.error);
  return result;
}

const schemaInstruction = `Return only valid JSON using this exact shape: {"documentType":"purchase_order|sales_order|unknown","documentNumber":"","documentDate":"","vendorName":"","customerName":"","currency":"","subtotalAmount":"","taxAmount":"","totalAmount":"","lineItems":[{"description":"","quantity":"","unitPrice":"","amount":""}]}. Do not invent values; use empty strings or an empty array when unavailable. Amounts must contain digits and a decimal separator only.`;

function parseModelJson(value) {
  const json = value.match(/\{[\s\S]*\}/)?.[0];
  if (!json) throw new Error('Model did not return an extraction JSON object.');
  const parsed = JSON.parse(json);
  return { ...emptyOrder(), ...parsed, lineItems: Array.isArray(parsed.lineItems) ? parsed.lineItems : [] };
}

export async function extractWithClaude(text) {
  if (!process.env.ANTHROPIC_API_KEY) throw new Error('ANTHROPIC_API_KEY is not configured.');
  const response = await fetch('https://api.anthropic.com/v1/messages', {
    method: 'POST', headers: { 'content-type': 'application/json', 'x-api-key': process.env.ANTHROPIC_API_KEY, 'anthropic-version': '2023-06-01' },
    body: JSON.stringify({ model: process.env.ANTHROPIC_MODEL || 'claude-haiku-4-5', max_tokens: 1500, messages: [{ role: 'user', content: `${schemaInstruction}\n\nDOCUMENT TEXT:\n${text.slice(0, 80_000)}` }] }),
  });
  if (!response.ok) throw new Error(`Claude request failed (${response.status}): ${await response.text()}`);
  return parseModelJson((await response.json()).content?.[0]?.text || '');
}

export async function extractWithGemini(text) {
  if (!process.env.GEMINI_API_KEY) throw new Error('GEMINI_API_KEY is not configured.');
  const model = process.env.GEMINI_MODEL || 'gemini-3.1-pro-preview';
  const response = await fetch(`https://generativelanguage.googleapis.com/v1beta/models/${model}:generateContent?key=${process.env.GEMINI_API_KEY}`, {
    method: 'POST', headers: { 'content-type': 'application/json' },
    body: JSON.stringify({ contents: [{ parts: [{ text: `${schemaInstruction}\n\nDOCUMENT TEXT:\n${text.slice(0, 80_000)}` }] }], generationConfig: { responseMimeType: 'application/json' } }),
  });
  if (!response.ok) throw new Error(`Gemini request failed (${response.status}): ${await response.text()}`);
  return parseModelJson((await response.json()).candidates?.[0]?.content?.parts?.[0]?.text || '');
}
