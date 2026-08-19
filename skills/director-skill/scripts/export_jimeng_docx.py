#!/usr/bin/env python3
"""Export an approved Jimeng project as a first-frame + copyable-prompt DOCX."""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import subprocess
import sys
import zipfile
from datetime import datetime
from pathlib import Path
from typing import Any

from docx import Document
from docx.enum.section import WD_SECTION
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.enum.table import WD_CELL_VERTICAL_ALIGNMENT
from docx.oxml import OxmlElement
from docx.oxml.ns import qn
from docx.shared import Inches, Pt, RGBColor

BLUE = RGBColor(43, 102, 158)
ORANGE = RGBColor(237, 123, 40)
PALE_BLUE = "EEF4FA"


def read_json(path: Path) -> dict[str, Any]:
    with path.open(encoding="utf-8") as handle:
        return json.load(handle)


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def resolve(project: Path, value: str | None) -> Path | None:
    if not value:
        return None
    path = Path(value)
    return path if path.is_absolute() else project / path


def prompt_from_markdown(path: Path) -> str:
    value = path.read_text(encoding="utf-8")
    match = re.search(r"```text\s*\n(.*?)\n```", value, re.S)
    if not match:
        raise ValueError(f"No copyable text code block in {path}")
    return match.group(1).strip()


def set_cell_shading(cell, fill: str) -> None:
    props = cell._tc.get_or_add_tcPr()
    shade = OxmlElement("w:shd")
    shade.set(qn("w:fill"), fill)
    props.append(shade)


def set_cell_margins(cell, top: int = 120, start: int = 150, bottom: int = 120, end: int = 150) -> None:
    props = cell._tc.get_or_add_tcPr()
    margins = props.first_child_found_in("w:tcMar")
    if margins is None:
        margins = OxmlElement("w:tcMar")
        props.append(margins)
    for side, value in (("top", top), ("start", start), ("bottom", bottom), ("end", end)):
        node = margins.find(qn(f"w:{side}"))
        if node is None:
            node = OxmlElement(f"w:{side}")
            margins.append(node)
        node.set(qn("w:w"), str(value)); node.set(qn("w:type"), "dxa")


def add_run(paragraph, text: str, size: float = 10.5, color: RGBColor | None = None, bold: bool = False) -> None:
    run = paragraph.add_run(text)
    # Arial Unicode MS is available to both Word on this Mac and headless LibreOffice;
    # set all script families to prevent Chinese text from falling back to missing glyphs.
    run.font.name = "Arial Unicode MS"
    for family in ("ascii", "hAnsi", "eastAsia", "cs"):
        run._element.rPr.rFonts.set(qn(f"w:{family}"), "Arial Unicode MS")
    run.font.size = Pt(size); run.bold = bold
    if color:
        run.font.color.rgb = color


def add_label(doc: Document, label: str, value: str) -> None:
    p = doc.add_paragraph(); p.paragraph_format.space_after = Pt(2)
    add_run(p, label + "：", 9.5, BLUE, True); add_run(p, value, 9.5)


def add_header_footer(document: Document, project_id: str) -> None:
    section = document.sections[0]
    p = section.header.paragraphs[0]; add_run(p, project_id, 8, RGBColor(128, 136, 146))
    p = section.footer.paragraphs[0]; p.alignment = WD_ALIGN_PARAGRAPH.RIGHT
    field = OxmlElement("w:fldSimple"); field.set(qn("w:instr"), "PAGE")
    p._p.append(field)


def add_image(cell, path: Path | None, width: float, caption: str) -> None:
    p = cell.paragraphs[0]; p.alignment = WD_ALIGN_PARAGRAPH.CENTER
    if path and path.is_file():
        p.add_run().add_picture(str(path), width=Inches(width))
    else:
        add_run(p, "首帧待批准", 9, RGBColor(150, 150, 150))
    cap = cell.add_paragraph(); cap.alignment = WD_ALIGN_PARAGRAPH.CENTER; add_run(cap, caption, 8, RGBColor(100, 100, 100))


