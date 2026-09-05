#!/usr/bin/env python3
"""V2 source transcript handoff and revised-script impact bookkeeping.

This adapter deliberately does not pretend to be ASR. It normalizes an
existing transcript produced by the source-intake tools, binds it to the
source-video hash, and records which line IDs changed before compilation.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path


def sha(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for block in iter(lambda: f.read(1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()


def digest_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def load(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def normalize_segments(value: dict) -> list[dict]:
    raw = value.get("segments") or []
    out = []
    for index, item in enumerate(raw, 1):
        if isinstance(item, dict):
            start, end, text = item.get("start"), item.get("end"), item.get("text", "")
        else:
            start, end, text = item[0], item[1], item[2]
        out.append({
            "segment_id": f"SRC-LINE-{index:03d}",
            "start": float(start), "end": float(end), "text": str(text).strip(),
            "speaker_id": value.get("speaker_key", "A"),
            "evidence": ["source_transcript_input"],
        })
    return out


def normalize(args):
    source = args.source.resolve()
    transcript_path = args.transcript.resolve()
    transcript = load(transcript_path)
    segments = normalize_segments(transcript)
    if not segments:
        raise SystemExit("source transcript has no segments")
    editable = "\n".join(item["text"] for item in segments)
    output = {
        "schema_version": "v2-source-intake-v1",
        "status": "transcript_ready",
        "source_video": str(source),
        "source_video_sha256": sha(source),
        "transcript_input": str(transcript_path),
        "transcript_input_sha256": sha(transcript_path),
        "transcript_method": transcript.get("method", "external_source_intake"),
        "transcript_evidence_status": "input_transcript_not_retranscribed",
        "speaker_key": transcript.get("speaker_key", "A"),
        "segments": segments,
        "editable_text": editable,
        "line_ids": [f"L{i:03d}" for i in range(1, len(segments) + 1)],
        "script_version": digest_text(editable),
        "next_step": "user_review_or_revised_script",
    }
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(output, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"status": output["status"], "segments": len(segments), "source_video_sha256": output["source_video_sha256"]}, ensure_ascii=False))


def revised_lines(path: Path) -> list[dict]:
    value = load(path)
    if isinstance(value, list):
        return [{"line_id": f"L{i:03d}", "text": str(item).strip()} for i, item in enumerate(value, 1)]
    lines = value.get("lines") or value.get("script_lines")
    if not isinstance(lines, list):
        raise SystemExit("revised script must contain lines or script_lines")
    out = []
    for i, item in enumerate(lines, 1):
        if isinstance(item, str):
            out.append({"line_id": f"L{i:03d}", "text": item.strip()})
        else:
            out.append({"line_id": item.get("line_id", f"L{i:03d}"), "text": str(item.get("text", "")).strip()})
    return out


def diff(args):
    base = load(args.base)
    new = revised_lines(args.revised)
    old_by_id = {f"L{i:03d}": item["text"] for i, item in enumerate(base.get("segments", []), 1)}
    new_by_id = {item["line_id"]: item["text"] for item in new}
    changed = []
    for line_id in sorted(set(old_by_id) | set(new_by_id), key=lambda x: int(x[1:])):
        if old_by_id.get(line_id) != new_by_id.get(line_id):
            changed.append({"line_id": line_id, "old_text": old_by_id.get(line_id), "new_text": new_by_id.get(line_id)})
    value = {
        "schema_version": "v2-script-revision-impact-v1",
        "status": "REVISION_IMPACT_READY",
        "base_source_video_sha256": base.get("source_video_sha256"),
        "base_script_version": base.get("script_version"),
        "revised_script_version": digest_text("\n".join(item["text"] for item in new)),
        "changed_line_ids": [item["line_id"] for item in changed],
        "changed_lines": changed,
        "requires_recompile": True,
        "requires_prompt_rebuild": bool(changed),
        "requires_image_reqa": bool(changed),
        "stale_artifacts": ["semantic_role_performance_gate", "prompt_task_manifest", "image_task_manifest", "docx"] if changed else [],
    }
    if set(old_by_id) != set(new_by_id):
        value["status"] = "BLOCKED_REVISED_SCRIPT_COVERAGE"
        value["coverage_error"] = {
            "missing_line_ids": sorted(set(old_by_id) - set(new_by_id)),
            "new_line_ids": sorted(set(new_by_id) - set(old_by_id)),
            "reason": "必须先交回完整新版口播，不能用部分句子静默删除旧稿",
        }
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"status": value["status"], "changed_line_ids": value["changed_line_ids"]}, ensure_ascii=False))


def main():
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="command", required=True)
    p = sub.add_parser("normalize"); p.add_argument("--source", type=Path, required=True); p.add_argument("--transcript", type=Path, required=True); p.add_argument("--out", type=Path, required=True); p.set_defaults(fn=normalize)
    p = sub.add_parser("diff"); p.add_argument("--base", type=Path, required=True); p.add_argument("--revised", type=Path, required=True); p.add_argument("--out", type=Path, required=True); p.set_defaults(fn=diff)
    args = parser.parse_args(); args.fn(args)


if __name__ == "__main__":
    main()
