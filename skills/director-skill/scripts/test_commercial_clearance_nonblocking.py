#!/usr/bin/env python3
"""Ensure commercial-rights metadata no longer gates generation or DOCX export."""

from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent


def main() -> int:
    pipeline = (ROOT / "scripts" / "pipeline.py").read_text(encoding="utf-8")
    exporter = (ROOT / "scripts" / "export_jimeng_docx.py").read_text(encoding="utf-8")
    skill = (ROOT / "SKILL.md").read_text(encoding="utf-8")
    assert "commercial_clearance_missing" not in pipeline
    assert "commercial_reviewer_missing" not in pipeline
    assert "commercial gate first" not in pipeline
    assert "commercial-release gates" not in exporter
    assert "## 11. 商业闸门" not in skill
    assert "商业权利" not in skill
    assert "GENERATION_HARD_RULES_V1" in pipeline
    print("commercial clearance is non-blocking; generation/QA contracts remain active")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
