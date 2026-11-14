-- Run explicitly in an existing Databricks SQL warehouse. Adjust all three identifiers if needed.
CREATE TABLE IF NOT EXISTS workspace.default.ducktape_runs (
  run_id STRING NOT NULL,
  repo_id STRING NOT NULL,
  status STRING NOT NULL,
  verified BOOLEAN NOT NULL,
  finished_at TIMESTAMP NOT NULL,
  duration_ms DOUBLE NOT NULL,
  checks_count BIGINT NOT NULL,
  passed_checks BIGINT NOT NULL,
  provider_calls BIGINT,
  provider_latency_ms DOUBLE,
  input_tokens BIGINT,
  output_tokens BIGINT,
  regression_reproduced BOOLEAN
) USING DELTA;
