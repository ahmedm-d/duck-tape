# Inference integration

Duck Tape keeps its existing worker, retrieval, schema validation, patch checks and sandbox boundary. The provider adapter only sends model requests and validates responses. No SDK dependency is needed: both integrations use the existing bounded HTTP transport.

## Configure one provider

Set values in the server environment or the ignored project `.env`; restart the server after changes. Never put credentials in the browser, endpoint URL, committed configuration or screenshots. Remove old `STEWARD_MODEL_ENDPOINT` overrides when switching providers: explicit environment values take precedence over saved settings and defaults.

| Variable | Adaption | Hugging Face |
| --- | --- | --- |
| `STEWARD_MODEL_PROVIDER` | `adaption` | `huggingface` |
| `STEWARD_MODEL` | An exact model or stack ID granted to your account | An available Hub model ID, optionally `org/model:provider` |
| Native credential | `ADAPTION_API_KEY` | `HF_TOKEN` with Inference Providers permission |
| Default endpoint | `https://api.prod.adaptionlabs.ai/api/v1` | `https://router.huggingface.co/v1` |

Model choice is explicit; the integration does not assume a model is available or qualified. The Adaption guide uses `qwen3.5-397b` as an example. Check account access rather than treating that example as a quality recommendation. [Adaption quickstart](https://docs.adaptionlabs.ai/inference-stacks/quickstart/)

`STEWARD_MODEL_KEY` explicitly overrides the native credential, including for a custom endpoint. Native sponsor credentials are used only at the exact default HTTPS origin; they are never automatically sent to custom hosts or redirects. `STEWARD_MODEL_ENDPOINT` can override the endpoint for a deliberately configured gateway. Network proxies from the environment are disabled by the existing transport.

Existing budgets still apply: `STEWARD_CONTEXT_LIMIT`, `STEWARD_MAX_OUTPUT_TOKENS`, `STEWARD_MODEL_TIMEOUT` and the per-role overrides. Context accounting includes instructions, schema and reserved output. Requests retain their maximum two attempts, cancellation, response-size bound, local validation and cache identity. Blank `STEWARD_SMALL_MODEL` falls back to the main model.

## Adaption contract

The adapter calls `/responses`, not `/chat/completions`, using `model`, `instructions`, `input` and `max_output_tokens`. It disables hosted tools, agentic memory, streaming and response storage explicitly. Only completed assistant message text is accepted; refusals, tool outputs, incomplete responses and invalid local schemas fail without publishing a patch. Token usage is read from `input_tokens` and `output_tokens` when supplied. [Responses reference](https://docs.adaptionlabs.ai/api/resources/responses/methods/create)

The current public request schema does not document JSON-schema constrained generation. `capabilities()` and run telemetry disclose `structured_output_mode: prompt_and_validate`: JSON is requested in the prompt and then checked against the same strict local role schema as other providers. `structured_output` remains the existing enable/disable switch for structured application requests; it is not a provider-side guarantee. There is no automatic hosted memory, dataset upload, training, or continuous learning in this adapter. [Responses reference](https://docs.adaptionlabs.ai/api/resources/responses/methods/create)

## Hugging Face contract

The adapter reuses the existing `/chat/completions` request and strict `response_format` schema. The selected provider suffix is sent unchanged; only model-catalog comparison removes it. `structured_output_mode: json_schema` describes the requested mode, not a proven capability of every hosted model. Choose and test a specific provider/model combination because schema support varies. [Router API](https://huggingface.co/docs/inference-providers/index), [structured output guide](https://huggingface.co/docs/inference-providers/guides/structured-output)

The router model catalog includes provider metadata and schema-support flags. A successful catalog response shows advertised availability, not successful inference, permission to use every provider, sufficient credits, or application qualification. [HF catalog](https://huggingface.co/docs/inference-providers/hub-api)

## Anthropic (Claude) contract

`STEWARD_MODEL_PROVIDER=anthropic` calls the Messages API through the official `anthropic` Python SDK (`backend/anthropic_provider.py`). The credential is `ANTHROPIC_API_KEY` (or any other credential source the SDK resolves); `run.py` reads it from `.env`. The default model is `claude-opus-5-5` and the default endpoint `https://api.anthropic.com`; `STEWARD_MODEL` and `STEWARD_MODEL_ENDPOINT` override them.

Structured requests use native structured outputs (`output_config.format` with the role's JSON schema), so `structured_output_mode` is `json_schema`. Claude's constrained decoding does not accept numeric bounds, string-length bounds or array-length bounds, so only those keywords are removed from the copy sent to the API; every reply is still validated against the full local schema. Requests stream and collect the final message, so long outputs are not cut off by HTTP timeouts. The SDK's own retries are off; Duck Tape's retry loop retries 429, 5xx and 529 (overloaded) once. `stop_reason` `max_tokens` is reported as `truncated` and `refusal` as `refusal`; neither is retried.

Claude Opus 5.5 always thinks. `STEWARD_ANTHROPIC_EFFORT` (`low`, `medium`, `high`, `xhigh`, `max`; default `high`) sets the depth. Thinking and answer tokens share the caller's output reservation. `STEWARD_ANTHROPIC_MAX_TOKENS` (default 16000) can lower that allowance but never increase it; increase the configured request budget explicitly when more output is needed. Streaming calls enforce the absolute `STEWARD_MODEL_TIMEOUT` deadline as well as network timeouts. Anthropic has no embeddings API, so semantic retrieval needs a separate `STEWARD_EMBEDDING_ENDPOINT` (for example a local Ollama `/v1`). [Messages API](https://platform.claude.com/docs/en/api/messages), [structured outputs](https://platform.claude.com/docs/en/build-with-claude/structured-outputs)

## Embeddings and fallback

Leave `STEWARD_EMBEDDING_MODEL` unset for the existing lexical retrieval path. Neither sponsor adapter assumes that its chat endpoint supports the application's embedding API. For semantic retrieval, configure a separate OpenAI-compatible `STEWARD_EMBEDDING_ENDPOINT` and `STEWARD_EMBEDDING_MODEL`; only `STEWARD_EMBEDDING_KEY` is sent there, never the model credential. A local endpoint may require no embedding key. The existing verified local embedding runner remains supported.

## What has and has not been verified

Offline contract tests exercise real request construction and response handling through `httpx.MockTransport`. They cover both wire formats, native/custom credential boundaries, redirects, malformed JSON envelopes, invalid schemas, incomplete/refused/tool outputs, retry bounds, cancellation, cache behavior, context limits and embedding isolation:

```powershell
python -m pytest tests/test_sponsor_providers.py tests/test_provider_contracts.py tests/test_test_role_schema.py tests/test_runner_startup.py -q
```

These checks do not call a sponsor API or establish live model quality. Settings health uses the model catalog and reports `connection_check: model_catalog`; all roles remain unqualified until their recorded application checks establish otherwise. Adaption's catalog lists models available to the calling account. [Adaption model catalog](https://docs.adaptionlabs.ai/api/resources/models/methods/list)

After credentials, budget and a model are deliberately selected, the existing live smoke suite can verify response compatibility. It spends inference credits and is separate from the 12-task application qualification gate:

```powershell
$env:STEWARD_LIVE_TESTS = '1'
python -c "import run; run.load_environment(); import pytest; raise SystemExit(pytest.main(['tests/live/test_provider_live.py', '-q']))"
```

The command loads Duck Tape's literal project `.env` before pytest; direct `python -m pytest` does not. Test databases intentionally ignore saved UI provider settings, so select the provider and model through the process environment or project `.env`.

Run full application qualification using the existing qualification runbook and retain its evidence. Direct `scripts/evaluate_models.py` also needs process environment configuration; load the project `.env` first when using that file:

```powershell
python -c "import run; run.load_environment(); from scripts.evaluate_models import main; raise SystemExit(main())" --driver scripts.qualification_driver:driver --output data/qualification/current.json
```

Passing the smoke suite alone does not qualify autonomous review. This foundation includes no live sponsor test results, training job, model download or hosted-tool integration.
