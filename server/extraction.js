import { execFile } from 'node:child_process';
import { existsSync } from 'node:fs';
import path from 'node:path';
import { promisify } from 'node:util';

const execFileAsync = promisify(execFile);
export const requiredFields = ['documentType', 'documentNumber', 'documentDate', 'vendorName', 'customerName', 'currency', 'subtotalAmount', 'taxAmount', 'totalAmount'];

export const CORE_FIELD_DICTIONARY = {
  documentType: {
    purchase_order: ['purchase order', 'p.o. details', 'p u r c h a s e o r d e r', 'po details', 'purchase'],
    sales_order: ['sales order', 'so details', 'customer order form', 'order summary', 'sales order no', 'so ref']
  },
  documentNumber: [
    'document number', 'purchase order no', 'po number', 'order number', 'order no.', 'order no',
    'reference no.', 'reference no', 'order id', 'so ref', 'sales order no', 'po no.', 'po no', 'po#', 'so number',
    'invoice number', 'invoice no', 'invoice #'
  ],
  documentDate: [
    'document date', 'order date', 'date of order', 'issued on', 'created',
    'po date', 'date', 'date of issue', 'invoice date', 'issued date'
  ],
  vendorName: [
    'seller / vendor', 'vendor account', 'vendor name', 'supplier', 'seller', 'sold by', 'vendor'
  ],
  customerName: [
    'customer / purchaser', 'customer account', 'customer name', 'ordered by', 'bill to', 'ship to', 'buyer', 'client', 'customer'
  ],
  subtotalAmount: [
    'amount before tax', 'taxable amount', 'net amount', 'base value',
    'sub total', 'sub-total', 'subtotal'
  ],
  taxAmount: [
    'applicable tax', 'tax amount', 'gst/tax', 'tax included', 'tax total',
    'tax amt.', 'tax amt', 'total gst', 'gst', 'vat', 'tax'
  ],
  totalAmount: [
    'total order value', 'total payable', 'amount payable', 'order total',
    'final amount', 'net total', 'total amt.', 'total amt', 'grand total', 'total amount', 'total'
  ]
};

// Backwards compatibility alias
export const fieldLabels = {
  documentNumber: CORE_FIELD_DICTIONARY.documentNumber,
  documentDate: CORE_FIELD_DICTIONARY.documentDate,
  vendorName: CORE_FIELD_DICTIONARY.vendorName,
  customerName: CORE_FIELD_DICTIONARY.customerName,
  subtotalAmount: CORE_FIELD_DICTIONARY.subtotalAmount,
  taxAmount: CORE_FIELD_DICTIONARY.taxAmount,
  totalAmount: CORE_FIELD_DICTIONARY.totalAmount,
};

export function emptyOrder() {
  return { documentType: '', documentNumber: '', documentDate: '', vendorName: '', customerName: '', currency: '', subtotalAmount: '', taxAmount: '', totalAmount: '', lineItems: [] };
}

export function normalizeText(value = '') { return value.replace(/\u00a0/g, ' ').replace(/\s+/g, ' ').trim(); }
export function clean(value) { return normalizeText(value || '').replace(/^[\-\u2013\u2014|:]\s*/, '').replace(/\s*[\-\u2013\u2014|:]$/, ''); }
export function amount(value) {
  if (!value) return '';
  // Start at the first digit so a preceding '.' from a currency symbol
  // ("Rs." / "$.") is not captured as part of the number.
  const m = String(value).match(/\d[\d, .]*/);
  if (!m) return '';
  let s = m[0].trim();
  const lastComma = s.lastIndexOf(',');
  const lastDot = s.lastIndexOf('.');
  if (lastComma > lastDot && s.length - lastComma <= 3) {
    s = s.substring(0, lastComma) + '.' + s.substring(lastComma + 1);
  }
  return s.replace(/[ ,]/g, '');
}
export function escapeRegex(value) { return value.replace(/[.*+?^${}()|[\]\\]/g, '\\$&'); }


export function extractSummaryTable(text, data) {
  const grid = text.split(/\r?\n/)
    .map((line) => line.split('\t').map((c) => c.trim()))
    .filter((row) => row.some(Boolean));
  if (grid.length < 3) return data;

  const totalIdx = grid.findIndex((row) => row.some((c) => /^total$/i.test(clean(c))));
  if (totalIdx < 2) return data;

  const isSummaryHeader = (row) => row.some((c) => /gross|net\s+worth|subtotal|grand\s+total|\bvat\b|tax\s+amount/i.test(c));
  let headerIdx = -1;
  for (let i = totalIdx - 1; i >= 0; i--) {
    if (isSummaryHeader(grid[i])) { headerIdx = i; break; }
  }
  if (headerIdx < 0) return data;

  const amountsOf = (row) => row
    .filter((cell) => !/%/.test(cell) && !/^total$/i.test(clean(cell)))
    .map((cell) => amount(cell))
    .filter(Boolean);

  const subtotalAmounts = amountsOf(grid[totalIdx - 1]);
  const totalAmounts = amountsOf(grid[totalIdx]);
  if (subtotalAmounts.length >= 2) {
    data.subtotalAmount = subtotalAmounts[0]; // net worth
    data.taxAmount = subtotalAmounts[1];      // vat / tax
  }
  if (totalAmounts.length >= 1) {
    data.totalAmount = totalAmounts[totalAmounts.length - 1]; // grand total (gross worth)
  }
  return data;
}

// ── Line items (ITEMS table) ─────────────────────────────────────────────────

export function emptyLineItem() {
  return { serialNo: '', itemName: '', hsnSac: '', quantity: '', unit: '', rate: '', discount: '', taxableValue: '', cgstAmount: '', sgstAmount: '', igstAmount: '', tax: '', amount: '', grossAmount: '' };
}

const LINE_ITEM_UNITS = new Set(['each', 'nos', 'no', 'pcs', 'pc', 'kg', 'g', 'gm', 'm', 'cm', 'mm', 'l', 'ml', 'box', 'boxes', 'dozen', 'unit', 'units', 'uom', 'set', 'pack', 'packs', 'bundle', 'carton']);
const isLineItemNumber = (token) => /^\d[\d, .]*$/.test(token);

