# Paused Databricks refresh job

`databricks-job.json` is a Jobs API create payload for the existing **DUCKTAPE** profile and workspace `https://dbc-a762c421-e9f8.cloud.databricks.com`. It uses existing SQL warehouse `710499d60f04f73d`; it creates no warehouse, cluster, account or paid resource. The serverless task is scheduled for 07:00 UTC daily but starts **PAUSED**, allows one concurrent run, has a 900-second timeout and no whole-job automatic retries. Individual cloud writes retain their bounded retries.

Serverless auto-optimization can retry independently of `max_retries=0`; live verification observed this behavior. The definition now explicitly sets `disable_auto_optimization=true` to keep whole-task retries disabled. See [serverless auto-optimization](https://docs.databricks.com/aws/en/jobs/run-serverless-jobs).

The notebook fetches the real OSV advisory and attributed Python improvement, calls the existing normalization/curation job, and optionally publishes to the public Atlas database. `publish=false` initially. The deterministic tests do not connect to Databricks; actual live execution is recorded separately below.

## Recorded live verification

On 14 November 2026, job `486190608689437` was created paused in this workspace. Manual run `283630747207922` completed successfully: the notebook restored two Delta revisions, fetched/normalized both actual public sources inside Databricks and curated both at material revision 1. Notebook-native ephemeral authentication worked without a saved warehouse token. Earlier live local normalization plus repeated Delta curation/restoration also retained one row per revision. See [live-databricks-evidence.json](live-databricks-evidence.json) for source hashes and exact outcomes.

This proves a real Databricks pipeline run, not Atlas publication or a scheduled trigger. The trigger remained paused and publication was not requested during that run. [Open the successful run](https://dbc-a762c421-e9f8.cloud.databricks.com/?o=7474650090762920#job/486190608689437/run/283630747207922).

## Prepare the existing account

1. The coordinator checks the `DUCKTAPE` profile targets the host above. Existing MCP SQL success demonstrates warehouse access only, not Jobs, Secrets, serverless package installation, public fetches or Atlas access.
2. Apply the existing `schema.sql` if the public Delta table does not already exist. Keep the public table separate from run metrics and do not use private vectors as catalogue input.
3. The default `warehouse_auth=notebook` uses the SDK's notebook-native authentication and holds its ephemeral bearer only in the running process. No saved warehouse token is needed. Verify the notebook's run-as identity can use the selected warehouse. If native authentication is unavailable and the operator explicitly chooses `warehouse_auth=secret`, create/reuse a dedicated `ducktape-public` scope restricted to that identity and required administrators, then add a renewable `warehouse-token` through a secure secret editor or CLI prompt. Rotate expiring credentials; never pass secret values in parameters, shell arguments or source files.
4. Only for Atlas publication, add `public-atlas-uri` to that scope. Its database principal must have read/write access only to `ducktape_public`, without private repository-vector or run-metric access. Check the Atlas network policy allows the Databricks serverless connection without opening unrestricted access merely to make a test pass. `publish=false` does not read this secret.
5. Confirm the workspace offers serverless environment version 4 and permits the specified dependency installation. Free Edition is serverless-only with fair-use and concurrency limits; do not provision paid compute to bypass an unavailable capability.

Workspace secret scopes support `dbutils.secrets` in jobs; scope access applies to all secrets within the scope. See [secret management](https://docs.databricks.com/aws/en/security/secrets/). Serverless jobs accept an explicit environment/dependency list; see [serverless dependencies](https://docs.databricks.com/aws/en/compute/serverless/dependencies). Free Edition can stop compute after quota exhaustion; see [Free Edition limitations](https://docs.databricks.com/aws/en/getting-started/free-edition-limitations).

The SDK can obtain credentials from the notebook context without explicit arguments; the wrapper checks the returned workspace host before using its bearer. See [notebook-native authentication](https://databricks-sdk-py.readthedocs.io/en/stable/authentication.html).

## Upload only the public application files

Use the coordinator's existing SDK installation; the application does not acquire an SDK dependency. Start in this repository root. The following recipe is a cloud mutation for the coordinator to execute after checking the destination. It refuses to overwrite existing files. For an existing deployment, inspect its current revision before intentionally updating these same allowlisted files.

```python
import json
from pathlib import Path
from databricks.sdk import WorkspaceClient
from databricks.sdk.service.workspace import ImportFormat, Language

w = WorkspaceClient(profile='DUCKTAPE')
assert w.config.host.rstrip('/') == 'https://dbc-a762c421-e9f8.cloud.databricks.com'
destination = '/Workspace/Shared/duck-tape-public'
files = sorted(Path('backend').glob('*.py')) + [
    Path('requirements.txt'), Path('requirements-cloud.txt'),
    Path('integrations/global/databricks_job.py'),
    Path('integrations/global/python313-improvement.json'),
]
for source in files:
    target = destination + '/' + source.as_posix()
    w.workspace.mkdirs(str(Path(target).parent).replace('\\', '/'))
    w.workspace.upload(target, source.read_bytes(), format=ImportFormat.RAW, overwrite=False)

notebook = Path('integrations/global/scheduled_notebook.py')
w.workspace.upload(
    destination + '/integrations/global/scheduled_notebook', notebook.read_bytes(),
    format=ImportFormat.SOURCE, language=Language.PYTHON, overwrite=False,
)
definition = json.loads(Path('integrations/global/databricks-job.json').read_text())
assert definition['schedule']['pause_status'] == 'PAUSED'
created = w.api_client.do('POST', '/api/2.2/jobs/create', body=definition)
print({'job_id': created['job_id'], 'schedule': 'PAUSED'})
```

This uploads Duck Tape's own Python application modules and the two selected public-source artifacts. Do **not** recursively upload the checkout: `.env`, data, imported repositories, team/hackathon notes, credentials and test runtime files are excluded. Ordinary modules must remain workspace files with `.py` names; only `scheduled_notebook.py` is imported as a notebook. See [workspace upload](https://databricks-sdk-py.readthedocs.io/en/stable/workspace/workspace/workspace.html).

## Verify before unpausing

With the returned job ID, the coordinator performs a manual run while the schedule remains paused:

```python
run = w.jobs.run_now(job_id=job_id).result()
print({'run_id': run.run_id, 'result': str(run.state.result_state)})
```

Inspect the task's safe run output and warehouse statement history. Confirm both public records were normalized inside Databricks, complete material revisions exist in Delta, and a second manual run preserves unchanged material revisions. Failed stages must make the job fail and preserve prior published records; a successful run with `publish=false` is not Atlas evidence.

After the scoped Atlas credential/network path is usable, run once with the task notebook parameter `publish=true` using **Run now with different parameters**, retaining the paused trigger. Verify Atlas current records, immutable revisions, exact bounded query and repeated publication. Set the task's default `publish=true` only if ongoing publication is intended. Then unpause the trigger in Jobs & Pipelines and record the next actually scheduled run ID/outcome; manually running a paused job does not prove the scheduler fired. [Databricks schedule controls](https://docs.databricks.com/aws/en/jobs/scheduled)

## Remaining live checks

- Jobs create/run permission, serverless environment 4, dependency installation, notebook-native authentication, public OSV/Python fetches and warehouse writes passed in the recorded manual run. Subsequent publication runs successfully read the dedicated Atlas secret but failed. A read-only probe resolved three Atlas SRV records and connected to TCP27017; TLS failed and the scoped Atlas ping timed out. HTTPS egress changed from `3.145.247.177` to `3.145.247.155` across probes, so the single observed `/32` allowlist is not a dependable recurring connection policy.
- Local Atlas publication is independently verified in [live-atlas-evidence.json](live-atlas-evidence.json); **Databricks-to-Atlas publication and an actual scheduled trigger remain unverified**. The clean post-allowlist manual attempt `994468391849329` failed. Exact TLS failure cause remains unproven; do not disable certificate verification or broaden network access to force success.
- A supported network policy must precede recurring publication. [Databricks serverless networking](https://docs.databricks.com/aws/en/security/network/serverless-network-security/) documents account-level connectivity controls and the retirement of the legacy stable-IP method; [firewall configuration](https://docs.databricks.com/aws/en/security/network/serverless-network-security/serverless-firewall-config) describes supported outbound policies. No paid networking or broad ranges were configured here. Observing one HTTPS egress IP does not establish a stable MongoDB egress path.
- The existing single-producer, 100-material-revision restore bound and source-failure visibility limits in [README.md](README.md) still apply. Retain job output; a fresh-looking Atlas record alone does not prove its latest source refresh succeeded.
- Keep the trigger paused if any required check fails. No successful scheduled trigger or Atlas publication is claimed by this evidence.

## Verified explicit local relay

When the Databricks-to-Atlas network path is unavailable, the existing integrations CLI can restore actual curated Delta revisions into an isolated local catalogue, then explicitly publish from the operator's already-allowed network. This path does not fetch/normalize sources locally and does not establish a scheduled cloud publication.

Use the application requirements plus `requirements-cloud.txt`. Configure the existing warehouse connection (`STEWARD_GLOBAL_DATABRICKS_ENABLED=1`, `STEWARD_DATABRICKS_HOST`, `STEWARD_DATABRICKS_WAREHOUSE_ID`, process-only `STEWARD_DATABRICKS_TOKEN`) and separate public publisher (`STEWARD_GLOBAL_ATLAS_ENABLED=1`, process-only `STEWARD_GLOBAL_ATLAS_URI`). Obtain credentials from the existing authenticated SDK/secret store; do not put them in command arguments or tracked files. Start with a new database path:

```sh
python scripts/integrations.py catalogue-restore --database data/global-relay-new.sqlite3
python scripts/integrations.py catalogue-query --database data/global-relay-new.sqlite3 --ecosystem PyPI --package requests
python scripts/integrations.py catalogue-publish --database data/global-relay-new.sqlite3
python scripts/integrations.py catalogue-query --atlas --ecosystem PyPI --package requests
```

`catalogue-restore` validates complete revision history and hashes through the same bounded restore helper used by the notebook; it does not publish. The operator must stop if restoration fails. The existing 100-history limit applies, and older retrievals cannot replace fresher Atlas records.

The live relay restored two revisions fetched by Databricks at 16:46:42 UTC, published both, repeated publication without new material revisions, and verified both exact Atlas package queries. See [live-relay-evidence.json](live-relay-evidence.json). Its first local attempt lacked the optional PyMongo driver and failed safely; using the existing isolated optional-driver target completed verification. This is a manual relay, not an automatic schedule or a workaround for Databricks network controls.
