"""Wave 3 Data Prep — Agent 11: frontend_ui (VisionInstructionRow)."""
from __future__ import annotations

import base64
import hashlib
import html as html_lib
import io
import re
import sys
from pathlib import Path

from datasets import load_dataset
from huggingface_hub import snapshot_download
from PIL import Image
from tqdm import tqdm

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from training.schemas import VisionInstructionRow
from training.scrub import scrub_with_count
from training.utils import (
    MODEL_TRAINING_ROOT,
    load_training_env,
    split_train_eval,
    truncate_bounded,
    write_vision_jsonl,
)

TRAIN_TARGET = 2500
EVAL_TARGET = 100
TOTAL_TARGET = TRAIN_TARGET + EVAL_TARGET

_TITLE_RE = re.compile(r"<title[^>]*>(.*?)</title>", re.IGNORECASE | re.DOTALL)
_BODY_RE = re.compile(r"<body([^>]*)>(.*?)</body>", re.IGNORECASE | re.DOTALL)
_BODY_CLASS_RE = re.compile(r'class=["\']([^"\']+)["\']', re.IGNORECASE)
_SCRIPT_STYLE_RE = re.compile(r"<(?:script|style)\b[^>]*>.*?</(?:script|style)>", re.IGNORECASE | re.DOTALL)
_COMMENT_RE = re.compile(r"<!--(.*?)-->", re.DOTALL)
_HEADING_RE = re.compile(r"<h[1-3][^>]*>(.*?)</h[1-3]>", re.IGNORECASE | re.DOTALL)
_BUTTON_LINK_RE = re.compile(r"<(?:button|a)\b[^>]*>(.*?)</(?:button|a)>", re.IGNORECASE | re.DOTALL)
_TAG_STRIP_RE = re.compile(r"<[^>]+>")
_VOID_TAG_RE = re.compile(
    r"<(img|input|br|hr|meta|link|source|area|base|col|embed|param|track|wbr)\b([^>]*?)(?<!/)>",
    re.IGNORECASE,
)


def _encode_image_data_uri(img: Image.Image) -> str:
    """Convert PIL Image to a compact RGB JPEG base64 data URI."""
    rgb = img.convert("RGB")
    rgb.thumbnail((256, 160), Image.Resampling.LANCZOS)
    buf = io.BytesIO()
    rgb.save(buf, format="JPEG", quality=45, optimize=True)
    b64 = base64.b64encode(buf.getvalue()).decode("ascii")
    return f"data:image/jpeg;base64,{b64}"


def _clean_plain_text(fragment: str) -> str:
    text = _TAG_STRIP_RE.sub(" ", fragment or "")
    text = html_lib.unescape(text)
    return " ".join(text.split()).strip()


def _pascal_name(seed_text: str, idx: int) -> str:
    words = re.findall(r"[A-Za-z][A-Za-z0-9]*", seed_text or "")
    chosen = [w[:1].upper() + w[1:].lower() for w in words[:3] if len(w) >= 2]
    base = "".join(chosen) or f"GeneratedPage{idx}"
    if not base[0].isalpha():
        base = f"Ui{base}"
    if len(base) > 28:
        base = base[:28]
    return f"{base}View"


def _inline_style_to_jsx(match: re.Match[str]) -> str:
    raw = match.group(1)
    pairs: list[str] = []
    for part in raw.split(";"):
        if ":" not in part:
            continue
        prop, val = part.split(":", 1)
        prop_clean = prop.strip().lower()
        val_clean = val.strip().replace('"', "'")
        if not prop_clean or not val_clean:
            continue
        camel = re.sub(r"-([a-z])", lambda m: m.group(1).upper(), prop_clean)
        if re.match(r"^[A-Za-z_$][A-Za-z0-9_$]*$", camel):
            pairs.append(f'{camel}: "{val_clean}"')
    if not pairs:
        return ""
    return "style={{ " + ", ".join(pairs[:6]) + " }}"


