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
  const { rows } = await db.query('SELECT id, name, fingerprint, field_rules AS "fieldRules", form_mapping AS "formMapping", status, times_matched AS "timesMatched", success_count AS "successCount", failure_count AS "failureCount", failure_rate AS "failureRate", avg_confidence AS "avgConfidence" FROM document_templates WHERE status IN ($1, $2)', ['active', 'draft']);
  return rows;
}

export async function getRunById(id) {
  const db = getPool();
  if (!db) return null;
  const { rows } = await db.query(
    'SELECT id, original_filename AS "originalFilename", mime_type AS "mimeType", document_type AS "documentType", extraction_source AS "extractionSource", template_id AS "templateId", confidence, extracted_data AS "extractedData", source_text AS "sourceText" FROM extraction_runs WHERE id = $1',
    [id],
  );
  return rows[0] || null;
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

export async function saveTemplate({ id, name, fingerprint, fieldRules, formMapping, runId, status = 'active' }) {
  const db = getPool();
  if (!db) throw new Error('DATABASE_URL is not configured. Run db/schema.sql and configure Postgres first.');
  const templateId = id || crypto.randomUUID();
  await db.query(
    `INSERT INTO document_templates (id, name, fingerprint, field_rules, form_mapping, status)
     VALUES ($1,$2,$3,$4,$5,$6)
     ON CONFLICT (id) DO UPDATE SET name = $2, fingerprint = $3, field_rules = $4, form_mapping = $5, status = $6, updated_at = NOW()`,
    [templateId, name, JSON.stringify(fingerprint), JSON.stringify(fieldRules), JSON.stringify(formMapping || {}), status],
  );
  if (runId) await db.query('UPDATE extraction_runs SET template_id = $1 WHERE id = $2', [templateId, runId]);
  return templateId;
}

/** Record a template match outcome and drive the lifecycle:
 * DRAFT -> ACTIVE after 2 successes; ACTIVE -> QUARANTINED when the failure
 * rate reaches >= 0.25 after >= 5 runs. Also updates reliability stats. */
export async function recordTemplateOutcome(id, { success, confidence }) {
  const db = getPool();
  if (!db || !id) return;
  const conf = Number.isFinite(confidence) ? confidence : 0;
  const { rows } = await db.query(
    'SELECT status, times_matched, success_count, failure_count, avg_confidence FROM document_templates WHERE id = $1',
    [id],
  );
  if (!rows.length) return;
  const t = rows[0];
  const times = (t.times_matched || 0) + 1;
  const successes = (t.success_count || 0) + (success ? 1 : 0);
  const failures = (t.failure_count || 0) + (success ? 0 : 1);
  const failureRate = failures / times;
  const avgConf = t.avg_confidence == null ? conf : 0.8 * t.avg_confidence + 0.2 * conf;

  let status = t.status;
  if (status === 'draft' && successes >= 2) status = 'active';
  else if (status === 'active' && times >= 5 && failureRate >= 0.25) status = 'quarantined';

  await db.query(
    `UPDATE document_templates SET
       times_matched = $2, success_count = $3, failure_count = $4, failure_rate = $5,
       avg_confidence = $6, status = $7, updated_at = NOW()
     WHERE id = $1`,
    [id, times, successes, failures, failureRate, avgConf, status],
  );
}

//  Dictionary


// Fetch all dictionary entries, optionally filtered by field_name.
 
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


export async function learnDictionary(fieldName, rawValue, normRaw, threshold, jaroWinkler, normalize) {
  const db = getPool();
  if (!db) return;

  // exact canonical match (case-insensitive) -> increment hit_count
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

  // fuzzy match against existing canonicals / aliases -> add as alias
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

  //  completely new value -> insert as new canonical entry
  await db.query(
    'INSERT INTO field_dictionary (id, field_name, canonical, aliases, hit_count) VALUES ($1,$2,$3,$4,1)',
    [crypto.randomUUID(), fieldName, rawValue, '[]'],
  );
}


 // delete a dictionary entry by ID

export async function deleteDictionaryEntry(id) {
  const db = getPool();
  if (!db) throw new Error('DATABASE_URL is not configured.');
  await db.query('DELETE FROM field_dictionary WHERE id = $1', [id]);
}


// save user-corrected data back onto an extraction run and return the updated row
 
export async function saveCorrections(runId, correctedData) {
  const db = getPool();
  if (!db) return false;
  await db.query(
    'UPDATE extraction_runs SET corrected_data = $1, corrected_at = NOW() WHERE id = $2',
    [JSON.stringify(correctedData), runId],
  );
  return true;
}
