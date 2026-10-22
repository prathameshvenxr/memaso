-- exact: the fact's own words appear in the chunk (they can be redacted when the fact is superseded).
-- not exact: the fact is a model's paraphrase tied to the nearest chunk (the whole chunk retires with it).
ALTER TABLE memory_chunks ADD COLUMN IF NOT EXISTS exact boolean NOT NULL DEFAULT true;
