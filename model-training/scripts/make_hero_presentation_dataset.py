#!/usr/bin/env python3
"""Generate 1,100-row hero presentation dataset and 100-row held-out eval set.

Pure local Python (< 5 seconds, zero API calls).
Produces `model-training/data/processed/hero_graph_security_triage.jsonl` (1,100 rows)
and `model-training/data/eval/hero_security_triage_eval.jsonl` (100 disjoint held-out rows) with:
- `<graph_context>` v1 blocks (`focal_symbols` with `signature`, `EXTRACTED` vs `INFERRED` edges,
  `blast_radius.linked_test_files`, and `catalogue_matches` with `directly_imported` and `sink_reachable: null`).
- Rich, authoritative 5-section Markdown reports (~250-350 words) with tables and bulleted `file:line` citations.
"""
from __future__ import annotations

import hashlib
import json
import time
from pathlib import Path
from typing import Any

SCRIPT_DIR = Path(__file__).resolve().parent
MODEL_TRAINING_ROOT = SCRIPT_DIR.parent
OUTPUT_PATH = MODEL_TRAINING_ROOT / "data" / "processed" / "hero_graph_security_triage.jsonl"
REL_OUTPUT_PATH = "model-training/data/processed/hero_graph_security_triage.jsonl"
EVAL_PATH = MODEL_TRAINING_ROOT / "data" / "eval" / "hero_security_triage_eval.jsonl"
REL_EVAL_PATH = "model-training/data/eval/hero_security_triage_eval.jsonl"

TOTAL_ROWS = 1100
EVAL_ROWS = 100

REPOSITORIES: tuple[dict[str, str], ...] = (
    {"repo": "duck-tape/checkout-gateway", "service": "checkout_gateway", "domain": "payment webhook settlement"},
    {"repo": "duck-tape/identity-broker", "service": "identity_broker", "domain": "OIDC token exchange and session signing"},
    {"repo": "duck-tape/artifact-ingestor", "service": "artifact_ingestor", "domain": "SBOM archive extraction and manifest parsing"},
    {"repo": "duck-tape/ledger-reconciler", "service": "ledger_reconciler", "domain": "double-entry invoice batch settlement"},
    {"repo": "duck-tape/telemetry-relay", "service": "telemetry_relay", "domain": "OTLP trace ingestion and payload forwarding"},
    {"repo": "duck-tape/policy-evaluator", "service": "policy_evaluator", "domain": "RBAC policy compilation and AST evaluation"},
    {"repo": "duck-tape/webhook-dispatcher", "service": "webhook_dispatcher", "domain": "outbound callback signing and retry routing"},
    {"repo": "duck-tape/document-renderer", "service": "document_renderer", "domain": "templated compliance report rendering"},
    {"repo": "duck-tape/schema-registry", "service": "schema_registry", "domain": "JSON schema validation and migration dispatch"},
    {"repo": "duck-tape/session-proxy", "service": "session_proxy", "domain": "edge request framing and header normalization"},
    {"repo": "duck-tape/audit-archiver", "service": "audit_archiver", "domain": "immutable audit log compression and upload"},
    {"repo": "duck-tape/feature-router", "service": "feature_router", "domain": "dynamic traffic splitting and rule evaluation"},
)

