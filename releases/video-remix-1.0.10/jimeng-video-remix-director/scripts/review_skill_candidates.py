#!/usr/bin/env python3
"""Incrementally review structured Skill-update candidates without rereading chats."""

from __future__ import annotations

import argparse
import json
import re
from datetime import datetime
from pathlib import Path
from typing import Any


def read_json(path: Path) -> dict[str, Any]:
    with path.open(encoding="utf-8") as handle:
        return json.load(handle)


def write_json(path: Path, value: dict[str, Any]) -> None:
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temporary.replace(path)


def normalize(value: str) -> str:
    return re.sub(r"\W+", "", value, flags=re.UNICODE).lower()


def main() -> int:
    parser = argparse.ArgumentParser(description="Review only new structured Skill-update candidates.")
    parser.add_argument("--project-dir", required=True, type=Path)
    parser.add_argument("--skill-dir", type=Path, default=Path(__file__).resolve().parent.parent)
    args = parser.parse_args()
    project_dir = args.project_dir.expanduser().resolve()
    candidate_path = project_dir / "planning" / "skill_update_candidates.json"
    data = read_json(candidate_path)
    skill_text = "\n".join(path.read_text(encoding="utf-8", errors="ignore") for path in args.skill_dir.rglob("*.md"))
    normalized_skill = normalize(skill_text)
    seen: set[str] = set()
    rows: list[dict[str, Any]] = []
    for item in data.get("candidates", []):
        if item.get("status") != "new":
            continue
        rule = str(item.get("proposed_rule") or "").strip()
        fingerprint = normalize(rule)
        reason = ""
        decision = "reviewed"
        if not rule or not item.get("candidate_id"):
            decision, reason = "rejected", "缺少 candidate_id 或 proposed_rule。"
        elif item.get("scope") != "cross_project":
            decision, reason = "rejected", "不是跨项目规则，保留在项目层。"
        elif not item.get("evidence"):
            decision, reason = "rejected", "缺少可追溯证据路径。"
        elif item.get("risk_level") not in {"low", "medium", "high"}:
            decision, reason = "rejected", "缺少合法 risk_level。"
        elif not isinstance(item.get("interaction_surfaces"), list) or not item.get("interaction_surfaces"):
            decision, reason = "rejected", "必须声明会受影响的 interaction_surfaces。"
        elif not isinstance(item.get("regression_case_ids"), list) or not item.get("regression_case_ids"):
            decision, reason = "rejected", "必须先声明保护旧能力和新失败的 regression_case_ids。"
        elif "replaces" not in item or not isinstance(item.get("replaces"), list):
            decision, reason = "rejected", "必须显式声明 replaces；没有旧规则可替换时使用空数组。"
        elif not isinstance(item.get("rollback_trigger"), str) or not item.get("rollback_trigger", "").strip():
            decision, reason = "rejected", "必须声明可观察的 rollback_trigger。"
        elif fingerprint in seen:
            decision, reason = "rejected", "与本批次候选重复。"
        elif fingerprint and fingerprint in normalized_skill:
            decision, reason = "rejected", "现有 Skill 已包含同一规则。"
        else:
            reason = "结构条件满足；等待用户批准、候选版本和 release gate，不得直接写 live Skill。"
        seen.add(fingerprint)
        item["status"] = decision
        item["reviewed_at"] = datetime.now().astimezone().replace(microsecond=0).isoformat()
        item["review_reason"] = reason
        rows.append({"id": item.get("candidate_id"), "decision": decision, "reason": reason, "rule": rule})
    write_json(candidate_path, data)

    report_path = project_dir / "review" / "skill_update_report.md"
    lines = ["# Skill 增量更新候选审核", "", "仅审核结构化新增候选；未读取完整对话。", "", "| 候选 | 结论 | 原因 |", "|---|---|---|"]
    for row in rows:
        lines.append(f"| {row['id']} | {row['decision']} | {row['reason']} |")
    if not rows:
        lines.append("| — | 无新增候选 | 本次没有 status=new 的条目。 |")
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(json.dumps({"reviewed": len(rows), "report": str(report_path)}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
