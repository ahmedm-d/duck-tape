"""Wave 3 Data Prep — Agent 10: iac (InstructionRow)."""
from __future__ import annotations

import hashlib
import re
import sys
from pathlib import Path

from datasets import load_dataset
from tqdm import tqdm

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from training.schemas import InstructionRow
from training.scrub import scrub_with_count
from training.utils import (
    MODEL_TRAINING_ROOT,
    load_training_env,
    split_train_eval,
    truncate_bounded,
    write_instruction_jsonl,
)

TRAIN_TARGET = 5000
EVAL_TARGET = 200
TOTAL_TARGET = TRAIN_TARGET + EVAL_TARGET

REQUIRED_HEADINGS = (
    "### Least-Privilege IAM & Network Security Notes",
    "### Infrastructure Configuration",
)

_K8S_INST_RE = re.compile(r"^(?:<s>)?\[INST\](.*?)\[/INST\](.*?)(?:</s>)?$", re.DOTALL)
_YAML_KIND_RE = re.compile(r"^\s*kind:\s*([A-Za-z0-9_-]+)", re.MULTILINE)
_YAML_NAME_RE = re.compile(r"^\s*name:\s*([A-Za-z0-9._-]+)", re.MULTILINE)
_YAML_NS_RE = re.compile(r"^\s*namespace:\s*([A-Za-z0-9._-]+)", re.MULTILINE)
_DF_FROM_RE = re.compile(r"^\s*FROM\s+([^\s]+)", re.MULTILINE | re.IGNORECASE)
_DF_EXPOSE_RE = re.compile(r"^\s*EXPOSE\s+([0-9/tcpud\s]+)", re.MULTILINE | re.IGNORECASE)


def _security_notes_terraform(code: str, resource: str, rego_intent: str = "") -> str:
    low = code.lower()
    iam_bullet = (
        "- **Least-Privilege IAM:** Scope IAM policy `Action` and `Resource` ARNs strictly to the target "
        f"`{resource or 'declared resource'}` instead of wildcard `*` principals or permissions."
    )
    if "iam" in low or "policy" in low or "role" in low:
        iam_bullet = (
            "- **Least-Privilege IAM:** Bind `aws_iam_role` / `aws_iam_policy` statements strictly to specific "
            f"resource ARNs and required actions for `{resource or 'this stack'}`, avoiding `Action = \"*\"`."
        )
    net_bullet = (
        "- **Network & Encryption Hardening:** Enforce private VPC/subnet placement, deny `0.0.0.0/0` ingress on "
        "sensitive ports, and enable encryption at rest/in transit."
    )
    if "security_group" in low or "ingress" in low or "cidr" in low:
        net_bullet = (
            "- **Network Isolation:** Restrict ingress/egress `cidr_blocks` to explicit internal CIDR ranges or security "
            "group IDs rather than `0.0.0.0/0`, and enforce TLS on exposed endpoints."
        )
    rego_line = ""
    if rego_intent.strip():
        rules = [ln.strip() for ln in rego_intent.splitlines() if ln.strip().startswith(("default ", "is_"))][:3]
        if rules:
            rego_line = f" (Rego guardrails: `{'; '.join(rules)}`)"
    state_bullet = (
        f"- **Terraform Safety & Policy Check:** Pin provider/module versions, keep secrets out of `.tf` variables, "
        f"and run `terraform plan` + OPA/Rego validation before apply{rego_line}."
    )
    return "\n".join((iam_bullet, net_bullet, state_bullet))


