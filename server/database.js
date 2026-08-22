import crypto from 'node:crypto';
import pg from 'pg';

let pool;
function getPool() {
  if (!process.env.DATABASE_URL) return null;
  pool ??= new pg.Pool({ connectionString: process.env.DATABASE_URL });
  return pool;
}

export async function getActiveTemplates() {
  const db = getPool();
  if (!db) return [];
  const { rows } = await db.query('SELECT id, name, fingerprint, field_rules AS "fieldRules", form_mapping AS "formMapping" FROM document_templates WHERE status = $1', ['active']);
  return rows;
}

export async function saveRun(run) {
  const db = getPool();
  if (!db) return false;
  await db.query(
    `INSERT INTO extraction_runs (id, original_filename, mime_type, document_type, extraction_source, template_id, confidence, extracted_data, attempts, source_text)
     VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10)`,
    [run.id, run.fileName, run.mimeType, run.data.documentType, run.source, run.templateId || null, run.confidence, JSON.stringify(run.data), JSON.stringify(run.attempts), run.text],
  );
  return true;
}

export async function saveTemplate({ id, name, fingerprint, fieldRules, formMapping, runId }) {
  const db = getPool();
  if (!db) throw new Error('DATABASE_URL is not configured. Run db/schema.sql and configure Postgres first.');
  await db.query(
    `INSERT INTO document_templates (id, name, fingerprint, field_rules, form_mapping)
     VALUES ($1,$2,$3,$4,$5)`,
    [id, name, JSON.stringify(fingerprint), JSON.stringify(fieldRules), JSON.stringify(formMapping || {})],
  );
  if (runId) await db.query('UPDATE extraction_runs SET template_id = $1 WHERE id = $2', [id, runId]);
}

export async function markTemplateMatched(id) {
  const db = getPool();
  if (db && id) await db.query('UPDATE document_templates SET times_matched = times_matched + 1, updated_at = NOW() WHERE id = $1', [id]);
}

// ─── Dictionary ──────────────────────────────────────────────────────────────

/**
 * Fetch all dictionary entries, optionally filtered by field_name.
 */
export async function getDictionaryEntries(fieldName) {
  const db = getPool();
  if (!db) return [];
  if (fieldName) {
    const { rows } = await db.query(
      'SELECT id, field_name AS "fieldName", canonical, aliases, hit_count AS "hitCount", created_at AS "createdAt" FROM field_dictionary WHERE field_name = $1 ORDER BY hit_count DESC',
      [fieldName],
    );
    return rows;
  }
  const { rows } = await db.query(
    'SELECT id, field_name AS "fieldName", canonical, aliases, hit_count AS "hitCount", created_at AS "createdAt" FROM field_dictionary ORDER BY field_name, hit_count DESC',
  );
  return rows;
}

/**
 * Upsert a learned value into the dictionary.
 * jaroWinkler and normalize are injected from dictionary.js to avoid circular deps.
 */
export async function learnDictionary(fieldName, rawValue, normRaw, threshold, jaroWinkler, normalize) {
  const db = getPool();
  if (!db) return; // gracefully skip when no DB is configured

  // 1. Exact canonical match (case-insensitive) → just increment hit_count
  const exact = await db.query(
    'SELECT id, aliases FROM field_dictionary WHERE field_name = $1 AND lower(canonical) = lower($2)',
    [fieldName, rawValue],
  );
  if (exact.rows.length) {
    await db.query(
      'UPDATE field_dictionary SET hit_count = hit_count + 1, updated_at = NOW() WHERE id = $1',
      [exact.rows[0].id],
    );
    return;
  }

  // 2. Fuzzy match against existing canonicals / aliases → add as alias
  const all = await db.query(
    'SELECT id, canonical, aliases FROM field_dictionary WHERE field_name = $1',
    [fieldName],
  );
  let bestMatch = null;
  for (const row of all.rows) {
    const candidates = [row.canonical, ...(row.aliases || [])];
    for (const candidate of candidates) {
      const score = jaroWinkler(normRaw, normalize(candidate));
      if (score >= threshold && (!bestMatch || score > bestMatch.score)) {
        bestMatch = { id: row.id, aliases: row.aliases || [], score };
      }
    }
  }
  if (bestMatch) {
    // Avoid duplicate aliases
    if (!bestMatch.aliases.some((a) => a.toLowerCase() === rawValue.toLowerCase())) {
      bestMatch.aliases.push(rawValue);
      await db.query(
        'UPDATE field_dictionary SET aliases = $1, hit_count = hit_count + 1, updated_at = NOW() WHERE id = $2',
        [JSON.stringify(bestMatch.aliases), bestMatch.id],
      );
    } else {
      await db.query(
        'UPDATE field_dictionary SET hit_count = hit_count + 1, updated_at = NOW() WHERE id = $1',
        [bestMatch.id],
      );
    }
    return;
  }

  // 3. Completely new value → insert as a new canonical entry
  await db.query(
    'INSERT INTO field_dictionary (id, field_name, canonical, aliases, hit_count) VALUES ($1,$2,$3,$4,1)',
    [crypto.randomUUID(), fieldName, rawValue, '[]'],
  );
}

/**
 * Delete a dictionary entry by ID.
 */
export async function deleteDictionaryEntry(id) {
  const db = getPool();
  if (!db) throw new Error('DATABASE_URL is not configured.');
  await db.query('DELETE FROM field_dictionary WHERE id = $1', [id]);
}

/**
 * Save user-corrected data back onto an extraction run and return the updated row.
 */
export async function saveCorrections(runId, correctedData) {
  const db = getPool();
  if (!db) return false;
  await db.query(
    'UPDATE extraction_runs SET corrected_data = $1, corrected_at = NOW() WHERE id = $2',
    [JSON.stringify(correctedData), runId],
  );
  return true;
}