ADVISORY_CATALOGUE: tuple[dict[str, str], ...] = (
    {
        "entry_id": "GHSA-9wx4-h78v-vm56",
        "cve": "CVE-2023-32681",
        "pkg": "requests",
        "import_mod": "requests.sessions",
        "affected_ver": "2.28.1",
        "fixed_ver": "2.31.0",
        "cwe": "CWE-200",
        "sink_api": "Session.rebuild_proxies",
        "summary": "Unintended Proxy-Authorization header leak on cross-origin redirect in requests.",
    },
    {
        "entry_id": "GHSA-j8r2-6x86-q33q",
        "cve": "CVE-2023-45803",
        "pkg": "urllib3",
        "import_mod": "urllib3.poolmanager",
        "affected_ver": "1.26.15",
        "fixed_ver": "1.26.18",
        "cwe": "CWE-200",
        "sink_api": "PoolManager.urlopen",
        "summary": "Request body not stripped after HTTP 303 redirect status change in urllib3.",
    },
    {
        "entry_id": "GHSA-r9hx-vwmv-q579",
        "cve": "CVE-2024-34064",
        "pkg": "jinja2",
        "import_mod": "jinja2.filters",
        "affected_ver": "3.1.2",
        "fixed_ver": "3.1.4",
        "cwe": "CWE-79",
        "sink_api": "do_xmlattr",
        "summary": "Attribute injection via keys containing spaces in Jinja2 xmlattr filter.",
    },
    {
        "entry_id": "GHSA-hrfv-mqp8-q5rw",
        "cve": "CVE-2023-46136",
        "pkg": "werkzeug",
        "import_mod": "werkzeug.formparser",
        "affected_ver": "2.3.6",
        "fixed_ver": "2.3.8",
        "cwe": "CWE-400",
        "sink_api": "MultiPartParser.parse",
        "summary": "High CPU resource consumption when parsing crafted multipart form boundary payloads.",
    },
    {
        "entry_id": "GHSA-5437-5m7r-vc3m",
        "cve": "CVE-2020-14343",
        "pkg": "pyyaml",
        "import_mod": "yaml.loader",
        "affected_ver": "5.3.1",
        "fixed_ver": "5.4.0",
        "cwe": "CWE-502",
        "sink_api": "FullLoader.construct_python_object",
        "summary": "Arbitrary code execution via untrusted YAML tags processed by FullLoader.",
    },
    {
        "entry_id": "GHSA-w7pp-m8wf-vj6r",
        "cve": "CVE-2023-49083",
        "pkg": "cryptography",
        "import_mod": "cryptography.hazmat.primitives.serialization.pkcs7",
        "affected_ver": "41.0.4",
        "fixed_ver": "41.0.6",
        "cwe": "CWE-476",
        "sink_api": "load_pem_pkcs7_certificates",
        "summary": "NULL pointer dereference when loading PKCS7 certificates lacking signed data content.",
    },
    {
        "entry_id": "GHSA-3ww4-gg4f-jr7f",
        "cve": "CVE-2024-26130",
        "pkg": "cryptography",
        "import_mod": "cryptography.hazmat.backends.openssl",
        "affected_ver": "42.0.2",
        "fixed_ver": "42.0.4",
        "cwe": "CWE-476",
        "sink_api": "serialize_pkcs12",
        "summary": "Pointer dereference fault in PKCS12 serialization with mismatched encryption certificate.",
    },
    {
        "entry_id": "GHSA-pxg6-952v-5xp7",
        "cve": "CVE-2024-24762",
        "pkg": "python-multipart",
        "import_mod": "multipart.multipart",
        "affected_ver": "0.0.6",
        "fixed_ver": "0.0.7",
        "cwe": "CWE-400",
        "sink_api": "MultipartParser.data_received",
        "summary": "Regular expression denial of service (ReDoS) on Content-Type header option parsing.",
    },
    {
        "entry_id": "GHSA-7f5h-v6xp-fcq8",
        "cve": "CVE-2023-37920",
        "pkg": "certifi",
        "import_mod": "certifi.core",
        "affected_ver": "2023.5.7",
        "fixed_ver": "2023.7.22",
        "cwe": "CWE-345",
        "sink_api": "where",
        "summary": "Presence of deprecated e-Tugra root certificate in bundled CA trust store.",
    },
    {
        "entry_id": "GHSA-jjg7-2v4v-x38h",
        "cve": "CVE-2024-3651",
        "pkg": "idna",
        "import_mod": "idna.core",
        "affected_ver": "3.6",
        "fixed_ver": "3.7",
        "cwe": "CWE-400",
        "sink_api": "encode",
        "summary": "Quadratic complexity resource exhaustion during punycode label validation in idna.encode.",
    },
    {
        "entry_id": "GHSA-248v-346w-9cwc",
        "cve": "CVE-2023-43804",
        "pkg": "urllib3",
        "import_mod": "urllib3.util.retry",
        "affected_ver": "2.0.5",
        "fixed_ver": "2.0.6",
        "cwe": "CWE-200",
        "sink_api": "Retry.increment",
        "summary": "Cookie HTTP header not stripped on cross-origin redirect when retries are configured.",
    },
    {
        "entry_id": "GHSA-h5c8-rqwp-cp95",
        "cve": "CVE-2024-22195",
        "pkg": "jinja2",
        "import_mod": "jinja2.Environment",
        "affected_ver": "3.1.1",
        "fixed_ver": "3.1.3",
        "cwe": "CWE-79",
        "sink_api": "Environment.compile_expression",
        "summary": "Cross-site scripting via newline-injected attributes in xmlattr template rendering.",
    },
)