def _security_notes_k8s(code: str, kind: str, name: str) -> str:
    low = code.lower()
    rbac_bullet = (
        f"- **Least-Privilege RBAC & Workload Identity:** Bind `{kind}/{name}` to a dedicated `ServiceAccount` "
        "(`automountServiceAccountToken: false` unless API access is required) and avoid `cluster-admin` or `*` verbs."
    )
    pod_bullet = (
        "- **Pod & Container Hardening:** Set `securityContext.runAsNonRoot: true`, `allowPrivilegeEscalation: false`, "
        "`readOnlyRootFilesystem: true`, `capabilities.drop: [\"ALL\"]`, and explicit CPU/memory limits."
    )
    if "clusterrole" in low or "rolebinding" in low or kind.lower() in ("role", "clusterrole", "rolebinding"):
        rbac_bullet = (
            f"- **Least-Privilege RBAC:** Restrict `{kind}/{name}` `rules[].apiGroups`, `resources`, and `verbs` "
            "exclusively to the minimum required namespace actions (no wildcard `*` verbs or secrets read across namespaces)."
        )
    net_bullet = (
        "- **NetworkPolicy & Secrets Hygiene:** Pair services/ingresses with a default-deny `NetworkPolicy` permitting "
        "only explicit pod selectors/ports, and source credentials from mounted external Secrets rather than inline YAML."
    )
    return "\n".join((rbac_bullet, pod_bullet, net_bullet))


def _security_notes_dockerfile(code: str, base_image: str) -> str:
    low = code.lower()
    user_bullet = (
        "- **Non-Root Execution & Least-Privilege Runtime:** Create and switch to an unprivileged `USER` "
        "(e.g., `USER 10001:10001`) before `ENTRYPOINT`/`CMD`, and drop setuid binaries where unneeded."
    )
    if re.search(r"^\s*USER\s+(?!root\b|0\b)\S+", code, re.MULTILINE | re.IGNORECASE):
        user_bullet = (
            "- **Non-Root Execution Verified:** Maintain the explicit non-root `USER` directive and avoid `sudo` or "
            "writable root-owned runtime directories."
        )
    supply_bullet = (
        f"- **Supply-Chain & Image Pinning:** Pin base image `{base_image}` to an immutable version tag or SHA-256 "
        "digest, clean package manager caches (`rm -rf /var/lib/apt/lists/*`), and use multi-stage builds to omit build toolchains."
    )
    net_bullet = (
        "- **Secrets & Network Exposure:** Never embed API keys or credentials via `ENV`/`ARG`/`COPY`; expose only "
        "required application ports and pass runtime secrets via orchestrator mounts."
    )
    if "arg " in low or "env " in low:
        net_bullet = (
            "- **Secrets & Network Exposure:** Keep `ARG`/`ENV` strictly for non-sensitive build/runtime config "
            "(use BuildKit `--mount=type=secret` for private registries) and expose only minimal service ports."
        )
    return "\n".join((user_bullet, supply_bullet, net_bullet))


def _build_iac_row(
    source_name: str,
    stack: str,
    code_lang: str,
    target_summary: str,
    task_desc: str,
    config_code: str,
    security_notes: str,
) -> tuple[InstructionRow, int] | None:
    c_target, r1 = scrub_with_count(target_summary)
    c_task, r2 = scrub_with_count(task_desc)
    c_code, r3 = scrub_with_count(config_code)
    c_notes, r4 = scrub_with_count(security_notes)
    redactions = r1 + r2 + r3 + r4

    if len(c_code.strip()) < 35 or len(c_task.strip()) < 15:
        return None

    prompt = (
        "Generate a hardened Infrastructure-as-Code configuration with least-privilege IAM and network security notes.\n"
        f"Source: {source_name}\n"
        f"IaC Stack: {stack}\n"
        f"Target Scope: {truncate_bounded(c_target, 280)}\n\n"
        f"Specification:\n{truncate_bounded(c_task, 1400)}\n\n"
        "Respond with Markdown containing:\n"
        "1. `### Least-Privilege IAM & Network Security Notes`\n"
        f"2. `### Infrastructure Configuration` (a valid ```{code_lang} code block)"
    )
    completion = (
        f"### Least-Privilege IAM & Network Security Notes\n{c_notes}\n\n"
        f"### Infrastructure Configuration\n```{code_lang}\n{truncate_bounded(c_code, 2600)}\n```"
    )
    return InstructionRow(prompt=prompt, completion=completion), redactions


def _clean_k8s_record(raw_text: str) -> tuple[str, str]:
    text = (raw_text or "").strip()
    m = _K8S_INST_RE.match(text)
    if m:
        inst = m.group(1).strip()
        yaml_body = m.group(2).strip()
    else:
        inst = "Generate a Kubernetes YAML resource configuration."
        yaml_body = text.removeprefix("<s>").removesuffix("</s>").strip()
    return inst, yaml_body


