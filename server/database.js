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
  const { rows } = await db.query('SELECT id, name, fingerprint, field_rules AS "fieldRules" FROM document_templates WHERE status = $1', ['active']);
  return rows;
}

export async function getDictionary() {
  const db = getPool();
  if (!db) return [];
  const { rows } = await db.query('SELECT field_name AS "fieldName", canonical_value AS "canonicalValue", aliases FROM field_dictionary');
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

export async function saveTemplate({ id, name, fingerprint, fieldRules, runId }) {
  const db = getPool();
  if (!db) throw new Error('DATABASE_URL is not configured. Run db/schema.sql and configure Postgres first.');
  await db.query(
    `INSERT INTO document_templates (id, name, fingerprint, field_rules)
     VALUES ($1,$2,$3,$4)`,
    [id, name, JSON.stringify(fingerprint), JSON.stringify(fieldRules)],
  );
  if (runId) await db.query('UPDATE extraction_runs SET template_id = $1 WHERE id = $2', [id, runId]);
}

export async function markTemplateMatched(id) {
  const db = getPool();
  if (db && id) await db.query('UPDATE document_templates SET times_matched = times_matched + 1, updated_at = NOW() WHERE id = $1', [id]);
}
