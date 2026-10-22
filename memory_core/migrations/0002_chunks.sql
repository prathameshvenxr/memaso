-- Layer A: verbatim chunks, the source of truth, searched directly with no model involved.
CREATE TABLE chunks (
  id text PRIMARY KEY,                           -- sha256(document_id:position): re-processing is idempotent
  tenant text NOT NULL,
  subject text NOT NULL,
  container text,
  document_id text NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
  position int NOT NULL,
  text text NOT NULL,
  observed_at timestamptz NOT NULL,
  meta jsonb NOT NULL DEFAULT '{}',
  model_id text,                                 -- embedder that produced `vector`
  vector vector,                                 -- unconstrained dimension, like embeddings
  tsv tsvector GENERATED ALWAYS AS (to_tsvector('english', text)) STORED
);
CREATE INDEX chunks_scope_idx ON chunks (tenant, subject, observed_at);
CREATE INDEX chunks_tsv_idx ON chunks USING gin (tsv);
CREATE INDEX chunks_doc_idx ON chunks (document_id);

-- Which facts were derived from which chunk. A chunk whose facts are all superseded is history, not current.
CREATE TABLE memory_chunks (
  memory_id text NOT NULL REFERENCES memories(id) ON DELETE CASCADE,
  chunk_id text NOT NULL REFERENCES chunks(id) ON DELETE CASCADE,
  tenant text NOT NULL,
  PRIMARY KEY (memory_id, chunk_id)
);
CREATE INDEX memory_chunks_chunk_idx ON memory_chunks (chunk_id);

DO $$
DECLARE t text;
BEGIN
  FOREACH t IN ARRAY ARRAY['chunks','memory_chunks']
  LOOP
    EXECUTE format('ALTER TABLE %I ENABLE ROW LEVEL SECURITY', t);
    EXECUTE format('ALTER TABLE %I FORCE ROW LEVEL SECURITY', t);
    EXECUTE format('CREATE POLICY tenant_isolation ON %I USING (tenant = current_setting(''app.tenant'', true)) '
                   'WITH CHECK (tenant = current_setting(''app.tenant'', true))', t);
  END LOOP;
  IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'memaso_app') THEN
    GRANT SELECT, INSERT, UPDATE, DELETE ON chunks, memory_chunks TO memaso_app;
  END IF;
END $$;
