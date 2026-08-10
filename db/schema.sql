CREATE TABLE IF NOT EXISTS extraction_runs (
  id UUID PRIMARY KEY,
  original_filename TEXT NOT NULL,
  mime_type TEXT NOT NULL,
  document_type TEXT,
  extraction_source TEXT NOT NULL,
  confidence NUMERIC(4,3) NOT NULL,
  extracted_data JSONB NOT NULL,
  attempts JSONB NOT NULL,
  created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