ENTRYPOINT_FUNCS: tuple[tuple[str, str], ...] = (
    ("dispatch_webhook_event", "def dispatch_webhook_event(request_ctx: RequestContext, raw_body: bytes) -> ResponsePayload:"),
    ("handle_ingest_batch", "def handle_ingest_batch(envelope: BatchEnvelope, headers: Mapping[str, str]) -> IngestReceipt:"),
    ("execute_settlement_pipeline", "def execute_settlement_pipeline(tx_batch: TransactionBatch, config: RuntimeConfig) -> SettlementResult:"),
    ("route_authenticated_rpc", "def route_authenticated_rpc(session_token: str, rpc_frame: dict[str, Any]) -> RpcOutcome:"),
    ("evaluate_policy_bundle", "def evaluate_policy_bundle(bundle_stream: bytes, caller_meta: CallerMetadata) -> PolicyVerdict:"),
    ("process_callback_delivery", "def process_callback_delivery(endpoint_url: str, signed_payload: dict[str, Any]) -> DeliveryStatus:"),
    ("reconcile_snapshot_manifest", "def reconcile_snapshot_manifest(manifest_doc: str, strict_mode: bool = True) -> ReconcileReport:"),
    ("transform_telemetry_frame", "def transform_telemetry_frame(frame_bytes: bytes, routing_key: str) -> NormalizedSpan:"),
)

SINK_ADAPTER_FUNCS: tuple[tuple[str, str], ...] = (
    ("parse_outbound_envelope", "def parse_outbound_envelope(raw_payload: bytes, content_type: str) -> ParsedEnvelope:"),
    ("build_upstream_transport", "def build_upstream_transport(target_uri: str, proxy_headers: dict[str, str]) -> TransportSession:"),
    ("render_audit_fragment", "def render_audit_fragment(template_ctx: dict[str, Any], attr_overrides: dict[str, str]) -> str:"),
    ("decode_signed_bundle", "def decode_signed_bundle(pem_blob: bytes, verify_chain: bool = True) -> BundleClaims:"),
    ("normalize_multipart_stream", "def normalize_multipart_stream(stream_buffer: bytes, boundary_token: str) -> list[PartRecord]:"),
    ("resolve_canonical_host", "def resolve_canonical_host(host_label: str, allow_punycode: bool = False) -> str:"),
)

DYNAMIC_HELPER_FUNCS: tuple[tuple[str, str], ...] = (
    ("resolve_dynamic_plugin_hook", "def resolve_dynamic_plugin_hook(hook_name: str, registry: dict[str, Any]) -> Callable[..., Any]:"),
    ("dispatch_lazy_middleware", "def dispatch_lazy_middleware(stage_key: str, context_bag: dict[str, Any]) -> MiddlewareResult:"),
    ("load_runtime_codec_adapter", "def load_runtime_codec_adapter(content_encoding: str, fallback: str = 'utf-8') -> CodecAdapter:"),
    ("invoke_registered_validator", "def invoke_registered_validator(schema_id: str, candidate_doc: dict[str, Any]) -> bool:"),
)


def _short_hash(text: str, length: int = 16) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:length]


