#!/usr/bin/env python3
"""Validate a paired video-remix Skill release before it can reach live."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import subprocess
import sys
from datetime import datetime
from pathlib import Path
from typing import Any


MANIFEST_RELATIVE = Path("references/skill-release.json")
GOLDEN_RELATIVE = Path("references/golden-regression-cases.json")
RELEASE_SCHEMA = "video-remix-skill-release-v1.0"
GOLDEN_SCHEMA = "video-remix-golden-cases-v1.0"
PROMPT_CONTRACT = "narrative-six-layer-v1"
SKILL_LINE_BUDGET = 500

REQUIRED_HEADERS = (
    "【生成目标与叙事职责】",
    "【口播原文与声源】",
    "【原片叙事复原】",
    "【原片逐时动作】",
    "【产品与动作物理】",
    "【摄影、灯光与声音】",
    "【最小纠错附录】",
)

REQUIRED_INVARIANTS = {
    "source_truth_precedes_inference",
    "narrative_before_six_layer_detail",
    "six_layers_integrated_not_six_sections",
    "audit_fields_internal_only",
    "negative_constraint_ratio_lte_0_15",
    "length_contract_project_owned",
    "length_never_filled_with_constraints_or_repetition",
    "prompt_only_compiles_without_delivery_assets",
    "noncanonical_prompt_bypass_rejected",
    "prompt_delivery_requires_compile_receipt",
    "final_user_artifact_docx_only",
}

REQUIRED_LINT_CODES = (
    "NARRATIVE_FORMAT_MISSING",
    "EMOTIONAL_CAUSALITY_MISSING",
    "SIX_LAYER_AUDIT_LEAKED_INTO_PROMPT",
    "NEGATIVE_CONSTRAINT_OVERLOAD",
)


def read_json(path: Path) -> dict[str, Any]:
    with path.open(encoding="utf-8") as handle:
        value = json.load(handle)
    if not isinstance(value, dict):
        raise ValueError(f"Expected JSON object: {path}")
    return value


def sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def semver(value: Any) -> tuple[int, int, int] | None:
    if not isinstance(value, str):
        return None
    match = re.fullmatch(r"(\d+)\.(\d+)\.(\d+)", value)
    if not match:
        return None
    return tuple(int(part) for part in match.groups())


def add_error(errors: list[dict[str, str]], code: str, path: Path | str, detail: str) -> None:
    errors.append({"code": code, "path": str(path), "detail": detail})


def require_text(
    path: Path,
    tokens: tuple[str, ...] | list[str],
    errors: list[dict[str, str]],
    code: str,
) -> None:
    if not path.is_file():
        add_error(errors, "REQUIRED_FILE_MISSING", path, "Release contract requires this file.")
        return
    text = path.read_text(encoding="utf-8", errors="ignore")
    missing = [token for token in tokens if token not in text]
    if missing:
        add_error(errors, code, path, f"Missing protected tokens: {missing}")


def run_runtime_tests(
    skill_dir: Path,
    manifest: dict[str, Any],
    errors: list[dict[str, str]],
) -> list[dict[str, Any]]:
    results: list[dict[str, Any]] = []
    for relative in manifest.get("required_runtime_tests") or []:
        test_path = skill_dir / str(relative)
        if not test_path.is_file():
            add_error(errors, "RELEASE_TEST_MISSING", test_path, "Manifest test path does not exist.")
            continue
        completed = subprocess.run(
            [sys.executable, str(test_path)],
            cwd=str(skill_dir),
            text=True,
            capture_output=True,
            check=False,
        )
        combined = (completed.stdout + "\n" + completed.stderr).strip()
        results.append(
            {
                "path": str(relative),
                "exit_code": completed.returncode,
                "output_tail": combined[-2000:],
            }
        )
        if completed.returncode != 0:
            add_error(
                errors,
                "RELEASE_TEST_FAILED",
                test_path,
                f"Exit {completed.returncode}: {combined[-800:]}",
            )
    return results


def validate_release(
    extract_dir: Path,
    director_dir: Path,
    *,
    baseline_extract_dir: Path | None = None,
    baseline_director_dir: Path | None = None,
    run_tests: bool = True,
) -> dict[str, Any]:
    extract_dir = extract_dir.resolve()
    director_dir = director_dir.resolve()
    errors: list[dict[str, str]] = []
    manifests: dict[str, dict[str, Any]] = {}

    for name, skill_dir in (("extract", extract_dir), ("director", director_dir)):
        manifest_path = skill_dir / MANIFEST_RELATIVE
        if not manifest_path.is_file():
            add_error(errors, "RELEASE_MANIFEST_MISSING", manifest_path, "Every paired Skill needs a release manifest.")
            manifests[name] = {}
            continue
        try:
            manifest = read_json(manifest_path)
        except (OSError, ValueError, json.JSONDecodeError) as exc:
            add_error(errors, "RELEASE_MANIFEST_INVALID", manifest_path, str(exc))
            manifests[name] = {}
            continue
        manifests[name] = manifest
        if manifest.get("schema_version") != RELEASE_SCHEMA:
            add_error(errors, "RELEASE_SCHEMA_MISMATCH", manifest_path, f"Expected {RELEASE_SCHEMA}.")
        if manifest.get("prompt_authoring_contract") != PROMPT_CONTRACT:
            add_error(errors, "PROMPT_CONTRACT_REGRESSION", manifest_path, f"Expected {PROMPT_CONTRACT}.")
        version = semver(manifest.get("skill_version"))
        if version is None:
            add_error(errors, "SKILL_VERSION_INVALID", manifest_path, "skill_version must be semantic x.y.z.")
        protected = set(manifest.get("protected_invariants") or [])
        missing_invariants = sorted(REQUIRED_INVARIANTS - protected)
        if missing_invariants:
            add_error(errors, "PROTECTED_INVARIANT_REMOVED", manifest_path, f"Missing: {missing_invariants}")
        for relative in manifest.get("required_runtime_tests") or []:
            test_path = skill_dir / str(relative)
            if not test_path.is_file():
                add_error(errors, "RELEASE_TEST_MISSING", test_path, "Manifest test path does not exist.")

        skill_path = skill_dir / "SKILL.md"
        if not skill_path.is_file():
            add_error(errors, "SKILL_FILE_MISSING", skill_path, "SKILL.md is required.")
        else:
            line_count = len(skill_path.read_text(encoding="utf-8", errors="ignore").splitlines())
            if line_count > SKILL_LINE_BUDGET:
                add_error(
                    errors,
                    "SKILL_CONTEXT_BUDGET_EXCEEDED",
                    skill_path,
                    f"{line_count} lines exceeds {SKILL_LINE_BUDGET}; move detail to one-level references.",
                )

    extract_manifest = manifests.get("extract") or {}
    director_manifest = manifests.get("director") or {}
    release_ids = {
        extract_manifest.get("bundle_release_id"),
        director_manifest.get("bundle_release_id"),
    }
    if len(release_ids) != 1 or None in release_ids:
        add_error(
            errors,
            "PAIRED_RELEASE_ID_MISMATCH",
            MANIFEST_RELATIVE,
            f"Paired release IDs must match: {sorted(str(value) for value in release_ids)}",
        )

    require_text(
        extract_dir / "SKILL.md",
        ("叙事优先的活人感六层", "narrative-six-layer-v1"),
        errors,
        "EXTRACT_NARRATIVE_CONTRACT_MISSING",
    )
    require_text(
        extract_dir / "references/semantic-role-performance-gate.md",
        ("原片叙事复原", "正向描述与限制词预算", "15%"),
        errors,
        "SEMANTIC_GATE_REGRESSION",
    )
    require_text(
        extract_dir / "scripts/lint_prompt_txt.py",
        REQUIRED_LINT_CODES,
        errors,
        "LINT_GUARD_REGRESSION",
    )
    require_text(
        director_dir / "SKILL.md",
        (PROMPT_CONTRACT, "skill-change-governance.md"),
        errors,
        "DIRECTOR_RELEASE_GOVERNANCE_MISSING",
    )
    require_text(
        director_dir / "references/prompt-rules.md",
        list(REQUIRED_HEADERS) + [PROMPT_CONTRACT, "15%"],
        errors,
        "PROMPT_FORMAT_REGRESSION",
    )
    require_text(
        director_dir / "scripts/pipeline.py",
        (
            f'"prompt_authoring_contract": "{PROMPT_CONTRACT}"',
            "skill_release_lock",
            "PROMPT_COMPILE_TIERS",
            "NON_CANONICAL_PROMPT_BYPASS",
            "prompt_delivery_receipt.json",
            "verify_prompt_delivery",
        ),
        errors,
        "PIPELINE_RELEASE_LOCK_MISSING",
    )

    golden_path = director_dir / GOLDEN_RELATIVE
    golden: dict[str, Any] = {}
    if not golden_path.is_file():
        add_error(errors, "GOLDEN_CORPUS_MISSING", golden_path, "A stable release requires its prior golden cases.")
    else:
        try:
            golden = read_json(golden_path)
        except (OSError, ValueError, json.JSONDecodeError) as exc:
            add_error(errors, "GOLDEN_CORPUS_INVALID", golden_path, str(exc))
        if golden.get("schema_version") != GOLDEN_SCHEMA:
            add_error(errors, "GOLDEN_SCHEMA_MISMATCH", golden_path, f"Expected {GOLDEN_SCHEMA}.")
        if golden.get("bundle_release_id") != director_manifest.get("bundle_release_id"):
            add_error(errors, "GOLDEN_RELEASE_ID_MISMATCH", golden_path, "Golden corpus must be versioned with the release.")
        protected_cases = {item.get("protects") for item in golden.get("cases") or [] if isinstance(item, dict)}
        missing_golden = sorted(REQUIRED_INVARIANTS - protected_cases)
        if missing_golden:
            add_error(errors, "GOLDEN_INVARIANT_MISSING", golden_path, f"Missing cases for: {missing_golden}")

    for candidate, baseline in (
        (extract_manifest, baseline_extract_dir),
        (director_manifest, baseline_director_dir),
    ):
        if baseline is None:
            continue
        baseline_path = baseline.resolve() / MANIFEST_RELATIVE
        try:
            baseline_manifest = read_json(baseline_path)
        except (OSError, ValueError, json.JSONDecodeError) as exc:
            add_error(errors, "BASELINE_MANIFEST_INVALID", baseline_path, str(exc))
            continue
        candidate_version = semver(candidate.get("skill_version"))
        baseline_version = semver(baseline_manifest.get("skill_version"))
        if candidate_version is None or baseline_version is None or candidate_version <= baseline_version:
            add_error(errors, "VERSION_NOT_INCREMENTED", MANIFEST_RELATIVE, "Candidate version must exceed baseline.")
        if candidate.get("supersedes") != baseline_manifest.get("bundle_release_id"):
            add_error(
                errors,
                "SUPERSEDES_MISMATCH",
                MANIFEST_RELATIVE,
                "Candidate supersedes must name the exact prior bundle_release_id.",
            )
        previous_invariants = set(baseline_manifest.get("protected_invariants") or [])
        removed = sorted(previous_invariants - set(candidate.get("protected_invariants") or []))
        if removed:
            add_error(errors, "BASELINE_INVARIANT_REMOVED", MANIFEST_RELATIVE, f"Removed: {removed}")

    test_results: dict[str, list[dict[str, Any]]] = {"extract": [], "director": []}
    if run_tests and not errors:
        test_results["extract"] = run_runtime_tests(extract_dir, extract_manifest, errors)
        test_results["director"] = run_runtime_tests(director_dir, director_manifest, errors)

    tracked_files = [
        extract_dir / "SKILL.md",
        extract_dir / "references/semantic-role-performance-gate.md",
        extract_dir / "scripts/lint_prompt_txt.py",
        director_dir / "SKILL.md",
        director_dir / "references/prompt-rules.md",
        director_dir / "references/skill-change-governance.md",
        director_dir / "scripts/pipeline.py",
    ]
    hashes = {str(path): sha256_file(path) for path in tracked_files if path.is_file()}
    return {
        "schema_version": "video-remix-release-report-v1.0",
        "checked_at": datetime.now().astimezone().replace(microsecond=0).isoformat(),
        "status": "valid" if not errors else "invalid",
        "bundle_release_id": director_manifest.get("bundle_release_id"),
        "prompt_authoring_contract": director_manifest.get("prompt_authoring_contract"),
        "error_count": len(errors),
        "errors": errors,
        "tests": test_results,
        "tracked_file_sha256": hashes,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Block unsafe paired Skill releases before live installation.")
    parser.add_argument("--extract-skill-dir", required=True, type=Path)
    parser.add_argument("--director-skill-dir", required=True, type=Path)
    parser.add_argument("--baseline-extract-skill-dir", type=Path)
    parser.add_argument("--baseline-director-skill-dir", type=Path)
    parser.add_argument("--report", required=True, type=Path)
    parser.add_argument("--skip-runtime-tests", action="store_true", help="Static test use only; never use for promotion.")
    args = parser.parse_args()
    report = validate_release(
        args.extract_skill_dir,
        args.director_skill_dir,
        baseline_extract_dir=args.baseline_extract_skill_dir,
        baseline_director_dir=args.baseline_director_skill_dir,
        run_tests=not args.skip_runtime_tests,
    )
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"status": report["status"], "error_count": report["error_count"], "report": str(args.report)}, ensure_ascii=False))
    return 0 if report["status"] == "valid" else 2


if __name__ == "__main__":
    raise SystemExit(main())
