-- Export all relevant terminal runs before interpreting these rates.
-- Imported runs are a manually selected subset, not an automatic census.
SELECT
  COUNT(*) AS terminal_runs,
  SUM(CASE WHEN status = 'complete' THEN 1 ELSE 0 END) AS complete_runs,
  SUM(CASE WHEN verified THEN 1 ELSE 0 END) AS verified_runs,
  100.0 * SUM(CASE WHEN verified THEN 1 ELSE 0 END) / NULLIF(COUNT(*), 0) AS verified_percent,
  AVG(duration_ms) AS average_duration_ms,
  COUNT(input_tokens) AS runs_with_known_input_usage,
  COUNT(output_tokens) AS runs_with_known_output_usage,
  SUM(input_tokens) AS known_input_tokens,
  SUM(output_tokens) AS known_output_tokens
FROM workspace.default.ducktape_runs;

-- Failures remain visible; NULL usage is unknown and is never coerced to zero.
SELECT status, COUNT(*) AS runs,
       SUM(CASE WHEN verified THEN 1 ELSE 0 END) AS verified_runs,
       AVG(duration_ms) AS average_duration_ms
FROM workspace.default.ducktape_runs
GROUP BY status
ORDER BY status;
