# Duck Tape internal technology stack

These services power Duck Tape itself. They are server-side infrastructure for its maintenance agent, not integrations offered to imported client repositories. Imported repositories provide evidence and receive reviewed patches; these adapters never add sponsor dependencies or credentials to their code. The local SQLite workflow and labelled sample remain usable without a cloud account.

| Internal service | Implemented foundation | Next live acceptance gate |
| --- | --- | --- |
| Adaption: agent inference/adaptation | Responses API, existing bounded model loop, local schema validation; hosted tools/memory/storage disabled | Account health, actual sandboxed repair/review, then held-out adaptation comparison |
| Hugging Face: open-weight agent models | Inference Providers chat, server token, provider-suffixed model IDs | Pin an available open-weight model/provider and qualify its actual workflow |
| MongoDB Atlas: repository memory | Explicit vector projection/search, namespace filters, local evidence resolution | Provision cluster/index, live sync/query, then connect evaluated candidates to worker retrieval |
| Databricks Data Intelligence Platform: agent evaluation | Explicit terminal-run metrics and SQL summary; schema/query artifacts | Provision warehouse/table, upload failed and successful runs, inspect measured results |

Tests use mocked services unless evidence explicitly says live. Configured is not connected or qualified. Atlas embeddings are source-derived information: use only repositories approved for that transfer. Databricks receives allowlisted metrics, never source, notes, prompts or logs. Nothing uploads at startup or during the local operator status check.

Read [inference configuration](INFERENCE.md) and [data setup](DATA_PLATFORMS.md). Keep credentials in ignored .env or process environment. Atlas needs the optional requirements-cloud.txt in your virtual environment.

```sh
python scripts/integrations.py status
python scripts/integrations.py atlas-index
python scripts/integrations.py atlas-sync --repo REPOSITORY_ID --snapshot SNAPSHOT_ID
python scripts/integrations.py atlas-search --repo REPOSITORY_ID --snapshot SNAPSHOT_ID --query "how are quotes computed?"
python scripts/integrations.py databricks-sync --run RUN_ID
python scripts/integrations.py databricks-summary
```

Status reads local configuration. Atlas-index prints a definition without provisioning. Sync/search/summary can contact remote services only after explicitly enabling their integration. The CLI is a manual operator path; cloud sync is not a worker queue yet.

The [three developer prompts](../hackathon/START_HERE.md) assign the next milestones. Retain actual request/run IDs, snapshots and measured outcomes before claiming live stack use or measured agent improvement.
