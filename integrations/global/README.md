# Duck Tape public catalogue pipeline

This directory contains public-only source fixtures, operator setup, and an executable Databricks Python job. It does not configure imported repositories. Private profiles, code, decisions, team notes, private vectors and run metrics are not inputs to this pipeline.

## Local demonstration

From the repository root, with the application's Python dependencies installed:

```sh
python scripts/integrations.py catalogue-import --database data/global-demo.sqlite3 --record integrations/global/fixtures/global-vulnerability.json
python scripts/integrations.py catalogue-import --database data/global-demo.sqlite3 --record integrations/global/fixtures/global-improvement.json
python scripts/integrations.py catalogue-query --database data/global-demo.sqlite3 --ecosystem PyPI --package duck-tape-fixture
```

The two `global-*.json` files are validated `GlobalEntry` examples for B's local matcher. The other fixture files are the raw normalizer inputs. All fixture entries must retain `source_quality=fixture`; cloud publication rejects that quality. Synthetic OSV identifiers are not real advisories. Fixtures and fake cloud transports do not establish live service use.

Use a separate database for actual public HTTP ingestion:

```sh
python scripts/integrations.py catalogue-ingest-osv --database data/global-public.sqlite3 --id GHSA-9wx4-h78v-vm56
python scripts/integrations.py catalogue-ingest-improvement --database data/global-public.sqlite3 --record integrations/global/python313-improvement.json
python scripts/integrations.py catalogue-query --database data/global-public.sqlite3 --ecosystem PyPI --package requests
python scripts/integrations.py catalogue-query --database data/global-public.sqlite3 --ecosystem CPython --package python
python scripts/integrations.py catalogue-operations --database data/global-public.sqlite3
```

Source retrieval accepts only the hosts in `backend/global_pipeline.py:PUBLIC_HOSTS`, HTTPS port 443, no query/credentials, a 1 MiB body, pinned public DNS, and no redirects. TCP connection, TLS handshake, headers and body share a 30-second transfer deadline; an interrupting socket shutdown prevents slow-drip responses bypassing the inactivity timeout. The operating system DNS resolver used by the existing URL guard has its own timeout and cannot be interrupted here; total invocation time can exceed that transfer deadline while DNS resolves. The improvement summary is curated interpretation of Python's attributed release notes; it is not a measured gain. Repeated polling preserves material revisions. Coverage is always partial/unknown; version ranges are returned for B's matcher, not evaluated as exploitability.

## Separate public Atlas access

Set `STEWARD_GLOBAL_ATLAS_ENABLED=1` and `STEWARD_GLOBAL_ATLAS_URI` in the server environment. This URI is separate from `STEWARD_ATLAS_URI`; private vector settings do not enable public publication. TLS, bounded timeouts and the existing optional PyMongo dependency are reused. URI write concern, when supplied, must be `majority`.

The fixed database is `ducktape_public`, with `global_entries` current records and immutable `global_revisions`. Operators must use dedicated public-only credentials: publisher read/write on this database, runtime reader read only. No private database permissions. Run `atlas-setup.js` manually with an administrator after reviewing the namespace. The application never provisions infrastructure or changes roles/indexes.

`catalogue-publish --database ...` validates the entire batch before connecting, writes an immutable revision, then atomically replaces its current record. Retries are bounded to three attempts. A failed replacement preserves the previous complete head; readers never use incomplete revisions. Conflicting material content at the same revision and timestamp rollback fail. Current-record compare-and-swap also includes the prior retrieval time to protect concurrent refreshes.

`catalogue-query --atlas --ecosystem ... --package ...` uses exact indexed package fields and a limit of 1–100, validates returned payloads and hashes, and reports partial/unknown coverage. A different database configured for private vectors is required. Changing credentials does not establish connectivity or qualification.

## Databricks normalization job

Run `schema.sql` manually in an existing warehouse. Public revisions go to fixed `workspace.default.ducktape_global_revisions`, separate from the run-metrics table. Set `STEWARD_GLOBAL_DATABRICKS_ENABLED=1`; the existing `STEWARD_DATABRICKS_HOST`, `STEWARD_DATABRICKS_TOKEN`, and `STEWARD_DATABRICKS_WAREHOUSE_ID` supply the operator's warehouse connection. Enabling the metrics integration does not enable this pipeline.

Check out this Duck Tape revision as the Databricks job source and install its Python requirements and optional cloud driver in the Duck Tape job environment. Use a Python script task with `integrations/global/databricks_job.py`. Supply an explicit JSON source manifest:

```json
[{"kind":"osv","id":"GHSA-9wx4-h78v-vm56"}]
```

For an improvement, add `{"kind":"improvement","record":{...}}` using the contents of `python313-improvement.json` as `record`. This manifest accepts public source descriptors only. Set the job's **maximum concurrent runs to 1**; Delta revision identity assumes one curation producer. No schedule or compute is provisioned here.

```sh
python integrations/global/databricks_job.py --sources /path/to/public-sources.json
# Only after explicit Atlas configuration, publish successfully curated entries:
python integrations/global/databricks_job.py --sources /path/to/public-sources.json --publish
```

The executable job restores validated public material history from Delta, fetches and normalizes new public sources inside the job, assigns revisions through the same catalogue implementation, merges curated revisions, then optionally publishes only successful curation results. Prior complete Atlas records survive curation/fetch/publication failures. The job returns safe per-source outcomes and a nonzero exit on partial failure. Retain Databricks run output for failed-source history; the driver's temporary catalogue is not durable storage.

Delta updates use bound parameters and [Databricks `raise_error`](https://docs.databricks.com/aws/en/sql/language-manual/functions/raise_error) to fail conflicting same-revision content, rather than reporting a silent no-op as successful curation. Retrieval comparisons cast to timestamps. The CLI `catalogue-curate` uploads a locally normalized batch; the Python job is the path that actually performs normalization on Databricks.

## Bounds and evidence limitations

- Public fetches and descriptor files: 1 MiB. Each cloud batch: 100 entries. Stateless job history restore: at most 100 complete material revisions and the existing SQL response byte limit. Overflow/incomplete history stops the job; larger catalogues need partitioned restoration before use.
- The local catalogue persists latest per-source ingestion/publication outcomes. Local queries mark failed source evidence unknown. An Atlas-only reader cannot see a new source-fetch failure from a different machine; it sees stored retrieval age and withdrawal until a successful refresh. Consumers must consult job outcomes and withhold automatic proposals after a known failed refresh.
- The pipeline never deletes old revisions automatically. Withdrawal is material public evidence, not deletion. No model, proposal permission, patch approval or private repository applicability is changed here.
- Deterministic tests cover mocked transport/cloud behavior. No live Databricks or Atlas account was available during this implementation. Public HTTP evidence, when recorded, establishes only that source retrieval ran.
