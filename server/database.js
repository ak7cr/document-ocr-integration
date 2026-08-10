import pg from 'pg';

let pool;

function getPool() {
  if (!process.env.DATABASE_URL) return null;
  pool ??= new pg.Pool({ connectionString: process.env.DATABASE_URL });
  return pool;
}

export async function saveRun(run) {
  const db = getPool();
  if (!db) return false;
  await db.query(
    `INSERT INTO extraction_runs
      (id, original_filename, mime_type, document_type, extraction_source, confidence, extracted_data, attempts)
     VALUES ($1,$2,$3,$4,$5,$6,$7,$8)`,
    [run.id, run.fileName, run.mimeType, run.data.documentType, run.source, run.confidence,
      JSON.stringify(run.data), JSON.stringify(run.attempts)],
  );
  return true;
}
