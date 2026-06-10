from __future__ import annotations

import argparse
import base64
import html
import json
import re
import sys
from pathlib import Path
from typing import Any

import fitz

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


def main() -> int:
    parser = argparse.ArgumentParser(description="Generate a task layout HTML report.")
    parser.add_argument("task_dir", type=Path)
    parser.add_argument("--output", type=Path, default=Path(
        "../../storage/tasks/28ff9dfa-7b4f-4d68-be61-56a64cb0b93d/.layout-report/task-layout"))
    args = parser.parse_args()

    task_dir = args.task_dir.resolve()
    output_dir = args.output.resolve()
    output_dir.mkdir(parents=True, exist_ok=True)

    task = _read_json(task_dir / "task.json")
    pages: list[tuple[str, Path]] = []
    for side in ("original", "compare"):
        hybrid_path = _raw_path(task_dir, task, side, "hybrid")
        pdf_path = _upload_pdf(task_dir, side)
        title = _side_title(side, hybrid_path, pdf_path)
        filename = f"{_safe_stem(pdf_path or hybrid_path)}.html"
        report_path = output_dir / filename
        report_path.write_text(
            _document_html(
                title=title,
                side=side,
                payload=_read_json(hybrid_path),
                pdf_path=pdf_path,
            ),
            encoding="utf-8",
        )
        pages.append((title, report_path))

    (output_dir / "index.html").write_text(_index_html(task_dir.name, pages), encoding="utf-8")
    print(output_dir / "index.html")
    for _, path in pages:
        print(path)
    return 0


def _raw_path(task_dir: Path, task: dict[str, Any], side: str, kind: str) -> Path:
    raw_paths = task.get("ocr_raw_result_paths") or {}
    value = (raw_paths.get(side) or {}).get(kind)
    if value:
        return task_dir / value
    matches = sorted((task_dir / "ocr").glob(f"{side}_*_{'ppstructure_ocr_hybrid_raw' if kind == 'hybrid' else kind}.json"))
    if not matches:
        raise FileNotFoundError(f"Cannot find {side} {kind} OCR payload under {task_dir}")
    return matches[0]


def _upload_pdf(task_dir: Path, side: str) -> Path | None:
    matches = sorted((task_dir / "uploads").glob(f"{side}_*.pdf"))
    return matches[0] if matches else None


def _side_title(side: str, hybrid_path: Path, pdf_path: Path | None) -> str:
    label = "Original" if side == "original" else "Compare"
    source = pdf_path.stem if pdf_path else hybrid_path.name.removesuffix("_ppstructure_ocr_hybrid_raw.json")
    return f"{label}: {source}"


def _document_html(title: str, side: str, payload: dict[str, Any], pdf_path: Path | None) -> str:
    document = payload.get("document") or payload
    pages = document.get("pages") or []
    pdf_images = _pdf_images(pdf_path) if pdf_path else {}
    sections = []
    for page in pages:
        page_no = int(page.get("page_no") or 0)
        width = float(page.get("width") or 595)
        height = float(page.get("height") or 842)
        background = (
            f"<image href='data:image/png;base64,{pdf_images[page_no]}' width='{width}' height='{height}'/>"
            if page_no in pdf_images
            else ""
        )
        blocks = "".join(_svg_block(block) for block in page.get("blocks") or [])
        sections.append(
            "<section class='page'>"
            f"<h2>Page {page_no}</h2>"
            f"<svg viewBox='0 0 {width:g} {height:g}'>{background}{blocks}</svg>"
            "</section>"
        )
    return (
        "<!doctype html><html><head><meta charset='utf-8'>"
        f"<title>{html.escape(title)}</title>"
        f"{_style()}{_script()}</head><body>"
        f"<header><h1>{html.escape(title)}</h1><p>{html.escape(side)} layout/OCR hybrid blocks</p>"
        f"{_controls()}</header>"
        f"{''.join(sections)}"
        "</body></html>"
    )


def _svg_block(block: dict[str, Any]) -> str:
    bbox = block.get("bbox") or {}
    if not _valid_bbox(bbox):
        return ""
    layer = _layer(block)
    status = _status(block)
    label = _block_label(block)
    title = _block_title(block)
    x0 = float(bbox["x0"])
    y0 = float(bbox["y0"])
    width = float(bbox["x1"]) - x0
    height = float(bbox["y1"]) - y0
    return (
        f"<g class='block layer-{layer} status-{status}' data-layer='{layer}'>"
        f"<title>{html.escape(title)}</title>"
        f"<rect x='{x0:g}' y='{y0:g}' width='{width:g}' height='{height:g}'/>"
        f"<text x='{x0:g}' y='{y0 + 11:g}'>{html.escape(label)}</text>"
        "</g>"
    )


def _block_label(block: dict[str, Any]) -> str:
    parts = [
        str(block.get("block_type") or "-"),
        str(block.get("block_role") or "-"),
        f"ro:{block.get('reading_order', '-')}",
        f"lo:{block.get('layout_order', '-')}",
    ]
    return " | ".join(parts)