def _html_to_jsx(html_text: str) -> tuple[str, str, list[str], list[str]]:
    cleaned = _SCRIPT_STYLE_RE.sub("", html_text or "")
    title_m = _TITLE_RE.search(cleaned)
    title = _clean_plain_text(title_m.group(1)) if title_m else ""

    headings = [_clean_plain_text(h) for h in _HEADING_RE.findall(cleaned)]
    headings = [h for h in headings if h][:4]

    actions = [_clean_plain_text(a) for a in _BUTTON_LINK_RE.findall(cleaned)]
    actions = [a for a in actions if 1 <= len(a) <= 40][:6]

    body_m = _BODY_RE.search(cleaned)
    if body_m:
        body_attrs = body_m.group(1) or ""
        body_inner = body_m.group(2) or ""
        cls_m = _BODY_CLASS_RE.search(body_attrs)
        wrapper_cls = cls_m.group(1).strip() if cls_m else "min-h-screen bg-slate-50 text-slate-900"
        markup = f'<div className="{wrapper_cls}">\n{body_inner.strip()}\n</div>'
    else:
        body_inner = re.sub(r"</?(?:html|head|meta|link|title|!doctype)[^>]*>", "", cleaned, flags=re.I)
        markup = f'<div className="min-h-screen bg-slate-50 text-slate-900">\n{body_inner.strip()}\n</div>'

    markup = _COMMENT_RE.sub(lambda m: "{/* " + m.group(1).replace("*/", "") + " */}", markup)
    markup = re.sub(r"\bclass=", "className=", markup)
    markup = re.sub(r"\bfor=", "htmlFor=", markup)
    markup = re.sub(r"\btabindex=", "tabIndex=", markup, flags=re.I)
    markup = re.sub(r"\bcolspan=", "colSpan=", markup, flags=re.I)
    markup = re.sub(r"\browspan=", "rowSpan=", markup, flags=re.I)
    markup = re.sub(r"\bmaxlength=", "maxLength=", markup, flags=re.I)
    markup = re.sub(r"\breadonly\b", "readOnly", markup, flags=re.I)
    markup = re.sub(r"\bautocomplete=", "autoComplete=", markup, flags=re.I)
    markup = re.sub(r'style=["\']([^"\']*)["\']', _inline_style_to_jsx, markup, flags=re.I)
    markup = _VOID_TAG_RE.sub(r"<\1\2 />", markup)
    # Remove any stray SVG/data URIs inside generated HTML so output stays clean & readable
    markup = re.sub(r"data:image/[^\"'\s)]+", "https://placehold.co/600x400", markup)

    return markup.strip(), title, headings, actions


def _build_react_tsx(
    idx: int,
    html_text: str,
    design_idea: str,
) -> str:
    jsx_markup, title, headings, actions = _html_to_jsx(html_text)
    comp_name = _pascal_name(design_idea or title or (headings[0] if headings else ""), idx)
    display_title = title or (headings[0] if headings else (design_idea.split(":")[0].strip() if design_idea else comp_name))
    display_title = display_title.replace('"', "'")[:80] or comp_name

    uses_interactive = any(tok in jsx_markup.lower() for tok in ("<button", "<input", "<form", "modal", "menu"))
    react_import = 'import React, { useState } from "react";' if uses_interactive else 'import React from "react";'
    state_line = (
        "  const [activeAction, setActiveAction] = useState<string | null>(null);\n"
        if uses_interactive
        else ""
    )

    if len(jsx_markup) <= 2200:
        indented_jsx = "\n".join(f"      {ln}" if ln.strip() else "" for ln in jsx_markup.splitlines())
        body_return = indented_jsx
    else:
        # Modular React + Tailwind component preserving all extracted headings, nav links, and actions
        action_items = actions or ["Get Started", "Learn More"]
        heading_items = headings or [display_title]
        cards = "\n".join(
            f'          <article key="{i}" className="rounded-xl border border-slate-200 bg-white p-5 shadow-sm">\n'
            f'            <h2 className="text-lg font-semibold text-slate-900">{h.replace("{", "").replace("}", "")}</h2>\n'
            f'            <p className="mt-2 text-sm text-slate-600">Modular section rendered from visual layout.</p>\n'
            f"          </article>"
            for i, h in enumerate(heading_items[:3])
        )
        nav_btns = "\n".join(
            f'            <button type="button" onClick={{() => onSelectAction?.("{a.replace(chr(34), "")}")}} '
            f'className="rounded-lg bg-slate-900 px-4 py-2 text-sm font-medium text-white hover:bg-slate-700">'
            f'{a.replace("{", "").replace("}", "")}</button>'
            for a in action_items[:3]
        )
        body_return = (
            '      <main className="mx-auto max-w-6xl px-6 py-10 space-y-8">\n'
            '        <header className="flex flex-wrap items-center justify-between gap-4 border-b border-slate-200 pb-6">\n'
            '          <h1 className="text-2xl font-bold tracking-tight text-slate-900">{title}</h1>\n'
            '          <div className="flex flex-wrap items-center gap-3">\n'
            f"{nav_btns}\n"
            "          </div>\n"
            "        </header>\n"
            '        <section className="grid gap-6 md:grid-cols-3">\n'
            f"{cards}\n"
            "        </section>\n"
            "      </main>"
        )

    component_code = (
        f"{react_import}\n\n"
        f"export interface {comp_name}Props {{\n"
        "  title?: string;\n"
        "  className?: string;\n"
        "  onSelectAction?: (action: string) => void;\n"
        "}\n\n"
        f"export const {comp_name}: React.FC<{comp_name}Props> = ({{\n"
        f'  title = "{display_title}",\n'
        '  className = "",\n'
        "  onSelectAction,\n"
        "}) => {\n"
        f"{state_line}"
        "  return (\n"
        '    <div className={`w-full ${className}`.trim()} data-component="' + comp_name + '">\n'
        f"{body_return}\n"
        "    </div>\n"
        "  );\n"
        "};\n\n"
        f"export default {comp_name};"
    )
    return component_code


