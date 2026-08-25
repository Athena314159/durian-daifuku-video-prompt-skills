#!/usr/bin/env python3
"""Regression and failure-injection tests for the paired Skill release gate."""

from __future__ import annotations

import json
import shutil
import tempfile
from pathlib import Path

from skill_release_gate import validate_release


DIRECTOR_DIR = Path(__file__).resolve().parent.parent
EXTRACT_DIR = DIRECTOR_DIR.parent / "extract-video-prompt"


def copy_static_fixture(root: Path) -> tuple[Path, Path]:
    extract = root / "extract-video-prompt"
    director = root / "jimeng-video-remix-director"
    extract_files = (
        "SKILL.md",
        "references/skill-release.json",
        "references/semantic-role-performance-gate.md",
        "scripts/lint_prompt_txt.py",
        "scripts/test_lint_prompt_txt.py",
        "scripts/test_validate_text_handoff.py",
    )
    director_files = (
        "SKILL.md",
        "references/skill-release.json",
        "references/golden-regression-cases.json",
        "references/prompt-rules.md",
        "references/skill-change-governance.md",
        "scripts/pipeline.py",
        "scripts/test_delivery_contract.py",
        "scripts/test_pipeline_contract_hardening.py",
        "scripts/test_validate_branch_handoff.py",
        "scripts/test_merge_branch_handoffs.py",
        "scripts/test_source_intake_contract.py",
        "scripts/test_resolve_launch_contract.py",
        "scripts/test_format_dual_thread_titles.py",
        "scripts/test_init_project_modes.py",
        "scripts/test_project_package_master.py",
        "scripts/test_workflow_state_statuses.py",
        "scripts/self_test.py",
        "scripts/test_skill_release_gate.py",
    )
    for source_root, target_root, files in (
        (EXTRACT_DIR, extract, extract_files),
        (DIRECTOR_DIR, director, director_files),
    ):
        for relative in files:
            source = source_root / relative
            target = target_root / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, target)
            target.chmod(0o644)
    return extract, director


def assert_code(report: dict, code: str) -> None:
    assert any(error.get("code") == code for error in report["errors"]), report["errors"]


def main() -> int:
    baseline = validate_release(EXTRACT_DIR, DIRECTOR_DIR, run_tests=False)
    assert baseline["status"] == "valid", baseline["errors"]

    with tempfile.TemporaryDirectory(prefix="skill-release-gate-") as temporary:
        extract, director = copy_static_fixture(Path(temporary))

        prompt_rules = director / "references/prompt-rules.md"
        prompt_rules.write_text(
            prompt_rules.read_text(encoding="utf-8").replace("【原片叙事复原】", "【已删除叙事段】"),
            encoding="utf-8",
        )
        broken_format = validate_release(extract, director, run_tests=False)
        assert broken_format["status"] == "invalid"
        assert_code(broken_format, "PROMPT_FORMAT_REGRESSION")

    with tempfile.TemporaryDirectory(prefix="skill-release-gate-") as temporary:
        extract, director = copy_static_fixture(Path(temporary))
        manifest_path = extract / "references/skill-release.json"
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        manifest["bundle_release_id"] = "video-remix-diverged"
        manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        mismatched = validate_release(extract, director, run_tests=False)
        assert mismatched["status"] == "invalid"
        assert_code(mismatched, "PAIRED_RELEASE_ID_MISMATCH")

    with tempfile.TemporaryDirectory(prefix="skill-release-gate-") as temporary:
        extract, director = copy_static_fixture(Path(temporary))
        manifest_path = director / "references/skill-release.json"
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        manifest["protected_invariants"].remove("audit_fields_internal_only")
        manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        weakened = validate_release(extract, director, run_tests=False)
        assert weakened["status"] == "invalid"
        assert_code(weakened, "PROTECTED_INVARIANT_REMOVED")

    print("Skill release gate tests passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
