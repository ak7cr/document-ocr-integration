

import { deleteDictionaryEntry, getDictionaryEntries, learnDictionary } from './database.js';

// Fields we actively track and normalize via the dictionary. 
export const TRACKED_FIELDS = new Set(['vendorName', 'customerName', 'currency', 'documentType']);

// Minimum Jaro-Winkler similarity (0–1) to consider a match
const MATCH_THRESHOLD = 0.80;


function jaro(s1, s2) {
  if (s1 === s2) return 1;
  const len1 = s1.length, len2 = s2.length;
  if (len1 === 0 || len2 === 0) return 0;
  const matchDist = Math.floor(Math.max(len1, len2) / 2) - 1;
  const s1Matches = new Array(len1).fill(false);
  const s2Matches = new Array(len2).fill(false);
  let matches = 0, transpositions = 0;
  for (let i = 0; i < len1; i++) {
    const start = Math.max(0, i - matchDist);
    const end   = Math.min(i + matchDist + 1, len2);
    for (let j = start; j < end; j++) {
      if (s2Matches[j] || s1[i] !== s2[j]) continue;
      s1Matches[i] = true; s2Matches[j] = true; matches++; break;
    }
  }
  if (matches === 0) return 0;
  let k = 0;
  for (let i = 0; i < len1; i++) {
    if (!s1Matches[i]) continue;
    while (!s2Matches[k]) k++;
    if (s1[i] !== s2[k]) transpositions++;
    k++;
  }
  return (matches / len1 + matches / len2 + (matches - transpositions / 2) / matches) / 3;
}

function jaroWinkler(s1, s2, p = 0.1) {
  const j = jaro(s1, s2);
  let prefix = 0;
  for (let i = 0; i < Math.min(4, s1.length, s2.length); i++) {
    if (s1[i] === s2[i]) prefix++; else break;
  }
  return j + prefix * p * (1 - j);
}

function normalize(str = '') { return str.toLowerCase().replace(/[^a-z0-9 ]/g, '').replace(/\s+/g, ' ').trim(); }



export async function lookupField(fieldName, rawValue) {
  if (!rawValue || !TRACKED_FIELDS.has(fieldName)) {
    return { value: rawValue, matched: false, canonical: null, score: 0 };
  }
  const entries = await getDictionaryEntries(fieldName);
  if (!entries.length) return { value: rawValue, matched: false, canonical: null, score: 0 };

  const normRaw = normalize(rawValue);
  let best = null;

  for (const entry of entries) {
    const candidates = [entry.canonical, ...(entry.aliases || [])];
    for (const candidate of candidates) {
      const score = jaroWinkler(normRaw, normalize(candidate));
      if (!best || score > best.score) best = { entry, score };
    }
  }

  if (best && best.score >= MATCH_THRESHOLD) {
    return { value: best.entry.canonical, matched: true, canonical: best.entry.canonical, score: best.score };
  }
  return { value: rawValue, matched: false, canonical: null, score: best?.score ?? 0 };
}


export async function applyDictionary(data) {
  const dictionaryHits = {};
  const corrected = { ...data };
  for (const field of TRACKED_FIELDS) {
    if (!corrected[field]) continue;
    const result = await lookupField(field, corrected[field]);
    if (result.matched) {
      corrected[field] = result.value;
      dictionaryHits[field] = { canonical: result.canonical, score: result.score };
    }
  }
  return { data: corrected, dictionaryHits };
}


export async function learnField(fieldName, value) {
  if (!value || !TRACKED_FIELDS.has(fieldName)) return;
  const trimmed = value.trim();
  if (!trimmed) return;
  await learnDictionary(fieldName, trimmed, normalize(trimmed), MATCH_THRESHOLD, jaroWinkler, normalize);
}


export async function learnFromData(data) {
  await Promise.allSettled(
    [...TRACKED_FIELDS].map((field) => learnField(field, data[field]))
  );
}

/**
 * List all dictionary entries, optionally filtered by field name.
 */
export async function listDictionary(fieldName) {
  return getDictionaryEntries(fieldName || null);
}

/**
 * Delete a dictionary entry by ID.
 */
export async function removeDictionaryEntry(id) {
  return deleteDictionaryEntry(id);
}