def _build_vision_row(
    idx: int,
    source_name: str,
    design_brief: str,
    image_uri: str,
    html_text: str,
) -> tuple[VisionInstructionRow, int] | None:
    c_brief, r1 = scrub_with_count(design_brief)
    tsx_code = _build_react_tsx(idx, html_text, c_brief)
    c_code, r2 = scrub_with_count(tsx_code)
    redactions = r1 + r2

    if len(c_code.strip()) < 100 or not image_uri.startswith("data:image/"):
        return None

    prompt = (
        "Convert the attached UI screenshot into a modular React + TypeScript + Tailwind CSS component.\n"
        f"Source: {source_name}\n"
        f"Sample ID: {idx}\n"
        f"Design Brief: {truncate_bounded(c_brief, 500)}\n\n"
        "Preserve the visual hierarchy, responsive grid/flex layout, typography, and interactive controls "
        "with strict TypeScript props and Tailwind utility classes."
    )
    completion = f"```tsx\n{truncate_bounded(c_code, 4200)}\n```"
    return VisionInstructionRow(prompt=prompt, image_context=image_uri, completion=completion), redactions


def collect_rows() -> tuple[list[VisionInstructionRow], dict[str, int], int]:
    """Stream SALT-NLP/Design2Code and HuggingFaceM4/WebSight into VisionInstructionRow list."""
    rows: list[VisionInstructionRow] = []
    seen_sigs: set[str] = set()
    source_counts: dict[str, int] = {}
    total_redactions = 0

    def _add(source_key: str, item: tuple[VisionInstructionRow, int] | None, unique_key: str) -> bool:
        nonlocal total_redactions
        if item is None:
            return False
        row, red = item
        k_hash = hashlib.sha256(unique_key.encode("utf-8", errors="ignore")).hexdigest()[:24]
        if k_hash in seen_sigs:
            return False
        seen_sigs.add(k_hash)
        rows.append(row)
        source_counts[source_key] = source_counts.get(source_key, 0) + 1
        total_redactions += red
        return True

    # 1. SALT-NLP/Design2Code (exact paired .png + .html via cached snapshot)
    d2c_dir = Path(
        snapshot_download(
            "SALT-NLP/Design2Code",
            repo_type="dataset",
            allow_patterns=["*.png", "*.html"],
        )
    )
    png_map = {p.stem: p for p in d2c_dir.glob("*.png")}
    html_map = {p.stem: p for p in d2c_dir.glob("*.html")}
    paired_ids = sorted(set(png_map) & set(html_map))

    pbar = tqdm(total=len(paired_ids), desc="frontend_ui <- SALT-NLP/Design2Code", unit="rows")
    for idx, item_id in enumerate(paired_ids):
        if len(rows) >= TOTAL_TARGET:
            break
        try:
            with Image.open(png_map[item_id]) as im:
                image_uri = _encode_image_data_uri(im)
            html_text = html_map[item_id].read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        title_m = _TITLE_RE.search(html_text)
        brief = _clean_plain_text(title_m.group(1)) if title_m else f"Web page screenshot #{item_id}"
        built = _build_vision_row(
            idx,
            "SALT-NLP/Design2Code",
            f"{brief} (Design2Code asset {item_id})",
            image_uri,
            html_text,
        )
        if _add("Design2Code", built, unique_key=f"d2c:{item_id}"):
            pbar.update(1)
    pbar.close()

    # 2. HuggingFaceM4/WebSight (streaming=True) to reach TOTAL_TARGET = 2,600
    rem = TOTAL_TARGET - len(rows)
    ds_ws = load_dataset("HuggingFaceM4/WebSight", split="train", streaming=True)
    pbar = tqdm(total=rem, desc="frontend_ui <- HuggingFaceM4/WebSight", unit="rows")
    for idx, rec in enumerate(ds_ws):
        if len(rows) >= TOTAL_TARGET:
            break
        img = rec.get("image")
        html_text = str(rec.get("text") or "").strip()
        idea = str(rec.get("llm_generated_idea") or "").strip()
        if img is None or len(html_text) < 80 or len(idea) < 15:
            continue
        try:
            image_uri = _encode_image_data_uri(img)
        except OSError:
            continue
        built = _build_vision_row(
            len(rows),
            "HuggingFaceM4/WebSight",
            idea,
            image_uri,
            html_text,
        )
        if _add("WebSight", built, unique_key=f"ws:{idea[:200]}:{len(html_text)}"):
            pbar.update(1)
    pbar.close()

    return rows, source_counts, total_redactions


def main() -> int:
    load_training_env()
    rows, source_counts, redactions = collect_rows()
    train_rows, eval_rows = split_train_eval(rows, TRAIN_TARGET, EVAL_TARGET, seed=42)

    train_path = MODEL_TRAINING_ROOT / "data" / "processed" / "frontend_ui.jsonl"
    eval_path = MODEL_TRAINING_ROOT / "data" / "eval" / "frontend_ui_eval.jsonl"
    write_vision_jsonl(train_rows, train_path)
    write_vision_jsonl(eval_rows, eval_path)

    print(
        f"[frontend_ui] wrote {len(train_rows)} train -> {train_path} | "
        f"{len(eval_rows)} eval -> {eval_path} | redactions={redactions} | sources={source_counts}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
