-- Arrival order for documents that share an observed time (bulk imports, date-only timestamps).
ALTER TABLE documents ADD COLUMN IF NOT EXISTS seq bigserial;
CREATE INDEX IF NOT EXISTS documents_order_idx ON documents (tenant, subject, created_at, seq);
