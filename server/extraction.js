import { execFile } from 'node:child_process';
import { existsSync } from 'node:fs';
import path from 'node:path';
import { promisify } from 'node:util';

const execFileAsync = promisify(execFile);
const requiredFields = ['documentNumber', 'documentDate', 'vendorName', 'totalAmount'];
const fieldLabels = {
  documentNumber: ['purchase order no', 'po number', 'order number', 'order no'],
  documentDate: ['order date', 'date'], vendorName: ['vendor', 'supplier', 'sold by'],
  customerName: ['customer', 'buyer', 'bill to', 'ship to'], subtotalAmount: ['subtotal', 'sub total'],
  taxAmount: ['tax', 'gst', 'vat'], totalAmount: ['grand total', 'total amount', 'total'],
};

export function emptyOrder() {
  return { documentType: '', documentNumber: '', documentDate: '', vendorName: '', customerName: '', currency: '', subtotalAmount: '', taxAmount: '', totalAmount: '', lineItems: [] };
}

export function normalizeText(value = '') { return value.replace(/\u00a0/g, ' ').replace(/\s+/g, ' ').trim(); }
function clean(value) { return normalizeText(value || '').replace(/^[\-\u2013\u2014]\s*/, ''); }
function amount(value) { return value ? value.replace(/[^0-9.,-]/g, '').replace(/,/g, '') : ''; }
function escapeRegex(value) { return value.replace(/[.*+?^${}()|[\]\\]/g, '\\$&'); }

