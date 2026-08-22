CREATE TABLE IF NOT EXISTS document_templates (
  id UUID PRIMARY KEY,
  name TEXT NOT NULL,
  fingerprint JSONB NOT NULL,
  field_rules JSONB NOT NULL,
  form_mapping JSONB NOT NULL DEFAULT '{}'::jsonb,
  status TEXT NOT NULL DEFAULT 'active' CHECK (status IN ('active', 'draft', 'archived')),
  times_matched INTEGER NOT NULL DEFAULT 0,
  created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS extraction_runs (
  id UUID PRIMARY KEY,
  original_filename TEXT NOT NULL,
  mime_type TEXT NOT NULL,
  document_type TEXT,
  extraction_source TEXT NOT NULL,
  template_id UUID REFERENCES document_templates(id),
  confidence NUMERIC(4,3) NOT NULL,
  extracted_data JSONB NOT NULL,
  attempts JSONB NOT NULL,
  source_text TEXT,
  created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- Safe upgrades for databases created by the earlier extraction-only demo.
ALTER TABLE extraction_runs ADD COLUMN IF NOT EXISTS template_id UUID REFERENCES document_templates(id);
ALTER TABLE extraction_runs ADD COLUMN IF NOT EXISTS source_text TEXT;
ALTER TABLE document_templates ADD COLUMN IF NOT EXISTS form_mapping JSONB NOT NULL DEFAULT '{}'::jsonb;

CREATE INDEX IF NOT EXISTS document_templates_status_idx ON document_templates (status);
CREATE INDEX IF NOT EXISTS extraction_runs_template_idx ON extraction_runs (template_id);

-- Field value dictionary: stores canonical values and their observed aliases per field.
-- Used to normalize extracted values (fuzzy lookup) and learn from confirmed extractions.
CREATE TABLE IF NOT EXISTS field_dictionary (
  id          UUID PRIMARY KEY,
  field_name  TEXT NOT NULL,                              -- e.g. 'vendorName', 'customerName'
  canonical   TEXT NOT NULL,                              -- the normalized "correct" value
  aliases     JSONB NOT NULL DEFAULT '[]'::jsonb,         -- array of raw variant strings seen
  hit_count   INTEGER NOT NULL DEFAULT 1,
  created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  updated_at  TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE UNIQUE INDEX IF NOT EXISTS field_dictionary_field_canonical_idx
  ON field_dictionary (field_name, lower(canonical));
CREATE INDEX IF NOT EXISTS field_dictionary_field_idx ON field_dictionary (field_name);

-- Allow corrections to be stored back on an extraction run
ALTER TABLE extraction_runs ADD COLUMN IF NOT EXISTS corrected_data JSONB;
ALTER TABLE extraction_runs ADD COLUMN IF NOT EXISTS corrected_at TIMESTAMPTZ;