/** Map a table column header to a canonical line-item field (priority order). */
function classifyLineItemColumn(label) {
  const l = clean(label).toLowerCase();
  if (!l) return null;
  if (/^(no\.?|s\.?no\.?|sr\.?no\.?|sl\.?no\.?|#|serial)$/.test(l) || /^no$/.test(l)) return 'serialNo';
  if (/hsn|sac|itc\s*code/.test(l)) return 'hsnSac';
  if (/qty|quantity/.test(l)) return 'quantity';
  if (/^(um|uom|unit)$/.test(l)) return 'unit';
  if (/disc/.test(l)) return 'discount';
  if (/cgst/.test(l)) return 'cgstAmount';
  if (/sgst/.test(l)) return 'sgstAmount';
  if (/igst/.test(l)) return 'igstAmount';
  if (/taxable/.test(l)) return 'taxableValue';
  if (/desc|item|particular|product|name|goods|service/.test(l)) return 'itemName';
  // percentage tax columns ("VAT [%]") come before "Gross worth" merged cells
  if (/vat|gst|tax/.test(l) && /%/.test(l)) return 'tax';
  if (/gross|grand\s+total|total/.test(l)) return 'grossAmount';
  if (/rate|unit\s*price|unit\s*cost|net\s*price|\bprice\b/.test(l)) return 'rate';
  if (/net\s*(worth|value|amount)|amount\s*before\s*tax|net\s*amount/.test(l)) return 'amount';
  if (/vat|gst|tax/.test(l)) return 'tax';
  if (/amount|worth|value|net/.test(l)) return 'amount';
  return null;
}

/** Assign cells by their header column position; leftover cells are classified
 * by pattern so a merged header (e.g. "VAT [%] Gross worth") still captures
 * the extra column (gross amount). */
function parseLineItemRowByColumns(row, colMap) {
  const item = emptyLineItem();
  const cells = [...row];

  
  const serialIdx = Object.keys(colMap).find((i) => colMap[i] === 'serialNo');
  if (serialIdx != null && cells[serialIdx]) {
    if (colMap[Number(serialIdx) + 1] === 'hsnSac') {
      const hm = cells[serialIdx].match(/^(\d{1,3})\s+(\d{4,8})$/);
      if (hm) {
        item.serialNo = hm[1];
        cells[Number(serialIdx) + 1] = hm[2];
        cells[serialIdx] = '';
      }
    }
    if (!item.serialNo) {
      const m = cells[serialIdx].match(/^(\d{1,3})\s*[.)]\s*(.*)$/) || (/^\d{1,3}$/.test(cells[serialIdx]) ? cells[serialIdx].match(/^(\d{1,3})/) : null);
      if (m) {
        item.serialNo = m[1];
        if (m[2]) {
          const nameIdx = Number(serialIdx) + 1;
          if (colMap[nameIdx] === 'itemName') cells[nameIdx] = (m[2] + ' ' + (cells[nameIdx] || '')).trim();
        }
        cells[serialIdx] = '';
      }
    }
  }

  for (const [idxStr, field] of Object.entries(colMap)) {
    const idx = Number(idxStr);
    if (field === 'serialNo') continue; // already handled above
    if (idx < cells.length && cells[idx]) item[field] = cells[idx];
  }

  if (!item.itemName) {
    const nameParts = [];
    let k = 0;
    while (k < cells.length && !isLineItemNumber(cells[k]) && !LINE_ITEM_UNITS.has(cells[k].toLowerCase()) && !/%/.test(cells[k])) nameParts.push(cells[k++]);
    item.itemName = nameParts.join(' ').trim();
  }

  const keys = Object.keys(colMap).map(Number);
  const mappedMax = keys.length ? Math.max(...keys) : -1;
  const leftover = cells.slice(mappedMax + 1);
  // Rejoin space-thousands in merged leftover cells ("1 220,95").
  const tokens = [];
  for (const cell of leftover) for (const token of cell.split(/\s+/)) if (token) tokens.push(token);
  const merged = [];
  for (let i = 0; i < tokens.length; i++) {
    const cur = tokens[i];
    const next = tokens[i + 1];
    if (/^\d{1,3}$/.test(cur) && next && /^\d{2,3}[,]\d{1,2}$/.test(next) && !/%/.test(next)) {
      merged.push(`${cur} ${next}`);
      i++;
    } else {
      merged.push(cur);
    }
  }
  const numbers = [];
  for (const token of merged) {
    if (/%/.test(token)) { item.tax = token; continue; }
    if (LINE_ITEM_UNITS.has(token.toLowerCase())) { if (!item.unit) item.unit = token; continue; }
    if (isLineItemNumber(token)) numbers.push(token);
  }
  const order = ['rate', 'amount', 'grossAmount', 'cgstAmount', 'sgstAmount', 'igstAmount', 'taxableValue'];
  for (const n of numbers) {
    const f = order.find((o) => !item[o]);
    if (!f) break;
    item[f] = n;
  }
  return splitSerialFromHsn(item);
}

/** If the serial was merged with the HSN/SAC in one cell ("1 84713020"), split
 * them. Shared by both parsers. */
function splitSerialFromHsn(item) {
  if (!item.serialNo && item.hsnSac) {
    const m = item.hsnSac.match(/^(\d{1,3})\s+(\d{4,8})$/);
    if (m) {
      item.serialNo = m[1];
      item.hsnSac = m[2];
    }
  }
  return item;
}

/** Expand a merged leading header label ("No. HSN/SAC") into separate columns. */
function expandHeader(row) {
  const cells = [...row];
  const m = cells[0] && cells[0].match(/^(no\.?|s\.?no\.?|sr\.?no\.?|sl\.?no\.?|#)\s+(.+)$/i);
  if (m) cells.splice(0, 1, m[1], m[2]);
  return cells;
}

/**
 * Pattern-based fallback for rows whose cells are merged/misaligned with the
 * header: serial -> name -> [qty] -> rate -> amount -> gross, % cells = tax.
 */
function parseLineItemRow(row) {
  const item = emptyLineItem();
  let cells = [...row];

  // Serial may be "1.", "1" or a prefix of the first cell.
  if (cells.length) {
    const m = cells[0].match(/^(\d{1,3})\s*[.)]\s*(.*)$/) || (/^\d{1,3}$/.test(cells[0]) ? cells[0].match(/^(\d{1,3})/) : null);
    if (m) {
      item.serialNo = m[1];
      cells[0] = (m[2] || '').trim();
      if (!cells[0]) cells.shift();
    }
  }

  const nameParts = [];
  while (cells.length && !isLineItemNumber(cells[0]) && !LINE_ITEM_UNITS.has(cells[0].toLowerCase()) && !/%/.test(cells[0])) {
    nameParts.push(cells.shift());
  }
  if (nameParts.length) {
    const qm = nameParts[nameParts.length - 1].match(/^(.+?)\s+(\d{1,6}[.,]\d{2})\s*$/);
    if (qm) {
      nameParts[nameParts.length - 1] = qm[1];
      item.quantity = qm[2];
    }
  }
  item.itemName = nameParts.join(' ').trim();

  const tokens = [];
  for (const cell of cells) for (const token of cell.split(/\s+/)) if (token) tokens.push(token);
  const merged = [];
  for (let i = 0; i < tokens.length; i++) {
    const cur = tokens[i];
    const next = tokens[i + 1];
    // Space-thousands only occur in comma-decimal locales ("1 394,67");
    // don't merge "5 800.00" (qty + rate) in dot-decimal invoices.
    if (/^\d{1,3}$/.test(cur) && next && /^\d{2,3}[,]\d{1,2}$/.test(next) && !/%/.test(next)) {
      merged.push(`${cur} ${next}`);
      i++;
    } else {
      merged.push(cur);
    }
  }

  const numbers = [];
  for (const token of merged) {
    if (/%/.test(token)) { item.tax = token; continue; }
    if (LINE_ITEM_UNITS.has(token.toLowerCase())) { if (!item.unit) item.unit = token; continue; }
    // 6-8 digit bare integer is likely an HSN/SAC code (GST invoices)
    if (/^\d{6,8}$/.test(token)) { if (!item.hsnSac) item.hsnSac = token; continue; }
    if (isLineItemNumber(token)) numbers.push(token);
  }
  const order = item.quantity ? ['rate', 'amount', 'grossAmount'] : ['quantity', 'rate', 'amount', 'grossAmount'];
  numbers.slice(0, order.length).forEach((n, i) => { item[order[i]] = n; });
  return splitSerialFromHsn(item);
}

/** Rough quality score for choosing the best of several row parses. */
function scoreLineItem(item) {
  let s = 0;
  if (item.itemName) s += 2;
  if (item.quantity && isLineItemNumber(item.quantity)) s += 1;
  if (item.rate) s += 1;
  if (/^\d{4,8}$/.test(item.hsnSac)) s += 1;
  if (item.amount || item.grossAmount || item.taxableValue) s += 1;
  return s;
}


export function extractLineItems(text) {
  const rows = text.split(/\r?\n/)
    .map((line) => line.split('\t').map((c) => c.trim()))
    .filter((row) => row.some(Boolean));

  const isItemsHeader = (row) => row.some((c) => /desc|item|particular|product|service/i.test(c))
    && row.some((c) => /qty|quantity|rate|price|amount|worth|vat|gst|cgst|sgst|igst|hsn|^no/i.test(c));
  const headerIdx = rows.findIndex(isItemsHeader);
  if (headerIdx < 0) return [];

  const headerRaw = rows[headerIdx];
  const headerExpanded = expandHeader(headerRaw);
  const buildColMap = (cells) => {
    const m = {};
    cells.forEach((cell, idx) => {
      const f = classifyLineItemColumn(cell);
      if (f) m[idx] = f;
    });
    return m;
  };
  const colMapExpanded = buildColMap(headerExpanded);
  const colMapRaw = buildColMap(headerRaw);

  const isSummaryRow = (row) =>
    (row.some((c) => /^total$/i.test(clean(c))) && row.some((c) => amount(c)))
    || (row.some((c) => /gross|net\s+worth|taxable|cgst|sgst|igst|grand\s+total/i.test(c))
      && row.some((c) => /vat|tax|gst|cgst|sgst|igst/i.test(c)) && !isItemsHeader(row));
  let end = rows.length;
  for (let i = headerIdx + 1; i < rows.length; i++) {
    if (isSummaryRow(rows[i])) { end = i; break; }
  }

  const canMap = (colMap, row) => Object.keys(colMap).length >= 2 && row.length >= Object.keys(colMap).length - 1;
  const items = [];
  for (let i = headerIdx + 1; i < end; i++) {
    const row = rows[i];
    const isContinuation = row.length === 1 && items.length > 0
      && !/^\d{1,3}\s*[.)]/.test(row[0]) && !isLineItemNumber(row[0]);
    if (isContinuation) {
      items[items.length - 1].itemName = (items[items.length - 1].itemName ? `${items[items.length - 1].itemName} ` : '') + row[0];
      continue;
    }
    const candidates = [];
    if (canMap(colMapExpanded, row)) candidates.push(parseLineItemRowByColumns(row, colMapExpanded));
    if (canMap(colMapRaw, row)) candidates.push(parseLineItemRowByColumns(row, colMapRaw));
    candidates.push(parseLineItemRow(row));
    candidates.sort((a, b) => scoreLineItem(b) - scoreLineItem(a));
    items.push(candidates[0]);
  }
  return items;
}


function classifyLocalColumn(label) {
  const l = clean(label).toLowerCase();
  if (!l) return null;
  if (/^(no\.?|s\.?no\.?|sr\.?no\.?|sl\.?no\.?|#|serial)$/.test(l) || /^no$/.test(l)) return 'serialNo';
  if (/desc|item|particular|product|name|goods|service/.test(l)) return 'itemName';
  if (/hsn|sac|itc\s*code/.test(l)) return 'hsnSac';
  if (/qty|quantity/.test(l)) return 'quantity';
  if (/^(um|uom|unit|uinit|unt)$/.test(l)) return 'unit';
  if (/disc/.test(l)) return 'discount';
  if (/cgst/.test(l)) return 'cgstAmount';
  if (/sgst/.test(l)) return 'sgstAmount';
  if (/igst/.test(l)) return 'igstAmount';
  if (/taxable/.test(l)) return 'taxableValue';
  if (/vat|gst|tax/.test(l) && /%/.test(l)) return 'tax';
  if (/gross|grand\s+total/.test(l)) return 'grossAmount';
  if (/rate|unit\s*price|unit\s*cost|net\s*price|\bprice\b/.test(l)) return 'rate';
  if (/vat|gst|tax/.test(l)) return 'tax';
  if (/amount|worth|value|net/.test(l)) return 'amount';
  return null;
}

/** Strip leading checkbox / currency artifacts (□ ☑ ☐ ₹ $ € £) from a cell. */
const stripGlyphs = (value = '') => String(value).replace(/^[\s\u25a1\u2611\u2610\u20b9$€£]*/, '').trim();

export function parseLocalTableGrid(gridText) {
  const rows = String(gridText).split(/\r?\n/)
    .map((line) => line.split('\t').map((c) => c.trim()))
    .filter((row) => row.some(Boolean));
  const isHeader = (row) => row.some((c) => /desc|item|particular|product|service/i.test(c))
    && row.some((c) => /qty|quantity|rate|price|amount|worth|vat|gst|cgst|sgst|igst|hsn|^no/i.test(c));
  const headerIdx = rows.findIndex(isHeader);
  if (headerIdx < 0) return [];

  const colMap = {};
  rows[headerIdx].forEach((cell, idx) => { const f = classifyLocalColumn(cell); if (f) colMap[idx] = f; });
  const hasHsnColumn = Object.values(colMap).includes('hsnSac');

  const isSummary = (row) =>
    (row.some((c) => /^total$/i.test(clean(c))) && row.some((c) => amount(c)))
    || (row.some((c) => /subtotal|tax\s*summary|grand\s*total|total\s*tax|total\s*gst/i.test(c)) && !isHeader(row));
  let end = rows.length;
  for (let i = headerIdx + 1; i < rows.length; i++) { if (isSummary(rows[i])) { end = i; break; } }

  const idxOf = (field) => Object.keys(colMap).find((k) => colMap[k] === field);
  const serialIdx = idxOf('serialNo');
  const nameIdx = idxOf('itemName');
  const qtyIdx = idxOf('quantity');
  const unitIdx = idxOf('unit');
  const rateIdx = idxOf('rate');
  const amountIdx = idxOf('amount');

  const items = [];
  for (let i = headerIdx + 1; i < end; i++) {
    const cells = rows[i];
    if (!cells.length) continue;
    const item = emptyLineItem();

    // Serial from the first cell.
    let first = cells[0] || '';
    const sm = first.match(/^(\d{1,3})\s*[.)]\s*(.*)$/) || (/^\d{1,3}$/.test(first) ? first.match(/^(\d{1,3})/) : null);
    if (sm) { item.serialNo = sm[1]; if (sm[2]) first = sm[2]; }

    // Item name, with an embedded 4-8 digit HSN/SAC code split out (only when
    // there is no dedicated HSN column).
    let name = '';
    if (nameIdx != null && cells[Number(nameIdx)] && cells[Number(nameIdx)] !== cells[0]) name = cells[Number(nameIdx)];
    else name = sm?.[2] || first;
    name = stripGlyphs(name).replace(/\s+/g, ' ').trim();
    const hm = name.match(/^(.*?)\s+(\d{4,8})\s+(.*)$/);
    if (hm && !hasHsnColumn) { item.itemName = [hm[1], hm[3]].filter(Boolean).join(' ').trim(); item.hsnSac = hm[2]; }
    else item.itemName = name;

    // Unit.
    if (unitIdx != null) { const u = stripGlyphs(cells[Number(unitIdx)]); if (u && !isLineItemNumber(u)) item.unit = u; }

    // Value tokens (exclude serial/name/unit columns).
    const excluded = new Set([0, serialIdx, nameIdx, unitIdx].map((x) => (x == null ? -1 : Number(x))));
    const tokens = [];
    for (let ci = 0; ci < cells.length; ci++) {
      if (excluded.has(ci)) continue;
      for (const t of cells[ci].split(/\s+/)) {
        const cleanT = stripGlyphs(t);
        if (cleanT) tokens.push(cleanT);
      }
    }

    const numbers = [], percents = [];
    for (const t of tokens) {
      if (/%/.test(t)) percents.push(t);
      else if (isLineItemNumber(t)) numbers.push(t);
      else if (!item.unit && LINE_ITEM_UNITS.has(t.toLowerCase())) item.unit = t;
    }
    const toNum = (t) => { const a = amount(t); return a ? Number(a) : null; };

    // Quantity: its column first, else a small integer token.
    if (qtyIdx != null) { const q = stripGlyphs(cells[Number(qtyIdx)]); if (q && isLineItemNumber(q)) item.quantity = amount(q); }
    if (!item.quantity) {
      const q = numbers.find((n) => /^\d{1,3}$/.test(n.replace(/[.,]\d{2}$/, '')) && toNum(n) < 1000);
      if (q) { item.quantity = amount(q); numbers.splice(numbers.indexOf(q), 1); }
    }

    // Rate: its column first, else first number.
    if (rateIdx != null) { const r = stripGlyphs(cells[Number(rateIdx)]); if (r && isLineItemNumber(r)) item.rate = amount(r); }
    if (!item.rate) { const r = numbers.shift(); if (r) item.rate = amount(r); }

    // Amount: its column first, else the dominant (largest) remaining number.
    if (amountIdx != null) { const a = stripGlyphs(cells[Number(amountIdx)]); if (a && isLineItemNumber(a)) item.amount = amount(a); }
    if (!item.amount) {
      const nums = numbers.map(toNum).filter((n) => n != null);
      if (nums.length) {
        const max = Math.max(...nums);
        const t = numbers.find((n) => toNum(n) === max);
        if (t) { item.amount = amount(t); numbers.splice(numbers.indexOf(t), 1); }
      }
    }

    // Percentages: two+ → discount then tax; one → tax.
    if (percents.length >= 2) { item.discount = percents[0].replace(/\s+/g, ''); item.tax = percents[1].replace(/\s+/g, ''); }
    else if (percents.length === 1) item.tax = percents[0].replace(/\s+/g, '');

    if (item.itemName || item.rate || item.amount) items.push(splitSerialFromHsn(item));
  }
  return items;
}

/** Parse a value like "1 394,67" / "10%" into a number (or null). */
function toNumber(value) {
  if (value == null) return null;
  const s = amount(value);
  if (!s) return null;
  const n = Number(s);
  return Number.isFinite(n) ? n : null;
}

/**
 * Compute subtotal / tax / total from the parsed line items, since the summary
 * fields can be recalculated from the table ("amount", qty×rate, CGST/SGST/IGST
 * or tax %, and gross amount). Returns '' for any part it cannot compute.
 */
export function summarizeLineItems(lineItems) {
  const summary = { subtotalAmount: '', taxAmount: '', totalAmount: '' };
  if (!Array.isArray(lineItems) || !lineItems.length) return summary;
  let subtotal = 0, tax = 0, total = 0;
  let hasSub = false, hasTax = false, hasTotal = false;
  for (const it of lineItems) {
    let amt = toNumber(it.amount);
    if (amt == null && it.quantity && it.rate) {
      const q = toNumber(it.quantity), r = toNumber(it.rate);
      if (q != null && r != null) amt = q * r;
    }
    if (amt != null) { subtotal += amt; hasSub = true; }

    const components = [toNumber(it.cgstAmount), toNumber(it.sgstAmount), toNumber(it.igstAmount)].filter((v) => v != null);
    if (components.length) {
      tax += components.reduce((a, b) => a + b, 0);
      hasTax = true;
    } else if (it.tax && amt != null) {
      const pct = toNumber(it.tax);
      if (pct != null) { tax += amt * pct / 100; hasTax = true; }
    }

    const g = toNumber(it.grossAmount);
    if (g != null) { total += g; hasTotal = true; }
    else if (amt != null) { total += amt; hasTotal = true; }
  }
  const fmt = (n) => String(Math.round(n * 100) / 100);
  if (hasSub) summary.subtotalAmount = fmt(subtotal);
  if (hasTax) summary.taxAmount = fmt(tax);
  if (hasTotal) summary.totalAmount = fmt(total);
  return summary;
}

/** Normalize AI-returned line items onto the canonical schema. */
export function normalizeLineItems(lineItems) {
  if (!Array.isArray(lineItems)) return [];
  const aliases = {
    description: 'itemName', item: 'itemName', name: 'itemName', product: 'itemName', particulars: 'itemName',
    itemname: 'itemName', productname: 'itemName', itemdescription: 'itemName', servicename: 'itemName',
    hsn: 'hsnSac', hsnsac: 'hsnSac', sac: 'hsnSac', hsncode: 'hsnSac',
    qty: 'quantity', quantity: 'quantity',
    uom: 'unit', um: 'unit', unit: 'unit',
    unitprice: 'rate', netprice: 'rate', price: 'rate', rate: 'rate',
    discount: 'discount', disc: 'discount',
    taxablevalue: 'taxableValue', taxable: 'taxableValue',
    cgst: 'cgstAmount', cgstamount: 'cgstAmount',
    sgst: 'sgstAmount', sgstamount: 'sgstAmount',
    igst: 'igstAmount', igstamount: 'igstAmount',
    tax: 'tax', gst: 'tax', vat: 'tax', taxrate: 'tax',
    amount: 'amount', netamount: 'amount', netvalue: 'amount', lineamount: 'amount',
    gross: 'grossAmount', grossamount: 'grossAmount', total: 'grossAmount',
    serialno: 'serialNo', sno: 'serialNo', no: 'serialNo', '#': 'serialNo',
  };
  return lineItems.map((raw) => {
    const item = emptyLineItem();
    for (const [k, v] of Object.entries(raw || {})) {
      const key = String(k).toLowerCase().replace(/[\s_-]/g, '');
      const canonical = aliases[key];
      if (canonical && v !== undefined && v !== null) item[canonical] = String(v);
    }
    return item;
  });
}

export function extractByRules(text, customDict = {}) {
  const dict = { ...CORE_FIELD_DICTIONARY, ...customDict };
  // Keep tab separators (column markers) — trimming would erase table layout.
  const lines = text.split(/\r?\n/).filter((l) => l.trim());
  const data = emptyOrder();

  // Currency detection
  if (/₹|\bINR\b/i.test(text)) data.currency = 'INR';
  else if (/\bRs\.?\b/i.test(text)) data.currency = 'INR';
  else if (/\$|\bUSD\b/i.test(text)) data.currency = 'USD';
  else if (/€|\bEUR\b/i.test(text)) data.currency = 'EUR';
  else if (/£|\bGBP\b/i.test(text)) data.currency = 'GBP';

  // Document Type detection
  const lowerText = text.toLowerCase();
  for (const [type, labels] of Object.entries(dict.documentType || {})) {
    if (labels.some((l) => lowerText.includes(l.toLowerCase()))) {
      data.documentType = type;
      break;
    }
  }

  // Build a set of every known label so a "value" that is really another
  // field's label (e.g. "Seller:" then "Client:") is skipped when pairing.
  const knownLabels = new Set();
  for (const [type, labels] of Object.entries(dict.documentType || {})) knownLabels.add(type);
  for (const list of Object.values(dict)) {
    for (const label of Array.isArray(list) ? list : []) knownLabels.add(clean(label).toLowerCase());
  }

  function toValue(raw, isAmt, isDate) {
    if (isAmt) {
      // Skip values that look like a full date (dd/mm/yyyy etc.), but keep
      // 2-decimal amounts ("5201.00") and Indian formats ("1,234.56").
      // The old check (two groups) wrongly rejected amounts like "5201.00"
      // as dates, so labelled totals ("Subtotal: 5201.00") were discarded.
      if (/^[0-9]{1,4}[\/.-][0-9]{1,2}[\/.-][0-9]{2,4}/.test(raw)) return '';
      return amount(raw);
    }
    if (isDate) {
      const dm = raw.match(/([0-9]{1,4}[\/.-][0-9A-Za-z]{1,9}[\/.-][0-9]{2,4}|[0-9]{1,2}\s+[A-Za-z]{3,9}\s+[0-9]{2,4})/);
      return dm ? dm[1] : '';
    }
    const val = raw.replace(/^[|:#\-\u2013\u2014.]\s*/, '');
    return (val && !/order form|purchase order|sales order/i.test(val)) ? val : '';
  }

  function isLabel(cell) {
    const c = clean(cell).toLowerCase();
    return Boolean(c) && knownLabels.has(c);
  }

  function extractField(field, aliases, isAmt = false, isDate = false) {
    if (!aliases || !aliases.length) return '';
    const sorted = [...aliases].sort((a, b) => b.length - a.length);

    for (const alias of sorted) {
      const aliasEsc = escapeRegex(alias);

      for (let i = 0; i < lines.length; i++) {
        const line = lines[i];
        const cells = line.split('\t').map((c) => c.trim());

        // Guard against total matching 'sub total' or tax matching 'amount before tax'
        if (field === 'totalAmount' && /\bsub\b/i.test(line)) continue;
        if (field === 'taxAmount' && /\bbefore\s+tax\b/i.test(line)) continue;
        // 'Tax Id:', 'VAT No:', 'GSTIN:' are identifiers, not the tax amount
        if (field === 'taxAmount' && /\b(?:tax|vat|gst)\s+(?:id|no\.?|number|n\.?|#|in|payer)\b/i.test(line)) continue;

        // Pattern A: a cell contains the label + separator + value
        for (const cell of cells) {
          // Aliases ending in a non-word char ('po#', 'invoice #') have no
          // trailing word boundary.
          const endB = /\w$/.test(alias) ? '\\b' : '';
          const match = cell.match(new RegExp('^[^a-zA-Z0-9]*\\b' + aliasEsc + endB + '[\\s:#\\-\\u2013\\u2014|.]*(.+)$', 'i'));
          if (!match || !match[1]) continue;
          const val = toValue(clean(match[1]), isAmt, isDate);
          if (val) return val;
        }

        // Pattern B: a cell is exactly the label -> value is a neighbour cell
        const labelCellIdx = cells.findIndex((c) => clean(c).toLowerCase() === alias.toLowerCase());
        if (labelCellIdx !== -1) {
          // B1: next non-empty, non-label cell in the same row.
          // For totals prefer the right-most amount (grand total) when the row
          // carries several amounts (e.g. "Total | net | vat | gross").
          const scanOrder = field === 'totalAmount'
            ? [...cells.keys()].reverse().filter((k) => k > labelCellIdx)
            : [...Array(cells.length - labelCellIdx - 1).keys()].map((k) => labelCellIdx + 1 + k);
          for (const k of scanOrder) {
            if (!cells[k]) continue;
            if (isLabel(cells[k])) {
              if (field !== 'totalAmount') break;
              continue;
            }
            const val = toValue(clean(cells[k]), isAmt, isDate);
            if (val) return val;
          }
          // B2: value below in the same column (side-by-side header blocks).
          for (let j = i + 1; j < lines.length; j++) {
            const nextCells = lines[j].split('\t').map((c) => c.trim());
            const cell = nextCells[labelCellIdx] || '';
            if (!cell || isLabel(cell)) continue;
            const val = toValue(clean(cell), isAmt, isDate);
            if (val) return val;
          }
        }
      }
    }
    return '';
  }

  data.documentNumber = extractField('documentNumber', dict.documentNumber);
  data.documentDate = extractField('documentDate', dict.documentDate, false, true);
  data.vendorName = extractField('vendorName', dict.vendorName);
  data.customerName = extractField('customerName', dict.customerName);
  data.subtotalAmount = extractField('subtotalAmount', dict.subtotalAmount, true);
  data.taxAmount = extractField('taxAmount', dict.taxAmount, true);
  data.totalAmount = extractField('totalAmount', dict.totalAmount, true);

  // GST-style labelled summary: "Taxable Value" -> subtotal, and the total tax
  // is the sum of the CGST + SGST (or IGST) lines. Only fills when the labelled
  // fields above were not captured, so other layouts keep priority.
  if (!data.subtotalAmount) {
    const tv = text.match(/taxable\s+(?:value|amount)\s*[:\s]*([\d][\d,.\s]*)/i);
    if (tv) data.subtotalAmount = amount(tv[1]);
  }
  if (!data.taxAmount) {
    // Sum the CGST + SGST (or IGST) summary lines. Skip header cells like
    // "CGST 9%" (they contain % and are rates, not amounts).
    const gstAmount = (label) => {
      for (const line of lines) {
        if (new RegExp(`\\b${label}\\b`, 'i').test(line)) {
          if (/%/.test(line)) continue;
          const m = line.match(/([\d][\d,.\s]*)/);
          if (m) return amount(m[1]);
        }
      }
      return '';
    };
    const taxParts = [gstAmount('cgst'), gstAmount('sgst'), gstAmount('igst')].filter(Boolean);
    if (taxParts.length) {
      const sum = taxParts.reduce((acc, v) => acc + (Number(v) || 0), 0);
      data.taxAmount = String(Math.round(sum * 100) / 100);
    }
  }

  // Table-aware pass: when the document has a SUMMARY grid, fill subtotal / tax
  // / total from their column headers (e.g. Net worth | VAT | Gross worth).
  extractSummaryTable(text, data);

  // Line items: parse the ITEMS table into structured rows for the UI.
  data.lineItems = extractLineItems(text);

  // Recalculate subtotal / tax / total from the parsed rows when the document
  // didn't expose them as labelled fields (cost/tax/total can be derived from
  // the table: amount, qty×rate, CGST/SGST/IGST or tax %, gross amount).
  const computed = summarizeLineItems(data.lineItems);
  for (const [k, v] of Object.entries(computed)) {
    if (v && !data[k]) data[k] = v;
  }

  // Fallback for document date if completely unanchored
  if (!data.documentDate) {
    const fallbackDate = text.match(/\b((?:19|20)\d{2}[\/.-]\d{2}[\/.-]\d{2}|\d{2}[\/.-]\d{2}[\/.-](?:19|20)?\d{2}|\d{1,2}\s+(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[a-z]*\s+(?:19|20)?\d{2})\b/i);
    if (fallbackDate) data.documentDate = fallbackDate[1];
  }

  // Fallback for vendor name if still empty
  if (!data.vendorName && lines.length) {
    data.vendorName = lines.find((line) => !/order|date|invoice|page|summary|details/i.test(line) && /[A-Za-z]{3}/.test(line)) || '';
  }

  return data;
}

function extractRule(text, rule, fieldName = '') {
  try {
    const anchorMatch = rule.anchor && text.match(new RegExp(`\\b${escapeRegex(rule.anchor)}\\b`, 'i'));
    const scope = anchorMatch?.index >= 0 ? text.slice(anchorMatch.index) : text;
    const result = scope.match(new RegExp(rule.regex, 'i'));
    const val = clean(result?.[1] || '');
    return /Amount$/.test(fieldName || rule.anchor || '') ? amount(val) : val;
  } catch { return ''; }
}

export function applyTemplate(text, template) {
  const data = extractByRules(text);
  const mappings = Object.entries(template.formMapping || {});
  const fields = mappings.length ? mappings.map(([formField, mapping]) => [formField, template.fieldRules?.[mapping.sourceField]]) : Object.entries(template.fieldRules || {});
  for (const [field, rule] of fields) {
    if (!rule?.regex) continue;
    // Templates store regexes learned from single-column AI text. The
    // rule-based pass is now tab/cell-aware, so a stale template can corrupt
    // correct values (e.g. capture the other column or the wrong occurrence).
    // Let the template only fill fields the rules left empty.
    if (data[field]) continue;
    const extracted = extractRule(text, rule, field);
    if (extracted) data[field] = extracted;
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
  const docIsSales = /sales\s+order|\bS\.?O\.?\b|so details/i.test(normalized);
  const docIsPurchase = /purchase\s+order|\bP\.?O\.?\b|p\.o\. details/i.test(normalized);

  let best = null;
  for (const template of templates) {
    const anchors = [...new Set(template.fingerprint?.anchors || [])];
    if (!anchors.length) continue;

    // Check document type compatibility if anchors specify one
    const templateIsSales = anchors.some((a) => /sales\s*order|so\s*details/i.test(a));
    const templateIsPurchase = anchors.some((a) => /purchase\s*order|p\.o\.\s*details/i.test(a));
    if (templateIsSales && !docIsSales) continue;
    if (templateIsPurchase && !docIsPurchase) continue;

    const matches = anchors.filter((anchor) => normalized.includes(anchor.toLowerCase())).length;
    const score = matches / anchors.length;
    if (!best || score > best.score) best = { template, score };
  }
  return best?.score >= 0.65 ? best : null;
}

export function confidence(data) {
  return Number((requiredFields.filter((key) => Boolean(data[key])).length / requiredFields.length).toFixed(2));
}

export function buildTemplateProposal(text, data, name = '') {
  const lines = text.split(/\r?\n/).map(clean).filter(Boolean);
  const fieldRules = {};
  const anchors = new Set();

  if (data.documentType === 'purchase_order') anchors.add('purchase order');
  if (data.documentType === 'sales_order') anchors.add('sales order');
  if (data.vendorName) anchors.add(data.vendorName.toLowerCase());

  // Search each line in text for the value of each field
  for (const [field, val] of Object.entries(data)) {
    if (!val || field === 'lineItems' || field === 'currency' || field === 'documentType') continue;

    for (let i = 0; i < lines.length; i++) {
      const line = lines[i];
      const isAmt = /Amount$/.test(field);
      const isMatch = isAmt 
        ? line.replace(/[^0-9]/g, '').includes(String(val).replace(/[^0-9]/g, ''))
        : line.toLowerCase().includes(String(val).toLowerCase());

      if (isMatch) {
        let labelPart = '';
        if (isAmt) {
          labelPart = line.replace(/(?:₹|\$|€|£|INR|USD|EUR|GBP|Rs\.?|\d)[\s\S]*/i, '').trim();
        } else {
          const idx = line.toLowerCase().indexOf(String(val).toLowerCase());
          labelPart = line.slice(0, idx).trim();
        }
        labelPart = labelPart.replace(/[:#\-\u2013\u2014|.]+$/, '').trim();

        if (labelPart.length < 2 && i > 0) {
          const prevLine = lines[i - 1].replace(/[:#\-\u2013\u2014|.]+$/, '').trim();
          if (prevLine.length >= 2 && !/\d/.test(prevLine)) {
            labelPart = prevLine;
          }
        }

        if (labelPart.length >= 2) {
          anchors.add(labelPart.toLowerCase());
          const esc = escapeRegex(labelPart);
          const valuePattern = isAmt 
            ? '(?:[₹$€£A-Z]{0,4}\\s*)?([\\d,.]+)'
            : field === 'documentDate'
            ? '([0-9A-Za-z./-]{6,20}|[0-9]{1,2}\\s+[A-Za-z]{3,9}\\s+[0-9]{2,4})'
            : '([^\\n\\r]{1,120})';

          fieldRules[field] = {
            anchor: labelPart.toLowerCase(),
            regex: esc + '\\s*[:#\\-\\u2013\\u2014|.]*\\s*' + valuePattern
          };
          break;
        }
      }
    }
  }

  // Fallback anchors
  const fallbackAnchors = lines.filter((line) => line.length >= 3 && line.length <= 60 && !/\d/.test(line)).slice(0, 5);
  for (const fa of fallbackAnchors) anchors.add(fa.toLowerCase());

  return {
    name: name || data.vendorName || 'Document template',
    fingerprint: { anchors: [...anchors].slice(0, 10) },
    fieldRules
  };
}

export async function runLocalExtractor(mode, filePath) {
  const virtualenvPython = path.join(process.cwd(), '.venv', 'bin', 'python');
  const python = process.env.PYTHON_BIN || (existsSync(virtualenvPython) ? virtualenvPython : 'python3');
  const { stdout } = await execFileAsync(python, ['server/pdf_worker.py', mode, filePath], { timeout: 90_000, maxBuffer: 8_000_000 });
  const result = JSON.parse(stdout);
  if (!result.ok) throw new Error(result.error);
  return result;
}

export async function runImageExtractor(filePath) {
  return runLocalExtractor('image_ocr', filePath);
}

/** Local table OCR (RapidOCR PP-Structure) — an offline, free alternative to
 * the vision-AI table pass. Returns { text, pages } where text is the table
 * serialized as a tab-separated grid. */
export async function runLocalTableExtractor(filePath) {
  return runLocalExtractor('table', filePath);
}

const aiInstruction = `Return JSON only with {"data":{"documentType":"","documentNumber":"","documentDate":"","vendorName":"","customerName":"","currency":"","subtotalAmount":"","taxAmount":"","totalAmount":"","lineItems":[{"serialNo":"","itemName":"","hsnSac":"","quantity":"","unit":"","rate":"","discount":"","taxableValue":"","cgstAmount":"","sgstAmount":"","igstAmount":"","tax":"","amount":"","grossAmount":""}]},"template":{"name":"","fingerprint":{"anchors":["stable label"]},"fieldRules":{"fieldName":{"anchor":"label","regex":"capturing regex"}}}}. Read the attached PDF/image visually when supplied and use the recovered text as supporting context. Never invent values. IMPORTANT: extract EVERY line item from the items table without omitting or merging any — description, quantity, unit, rate/unit price, discount, HSN/SAC, taxable value, CGST/SGST/IGST amounts, tax % and the line's net amount and gross amount. Line items are the priority. Regex must have one capture group.`;
function parseAi(value) { const json = value.match(/\{[\s\S]*\}/)?.[0]; if (!json) throw new Error('AI did not return JSON.'); const parsed = JSON.parse(json); return { data: { ...emptyOrder(), ...(parsed.data || {}), lineItems: normalizeLineItems(parsed.data?.lineItems) }, template: parsed.template }; }
function aiPrompt(text) { return `${aiInstruction}\nRECOVERED TEXT:\n${text.slice(0, 80_000)}`; }

export async function extractWithClaude(text, documentBuffer, mimeType = 'application/pdf') {
  if (!process.env.ANTHROPIC_API_KEY) throw new Error('ANTHROPIC_API_KEY is not configured.');
  const content = [];
  if (documentBuffer) {
    const isImage = mimeType.startsWith('image/');
    if (isImage) {
      content.push({ type: 'image', source: { type: 'base64', media_type: mimeType, data: documentBuffer.toString('base64') } });
    } else {
      content.push({ type: 'document', source: { type: 'base64', media_type: 'application/pdf', data: documentBuffer.toString('base64') } });
    }
  }
  content.push({ type: 'text', text: aiPrompt(text) });
  const response = await fetch('https://api.anthropic.com/v1/messages', { method: 'POST', headers: { 'content-type': 'application/json', 'x-api-key': process.env.ANTHROPIC_API_KEY, 'anthropic-version': '2023-06-01' }, body: JSON.stringify({ model: process.env.ANTHROPIC_MODEL || 'claude-haiku-4-5', max_tokens: 1800, messages: [{ role: 'user', content }] }) });
  if (!response.ok) throw new Error(`Claude request failed (${response.status})`);
  return parseAi((await response.json()).content?.[0]?.text || '');
}

export async function extractWithGemini(text, documentBuffer, mimeType = 'application/pdf') {
  if (!process.env.GEMINI_API_KEY) throw new Error('GEMINI_API_KEY is not configured.');
  const parts = [{ text: aiPrompt(text) }];
  if (documentBuffer) {
    parts.push({ inlineData: { mimeType, data: documentBuffer.toString('base64') } });
  }
  const model = process.env.GEMINI_MODEL || 'gemini-3.1-pro-preview';
  const response = await fetch(`https://generativelanguage.googleapis.com/v1beta/models/${encodeURIComponent(model)}:generateContent`, { method: 'POST', headers: { 'content-type': 'application/json', 'x-goog-api-key': process.env.GEMINI_API_KEY }, body: JSON.stringify({ contents: [{ role: 'user', parts }], generationConfig: { responseMimeType: 'application/json', maxOutputTokens: 1800 } }) });
  if (!response.ok) {
    const detail = (await response.text()).replace(/\s+/g, ' ').slice(0, 300);
    throw new Error(`Gemini request failed (${response.status})${detail ? `: ${detail}` : ''}`);
  }
  const body = await response.json();
  return parseAi(body.candidates?.[0]?.content?.parts?.map((part) => part.text || '').join('') || '');
}


export function lineItemQuality(lineItems) {
  if (!Array.isArray(lineItems) || !lineItems.length) return 0;
  let coherent = 0, total = 0;
  for (const it of lineItems) {
    if (!it.itemName) continue;
    total += 1;
    const q = toNumber(it.quantity), r = toNumber(it.rate), a = toNumber(it.amount);
    if (q != null && r != null && a != null && a > 0) {
      if (Math.abs(q * r - a) / a <= 0.3) coherent += 1;
    } else if (a != null || it.grossAmount || it.taxableValue) {
      coherent += 1;
    }
  }
  return total ? coherent / total : 0;
}

const tableAiInstruction = `Return JSON only with {"lineItems":[{"serialNo":"","itemName":"","hsnSac":"","quantity":"","unit":"","rate":"","discount":"","taxableValue":"","cgstAmount":"","sgstAmount":"","igstAmount":"","tax":"","amount":"","grossAmount":""}]}. Read the attached PDF/image visually and extract EVERY row of the items/line-items table exactly as shown. Each row is one product/service: serial no, description, HSN/SAC, quantity, unit, rate, discount, tax %, taxable value, CGST/SGST/IGST amounts, and the line's net amount and gross amount. Do NOT omit, merge, or invent rows; do NOT include column headers, summary rows, or totals as items. If the table is empty or missing, return {"lineItems":[]}.`;

function parseTableAi(value) {
  const json = value.match(/\{[\s\S]*\}/)?.[0];
  if (!json) throw new Error('AI did not return JSON.');
  const parsed = JSON.parse(json);
  return normalizeLineItems(parsed.lineItems || parsed.data?.lineItems || []);
}

function tableAiBody(text, documentBuffer, mimeType) {
  const content = [];
  if (documentBuffer) {
    const isImage = mimeType.startsWith('image/');
    content.push(isImage
      ? { type: 'image', source: { type: 'base64', media_type: mimeType, data: documentBuffer.toString('base64') } }
      : { type: 'document', source: { type: 'base64', media_type: 'application/pdf', data: documentBuffer.toString('base64') } });
  }
  content.push({ type: 'text', text: `${tableAiInstruction}\nRECOVERED TEXT (supporting context only):\n${text.slice(0, 40000)}` });
  return content;
}

export async function extractTableWithClaude(text, documentBuffer, mimeType = 'application/pdf') {
  if (!process.env.ANTHROPIC_API_KEY) throw new Error('ANTHROPIC_API_KEY is not configured.');
  const response = await fetch('https://api.anthropic.com/v1/messages', { method: 'POST', headers: { 'content-type': 'application/json', 'x-api-key': process.env.ANTHROPIC_API_KEY, 'anthropic-version': '2023-06-01' }, body: JSON.stringify({ model: process.env.ANTHROPIC_MODEL || 'claude-haiku-4-5', max_tokens: 1800, messages: [{ role: 'user', content: tableAiBody(text, documentBuffer, mimeType) }] }) });
  if (!response.ok) throw new Error(`Claude table request failed (${response.status})`);
  return parseTableAi((await response.json()).content?.[0]?.text || '');
}

export async function extractTableWithGemini(text, documentBuffer, mimeType = 'application/pdf') {
  if (!process.env.GEMINI_API_KEY) throw new Error('GEMINI_API_KEY is not configured.');
  const parts = [{ text: `${tableAiInstruction}\nRECOVERED TEXT (supporting context only):\n${text.slice(0, 40000)}` }];
  if (documentBuffer) parts.push({ inlineData: { mimeType, data: documentBuffer.toString('base64') } });
  const model = process.env.GEMINI_MODEL || 'gemini-3.1-pro-preview';
  const response = await fetch(`https://generativelanguage.googleapis.com/v1beta/models/${encodeURIComponent(model)}:generateContent`, { method: 'POST', headers: { 'content-type': 'application/json', 'x-goog-api-key': process.env.GEMINI_API_KEY }, body: JSON.stringify({ contents: [{ role: 'user', parts }], generationConfig: { responseMimeType: 'application/json', maxOutputTokens: 1800 } }) });
  if (!response.ok) throw new Error(`Gemini table request failed (${response.status})`);
  const body = await response.json();
  return parseTableAi(body.candidates?.[0]?.content?.parts?.map((part) => part.text || '').join('') || '');
}