def build_row(idx: int) -> dict[str, str]:
    repo_info = REPOSITORIES[idx % len(REPOSITORIES)]
    adv = ADVISORY_CATALOGUE[(idx // len(REPOSITORIES)) % len(ADVISORY_CATALOGUE)]
    entry_fn, entry_sig = ENTRYPOINT_FUNCS[idx % len(ENTRYPOINT_FUNCS)]
    adapter_fn, adapter_sig = SINK_ADAPTER_FUNCS[(idx // 3) % len(SINK_ADAPTER_FUNCS)]
    helper_fn, helper_sig = DYNAMIC_HELPER_FUNCS[(idx // 5) % len(DYNAMIC_HELPER_FUNCS)]

    repo = repo_info["repo"]
    svc = repo_info["service"]
    domain = repo_info["domain"]

    # Alternate `directly_imported` True (65%) vs False (35%) while keeping `sink_reachable: null`
    directly_imported = (idx % 20) < 13
    structural_risk_levels = ("high", "medium", "low")
    structural_risk = structural_risk_levels[idx % 3]

    entry_line = 18 + (idx % 17)
    entry_end_line = entry_line + 34
    adapter_line = 42 + (idx % 23)
    adapter_end_line = adapter_line + 29
    helper_line = 15 + (idx % 19)
    helper_end_line = helper_line + 24
    lock_line = 12 + (idx % 45)
    call_edge_line = entry_line + 9
    inferred_edge_line = adapter_line + 14
    import_edge_line = 4 + (idx % 6)
    test_edge_line = 21 + (idx % 15)

    entry_path = f"src/{svc}/router.py"
    adapter_path = f"src/{svc}/transport_adapter.py"
    helper_path = f"src/{svc}/plugin_loader.py"
    lockfile_path = "requirements.lock"

    test_file_1 = f"tests/unit/test_{svc}_router.py"
    test_file_2 = f"tests/security/test_{adv['pkg'].replace('-', '_')}_triage_{idx % 25:02d}.py"
    linked_tests = [test_file_1, test_file_2]

    repo_id = "repo_" + _short_hash(f"{repo}:{idx}", 12)
    snapshot_id = "snap_" + _short_hash(f"{repo}:{adv['entry_id']}:{idx}", 12)

    node_entry = f"graphify:{entry_path}:{entry_fn}:{entry_line}"
    node_adapter = f"graphify:{adapter_path}:{adapter_fn}:{adapter_line}"
    node_helper = f"graphify:{helper_path}:{helper_fn}:{helper_line}"
    node_test = f"file:{test_file_1}"

    deg_entry = 5 + (idx % 4)
    deg_adapter = 3 + (idx % 4)
    deg_helper = 2 + (idx % 3)

    focal_symbols: list[dict[str, Any]] = [
        {
            "node_id": node_entry,
            "path": entry_path,
            "start_line": entry_line,
            "end_line": entry_end_line,
            "kind": "callable",
            "label": entry_fn,
            "signature": entry_sig,
            "community": idx % 3,
            "degree": deg_entry,
            "high_degree": deg_entry >= 6,
        },
        {
            "node_id": node_adapter,
            "path": adapter_path,
            "start_line": adapter_line,
            "end_line": adapter_end_line,
            "kind": "callable",
            "label": adapter_fn,
            "signature": adapter_sig,
            "community": (idx + 1) % 3,
            "degree": deg_adapter,
            "high_degree": deg_adapter >= 6,
        },
        {
            "node_id": node_helper,
            "path": helper_path,
            "start_line": helper_line,
            "end_line": helper_end_line,
            "kind": "callable",
            "label": helper_fn,
            "signature": helper_sig,
            "community": (idx + 2) % 3,
            "degree": deg_helper,
            "high_degree": False,
        },
    ]

    edges: list[dict[str, Any]] = [
        {
            "source": node_entry,
            "target": node_adapter,
            "relation": "calls",
            "confidence": "EXTRACTED",
            "suggestion_only": False,
            "evidence": {"path": entry_path, "line": call_edge_line},
        },
        {
            "source": node_adapter,
            "target": node_helper,
            "relation": "calls",
            "confidence": "INFERRED",
            "suggestion_only": True,
            "evidence": {"path": adapter_path, "line": inferred_edge_line},
        },
        {
            "source": node_test,
            "target": node_entry,
            "relation": "tests",
            "confidence": "EXTRACTED",
            "suggestion_only": False,
            "evidence": {"path": test_file_1, "line": test_edge_line},
        },
    ]

    if directly_imported:
        edges.insert(
            1,
            {
                "source": f"file:{adapter_path}",
                "target": node_adapter,
                "relation": "imports_from",
                "confidence": "EXTRACTED",
                "suggestion_only": False,
                "evidence": {"path": adapter_path, "line": import_edge_line},
            },
        )

    citations: list[dict[str, Any]] = [
        {
            "path": lockfile_path,
            "start_line": lock_line,
            "end_line": lock_line,
            "hash": _short_hash(f"{adv['pkg']}=={adv['affected_ver']}:{lock_line}", 32),
        }
    ]
    if directly_imported:
        citations.append(
            {
                "path": adapter_path,
                "start_line": import_edge_line,
                "end_line": import_edge_line,
                "hash": _short_hash(f"import {adv['import_mod']}:{import_edge_line}", 32),
            }
        )

    catalogue_match: dict[str, Any] = {
        "entry_id": adv["entry_id"],
        "cve_id": adv["cve"],
        "package": adv["pkg"],
        "installed_version": adv["affected_ver"],
        "fixed_version": adv["fixed_ver"],
        "entry_revision": 1,
        "kind": "advisory",
        "status": "matched",
        "directly_imported": directly_imported,
        "sink_symbol": f"{adv['import_mod']}.{adv['sink_api']}",
        "sink_reachable": None,
        "cwe": adv["cwe"],
        "citations": citations,
        "limitations": [
            "A version match establishes affected-version applicability, not exploitability.",
            "Runtime sink reachability is null (unknown) and must never be treated as false.",
        ],
    }

    communities_touched = len({s["community"] for s in focal_symbols})
    dep_files = [entry_path, adapter_path, helper_path, lockfile_path]
    basis = (
        f"Computed from {len(edges)} graph edges ({sum(1 for e in edges if e['confidence'] == 'EXTRACTED')} EXTRACTED, "
        f"{sum(1 for e in edges if e['confidence'] == 'INFERRED')} INFERRED) across {communities_touched} communities "
        f"and {len(linked_tests)} linked test files."
    )

    gaps = [
        f"Runtime call-chain reachability to sink `{adv['import_mod']}.{adv['sink_api']}` is unverified (`sink_reachable: null`).",
        f"Dynamic dispatch edge `{adapter_fn} -> {helper_fn}` at `{adapter_path}:{inferred_edge_line}` is `INFERRED` (`suggestion_only: true`).",
    ]
    if not directly_imported:
        gaps.append(
            f"No top-level AST import of `{adv['pkg']}` detected in `{adapter_path}` (`directly_imported: false`), "
            "leaving transitive or dynamic invocation paths unverified."
        )

    graph_ctx: dict[str, Any] = {
        "scope": {
            "repo_id": repo_id,
            "snapshot_id": snapshot_id,
            "engine": "graphify",
            "engine_version": "0.9.108",
            "graph_hash": _short_hash(f"{repo_id}:{snapshot_id}:{idx}", 32),
        },
        "focal_symbols": focal_symbols,
        "edges": edges,
        "blast_radius": {
            "dependent_files": dep_files,
            "communities_touched": communities_touched,
            "linked_test_files": linked_tests,
            "structural_risk": structural_risk,
            "basis": basis,
        },
        "catalogue_matches": [catalogue_match],
        "approved_knowledge": [
            {
                "id": "know_" + _short_hash(f"{repo_id}:policy", 8),
                "title": "Duck Tape Null-Safety & Worktree Verification Policy",
                "content": (
                    "Treat null reachability fields as unknown (not shown reachable); distinguish EXTRACTED vs "
                    "INFERRED edges; execute blast_radius.linked_test_files in an isolated Git worktree."
                ),
                "scope": entry_path,
                "version": 1,
            }
        ],
        "gaps": gaps,
    }

    graph_json = json.dumps(graph_ctx, ensure_ascii=False, separators=(",", ":"))

    prompt = (
        "Produce a **Duck Tape Executive Graph & Security Triage Report** for the repository snapshot and "
        "`<graph_context>` v1 payload below.\n\n"
        f"Repository: `{repo}` (service `{svc}` — {domain})\n"
        f"Snapshot ID: `{snapshot_id}` | Advisory: `{adv['entry_id']}` (`{adv['cve']}`, `{adv['cwe']}`)\n"
        f"Pinned Lockfile Entry (`{lockfile_path}:{lock_line}`): `{adv['pkg']}=={adv['affected_ver']}` "
        f"(patched in `>={adv['fixed_ver']}`)\n"
        f"Advisory Summary: {adv['summary']}\n\n"
        "Required Report Structure:\n"
        "1. `## 1. Executive Summary & Triage Decision`\n"
        "2. `## 2. Lockfile Applicability & Graph Reachability Table` (contrast `directly_imported` evidence and explicitly state `\"not shown reachable\"` when `sink_reachable: null`)\n"
        "3. `## 3. Structural Blast Radius & Call-Graph Evidence` (distinguish `EXTRACTED` vs `INFERRED` `suggestion_only: true` edges and cite `focal_symbols[].signature`)\n"
        "4. `## 4. Separate Confidence vs. Risk Matrix`\n"
        "5. `## 5. Isolated Worktree Verification Plan` (include exact `pytest` commands for `blast_radius.linked_test_files`)\n\n"
        f"<graph_context>\n{graph_json}\n</graph_context>"
    )

    if directly_imported:
        import_status_cell = f"`true` (`{adapter_path}:{import_edge_line}` imports `{adv['import_mod']}`)"
        import_bullet = (
            f"- **Direct Import (`EXTRACTED`):** `file:{adapter_path}` -> `{node_adapter}` "
            f"(`imports_from`, `suggestion_only: false`) at `{adapter_path}:{import_edge_line}`."
        )
        triage_action = (
            f"**ESCALATE FOR CALL-PATH AUDIT & LOCKFILE UPGRADE** — `{adv['pkg']}=={adv['affected_ver']}` is "
            f"pinned (`{lockfile_path}:{lock_line}`) and directly imported (`{adapter_path}:{import_edge_line}`), "
            f"while sink `{adv['import_mod']}.{adv['sink_api']}` has `sink_reachable: null` (**not shown reachable**)."
        )
    else:
        import_status_cell = f"`false` (no static import of `{adv['pkg']}` in `{adapter_path}`)"
        import_bullet = (
            f"- **Direct Import Status:** `directly_imported: false` — no static `imports_from` edge in "
            f"`{adapter_path}:{adapter_line}-{adapter_end_line}`; dynamic loading via `{helper_path}:{helper_line}` is unverified."
        )
        triage_action = (
            f"**RETAIN FINDING & SCHEDULE LOCKFILE BUMP** — `{adv['pkg']}=={adv['affected_ver']}` "
            f"is pinned (`{lockfile_path}:{lock_line}`) with `directly_imported: false`, and sink "
            f"`{adv['import_mod']}.{adv['sink_api']}` has `sink_reachable: null` (**not shown reachable**)."
        )

    completion = (
        "## 1. Executive Summary & Triage Decision\n"
        f"- **Verdict:** {triage_action}\n"
        f"- **Null-Safety Rule:** In `<graph_context>` (`{snapshot_id}`), `sink_reachable` is `null` (unknown, never `false`). "
        f"Sink `{adv['import_mod']}.{adv['sink_api']}` is **not shown reachable** from `{entry_path}:{entry_line}`; "
        f"keep `{adv['entry_id']}` (`{adv['cve']}`) active.\n\n"
        "## 2. Lockfile Applicability & Graph Reachability Table\n"
        "| Advisory / CVE | Lockfile Citation | `directly_imported` | `sink_reachable` | Interpretation |\n"
        "| :--- | :--- | :--- | :--- | :--- |\n"
        f"| `{adv['entry_id']}` (`{adv['cve']}`, `{adv['cwe']}`) | `{lockfile_path}:{lock_line}` (`{adv['pkg']}=={adv['affected_ver']}`, fix `>={adv['fixed_ver']}`) | {import_status_cell} | `null` | **not shown reachable** — version match proves applicability only |\n\n"
        "## 3. Structural Blast Radius & Call-Graph Evidence\n"
        f"- **Entrypoint Symbol:** `{entry_path}:{entry_line}-{entry_end_line}` (`{node_entry}`, `{entry_sig}`).\n"
        f"- **Adapter Symbol:** `{adapter_path}:{adapter_line}-{adapter_end_line}` (`{node_adapter}`, `{adapter_sig}`).\n"
        f"- **Helper Symbol:** `{helper_path}:{helper_line}-{helper_end_line}` (`{node_helper}`, `{helper_sig}`).\n"
        f"- **Call Edge (`EXTRACTED`):** `{node_entry}` -> `{node_adapter}` (`calls`, `suggestion_only: false`) at `{entry_path}:{call_edge_line}`.\n"
        f"{import_bullet}\n"
        f"- **Heuristic Edge (`INFERRED`):** `{node_adapter}` -> `{node_helper}` (`calls`, `confidence: INFERRED`, `suggestion_only: true`) at `{adapter_path}:{inferred_edge_line}`.\n"
        f"- **Test Edge (`EXTRACTED`):** `{node_test}` -> `{node_entry}` (`tests`, `suggestion_only: false`) at `{test_file_1}:{test_edge_line}`.\n\n"
        "## 4. Separate Confidence vs. Risk Matrix\n"
        "| Dimension | Rating | Grounded Basis in `<graph_context>` |\n"
        "| :--- | :--- | :--- |\n"
        f"| **Evidence Confidence** | **Mixed (`EXTRACTED` + `INFERRED` / `null`)** | `{lockfile_path}:{lock_line}` and `{entry_path}:{call_edge_line}` are `EXTRACTED`; `{adapter_path}:{inferred_edge_line}` is `INFERRED` (`suggestion_only: true`) and `sink_reachable: null` (**not shown reachable**). |\n"
        f"| **Structural & Security Risk** | **`{structural_risk.upper()}` (`{adv['cwe']}`)** | Blast radius spans `{len(dep_files)}` files across `{communities_touched}` communities (`{entry_path}`, `{adapter_path}`, `{helper_path}`). |\n\n"
        "## 5. Isolated Worktree Verification Plan\n"
        f"1. **Worktree:** Checkout `{snapshot_id}` in an isolated Git worktree before editing `{lockfile_path}:{lock_line}` or `{adapter_path}:{adapter_line}`.\n"
        f"2. **Baseline (`regression-before`):** Run `blast_radius.linked_test_files`:\n"
        f"   - `pytest -q {test_file_1} {test_file_2}`\n"
        f"   - `pytest -q {test_file_2} -k \"test_{svc}\"`\n"
        f"3. **Remediation (`after`):** Upgrade `{adv['pkg']}` to `>={adv['fixed_ver']}` in `{lockfile_path}:{lock_line}` and re-run `pytest -q {test_file_1} {test_file_2}`."
    )

    return {"prompt": prompt, "completion": completion}


def main() -> None:
    t0 = time.perf_counter()
    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    EVAL_PATH.parent.mkdir(parents=True, exist_ok=True)

    rows = [build_row(i) for i in range(TOTAL_ROWS)]
    with OUTPUT_PATH.open("w", encoding="utf-8", newline="\n") as fh:
        for row in rows:
            fh.write(json.dumps(row, ensure_ascii=False) + "\n")

    eval_rows = [build_row(i) for i in range(TOTAL_ROWS, TOTAL_ROWS + EVAL_ROWS)]
    with EVAL_PATH.open("w", encoding="utf-8", newline="\n") as fh:
        for row in eval_rows:
            fh.write(json.dumps(row, ensure_ascii=False) + "\n")

    elapsed = time.perf_counter() - t0
    word_counts = [len(r["completion"].split()) for r in rows]
    min_words = min(word_counts)
    max_words = max(word_counts)
    avg_words = sum(word_counts) / len(word_counts)

    print(
        f"Generated {len(rows)} train rows + {len(eval_rows)} eval rows in {elapsed:.2f}s | "
        f"completion words: min={min_words}, avg={avg_words:.1f}, max={max_words}"
    )
    print(REL_OUTPUT_PATH)
    print(REL_EVAL_PATH)


if __name__ == "__main__":
    main()