export function extractByRules(text, dictionary = []) {
  const data = emptyOrder();
  const lines = text.split(/\r?\n/).map(clean).filter(Boolean);
  const match = (patterns) => {
    for (const pattern of patterns) { const result = text.match(pattern); if (result?.[1]) return clean(result[1]); }
    return '';
  };
  data.documentType = /purchase\s+order|\bP\.?O\.?\b/i.test(text) ? 'purchase_order' : /sales\s+order|\bS\.?O\.?\b/i.test(text) ? 'sales_order' : '';
  data.documentNumber = match([/\border\s*(?:no\.?|number|#|:)\s*(?:[:#\-\u2013\u2014]\s*)?([A-Z0-9][A-Z0-9\-/]+)/i, /\b(?:PO|SO)[\s#:\-\u2013\u2014]*([A-Z0-9\-/]+)/i]);
  data.documentDate = match([/(?:order\s*)?date\s*(?:[:#\-\u2013\u2014]\s*)?([0-9]{1,4}[\/.\-][A-Z0-9]{1,3}[\/.\-][0-9]{2,4})/i]);
  data.vendorName = match([/(?:vendor|supplier|sold\s+by)\s*(?:[:#\-\u2013\u2014]\s*)?([^\n]+)/i]);
  data.customerName = match([/(?:customer|buyer|bill\s+to|ship\s+to)\s*(?:[:#\-\u2013\u2014]\s*)?([^\n]+)/i]);
  const currencyMark = text.match(/(?:₹|\$|€|£|\bINR\b|\bUSD\b|\bEUR\b|\bGBP\b)/i)?.[0] || '';
  data.currency = ({ '₹': 'INR', '$': 'USD', '€': 'EUR', '£': 'GBP' })[currencyMark] || currencyMark.toUpperCase();
  data.subtotalAmount = amount(match([/\b(?:sub\s*total)\b\s*(?:[:#\-\u2013\u2014]\s*)?((?:₹|\$|€|£|INR|USD|EUR|GBP)?\s*[\d,.-]+)/i]));
  data.taxAmount = amount(match([/\b(?:tax|gst|vat)\b\s*(?:amount)?\s*(?:[:#\-\u2013\u2014]\s*)?((?:₹|\$|€|£|INR|USD|EUR|GBP)?\s*[\d,.-]+)/i]));
  data.totalAmount = amount(match([/\b(?:grand\s*)?total\b\s*(?:amount)?\s*(?:[:#\-\u2013\u2014]\s*)?((?:₹|\$|€|£|INR|USD|EUR|GBP)?\s*[\d,.-]+)/i]));
  for (const entry of dictionary) {
    const aliases = [entry.canonicalValue, ...(entry.aliases || [])].filter(Boolean);
    if (entry.fieldName === 'vendorName' && !data.vendorName && aliases.some((alias) => text.toLowerCase().includes(alias.toLowerCase()))) data.vendorName = entry.canonicalValue;
  }
  if (!data.vendorName && lines.length) data.vendorName = lines.find((line) => !/order|date|invoice|page/i.test(line) && /[A-Za-z]{3}/.test(line)) || '';
  return data;
}

function extractRule(text, rule) {
  try {
    const anchorMatch = rule.anchor && text.match(new RegExp(`\\b${escapeRegex(rule.anchor)}\\b`, 'i'));
    const scope = anchorMatch?.index >= 0 ? text.slice(anchorMatch.index) : text;
    const result = scope.match(new RegExp(rule.regex, 'i'));
    return clean(result?.[1] || '');
  } catch { return ''; }
}

export function applyTemplate(text, template, dictionary) {
  const data = extractByRules(text, dictionary);
  const mappings = Object.entries(template.formMapping || {});
  const fields = mappings.length ? mappings.map(([formField, mapping]) => [formField, template.fieldRules?.[mapping.sourceField]]) : Object.entries(template.fieldRules || {});
  for (const [field, rule] of fields) {
    if (rule?.regex) data[field] = extractRule(text, rule) || data[field] || '';
  }
  return data;
}

export function requiredMappingStatus(data, formMapping = {}) {
  const required = Object.entries(formMapping).filter(([, mapping]) => mapping?.required).map(([field]) => field);
  const missing = required.filter((field) => !data[field]);
  return { required, missing };
}

export function matchTemplate(text, templates) {
  const normalized = normalizeText(text).toLowerCase();
  const stableAnchor = (anchor) => Object.values(fieldLabels).flat().find((label) => anchor.toLowerCase().includes(label)) || anchor;
  let best = null;
  for (const template of templates) {
    const anchors = [...new Set((template.fingerprint?.anchors || []).map(stableAnchor))];
    if (!anchors.length) continue;
    const matches = anchors.filter((anchor) => normalized.includes(anchor.toLowerCase())).length;
    const score = matches / anchors.length;
    if (!best || score > best.score) best = { template, score };
  }
  return best?.score >= 0.6 ? best : null;
}

export function confidence(data) { return Number((requiredFields.filter((key) => Boolean(data[key])).length / requiredFields.length).toFixed(2)); }

export function buildTemplateProposal(text, data, name = '') {
  const lines = text.split(/\r?\n/).map(clean).filter(Boolean);
  const fieldRules = {};
  const anchors = new Set();
  if (data.documentType === 'purchase_order') anchors.add('purchase order');
  if (data.documentType === 'sales_order') anchors.add('sales order');
  for (const [field, labels] of Object.entries(fieldLabels)) {
    const label = labels.find((candidate) => text.toLowerCase().includes(candidate));
    if (!label) continue;
    anchors.add(label);
    const valuePattern = /Amount$/.test(field) ? '([₹$€£A-Z]{0,4}\\s*[\\d,.]+)' : field === 'documentDate' ? '([0-9A-Za-z./-]{6,20})' : '([^\\n]{1,120})';
    fieldRules[field] = { anchor: label, regex: `${escapeRegex(label)}\\s*[:#\\-\\u2013\\u2014]?\\s*${valuePattern}` };
  }
  const fallbackAnchors = lines.filter((line) => line.length >= 3 && line.length <= 60 && !/\d/.test(line)).slice(0, 8);
  return { name: name || data.vendorName || 'New document template', fingerprint: { anchors: [...anchors, ...fallbackAnchors].slice(0, 8) }, fieldRules };
}

export async function runLocalExtractor(mode, filePath) {
  const virtualenvPython = path.join(process.cwd(), '.venv', 'bin', 'python');
  const python = process.env.PYTHON_BIN || (existsSync(virtualenvPython) ? virtualenvPython : 'python3');
  const { stdout } = await execFileAsync(python, ['server/pdf_worker.py', mode, filePath], { timeout: 90_000, maxBuffer: 8_000_000 });
  const result = JSON.parse(stdout);
  if (!result.ok) throw new Error(result.error);
  return result;
}

const aiInstruction = `Return JSON only with {"data":{"documentType":"","documentNumber":"","documentDate":"","vendorName":"","customerName":"","currency":"","subtotalAmount":"","taxAmount":"","lineItems":[]},"template":{"name":"","fingerprint":{"anchors":["stable label"]},"fieldRules":{"fieldName":{"anchor":"label","regex":"capturing regex"}}}}. Read the attached PDF visually when supplied and use the recovered text as supporting context. Never invent values. Regex must have one capture group.`;
function parseAi(value) { const json = value.match(/\{[\s\S]*\}/)?.[0]; if (!json) throw new Error('AI did not return JSON.'); const parsed = JSON.parse(json); return { data: { ...emptyOrder(), ...(parsed.data || {}) }, template: parsed.template }; }
function aiPrompt(text) { return `${aiInstruction}\nRECOVERED TEXT:\n${text.slice(0, 80_000)}`; }

export async function extractWithClaude(text, documentBuffer) {
  if (!process.env.ANTHROPIC_API_KEY) throw new Error('ANTHROPIC_API_KEY is not configured.');
  const content = [];
  if (documentBuffer) content.push({ type: 'document', source: { type: 'base64', media_type: 'application/pdf', data: documentBuffer.toString('base64') } });
  content.push({ type: 'text', text: aiPrompt(text) });
  const response = await fetch('https://api.anthropic.com/v1/messages', { method: 'POST', headers: { 'content-type': 'application/json', 'x-api-key': process.env.ANTHROPIC_API_KEY, 'anthropic-version': '2023-06-01' }, body: JSON.stringify({ model: process.env.ANTHROPIC_MODEL || 'claude-haiku-4-5', max_tokens: 1800, messages: [{ role: 'user', content }] }) });
  if (!response.ok) throw new Error(`Claude request failed (${response.status})`);
  return parseAi((await response.json()).content?.[0]?.text || '');
}

export async function extractWithGemini(text, documentBuffer) {
  if (!process.env.GEMINI_API_KEY) throw new Error('GEMINI_API_KEY is not configured.');
  const parts = [{ text: aiPrompt(text) }];
  if (documentBuffer) parts.push({ inlineData: { mimeType: 'application/pdf', data: documentBuffer.toString('base64') } });
  const model = process.env.GEMINI_MODEL || 'gemini-3.1-pro-preview';
  const response = await fetch(`https://generativelanguage.googleapis.com/v1beta/models/${encodeURIComponent(model)}:generateContent`, { method: 'POST', headers: { 'content-type': 'application/json', 'x-goog-api-key': process.env.GEMINI_API_KEY }, body: JSON.stringify({ contents: [{ role: 'user', parts }], generationConfig: { responseMimeType: 'application/json', maxOutputTokens: 1800 } }) });
  if (!response.ok) {
    const detail = (await response.text()).replace(/\s+/g, ' ').slice(0, 300);
    throw new Error(`Gemini request failed (${response.status})${detail ? `: ${detail}` : ''}`);
  }
  const body = await response.json();
  return parseAi(body.candidates?.[0]?.content?.parts?.map((part) => part.text || '').join('') || '');
}
