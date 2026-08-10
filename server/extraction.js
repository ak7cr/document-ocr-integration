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