def collect_rows() -> tuple[list[InstructionRow], dict[str, int], int]:
    """Stream IaC-Eval, commitpackft HCL/Dockerfile/YAML, Kubernetes, and Dockerfiles."""
    rows: list[InstructionRow] = []
    seen_sigs: set[str] = set()
    source_counts: dict[str, int] = {}
    total_redactions = 0

    def _add(source_key: str, item: tuple[InstructionRow, int] | None, unique_key: str = "") -> bool:
        nonlocal total_redactions
        if item is None:
            return False
        row, red = item
        p_hash = hashlib.sha256(row.prompt.encode("utf-8", errors="ignore")).hexdigest()[:24]
        k_hash = hashlib.sha256(unique_key.encode("utf-8", errors="ignore")).hexdigest()[:24] if unique_key else p_hash
        if p_hash in seen_sigs or k_hash in seen_sigs:
            return False
        seen_sigs.add(p_hash)
        seen_sigs.add(k_hash)
        rows.append(row)
        source_counts[source_key] = source_counts.get(source_key, 0) + 1
        total_redactions += red
        return True

    # 1. autoiac-project/iac-eval (all valid rows in test split)
    ds_iac_eval = load_dataset("autoiac-project/iac-eval", split="test", streaming=True)
    pbar = tqdm(total=458, desc="iac <- autoiac-project/iac-eval", unit="rows")
    for idx, rec in enumerate(ds_iac_eval):
        resource = str(rec.get("Resource") or "aws_resource").strip()
        task_prompt = str(rec.get("Prompt") or "").strip()
        intent = str(rec.get("Intent") or "").strip()
        rego = str(rec.get("Rego intent") or "").strip()
        difficulty = str(rec.get("Difficulty") or "")
        ref_code = str(rec.get("Reference output") or "").strip()
        spec = task_prompt
        if intent:
            spec += f"\n\nExpected Resource Intent:\n{truncate_bounded(intent, 400)}"
        notes = _security_notes_terraform(ref_code, resource, rego_intent=rego)
        built = _build_iac_row(
            "autoiac-project/iac-eval",
            "Terraform (.tf)",
            "hcl",
            f"Resources: {resource} | Difficulty: {difficulty}",
            spec,
            ref_code,
            notes,
        )
        if _add("iac_eval", built, unique_key=f"iaceval:{idx}:{task_prompt[:180]}"):
            pbar.update(1)
    pbar.close()

    # 2. bigcode/commitpackft HCL & Dockerfile subsets
    for lang, stack, code_lang, quota in (
        ("hcl", "Terraform (.tf)", "hcl", 420),
        ("dockerfile", "Dockerfile", "dockerfile", 40),
    ):
        ds_cp = load_dataset(
            "json",
            data_files=f"hf://datasets/bigcode/commitpackft/data/{lang}/data.jsonl",
            split="train",
            streaming=True,
        )
        pbar = tqdm(total=quota, desc=f"iac <- commitpackft/{lang}", unit="rows")
        for rec in ds_cp:
            if source_counts.get(f"commitpackft_{lang}", 0) >= quota:
                break
            new_file = str(rec.get("new_file") or rec.get("old_file") or f"main.{lang}")
            old_c = str(rec.get("old_contents") or "")
            new_c = str(rec.get("new_contents") or "")
            msg = str(rec.get("message") or rec.get("subject") or "").strip()
            repo = str(rec.get("repos") or "open-source/infra").split(",")[0].strip()
            if len(new_c.strip()) < 35 or len(msg) < 8:
                continue
            spec = f"{msg}\n\nPrevious File Context (`{new_file}`):\n```\n{truncate_bounded(old_c, 600)}\n```" if old_c.strip() else msg
            if lang == "hcl":
                notes = _security_notes_terraform(new_c, new_file)
            else:
                m_from = _DF_FROM_RE.search(new_c)
                notes = _security_notes_dockerfile(new_c, m_from.group(1) if m_from else "base-image")
            built = _build_iac_row(
                f"bigcode/commitpackft ({lang})",
                stack,
                code_lang,
                f"{repo} :: {new_file}",
                spec,
                new_c,
                notes,
            )
            if _add(f"commitpackft_{lang}", built, unique_key=f"cp:{lang}:{repo}:{new_file}:{new_c[:160]}"):
                pbar.update(1)
        pbar.close()

    # 3. HelloBoieeee/kubernetes_config (Kubernetes .yaml manifests)
    k8s_quota = 1800
    ds_k8s = load_dataset("HelloBoieeee/kubernetes_config", split="train", streaming=True)
    pbar = tqdm(total=k8s_quota, desc="iac <- HelloBoieeee/kubernetes_config", unit="rows")
    for idx, rec in enumerate(ds_k8s):
        if source_counts.get("kubernetes_config", 0) >= k8s_quota:
            break
        inst, yaml_body = _clean_k8s_record(str(rec.get("text") or ""))
        if "apiversion:" not in yaml_body.lower() and "kind:" not in yaml_body.lower():
            continue
        kind_m = _YAML_KIND_RE.search(yaml_body)
        name_m = _YAML_NAME_RE.search(yaml_body)
        ns_m = _YAML_NS_RE.search(yaml_body)
        kind = kind_m.group(1) if kind_m else "Resource"
        name = name_m.group(1) if name_m else f"workload-{idx}"
        ns = ns_m.group(1) if ns_m else "default"
        digest = hashlib.sha256(yaml_body.encode("utf-8", errors="ignore")).hexdigest()[:10]
        target = f"kind={kind} | metadata.name={name} | namespace={ns} | spec_hash={digest}"
        spec = (
            f"{inst} Configure Kubernetes `{kind}` named `{name}` in namespace `{ns}` "
            f"(profile `{digest}`) following least-privilege RBAC and Pod Security standards."
        )
        notes = _security_notes_k8s(yaml_body, kind, name)
        built = _build_iac_row(
            "HelloBoieeee/kubernetes_config",
            "Kubernetes (.yaml)",
            "yaml",
            target,
            spec,
            yaml_body,
            notes,
        )
        if _add("kubernetes_config", built, unique_key=f"k8s:{kind}:{name}:{digest}"):
            pbar.update(1)
    pbar.close()

    # 4. bigcode/commitpackft YAML (Kubernetes / Helm / Compose / GitHub Actions IaC commits)
    yaml_quota = 400
    ds_yaml = load_dataset(
        "json",
        data_files="hf://datasets/bigcode/commitpackft/data/yaml/data.jsonl",
        split="train",
        streaming=True,
    )
    pbar = tqdm(total=yaml_quota, desc="iac <- commitpackft/yaml (K8s/Helm/IaC)", unit="rows")
    for checked, rec in enumerate(ds_yaml):
        if source_counts.get("commitpackft_yaml", 0) >= yaml_quota or checked >= 20000:
            break
        new_file = str(rec.get("new_file") or rec.get("old_file") or "")
        new_c = str(rec.get("new_contents") or "")
        old_c = str(rec.get("old_contents") or "")
        msg = str(rec.get("message") or rec.get("subject") or "").strip()
        repo = str(rec.get("repos") or "open-source/k8s").split(",")[0].strip()
        low_all = f"{new_file}\n{new_c}".lower()
        if not any(
            kw in low_all
            for kw in ("apiversion:", "kind:", "deployment", "networkpolicy", "serviceaccount", "helm", "containers:", "services:")
        ):
            continue
        if len(new_c.strip()) < 40 or len(msg) < 10:
            continue
        kind_m = _YAML_KIND_RE.search(new_c)
        name_m = _YAML_NAME_RE.search(new_c)
        kind = kind_m.group(1) if kind_m else "Manifest"
        name = name_m.group(1) if name_m else Path(new_file).stem
        notes = _security_notes_k8s(new_c, kind, name)
        spec = f"{msg}\nTarget manifest path: `{new_file}`."
        if old_c.strip():
            spec += f"\nPrevious snippet:\n```yaml\n{truncate_bounded(old_c, 500)}\n```"
        built = _build_iac_row(
            "bigcode/commitpackft (yaml)",
            "Kubernetes / Infra (.yaml)",
            "yaml",
            f"{repo} :: {new_file}",
            spec,
            new_c,
            notes,
        )
        if _add("commitpackft_yaml", built, unique_key=f"cpyaml:{repo}:{new_file}:{new_c[:160]}"):
            pbar.update(1)
    pbar.close()

    # 5. LeeSek/dockerfiles-linted (validated real-world Dockerfiles)
    df_lint_quota = 1200
    ds_df1 = load_dataset("LeeSek/dockerfiles-linted", split="train", streaming=True)
    pbar = tqdm(total=df_lint_quota, desc="iac <- LeeSek/dockerfiles-linted", unit="rows")
    for rec in ds_df1:
        if source_counts.get("dockerfiles_linted", 0) >= df_lint_quota:
            break
        if rec.get("valid") is False:
            continue
        content = str(rec.get("dockerfile_content") or "").strip()
        image = str(rec.get("image") or "app/service").strip()
        sha = str(rec.get("dockerfile_sha256") or "")[:12]
        if len(content) < 40:
            continue
        m_from = _DF_FROM_RE.search(content)
        base_img = m_from.group(1) if m_from else "debian:bookworm-slim"
        m_exp = _DF_EXPOSE_RE.search(content)
        ports = m_exp.group(1).strip() if m_exp else "none explicitly exposed"
        spec = (
            f"Create a lint-clean production `Dockerfile` for image `{image}` (revision `{sha or 'latest'}`) "
            f"based on `{base_img}` with port exposure `{ports}`."
        )
        notes = _security_notes_dockerfile(content, base_img)
        built = _build_iac_row(
            "LeeSek/dockerfiles-linted",
            "Dockerfile",
            "dockerfile",
            f"Image: {image} | Base: {base_img}",
            spec,
            content,
            notes,
        )
        if _add("dockerfiles_linted", built, unique_key=f"dflint:{image}:{sha}:{content[:140]}"):
            pbar.update(1)
    pbar.close()

    # 6. loubnabnl/dockerfile_checks (top up remaining to TOTAL_TARGET = 5,200)
    if len(rows) < TOTAL_TARGET:
        rem = TOTAL_TARGET - len(rows)
        ds_df2 = load_dataset("loubnabnl/dockerfile_checks", split="train", streaming=True)
        pbar = tqdm(total=rem, desc="iac <- loubnabnl/dockerfile_checks", unit="rows")
        for rec in ds_df2:
            if len(rows) >= TOTAL_TARGET:
                break
            repo = str(rec.get("max_stars_repo_name") or "open-source/container").strip()
            path = str(rec.get("max_stars_repo_path") or "Dockerfile").strip()
            content = str(rec.get("new_content") or rec.get("content") or "").strip()
            if len(content) < 40:
                continue
            m_from = _DF_FROM_RE.search(content)
            base_img = m_from.group(1) if m_from else "alpine:3.19"
            spec = (
                f"Write a hardened `Dockerfile` at `{path}` for repository `{repo}` using base `{base_img}` "
                "with pinned layers and non-root container execution best practices."
            )
            notes = _security_notes_dockerfile(content, base_img)
            built = _build_iac_row(
                "loubnabnl/dockerfile_checks",
                "Dockerfile",
                "dockerfile",
                f"{repo} :: {path}",
                spec,
                content,
                notes,
            )
            if _add("dockerfile_checks", built, unique_key=f"dfchk:{repo}:{path}:{content[:140]}"):
                pbar.update(1)
        pbar.close()

    return rows, source_counts, total_redactions


def main() -> int:
    load_training_env()
    rows, source_counts, redactions = collect_rows()
    train_rows, eval_rows = split_train_eval(rows, TRAIN_TARGET, EVAL_TARGET, seed=42)

    train_path = MODEL_TRAINING_ROOT / "data" / "processed" / "iac.jsonl"
    eval_path = MODEL_TRAINING_ROOT / "data" / "eval" / "iac_eval.jsonl"
    write_instruction_jsonl(train_rows, train_path)
    write_instruction_jsonl(eval_rows, eval_path)

    print(
        f"[iac] wrote {len(train_rows)} train -> {train_path} | "
        f"{len(eval_rows)} eval -> {eval_path} | redactions={redactions} | sources={source_counts}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