def _block_title(block: dict[str, Any]) -> str:
    preview = re.sub(r"\s+", " ", str(block.get("text") or "")).strip()
    if len(preview) > 260:
        preview = f"{preview[:260]}..."
    fields = {
        "block_id": block.get("block_id", ""),
        "block_type": block.get("block_type", ""),
        "block_role": block.get("block_role", ""),
        "flow_role": block.get("flow_role", ""),
        "reading_order": block.get("reading_order", ""),
        "layout_order": block.get("layout_order", ""),
        "layout_block_id": block.get("layout_block_id", ""),
        "layout_match_status": block.get("layout_match_status", ""),
        "text": preview,
    }
    return "\n".join(f"{key}: {value}" for key, value in fields.items())


def _layer(block: dict[str, Any]) -> str:
    value = " ".join(
        str(block.get(key) or "").lower()
        for key in ("block_type", "block_role", "flow_role")
    )
    if "header" in value:
        return "header"
    if "footer" in value or "footnote" in value or "number" in value:
        return "footer"
    if "table" in value:
        return "table"
    if any(token in value for token in ("seal", "image", "figure", "stamp", "signature", "non_text")):
        return "nontext"
    return "text"


def _status(block: dict[str, Any]) -> str:
    value = str(block.get("layout_match_status") or "not_applicable").lower()
    return re.sub(r"[^a-z0-9_-]+", "-", value) or "not_applicable"


def _index_html(task_id: str, pages: list[tuple[str, Path]]) -> str:
    links = "".join(
        f"<li><a href='{html.escape(path.name)}'>{html.escape(title)}</a></li>"
        for title, path in pages
    )
    return (
        "<!doctype html><html><head><meta charset='utf-8'>"
        f"<title>Layout report {html.escape(task_id)}</title>{_index_style()}</head>"
        f"<body><h1>Layout report</h1><p>{html.escape(task_id)}</p><ul>{links}</ul></body></html>"
    )


def _controls() -> str:
    controls = [
        ("header", "Header"),
        ("footer", "Footer"),
        ("text", "Text"),
        ("table", "Table"),
        ("nontext", "Seal/Image"),
    ]
    return "<div class='controls'>" + "".join(
        f"<label><input type='checkbox' data-toggle='{key}' checked> {label}</label>"
        for key, label in controls
    ) + "</div>"


def _style() -> str:
    return """
<style>
body{font-family:Arial,"PingFang SC","Microsoft YaHei",sans-serif;margin:0;background:#f4f6f8;color:#1d2733}
header{position:sticky;top:0;z-index:10;background:white;border-bottom:1px solid #ccd3dc;padding:14px 20px}
h1{font-size:20px;margin:0 0 4px}p{margin:0 0 10px;color:#536170}.controls{display:flex;gap:14px;flex-wrap:wrap}
.page{padding:18px 20px}.page h2{font-size:16px;margin:0 0 8px}
svg{width:min(1180px,100%);background:white;border:1px solid #aeb8c4;box-shadow:0 1px 5px #0001}
.block rect{fill:transparent;stroke-width:1.6}.block text{font-size:9px;paint-order:stroke;stroke:white;stroke-width:3px;fill:#111}
.layer-header rect{stroke:#d33}.layer-footer rect{stroke:#7a4bd8}.layer-text rect{stroke:#1f7a38}
.layer-table rect{stroke:#c77900}.layer-nontext rect{stroke:#006fae}
.status-noise_unmatched rect{stroke-dasharray:3 3}.status-ambiguous rect{stroke-width:2.5}
.hidden{display:none}
</style>
"""


def _index_style() -> str:
    return (
        "<style>body{font-family:Arial,\"PingFang SC\",\"Microsoft YaHei\",sans-serif;margin:24px}"
        "li{margin:8px 0}</style>"
    )


def _script() -> str:
    return """
<script>
document.addEventListener('change', event => {
  const box = event.target.closest('input[data-toggle]');
  if (!box) return;
  document.querySelectorAll(`[data-layer="${box.dataset.toggle}"]`).forEach(item => {
    item.classList.toggle('hidden', !box.checked);
  });
});
</script>
"""


def _pdf_images(path: Path) -> dict[int, str]:
    if not path.exists():
        return {}
    pdf = fitz.open(path)
    try:
        return {
            index + 1: base64.b64encode(
                page.get_pixmap(matrix=fitz.Matrix(1, 1), alpha=False).tobytes("png")
            ).decode()
            for index, page in enumerate(pdf)
        }
    finally:
        pdf.close()


def _valid_bbox(bbox: Any) -> bool:
    return (
        isinstance(bbox, dict)
        and float(bbox.get("x1", 0)) > float(bbox.get("x0", 0))
        and float(bbox.get("y1", 0)) > float(bbox.get("y0", 0))
    )


def _safe_stem(path: Path) -> str:
    return re.sub(r"[^A-Za-z0-9._-]+", "_", path.stem).strip("_") or "document"


def _read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


if __name__ == "__main__":
    raise SystemExit(main())