def main() -> int:
    parser = argparse.ArgumentParser(description="Export approved Jimeng prompts to the fixed image-and-prompt DOCX layout.")
    parser.add_argument("--project-dir", required=True, type=Path)
    parser.add_argument("--out", required=True, type=Path)
    args = parser.parse_args()
    project_dir, out = args.project_dir.resolve(), args.out.resolve()
    reuse_plan_path = project_dir / "planning" / "asset_reuse_plan.json"
    reuse_audit = subprocess.run(
        [
            sys.executable,
            str(Path(__file__).resolve().parent / "audit_asset_reuse.py"),
            "--plan",
            str(reuse_plan_path),
            "--stage",
            "pre-word",
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    if reuse_audit.returncode != 0:
        raise SystemExit("DOCX export blocked by asset reuse audit:\n" + (reuse_audit.stdout or reuse_audit.stderr))
    reuse_plan = read_json(reuse_plan_path)
    reuse_assets = {item["asset_id"]: item for item in reuse_plan.get("inventory", [])}
    reuse_decisions = {item["shot_id"]: item for item in reuse_plan.get("shot_decisions", [])}

    def shot_delivery_assets(shot_id: str) -> list[dict[str, Any]]:
        decision = reuse_decisions.get(shot_id) or {}
        return [reuse_assets[item] for item in decision.get("selected_asset_ids", []) if item in reuse_assets]

    lint = read_json(project_dir / "review" / "lint_report.json")
    if lint.get("counts", {}).get("ERROR", 0):
        raise SystemExit("DOCX export blocked: project lint contains ERROR entries.")
    project = read_json(project_dir / "project.json")
    shots = read_json(project_dir / "shots" / "shot_manifest.json").get("shots", [])
    pack = read_json(project_dir / "prompts" / "generation_pack.json")
    pack_by_id = {item["shot_id"]: item for item in pack.get("shots", [])}
    export_errors: list[str] = []
    for shot in shots:
        shot_id = str(shot.get("id", "<unknown>"))
        meta = pack_by_id.get(shot_id)
        if not meta:
            export_errors.append(f"{shot_id}: generation_pack entry missing")
            continue
        delivery_assets = shot_delivery_assets(shot_id)
        if not delivery_assets:
            export_errors.append(f"{shot_id}: asset_reuse_plan has no selected approved delivery frame")
        if not meta.get("product_references"):
            export_errors.append(f"{shot_id}: product_references missing")
        prompt_file = project_dir / str(meta.get("prompt_file", ""))
        try:
            char_count = len(re.sub(r"\s+", "", prompt_from_markdown(prompt_file)))
            if not 3000 <= char_count <= 4000:
                export_errors.append(f"{shot_id}: Prompt has {char_count} non-whitespace characters; expected 3000–4000")
        except (FileNotFoundError, ValueError) as exc:
            export_errors.append(f"{shot_id}: {exc}")
    if export_errors:
        raise SystemExit("DOCX export blocked:\n- " + "\n- ".join(export_errors))
    doc = Document()
    section = doc.sections[0]
    section.top_margin = Inches(.60); section.bottom_margin = Inches(.60)
    section.left_margin = Inches(.80); section.right_margin = Inches(.80)
    add_header_footer(doc, str(project.get("project_id", "")))
    # Cover
    p = doc.add_paragraph(); p.alignment = WD_ALIGN_PARAGRAPH.CENTER; p.paragraph_format.space_before = Pt(28)
    add_run(p, "Prompt", 30, BLUE, True)
    p = doc.add_paragraph(); p.alignment = WD_ALIGN_PARAGRAPH.CENTER
    add_run(p, f"{project.get('project_id')}｜{project.get('product_profile')}｜逐镜视频 Prompt", 15, ORANGE, True)
    p = doc.add_paragraph(); p.alignment = WD_ALIGN_PARAGRAPH.CENTER
    add_run(p, f"{len(shots)} 镜｜每镜 3000–4000 字｜换脸 + 换产品 + 批准首帧闭环", 10, RGBColor(120, 130, 140))
    cover_assets = [
        (shot.get("id", ""), asset)
        for shot in shots
        for asset in shot_delivery_assets(str(shot.get("id", "")))
    ]
    grid = doc.add_table(rows=0, cols=3); grid.autofit = False
    for index in range(0, len(cover_assets), 3):
        cells = grid.add_row().cells
        for offset, cell in enumerate(cells):
            if index + offset < len(cover_assets):
                shot_id, asset = cover_assets[index + offset]
                add_image(cell, resolve(project_dir, asset.get("path")), 1.45, f"{shot_id}｜{asset.get('asset_id')}")
    doc.add_page_break()
    # Project overview
    h = doc.add_heading("项目生成总览", level=1); h.runs[0].font.color.rgb = BLUE
    add_label(doc, "上传顺序", "每镜单独上传本页批准首帧，再粘贴本页“即梦可复制 Prompt”；不可混传多镜首帧。")
    add_label(doc, "首帧链路", "原镜真实首帧 →（若授权）目标人脸参考 + 产品参考 → 批准生成首帧 → 本镜 Prompt → 生成结果回填。")
    add_label(doc, "保护锁", "未获得明确授权的人脸、儿童和非目标人物不得改动；全片不得新增字幕、贴纸、价格、认证角标、水印或平台 UI。")
    table = doc.add_table(rows=1, cols=5); table.style = "Table Grid"
    for cell, text in zip(table.rows[0].cells, ["镜头", "时长", "画面类型", "批准首帧", "Prompt 字符"]):
        set_cell_shading(cell, "DCE6F1"); set_cell_margins(cell); add_run(cell.paragraphs[0], text, 9, BLUE, True)
    for shot in shots:
        meta = pack_by_id.get(shot.get("id"), {}); prompt = prompt_from_markdown(project_dir / meta["prompt_file"])
        cells = table.add_row().cells
        delivery_assets = shot_delivery_assets(str(shot.get("id", "")))
        values = [shot.get("id", ""), f"{(shot.get('timecode') or {}).get('duration')} 秒", shot.get("visual_type", ""), f"已批准 {len(delivery_assets)} 张" if delivery_assets else "待批准", str(len(re.sub(r"\s+", "", prompt)))]
        for cell, text in zip(cells, values): set_cell_margins(cell); add_run(cell.paragraphs[0], text, 8.5)
    # One page per shot
    manifest: list[dict[str, Any]] = []
    for shot in shots:
        doc.add_page_break(); meta = pack_by_id.get(shot.get("id"), {})
        prompt_path = project_dir / meta["prompt_file"]; prompt = prompt_from_markdown(prompt_path)
        h = doc.add_heading(shot.get("id", "镜头"), level=1); h.runs[0].font.color.rgb = BLUE
        add_label(doc, "镜头信息", f"{shot.get('title')}｜{(shot.get('timecode') or {}).get('duration')} 秒｜{shot.get('visual_type')}｜{shot.get('narrative_role')}")
        delivery_assets = shot_delivery_assets(str(shot.get("id", "")))
        columns = min(3, max(1, len(delivery_assets)))
        frame_table = doc.add_table(rows=0, cols=columns); frame_table.autofit = False
        for frame_index in range(0, len(delivery_assets), columns):
            cells = frame_table.add_row().cells
            for offset, cell in enumerate(cells):
                if frame_index + offset < len(delivery_assets):
                    asset = delivery_assets[frame_index + offset]
                    width = 2.25 if columns == 1 else (1.85 if columns == 2 else 1.35)
                    add_image(cell, resolve(project_dir, asset.get("path")), width, f"{asset.get('asset_id')}｜批准分镜")
        right_table = doc.add_table(rows=1, cols=1)
        right = right_table.cell(0, 0); set_cell_margins(right)
        audio = shot.get("audio") or {}; assets = shot.get("asset_links") or {}
        for label, value in [("口播", audio.get("script_text") or "无"), ("产品状态", (shot.get("product_state") or {}).get("state", "")), ("人脸参考", str(assets.get("avatar_reference") or "未启用换脸")), ("产品参考", "；".join(map(str, assets.get("product_references") or []))), ("编辑范围", "人物/产品仅按本镜授权与已批准首帧修改")]:
            p = right.add_paragraph(); add_run(p, label + "：", 8.5, BLUE, True); add_run(p, value, 8.5)
        prompt_character_count = len(re.sub(r"\s+", "", prompt))
        p = doc.add_paragraph(); add_run(p, "即梦可复制 Prompt", 15, BLUE, True); add_run(p, f"  {prompt_character_count} 字", 10, ORANGE, True)
        box = doc.add_table(rows=1, cols=1).cell(0, 0); set_cell_shading(box, PALE_BLUE); set_cell_margins(box, 180, 180, 180, 180)
        p = box.paragraphs[0]; p.paragraph_format.line_spacing = 1.08; add_run(p, prompt, 8.5)
        add_label(doc, "原片动作对应", "；".join(map(str, shot.get("source_facts") or [])))
        add_label(doc, "内容审核记录", "首帧、人物/产品参考、Prompt 与镜头编号已关联；生成后仍需复核脸部融合、手嘴接触、产品微结构、包装和文字。")
        manifest_assets = []
        for asset in delivery_assets:
            asset_path = resolve(project_dir, asset.get("path"))
            manifest_assets.append(
                {
                    "asset_id": asset.get("asset_id"),
                    "path": asset.get("path"),
                    "sha256": digest(asset_path) if asset_path and asset_path.is_file() else None,
                }
            )
        manifest.append({"shot_id": shot.get("id"), "source_first_frame": meta.get("source_first_frame"), "approved_generation_first_frame": meta.get("approved_generation_first_frame"), "delivery_assets": manifest_assets, "avatar_reference": assets.get("avatar_reference"), "product_references": meta.get("product_references"), "prompt_sha256": hashlib.sha256(prompt.encode("utf-8")).hexdigest()})
    doc.add_page_break(); h = doc.add_heading("生成结果回填与复核", level=1); h.runs[0].font.color.rgb = BLUE
    add_label(doc, "回填规则", "任一镜首帧或生成结果返工后，替换对应 S 编号的批准首帧与 Prompt，再重新导出本 Word；禁止只替换 Word 图片。")
    for item in manifest: add_label(doc, item["shot_id"], "待填入即梦结果路径与像素复核结论。")
    out.parent.mkdir(parents=True, exist_ok=True); doc.save(out)
    expected_word_images = reuse_plan.get("summary", {}).get("expected_word_image_count")
    with zipfile.ZipFile(out) as archive:
        embedded_media_count = len([name for name in archive.namelist() if name.startswith("word/media/image")])
    if embedded_media_count != expected_word_images:
        out.unlink(missing_ok=True)
        raise SystemExit(
            f"DOCX export blocked: embedded image count {embedded_media_count} "
            f"does not equal asset_reuse_plan expected_word_image_count {expected_word_images}."
        )
    manifest_path = out.with_suffix(".manifest.json")
    manifest_path.write_text(json.dumps({"schema_version":"1.2","project_id":project.get("project_id"),"source_sha256":pack.get("source_sha256"),"docx_sha256":digest(out),"exported_at":datetime.now().astimezone().isoformat(),"reused_frame_count":reuse_plan.get("summary", {}).get("reused_frame_count"),"new_generation_count":reuse_plan.get("summary", {}).get("new_generation_count"),"expected_word_image_count":expected_word_images,"embedded_media_count":embedded_media_count,"shots":manifest}, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"docx": str(out), "manifest": str(manifest_path), "shot_count": len(manifest)}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    try: raise SystemExit(main())
    except (FileNotFoundError, KeyError, ValueError, json.JSONDecodeError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr); raise SystemExit(2)
