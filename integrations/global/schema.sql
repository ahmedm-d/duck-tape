-- Manual operator setup only. This is separate from ducktape_runs.
CREATE TABLE IF NOT EXISTS workspace.default.ducktape_global_revisions (
  canonical_id STRING NOT NULL,
  material_revision BIGINT NOT NULL,
  material_hash STRING NOT NULL,
  retrieved_at STRING NOT NULL,
  entry_json STRING NOT NULL
) USING DELTA;
