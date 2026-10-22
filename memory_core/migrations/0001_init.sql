-- the vector extension is created by migrate() in schema public; every table below lands in schema memaso

CREATE TABLE tenants (
  id text PRIMARY KEY,
  monthly_budget bigint,                       -- tokens per month, NULL = unlimited
  created_at timestamptz NOT NULL DEFAULT now()
);

-- tenants/api_keys have no RLS: the tenant is *derived* from the key lookup. The app role gets SELECT only.
CREATE TABLE api_keys (
  key_hash text PRIMARY KEY,
  tenant text NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
  subject text,
  created_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE documents (
  id text PRIMARY KEY,
  tenant text NOT NULL,
  subject text NOT NULL,
  container text,
  content text NOT NULL,
  hash text NOT NULL,
  status text NOT NULL,
  error text,
  meta jsonb NOT NULL DEFAULT '{}',
  created_at timestamptz NOT NULL,
  UNIQUE (tenant, hash)
);

CREATE TABLE memories (
  id text PRIMARY KEY,
  tenant text NOT NULL,
  subject text NOT NULL,
  container text,
  text text NOT NULL,
  kind text NOT NULL,
  entities text[] NOT NULL DEFAULT '{}',
  confidence real NOT NULL,
  importance real NOT NULL DEFAULT 0.5,
  valid_from timestamptz NOT NULL,
  valid_to timestamptz,
  extractor_version text NOT NULL,
  meta jsonb NOT NULL DEFAULT '{}',
  created_at timestamptz NOT NULL DEFAULT now(),
  last_accessed timestamptz,
  tsv tsvector GENERATED ALWAYS AS (to_tsvector('english', text)) STORED
);
CREATE INDEX memories_scope_idx ON memories (tenant, subject, valid_to);
CREATE INDEX memories_tsv_idx ON memories USING gin (tsv);

CREATE TABLE memory_sources (
  memory_id text NOT NULL REFERENCES memories(id) ON DELETE CASCADE,
  document_id text NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
  tenant text NOT NULL,
  PRIMARY KEY (memory_id, document_id)
);

CREATE TABLE memory_links (
  memory_id text NOT NULL REFERENCES memories(id) ON DELETE CASCADE,
  supersedes_id text NOT NULL REFERENCES memories(id) ON DELETE CASCADE,
  tenant text NOT NULL,
  verdict text NOT NULL,
  reason text,                                  -- lives and dies with the memory, never in audit_log
  PRIMARY KEY (memory_id, supersedes_id)
);

-- Unconstrained dimension on purpose: vectors from different embedders coexist, tagged by model_id.
-- ponytail: exact scan filtered by tenant/subject/model_id; add a per-model HNSW expression index past ~100k rows/tenant.
CREATE TABLE embeddings (
  memory_id text NOT NULL REFERENCES memories(id) ON DELETE CASCADE,
  model_id text NOT NULL,
  tenant text NOT NULL,
  vector vector NOT NULL,
  PRIMARY KEY (memory_id, model_id)
);

CREATE TABLE audit_log (
  id bigserial PRIMARY KEY,
  tenant text NOT NULL,
  actor text NOT NULL,
  action text NOT NULL,
  target text,
  detail jsonb NOT NULL DEFAULT '{}',          -- ids, verdicts, counts. Never memory text.
  at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE deletion_receipts (
  id text PRIMARY KEY,
  tenant text NOT NULL,
  requested jsonb NOT NULL,
  counts jsonb NOT NULL,
  at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE jobs_dead_letter (
  id bigserial PRIMARY KEY,
  tenant text NOT NULL,
  job jsonb NOT NULL,
  error text NOT NULL,
  at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE usage (
  tenant text NOT NULL,
  day date NOT NULL,
  tokens bigint NOT NULL DEFAULT 0,
  PRIMARY KEY (tenant, day)
);

DO $$
DECLARE t text;
BEGIN
  FOREACH t IN ARRAY ARRAY['documents','memories','memory_sources','memory_links','embeddings',
                           'audit_log','deletion_receipts','jobs_dead_letter','usage']
  LOOP
    EXECUTE format('ALTER TABLE %I ENABLE ROW LEVEL SECURITY', t);
    EXECUTE format('ALTER TABLE %I FORCE ROW LEVEL SECURITY', t);
    EXECUTE format('CREATE POLICY tenant_isolation ON %I USING (tenant = current_setting(''app.tenant'', true)) '
                   'WITH CHECK (tenant = current_setting(''app.tenant'', true))', t);
  END LOOP;
  IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'memaso_app') THEN
    GRANT SELECT, INSERT, UPDATE, DELETE ON documents, memories, memory_sources, memory_links, embeddings,
          audit_log, deletion_receipts, jobs_dead_letter, usage TO memaso_app;
    GRANT SELECT ON tenants, api_keys TO memaso_app;
    GRANT USAGE ON ALL SEQUENCES IN SCHEMA memaso TO memaso_app;
    GRANT USAGE ON SCHEMA memaso TO memaso_app;
  END IF;
END $$;
