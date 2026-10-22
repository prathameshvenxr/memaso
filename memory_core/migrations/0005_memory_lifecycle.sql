-- Memory lifecycle: how often a memory is used, whether it has gone cold, and an indexed entity lookup.
ALTER TABLE memories ADD COLUMN access_count integer NOT NULL DEFAULT 0;
ALTER TABLE memories ADD COLUMN archived_at timestamptz;          -- cold and low value: kept, excluded from current search
ALTER TABLE memories ADD COLUMN entity_keys text[] NOT NULL DEFAULT '{}';   -- lower-cased copy of entities, GIN-indexed

-- the backfill must see every tenant's rows, and FORCE applies to the owner too
ALTER TABLE memories NO FORCE ROW LEVEL SECURITY;
UPDATE memories SET entity_keys = ARRAY(SELECT lower(x) FROM unnest(entities) AS x) WHERE cardinality(entities) > 0;
ALTER TABLE memories FORCE ROW LEVEL SECURITY;

CREATE INDEX memories_entity_keys_idx ON memories USING gin (entity_keys);
CREATE INDEX memories_live_idx ON memories (tenant, subject) WHERE archived_at IS NULL AND valid_to IS NULL;
