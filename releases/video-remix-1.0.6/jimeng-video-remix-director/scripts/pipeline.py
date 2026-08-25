#!/usr/bin/env python3
"""Lint and compile a structured Jimeng video-remix project."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import subprocess
import sys
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

from PIL import Image


REQUIRED_FILES = {
    "project": Path("project.json"),
    "product": Path("library/product_bible.json"),
    "product_library": Path("library/product_library.json"),
    "style": Path("library/style_bible.json"),
    "corrections": Path("library/correction_memory.json"),
    "knowledge": Path("library/knowledge_index.json"),
    "avatars": Path("library/avatar_library.json"),
    "story": Path("planning/story_plan.json"),
    "asset_reuse": Path("planning/asset_reuse_plan.json"),
    "source": Path("source/source_manifest.json"),
    "shots": Path("shots/shot_manifest.json"),
}

VALID_RISKS = {"low", "medium", "high"}
VALID_SCOPES = {"shot", "project", "product", "style"}
VALID_VISUAL_TYPES = {"product_showcase", "person_product_showcase", "person_eating"}
VALID_DELIVERY_MODES = {"voiceover", "on_screen_speech", "silent"}
VALID_EXECUTION_TIERS = {"source_intake", "diagnose_only", "first_frame_only", "prompt_only", "full_delivery"}
PROMPT_COMPILE_TIERS = {"prompt_only", "full_delivery"}
PROMPT_ONLY_AGGREGATE = "canonical_prompt_only.md"
SKILL_DIR = Path(__file__).resolve().parent.parent
RELEASE_MANIFEST_PATH = SKILL_DIR / "references" / "skill-release.json"
REQUIRED_PROMPT_HEADERS = (
    "【生成目标与叙事职责】",
    "【口播原文与声源】",
    "【原片叙事复原】",
    "【原片逐时动作】",
    "【产品与动作物理】",
    "【摄影、灯光与声音】",
    "【最小纠错附录】",
)
COMMERCIAL_CLEARANCE_FIELDS = (
    "source_rights_cleared",
    "portrait_rights_cleared",
    "music_rights_cleared",
    "claims_approved",
)
PERFORMANCE_LAYER_KEYS = (
    "emotion_trigger",
    "gaze",
    "facial_microreaction",
    "body_hand_preparation",
    "breath_pause",
    "voice_speech",
)
PERFORMANCE_LAYER_STATUSES = {
    "observed",
    "audible",
    "not_visible",
    "not_applicable",
    "template_supplement",
}
PROMPT_NEGATIVE_MARKERS = (
    "禁止",
    "严禁",
    "不得",
    "不要",
    "避免",
    "不能",
    "不可",
    "不出现",
    "不生成",
    "绝不",
)


def now_iso() -> str:
    return datetime.now().astimezone().replace(microsecond=0).isoformat()


def load_json(path: Path) -> Dict[str, Any]:
    with path.open("r", encoding="utf-8") as handle:
        value = json.load(handle)
    if not isinstance(value, dict):
        raise ValueError(f"Expected a JSON object: {path}")
    return value


def write_json(path: Path, data: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp_path = path.with_suffix(path.suffix + ".tmp")
    with temp_path.open("w", encoding="utf-8") as handle:
        json.dump(data, handle, ensure_ascii=False, indent=2)
        handle.write("\n")
    temp_path.replace(path)


def write_text(path: Path, value: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp_path = path.with_suffix(path.suffix + ".tmp")
    with temp_path.open("w", encoding="utf-8") as handle:
        handle.write(value.rstrip() + "\n")
    temp_path.replace(path)


def sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def canonical_json_sha256(value: Any) -> str:
    """Hash structured compile input without depending on pretty-print layout."""
    canonical = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return sha256_text(canonical)


def sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def canonical_input_hashes(project_dir: Path) -> Dict[str, str]:
    """Hash every canonical compile input using stable project-relative keys.

    The resulting mapping is embedded in ``generation_pack.json`` and is the
    freshness contract consumed by the DOCX exporter and final aligner.  Review
    reports, prompts, exports and workflow state are intentionally excluded:
    they are derived outputs, not compile inputs.
    """
    hashes: Dict[str, str] = {}
    for relative_path in REQUIRED_FILES.values():
        path = project_dir / relative_path
        if not path.is_file():
            raise FileNotFoundError(path)
        hashes[relative_path.as_posix()] = sha256_file(path)
    return dict(sorted(hashes.items()))


def normalized_prompt_length_contract(project: Dict[str, Any]) -> Dict[str, Any]:
    """Return the single project-owned Prompt length contract.

    Disabled means *both* bounds are off.  Enabled means both bounds are hard
    gates; a half-enabled range is rejected by lint/compile consumers instead
    of being silently interpreted in different ways by different scripts.
    """
    raw = project.get("prompt_length_contract") or {}
    enabled = raw.get("enabled") is True
    if not enabled:
        return {
            "enabled": False,
            "minimum_non_whitespace_characters": 0,
            "maximum_non_whitespace_characters": 0,
        }
    minimum = raw.get("minimum_non_whitespace_characters")
    maximum = raw.get("maximum_non_whitespace_characters")
    # Turning the bundled disabled contract on without custom values activates
    # the platform default as a *pair*.  One-sided values remain invalid.
    if minimum in (None, 0) and maximum in (None, 0):
        minimum, maximum = 3000, 4000
    if (
        not isinstance(minimum, int)
        or isinstance(minimum, bool)
        or not isinstance(maximum, int)
        or isinstance(maximum, bool)
        or minimum < 1
        or maximum < minimum
    ):
        raise ValueError(
            "project.json.prompt_length_contract must provide positive minimum_non_whitespace_characters "
            "and maximum_non_whitespace_characters >= minimum when enabled=true"
        )
    return {
        "enabled": True,
        "minimum_non_whitespace_characters": minimum,
        "maximum_non_whitespace_characters": maximum,
    }


def normalized_skill_release_lock(project: Dict[str, Any]) -> Dict[str, Any]:
    """Pin compile behavior so live Skill updates cannot silently alter old projects."""
    raw = project.get("skill_release_lock")
    if not isinstance(raw, dict):
        return {
            "bundle_release_id": "unmanaged-legacy",
            "prompt_authoring_contract": "narrative-six-layer-v1",
            "auto_upgrade": False,
        }
    release_id = raw.get("bundle_release_id")
    authoring_contract = raw.get("prompt_authoring_contract")
    auto_upgrade = raw.get("auto_upgrade")
    if not has_text(release_id):
        raise ValueError("project.json.skill_release_lock.bundle_release_id is required")
    if authoring_contract != "narrative-six-layer-v1":
        raise ValueError("project.json.skill_release_lock.prompt_authoring_contract must be narrative-six-layer-v1")
    if auto_upgrade is not False:
        raise ValueError("project.json.skill_release_lock.auto_upgrade must be false; upgrades require explicit migration")
    return {
        "bundle_release_id": release_id,
        "prompt_authoring_contract": authoring_contract,
        "auto_upgrade": False,
    }


def current_release_manifest() -> Dict[str, Any]:
    return load_json(RELEASE_MANIFEST_PATH)


def normalized_execution_tier(project: Dict[str, Any]) -> str:
    """Return the canonical execution tier used by lint and compile.

    Legacy projects remain full-delivery projects until explicitly migrated.
    A workflow-state-only tier cannot silently change compile behavior because
    workflow state is derived output, not a canonical input.
    """
    value = project.get("execution_tier")
    if value is None:
        return "full_delivery"
    if value not in VALID_EXECUTION_TIERS:
        raise ValueError(f"project.json.execution_tier must be one of {sorted(VALID_EXECUTION_TIERS)}")
    return str(value)


def requires_delivery_assets(project: Dict[str, Any]) -> bool:
    return normalized_execution_tier(project) == "full_delivery"


def as_list(value: Any) -> List[Any]:
    if value is None:
        return []
    if isinstance(value, list):
        return value
    return [value]


def has_text(value: Any) -> bool:
    return isinstance(value, str) and bool(value.strip())


def spoken_char_count(value: str) -> int:
    return len(re.findall(r"[\u4e00-\u9fffA-Za-z0-9]", value))


def spoken_text_key(value: Any) -> str:
    """Normalize spoken text for loss/duplication checks without counting punctuation."""
    if not isinstance(value, str):
        return ""
    if value.strip().lower() in {"无", "无口播", "静默", "silent", "none", "-", "—"}:
        return ""
    return "".join(re.findall(r"[\u4e00-\u9fffA-Za-z0-9]", value)).casefold()


def flatten_text(values: Any) -> List[str]:
    output: List[str] = []
    for value in as_list(values):
        if isinstance(value, str) and value.strip():
            output.append(value.strip())
    return output


def contains_positive_without_negative(text: str, positive_terms: Sequence[str], negative_terms: Sequence[str]) -> bool:
    normalized = text.lower()
    if not any(term.lower() in normalized for term in positive_terms):
        return False
    return not any(term.lower() in normalized for term in negative_terms)


def valid_timecode(value: Any) -> bool:
    if not isinstance(value, dict):
        return False
    start, end, duration = value.get("start"), value.get("end"), value.get("duration")
    if not all(isinstance(item, (int, float)) for item in (start, end, duration)):
        return False
    return end > start >= 0 and duration > 0 and abs((end - start) - duration) <= 0.08


def frame_rate_value(value: Any) -> Optional[float]:
    """Parse ffprobe-style frame rates such as 30000/1001 without guessing."""
    if isinstance(value, (int, float)) and float(value) > 0:
        return float(value)
    if isinstance(value, str):
        token = value.strip()
        try:
            if "/" in token:
                numerator, denominator = token.split("/", 1)
                denominator_value = float(denominator)
                if denominator_value == 0:
                    return None
                result = float(numerator) / denominator_value
            else:
                result = float(token)
            return result if result > 0 else None
        except ValueError:
            return None
    return None


def half_frame_tolerance(source: Dict[str, Any]) -> Optional[float]:
    fps = frame_rate_value(source.get("frame_rate"))
    return (0.5 / fps) if fps else None


def timecode_contains(outer: Any, inner: Any, tolerance: float = 0.001) -> bool:
    if not valid_timecode(outer) or not valid_timecode(inner):
        return False
    return (
        float(inner["start"]) >= float(outer["start"]) - tolerance
        and float(inner["end"]) <= float(outer["end"]) + tolerance
    )


def timecode_matches(left: Any, right: Any, tolerance: float = 0.08) -> bool:
    if not valid_timecode(left) or not valid_timecode(right):
        return False
    return all(abs(float(left[key]) - float(right[key])) <= tolerance for key in ("start", "end", "duration"))


def is_butter_crisp_project(project: Dict[str, Any], product: Dict[str, Any]) -> bool:
    text = " ".join(
        str(value or "")
        for value in (
            project.get("project_name"),
            project.get("product_profile"),
            product.get("name"),
            product.get("profile_id"),
        )
    ).lower()
    return any(term in text for term in ("黄油脆丝", "butter-crisp", "butter crisp"))


def join_cn(values: Any, fallback: str = "未指定") -> str:
    items = flatten_text(values)
    return "；".join(items) if items else fallback


def is_negative_prompt_rule(value: Any) -> bool:
    if not has_text(value):
        return False
    return any(marker in str(value) for marker in PROMPT_NEGATIVE_MARKERS)


def unique_text(values: Iterable[Any]) -> List[str]:
    result: List[str] = []
    seen: set[str] = set()
    for value in values:
        if not has_text(value):
            continue
        text = str(value).strip()
        key = re.sub(r"[\s，。！？!?；;、：:]", "", text)
        if not key or key in seen:
            continue
        seen.add(key)
        result.append(text)
    return result


def compact_negative_constraints(values: Iterable[Any], *, max_items: int = 8, max_chars: int = 520) -> List[str]:
    """Keep a small, shot-specific correction appendix instead of a negative-word dump."""
    result: List[str] = []
    used = 0
    for text in unique_text(values):
        length = len(re.sub(r"\s+", "", text))
        if result and (len(result) >= max_items or used + length > max_chars):
            break
        result.append(text)
        used += length
    return result


def table_escape(value: Any) -> str:
    return str(value if value is not None else "").replace("|", "\\|").replace("\n", " ")


def add_issue(issues: List[Dict[str, Any]], level: str, code: str, path: str, message: str) -> None:
    issues.append({"level": level, "code": code, "path": path, "message": message})


GENERIC_EMOTION_TERMS = {
    "自然", "克制", "真实", "平稳", "平静", "轻微变化", "微微变化", "正常",
    "自然真实", "自然克制", "保持自然", "情绪自然", "适度", "松弛自然",
}
ACTION_PLACEHOLDER_TERMS = (
    "按各src", "按各add", "按各unit", "按本段各", "按六层", "六层证据",
    "继承六层", "根据六层", "按原片节奏完成", "自然完成动作", "依次完成动作",
)
ACTION_EVENT_GROUPS = (
    ("拿起", "取出", "撕袋", "打开包装"),
    ("掰", "折", "脆裂", "断开"),
    ("断面", "两半", "分离", "亮出"),
    ("送到嘴", "送入口", "咬", "牙齿接触"),
    ("离嘴", "撤离嘴部", "产品离口"),
    ("咀嚼", "鼓腮"),
    ("开口说", "说话", "口播"),
)


def validate_commercial_emotion_rhythm(
    shot: Dict[str, Any],
    duration: float,
    contract: Dict[str, Any],
    issues: List[Dict[str, Any]],
    base: str,
    *,
    source_unit: bool,
) -> None:
    """Reject generic emotion prose and require an executable, gapless beat timeline.

    Source observations and creative enhancement remain separate.  This validator
    deliberately checks what a video model can execute: trigger, visible change,
    voice change and the next action, rather than rewarding extra prose.
    """
    if contract.get("enabled") is not True:
        return

    emotion = shot.get("emotion") or {}
    required_emotion_fields = (
        "persona_drive", "primary_emotion", "undertone", "residue", "commercial_turn",
    )
    for field in required_emotion_fields:
        if not has_text(emotion.get(field)):
            add_issue(issues, "ERROR", "COMMERCIAL_EMOTION_FIELD_MISSING", f"{base}.emotion.{field}", "带货人物镜头必须写明人物欲望、主情绪、暗流、余韵和情绪转化，不得只写理论标签。")
    secondary = [str(item).strip() for item in as_list(emotion.get("secondary_emotions")) if has_text(item)]
    vocabulary = [str(item).strip() for item in as_list(emotion.get("emotion_vocabulary")) if has_text(item)]
    if len(secondary) < 2 or len(set(vocabulary)) < 4:
        add_issue(issues, "ERROR", "COMMERCIAL_EMOTION_PALETTE_TOO_THIN", f"{base}.emotion", "至少需要两个次级情绪和四个互不重复、镜头可见的情绪/感受词。")
    palette = [str(emotion.get("primary_emotion", "")).strip(), *secondary, str(emotion.get("undertone", "")).strip(), str(emotion.get("residue", "")).strip(), *vocabulary]
    meaningful = [term for term in palette if term and term.lower() not in GENERIC_EMOTION_TERMS]
    if len(set(meaningful)) < 4:
        add_issue(issues, "ERROR", "COMMERCIAL_EMOTION_GENERIC_ONLY", f"{base}.emotion", "“自然、克制、平稳、真实”不能充当情绪弧；必须写出馋意、惊喜、较真、分享冲动、回味等具体转折及可见反应。")
    if not flatten_text(emotion.get("evidence_basis")):
        add_issue(issues, "ERROR", "COMMERCIAL_EMOTION_SOURCE_EVIDENCE_MISSING", f"{base}.emotion.evidence_basis", "每个情绪判断都必须回指原片时码、视线、五官、动作或声音证据。")
    enhancement = emotion.get("creative_enhancement") or {}
    enhancement_status = enhancement.get("status")
    if enhancement_status not in {"none", "user_authorized"}:
        add_issue(issues, "ERROR", "CREATIVE_ENHANCEMENT_UNDECLARED", f"{base}.emotion.creative_enhancement.status", "创作增强必须明确为 none 或 user_authorized，不能冒充原片事实。")
    if enhancement_status == "user_authorized":
        if not flatten_text(enhancement.get("terms")) or not flatten_text(enhancement.get("observable_execution")):
            add_issue(issues, "ERROR", "CREATIVE_ENHANCEMENT_NOT_EXECUTABLE", f"{base}.emotion.creative_enhancement", "获授权的增强也必须列出情绪词和可见执行动作。")

    layer_sets: List[Dict[str, Any]] = []
    if isinstance(shot.get("source_performance_layers"), dict):
        layer_sets.append(shot["source_performance_layers"])
    for unit in as_list(shot.get("source_units")):
        if isinstance(unit, dict) and isinstance(unit.get("source_performance_layers"), dict):
            layer_sets.append(unit["source_performance_layers"])
    if source_unit:
        for layers in layer_sets:
            for key, value in layers.items():
                if isinstance(value, dict) and value.get("status") == "template_supplement":
                    add_issue(issues, "ERROR", "SOURCE_PERFORMANCE_TEMPLATE_INVENTION", f"{base}.source_performance_layers.{key}", "SRC 原片层只能记录 observed/audible/not_visible/not_applicable；模板补写不能伪装成原片人物表现。")

    beats = [beat for beat in as_list(shot.get("action_beats")) if isinstance(beat, dict)]
    if not beats:
        return
    previous_end = 0.0
    for index, beat in enumerate(beats):
        path = f"{base}.action_beats[{index}]"
        start, end = beat.get("start"), beat.get("end")
        if not isinstance(start, (int, float)) or not isinstance(end, (int, float)):
            continue
        if index == 0 and abs(start) > 0.02:
            add_issue(issues, "ERROR", "ACTION_BEAT_TIMELINE_GAP", path, "第一个动作节拍必须从镜头内 0.00 秒开始。")
        if start > previous_end + 0.02:
            add_issue(issues, "ERROR", "ACTION_BEAT_TIMELINE_GAP", path, "动作节拍之间存在未描述空档；原片节奏必须连续覆盖。")
        elif start < previous_end - 0.02:
            add_issue(issues, "ERROR", "ACTION_BEAT_TIMELINE_OVERLAP", path, "动作节拍彼此重叠；请按真实先后拆开。")
        previous_end = end
        action_text = str(beat.get("action", ""))
        normalized_action = action_text.lower()
        if any(term in normalized_action for term in ACTION_PLACEHOLDER_TERMS):
            add_issue(issues, "ERROR", "ACTION_BEAT_PLACEHOLDER", f"{path}.action", "禁止“按各 SRC/六层自然完成”之类占位语；必须逐节拍写清手、嘴、产品和镜头发生了什么。")
        for field in ("id", "trigger", "visible_change", "voice_change", "next_action"):
            if not has_text(beat.get(field)):
                add_issue(issues, "ERROR", "ACTION_BEAT_EXECUTION_FIELD_MISSING", f"{path}.{field}", "每个动作节拍必须有 ID、触发、可见变化、声音变化和下一动作。")
        if not flatten_text(beat.get("emotion_terms")):
            add_issue(issues, "ERROR", "ACTION_BEAT_EMOTION_MISSING", f"{path}.emotion_terms", "每个节拍至少绑定一个具体情绪/感受词。")
        beat_duration = end - start
        event_count = sum(any(term in action_text for term in group) for group in ACTION_EVENT_GROUPS)
        if beat_duration > 2.0 and not has_text(beat.get("hold_reason")):
            add_issue(issues, "ERROR", "LONG_ACTION_BEAT_UNJUSTIFIED", path, "超过 2 秒的节拍必须继续拆分；只有原片确有持续停顿/保持时才能填写 hold_reason。")
        if event_count >= 3:
            add_issue(issues, "ERROR", "ACTION_BEAT_OVERLOADED", path, "一个节拍塞入了三个以上不可逆事件；拿起、掰裂、展示断面、咬下、离嘴、咀嚼、开口必须按原片时序拆开。")
    if previous_end < duration - 0.02:
        add_issue(issues, "ERROR", "ACTION_BEAT_TIMELINE_GAP", f"{base}.action_beats", "最后一个动作节拍没有覆盖到镜头结束。")
    elif previous_end > duration + 0.02:
        add_issue(issues, "ERROR", "ACTION_BEAT_TIMELINE_OVERFLOW", f"{base}.action_beats", "动作节拍超出镜头时长。")


def compiled_prompt_quality_errors(prompt: str, shot: Dict[str, Any]) -> set[str]:
    """Return semantic anti-padding failures for the actual copyable Prompt."""
    errors: set[str] = set()
    normalized = re.sub(r"\s+", "", prompt).lower()
    if any(term in normalized for term in ACTION_PLACEHOLDER_TERMS):
        errors.add("PROMPT_PLACEHOLDER_LANGUAGE")
    for beat in as_list(shot.get("action_beats")):
        if not isinstance(beat, dict) or not has_text(beat.get("id")):
            continue
        if str(beat["id"]).lower() not in normalized:
            errors.add("PROMPT_ACTION_BEAT_MISSING")
    sentences = [
        re.sub(r"\s+", "", item).strip("：:；;，,")
        for item in re.split(r"[。！？!?\n]+", prompt)
    ]
    substantial = [item for item in sentences if len(item) >= 8]
    if any(substantial.count(item) > 1 for item in set(substantial)):
        errors.add("PROMPT_REPETITIVE_PADDING")
    # Repeated 12-character windows catch lightly edited/copied filler that
    # sentence equality misses.  Structural labels and short beat IDs are too
    # short to trigger this check.
    compact_body = re.sub(r"【[^】]+】", "", normalized)
    if len(compact_body) >= 36:
        windows = [compact_body[index:index + 12] for index in range(0, len(compact_body) - 11, 6)]
        if any(windows.count(window) >= 3 for window in set(windows)):
            errors.add("PROMPT_REPETITIVE_PADDING")
    return errors


def occurrence_phase_binding_errors(
    phase_beat_ids: Any,
    action_beats: Iterable[Dict[str, Any]],
    required_phases: Sequence[str],
    *,
    allow_shared_adjacent: bool,
) -> set[str]:
    """Validate that an eating/break occurrence is an ordered beat chain."""
    errors: set[str] = set()
    if not isinstance(phase_beat_ids, dict):
        return {"OCCURRENCE_PHASE_BINDING_MISSING"}
    beat_positions = {
        str(beat.get("id")): index
        for index, beat in enumerate(action_beats)
        if isinstance(beat, dict) and has_text(beat.get("id"))
    }
    positions: List[int] = []
    bound_ids: List[str] = []
    for phase in required_phases:
        beat_id = str(phase_beat_ids.get(phase) or "").strip()
        if not beat_id or beat_id not in beat_positions:
            errors.add("OCCURRENCE_PHASE_BEAT_MISSING")
            continue
        positions.append(beat_positions[beat_id])
        bound_ids.append(beat_id)
    if positions:
        for left, right in zip(positions, positions[1:]):
            if right < left or (not allow_shared_adjacent and right == left):
                errors.add("OCCURRENCE_PHASE_ORDER_INVALID")
    if len(required_phases) >= 3 and len(set(bound_ids)) < 2:
        errors.add("OCCURRENCE_PHASES_COLLAPSED")
    return errors


def read_bundle(project_dir: Path) -> Dict[str, Dict[str, Any]]:
    bundle: Dict[str, Dict[str, Any]] = {}
    for key, relative_path in REQUIRED_FILES.items():
        bundle[key] = load_json(project_dir / relative_path)
    return bundle


def resolve_path(project_dir: Path, value: Optional[str]) -> Optional[Path]:
    if not value:
        return None
    path = Path(value)
    return path if path.is_absolute() else project_dir / path


def validate_required_files(project_dir: Path, issues: List[Dict[str, Any]]) -> bool:
    valid = True
    for key, relative_path in REQUIRED_FILES.items():
        full_path = project_dir / relative_path
        if not full_path.is_file():
            add_issue(issues, "ERROR", "missing_file", str(relative_path), f"Missing required {key} file.")
            valid = False
            continue
        try:
            load_json(full_path)
        except (json.JSONDecodeError, OSError, ValueError) as exc:
            add_issue(issues, "ERROR", "invalid_json", str(relative_path), str(exc))
            valid = False
    return valid


def validate_story_plan(story: Dict[str, Any], issues: List[Dict[str, Any]]) -> None:
    story_arc = story.get("story_arc") or {}
    for field in ("opening_emotional_hook", "desire_build", "proof_turn", "sensory_payoff", "closing_impulse"):
        if not has_text(story_arc.get(field)):
            add_issue(issues, "ERROR", "STORY_ARC_MISSING", f"planning/story_plan.json.story_arc.{field}", "先确定开场情绪钩子、馋意累积、证据转折、感官兑现和收尾冲动，才能写逐镜 Prompt。")
    subtitle = story.get("subtitle_script") or {}
    if subtitle.get("provided_by_user") is not True:
        add_issue(issues, "ERROR", "subtitle_script_required", "planning/story_plan.json.subtitle_script", "A user-provided subtitle script is required before delivery-mode planning.")
    if not has_text(subtitle.get("text")) and not has_text(subtitle.get("path")):
        add_issue(issues, "ERROR", "subtitle_script_empty", "planning/story_plan.json.subtitle_script", "Store the subtitle text or its project-relative path.")

    assessment = story.get("source_style_assessment") or {}
    if assessment.get("delivery_style") not in {"voiceover_dominant", "on_screen_speech_dominant", "mixed", "silent", "unknown"}:
        add_issue(issues, "ERROR", "invalid_source_delivery_style", "planning/story_plan.json.source_style_assessment.delivery_style", "Use a supported source delivery style.")
    if assessment.get("delivery_style") == "unknown":
        add_issue(issues, "ERROR", "source_style_not_assessed", "planning/story_plan.json.source_style_assessment", "Assess the original video's delivery style before compiling prompts.")

    logic = story.get("narrative_logic") or {}
    for field in ("hook", "product_promise", "visual_proof", "eating_experience", "closing_payoff"):
        if not has_text(logic.get(field)):
            add_issue(issues, "ERROR", "missing_narrative_logic", f"planning/story_plan.json.narrative_logic.{field}", "Define the video's story function before shot prompting.")

    strategy = story.get("delivery_strategy") or {}
    if strategy.get("mode") not in {"voiceover_dominant", "on_screen_speech_dominant", "mixed", "silent"}:
        add_issue(issues, "ERROR", "delivery_strategy_undecided", "planning/story_plan.json.delivery_strategy.mode", "Choose the delivery strategy from the subtitle script and original-video style.")
    if not has_text(strategy.get("rationale")):
        add_issue(issues, "ERROR", "missing_delivery_rationale", "planning/story_plan.json.delivery_strategy.rationale", "Explain why this speech/voice-over split fits the script and source style.")

    ratio_fields = ("voiceover_target_ratio", "on_screen_speech_target_ratio", "silent_target_ratio")
    ratios = [strategy.get(field) for field in ratio_fields]
    for field, value in zip(ratio_fields, ratios):
        if not isinstance(value, (int, float)) or not 0 <= value <= 1:
            add_issue(issues, "ERROR", "invalid_delivery_ratio", f"planning/story_plan.json.delivery_strategy.{field}", "Ratio must be a number from 0 to 1.")
    if all(isinstance(value, (int, float)) for value in ratios) and abs(sum(ratios) - 1) > 0.03:
        add_issue(issues, "ERROR", "delivery_ratio_sum", "planning/story_plan.json.delivery_strategy", "Voice-over, on-screen speech and silent ratios must sum to 1.")

    targets = story.get("visual_mix_targets") or {}
    for visual_type in sorted(VALID_VISUAL_TYPES):
        target = targets.get(visual_type) or {}
        minimum, maximum = target.get("min_ratio"), target.get("max_ratio")
        if not isinstance(minimum, (int, float)) or not isinstance(maximum, (int, float)) or not 0 <= minimum <= maximum <= 1:
            add_issue(issues, "ERROR", "invalid_visual_mix_target", f"planning/story_plan.json.visual_mix_targets.{visual_type}", "min_ratio and max_ratio must define a valid 0-1 range.")

    pacing = story.get("pacing") or {}
    for field in (
        "opening_hook_seconds",
        "target_average_shot_seconds",
        "minimum_generation_clip_seconds",
        "maximum_single_shot_seconds",
        "maximum_on_screen_chars_per_second",
        "maximum_voiceover_chars_per_second",
    ):
        value = pacing.get(field)
        if not isinstance(value, (int, float)) or value <= 0:
            add_issue(issues, "ERROR", "invalid_pacing_value", f"planning/story_plan.json.pacing.{field}", "Pacing values must be positive numbers.")

    segments = story.get("segments")
    if not isinstance(segments, list) or not segments:
        add_issue(issues, "ERROR", "missing_script_segments", "planning/story_plan.json.segments", "Split the user subtitle script into timed narrative segments.")
        return
    seen_ids = set()
    for index, segment in enumerate(segments):
        path = f"planning/story_plan.json.segments[{index}]"
        if not isinstance(segment, dict):
            add_issue(issues, "ERROR", "invalid_script_segment", path, "Segment must be an object.")
            continue
        segment_id = segment.get("id")
        if not has_text(segment_id) or segment_id in seen_ids:
            add_issue(issues, "ERROR", "invalid_script_segment_id", f"{path}.id", "Segment id is required and must be unique.")
        else:
            seen_ids.add(segment_id)
        if not has_text(segment.get("text")):
            add_issue(issues, "ERROR", "missing_script_segment_text", f"{path}.text", "Segment text is required.")
        if segment.get("delivery_mode") not in VALID_DELIVERY_MODES:
            add_issue(issues, "ERROR", "invalid_segment_delivery", f"{path}.delivery_mode", f"Use one of {sorted(VALID_DELIVERY_MODES)}.")
        if not has_text(segment.get("delivery_rationale")):
            add_issue(issues, "ERROR", "missing_segment_delivery_rationale", f"{path}.delivery_rationale", "Explain why this line is voice-over, on-screen speech or silent.")
        if not as_list(segment.get("assigned_shots")):
            add_issue(issues, "ERROR", "unassigned_script_segment", f"{path}.assigned_shots", "Assign every script segment to at least one shot.")


def validate_mix_and_pacing(story: Dict[str, Any], shots: List[Dict[str, Any]], issues: List[Dict[str, Any]]) -> None:
    durations = []
    visual_seconds = {key: 0.0 for key in VALID_VISUAL_TYPES}
    delivery_seconds = {key: 0.0 for key in VALID_DELIVERY_MODES}
    for shot in shots:
        duration = (shot.get("timecode") or {}).get("duration")
        if not isinstance(duration, (int, float)) or duration <= 0:
            continue
        duration_value = float(duration)
        durations.append(duration_value)
        visual_type = shot.get("visual_type")
        delivery_mode = (shot.get("audio") or {}).get("delivery_mode")
        if visual_type in visual_seconds:
            visual_seconds[visual_type] += duration_value
        if delivery_mode in delivery_seconds:
            delivery_seconds[delivery_mode] += duration_value

    total = sum(durations)
    if total <= 0:
        return
    targets = story.get("visual_mix_targets") or {}
    for visual_type, seconds in visual_seconds.items():
        ratio = seconds / total
        target = targets.get(visual_type) or {}
        minimum, maximum = target.get("min_ratio"), target.get("max_ratio")
        if isinstance(minimum, (int, float)) and ratio < minimum - 0.01:
            add_issue(issues, "WARN", "visual_mix_too_low", f"shots/shot_manifest.json.{visual_type}", f"{visual_type} occupies {ratio:.0%}, below the planned minimum {minimum:.0%}.")
        if isinstance(maximum, (int, float)) and ratio > maximum + 0.01:
            add_issue(issues, "WARN", "visual_mix_too_high", f"shots/shot_manifest.json.{visual_type}", f"{visual_type} occupies {ratio:.0%}, above the planned maximum {maximum:.0%}.")

    strategy = story.get("delivery_strategy") or {}
    target_map = {
        "voiceover": strategy.get("voiceover_target_ratio"),
        "on_screen_speech": strategy.get("on_screen_speech_target_ratio"),
        "silent": strategy.get("silent_target_ratio"),
    }
    for delivery_mode, seconds in delivery_seconds.items():
        actual = seconds / total
        target = target_map.get(delivery_mode)
        if isinstance(target, (int, float)) and abs(actual - target) > 0.15:
            add_issue(issues, "WARN", "delivery_ratio_drift", f"shots/shot_manifest.json.{delivery_mode}", f"Actual {delivery_mode} ratio is {actual:.0%}, materially different from the planned {target:.0%}.")

    pacing = story.get("pacing") or {}
    maximum = pacing.get("maximum_single_shot_seconds")
    if isinstance(maximum, (int, float)):
        for shot in shots:
            duration = (shot.get("timecode") or {}).get("duration")
            if isinstance(duration, (int, float)) and duration > maximum:
                add_issue(issues, "ERROR", "shot_duration_exceeded", f"shots/shot_manifest.json.{shot.get('id')}", f"Shot duration {duration}s exceeds the planned maximum {maximum}s; split the semantic/action loop or record an explicit user override before compiling.")
    first = min(shots, key=lambda item: (item.get("timecode") or {}).get("start", float("inf")), default=None)
    if first and first.get("narrative_role") != "hook":
        add_issue(issues, "WARN", "opening_without_hook", f"shots/shot_manifest.json.{first.get('id')}.narrative_role", "The opening shot should normally perform the hook role.")


def validate_performance_layers(
    project_dir: Path,
    unit: Dict[str, Any],
    unit_path: str,
    source_by_id: Dict[str, Dict[str, Any]],
    source_ids: Sequence[str],
    issues: List[Dict[str, Any]],
) -> None:
    """Require evidence for all six performance layers without inventing invisible behavior."""
    layers = unit.get("source_performance_layers")
    if not isinstance(layers, dict):
        add_issue(
            issues,
            "ERROR",
            "SIX_LAYER_EVIDENCE_MISSING",
            f"{unit_path}.source_performance_layers",
            "Every SRC/ADD unit needs six structured source-performance records; prose keywords are not evidence.",
        )
        return
    expected_ids = [source_id for source_id in source_ids if source_id in source_by_id]
    for layer_key in PERFORMANCE_LAYER_KEYS:
        layer_path = f"{unit_path}.source_performance_layers.{layer_key}"
        record = layers.get(layer_key)
        if not isinstance(record, dict):
            add_issue(issues, "ERROR", "SIX_LAYER_RECORD_MISSING", layer_path, f"Missing structured {layer_key} evidence record.")
            continue
        status = record.get("status")
        if status not in PERFORMANCE_LAYER_STATUSES:
            add_issue(issues, "ERROR", "SIX_LAYER_STATUS_INVALID", f"{layer_path}.status", f"Use one of {sorted(PERFORMANCE_LAYER_STATUSES)}.")
        for required_field in ("source_timecode", "source_reference_frame", "observable_evidence", "confidence", "gap_reason"):
            if required_field not in record:
                add_issue(issues, "ERROR", "SIX_LAYER_FIELD_MISSING", f"{layer_path}.{required_field}", "Keep the complete evidence envelope, using null only when the status makes the field inapplicable.")
        if not has_text(record.get("observable_evidence")):
            add_issue(issues, "ERROR", "SIX_LAYER_EVIDENCE_EMPTY", f"{layer_path}.observable_evidence", "Describe the visible/audible fact or explain precisely why this layer is not visible/applicable.")
        confidence = record.get("confidence")
        if not isinstance(confidence, (int, float)) or isinstance(confidence, bool) or not 0 <= float(confidence) <= 1:
            add_issue(issues, "ERROR", "SIX_LAYER_CONFIDENCE_INVALID", f"{layer_path}.confidence", "Confidence must be a number from 0 to 1.")
        evidence_timecode = record.get("source_timecode")
        anchored_statuses = {"observed", "audible", "not_visible", "template_supplement"}
        if status in anchored_statuses:
            if not valid_timecode(evidence_timecode):
                add_issue(issues, "ERROR", "SIX_LAYER_SOURCE_TIMECODE_MISSING", f"{layer_path}.source_timecode", "Observed, audible, not-visible and template-supplement records need the exact inspected source interval.")
            elif expected_ids and not any(
                timecode_contains(source_by_id[source_id].get("timecode"), evidence_timecode, 0.001)
                for source_id in expected_ids
            ):
                add_issue(issues, "ERROR", "SIX_LAYER_SOURCE_TIMECODE_OUT_OF_RANGE", f"{layer_path}.source_timecode", "Evidence time must fall inside one of this unit's bound source shots.")
            reference_frame = resolve_path(project_dir, record.get("source_reference_frame"))
            if reference_frame is None or not reference_frame.is_file():
                add_issue(issues, "ERROR", "SIX_LAYER_REFERENCE_FRAME_MISSING", f"{layer_path}.source_reference_frame", "Every inspected or supplemented layer needs an accessible exact reference frame; the frame may prove that a behavior is not visible.")
        elif record.get("source_timecode") is not None and not valid_timecode(evidence_timecode):
            add_issue(issues, "ERROR", "SIX_LAYER_SOURCE_TIMECODE_INVALID", f"{layer_path}.source_timecode", "Use a valid interval or null for a genuinely non-visible/non-applicable layer.")
        if status in {"not_visible", "template_supplement"} and not has_text(record.get("gap_reason")):
            code = "SIX_LAYER_TEMPLATE_GAP_REASON_MISSING" if status == "template_supplement" else "SIX_LAYER_NOT_VISIBLE_REASON_MISSING"
            add_issue(issues, "ERROR", code, f"{layer_path}.gap_reason", "State the exact visibility/source gap. Do not replace an unseen behavior with an invented observation.")
        if status in {"observed", "audible"} and record.get("gap_reason") not in (None, ""):
            add_issue(issues, "ERROR", "SIX_LAYER_UNEXPECTED_GAP_REASON", f"{layer_path}.gap_reason", "Observed/audible records cannot claim a source gap at the same time.")

    unexpected = sorted(set(layers) - set(PERFORMANCE_LAYER_KEYS))
    if unexpected:
        add_issue(issues, "ERROR", "SIX_LAYER_UNKNOWN_KEYS", f"{unit_path}.source_performance_layers", f"Unknown layer keys: {unexpected}.")


def validate_source_shot_contract(
    project_dir: Path,
    project: Dict[str, Any],
    source: Dict[str, Any],
    shots: List[Dict[str, Any]],
    reuse_plan: Dict[str, Any],
    issues: List[Dict[str, Any]],
    *,
    require_delivery_assets: bool = True,
) -> None:
    rules = project.get("project_rules") or {}
    if rules.get("preserve_every_source_shot") is not True:
        add_issue(
            issues,
            "ERROR",
            "SOURCE_SHOT_PRESERVATION_RULE_DISABLED",
            "project.json.project_rules.preserve_every_source_shot",
            "Canonical Prompt compilation cannot disable one-time ordered coverage of every atomic SRC shot.",
        )

    source_shots = source.get("source_shots")
    if not isinstance(source_shots, list) or not source_shots:
        add_issue(
            issues,
            "ERROR",
            "SOURCE_SHOT_INVENTORY_MISSING",
            "source/source_manifest.json.source_shots",
            "Inventory every atomic source-video shot before grouping short shots for generation.",
        )
        return

    source_by_id: Dict[str, Dict[str, Any]] = {}
    source_order: List[str] = []
    source_duration = source.get("duration")
    fps = frame_rate_value(source.get("frame_rate"))
    tolerance = half_frame_tolerance(source)
    frame_accurate = True
    if rules.get("require_frame_accurate_source_timeline") is not True:
        add_issue(
            issues,
            "ERROR",
            "FRAME_ACCURATE_SOURCE_TIMELINE_RULE_DISABLED",
            "project.json.project_rules.require_frame_accurate_source_timeline",
            "Canonical source coverage always requires frame-accurate boundaries.",
        )
    if not isinstance(source_duration, (int, float)) or isinstance(source_duration, bool) or float(source_duration) <= 0:
        add_issue(issues, "ERROR", "SOURCE_DURATION_MISSING", "source/source_manifest.json.duration", "Use the ffprobe source duration as the authoritative end of the atomic-shot inventory.")
        source_duration = None
    if frame_accurate and (fps is None or tolerance is None):
        add_issue(issues, "ERROR", "SOURCE_FRAME_RATE_MISSING", "source/source_manifest.json.frame_rate", "Store an exact positive ffprobe frame rate such as 30 or 30000/1001.")
    effective_tolerance = tolerance if tolerance is not None else 0.001
    previous_end: Optional[float] = None
    previous_end_frame: Optional[int] = None
    for index, item in enumerate(source_shots):
        path = f"source/source_manifest.json.source_shots[{index}]"
        if not isinstance(item, dict):
            add_issue(issues, "ERROR", "INVALID_SOURCE_SHOT", path, "Each source shot must be an object.")
            continue
        source_id = str(item.get("id") or "").strip()
        if not source_id or source_id in source_by_id:
            add_issue(issues, "ERROR", "INVALID_SOURCE_SHOT_ID", f"{path}.id", "Source-shot ids must be non-empty and unique.")
            continue
        if not re.fullmatch(r"SRC\d+", source_id):
            add_issue(issues, "ERROR", "NON_CANONICAL_SOURCE_SHOT_ID", f"{path}.id", "Canonical source-shot ids must use SRC followed by digits.")
        timecode = item.get("timecode")
        if not valid_timecode(timecode):
            add_issue(issues, "ERROR", "INVALID_SOURCE_SHOT_TIMECODE", f"{path}.timecode", "Store exact numeric start, end and duration for every source shot.")
        else:
            start_value = float(timecode["start"])
            end_value = float(timecode["end"])
            duration_value = float(timecode["duration"])
            if abs((end_value - start_value) - duration_value) > effective_tolerance:
                add_issue(issues, "ERROR", "SOURCE_SHOT_DURATION_FRAME_MISMATCH", f"{path}.timecode", "Source-shot duration must equal end-start within half a source frame.")
            if index == 0 and abs(start_value) > effective_tolerance:
                add_issue(issues, "ERROR", "SOURCE_TIMELINE_START_MISMATCH", f"{path}.timecode.start", "The first atomic source shot must begin at 0.000 seconds.")
            if previous_end is not None:
                boundary_delta = start_value - previous_end
                if abs(boundary_delta) > effective_tolerance:
                    code = "SOURCE_TIMELINE_GAP" if boundary_delta > 0 else "SOURCE_TIMELINE_OVERLAP"
                    add_issue(issues, "ERROR", code, f"{path}.timecode.start", f"Atomic source shots must meet exactly; boundary drift {boundary_delta:+.6f}s exceeds half-frame tolerance {effective_tolerance:.6f}s.")
            previous_end = float(timecode["end"])
            if frame_accurate and fps is not None:
                start_frame, end_frame = item.get("start_frame"), item.get("end_frame")
                if not isinstance(start_frame, int) or isinstance(start_frame, bool) or not isinstance(end_frame, int) or isinstance(end_frame, bool) or end_frame <= start_frame:
                    add_issue(issues, "ERROR", "SOURCE_FRAME_INDEX_MISSING", f"{path}.start_frame|end_frame", "Store integer start_frame and exclusive end_frame for every atomic source shot.")
                else:
                    if index == 0 and start_frame != 0:
                        add_issue(issues, "ERROR", "SOURCE_FRAME_TIMELINE_START_MISMATCH", f"{path}.start_frame", "The first SRC must start at frame 0.")
                    if previous_end_frame is not None and start_frame != previous_end_frame:
                        code = "SOURCE_FRAME_TIMELINE_GAP" if start_frame > previous_end_frame else "SOURCE_FRAME_TIMELINE_OVERLAP"
                        add_issue(issues, "ERROR", code, f"{path}.start_frame", f"Atomic SRC frame ranges must meet exactly: previous exclusive end_frame={previous_end_frame}, current start_frame={start_frame}.")
                    if abs((float(start_frame) / fps) - start_value) > effective_tolerance:
                        add_issue(issues, "ERROR", "SOURCE_START_FRAME_MISMATCH", f"{path}.start_frame", "start_frame must resolve to source timecode.start within half a frame.")
                    if abs((float(end_frame) / fps) - end_value) > effective_tolerance:
                        add_issue(issues, "ERROR", "SOURCE_END_FRAME_MISMATCH", f"{path}.end_frame", "end_frame must resolve to source timecode.end within half a frame.")
                    previous_end_frame = end_frame
        if not has_text(item.get("storyboard_description")):
            add_issue(issues, "ERROR", "SOURCE_STORYBOARD_DESCRIPTION_MISSING", f"{path}.storyboard_description", "Describe the visible source shot before adapting it to the new script.")
        source_by_id[source_id] = item
        source_order.append(source_id)

    if previous_end is not None and source_duration is not None and abs(previous_end - float(source_duration)) > effective_tolerance:
        add_issue(
            issues,
            "ERROR",
            "SOURCE_TIMELINE_END_MISMATCH",
            "source/source_manifest.json.source_shots[-1].timecode.end",
            f"The final atomic shot must end at ffprobe duration {float(source_duration):.6f}s within half-frame tolerance {effective_tolerance:.6f}s.",
        )

    inventory = {
        str(item.get("asset_id")): item
        for item in reuse_plan.get("inventory", [])
        if isinstance(item, dict) and has_text(item.get("asset_id"))
    }
    selected_by_shot = {
        str(item.get("shot_id")): {str(value) for value in as_list(item.get("selected_asset_ids"))}
        for item in reuse_plan.get("shot_decisions", [])
        if isinstance(item, dict) and has_text(item.get("shot_id"))
    }
    selected_order_by_shot = {
        str(item.get("shot_id")): [str(value) for value in as_list(item.get("selected_asset_ids")) if has_text(value)]
        for item in reuse_plan.get("shot_decisions", [])
        if isinstance(item, dict) and has_text(item.get("shot_id"))
    }
    flattened_ids: List[str] = []
    inserted_ids: List[str] = []
    assigned_delivery_assets: Dict[str, str] = {}
    assigned_delivery_paths: Dict[str, str] = {}
    assigned_delivery_hashes: Dict[str, str] = {}
    unit_delivery_assets: Dict[str, List[str]] = {}
    unit_owner_shots: Dict[str, str] = {}
    selected_delivery_assets_by_shot: Dict[str, List[str]] = {}
    source_image_rule = rules.get("require_at_least_one_approved_image_per_source_shot")
    inserted_image_rule = rules.get("require_at_least_one_approved_image_per_inserted_shot")
    if require_delivery_assets and source_image_rule is not True:
        add_issue(
            issues,
            "ERROR",
            "SOURCE_IMAGE_CARDINALITY_RULE_DISABLED",
            "project.json.project_rules.require_at_least_one_approved_image_per_source_shot",
            "Every SRC needs at least one independently approved target frame; one SRC may retain several ordered action-state frames.",
        )
    if require_delivery_assets and inserted_image_rule is not True:
        add_issue(
            issues,
            "ERROR",
            "INSERTED_IMAGE_CARDINALITY_RULE_DISABLED",
            "project.json.project_rules.require_at_least_one_approved_image_per_inserted_shot",
            "Every ADD needs at least one independently approved target frame; one ADD may retain several ordered action-state frames.",
        )
    if rules.get("require_structured_six_layer_evidence") is not True:
        add_issue(
            issues,
            "ERROR",
            "SIX_LAYER_RULE_DISABLED",
            "project.json.project_rules.require_structured_six_layer_evidence",
            "Every SRC/ADD must retain the structured six-layer source evidence envelope.",
        )
    minimum = rules.get("minimum_generation_clip_seconds", 4.0)
    if not isinstance(minimum, (int, float)) or minimum <= 0:
        add_issue(issues, "ERROR", "INVALID_MINIMUM_GENERATION_DURATION", "project.json.project_rules.minimum_generation_clip_seconds", "Minimum generation duration must be a positive number.")
        minimum = 4.0

    def validate_delivery_assets(
        shot_id: str,
        unit_path: str,
        unit_id: str,
        unit: Dict[str, Any],
        delivery_ids: List[str],
        missing_code: str,
        missing_message: str,
    ) -> None:
        if not delivery_ids:
            add_issue(
                issues,
                "ERROR",
                missing_code,
                f"{unit_path}.delivery_asset_ids",
                missing_message,
            )
        if len(delivery_ids) != len(set(delivery_ids)):
            add_issue(
                issues,
                "ERROR",
                "STORYBOARD_ASSET_DUPLICATED_WITHIN_UNIT",
                f"{unit_path}.delivery_asset_ids",
                f"{unit_id} repeats an asset ID inside the same target-frame list.",
            )
        role_overrides = unit.get("delivery_asset_roles") or {}
        if role_overrides and not isinstance(role_overrides, dict):
            add_issue(
                issues,
                "ERROR",
                "STORYBOARD_ASSET_ROLE_MAP_INVALID",
                f"{unit_path}.delivery_asset_roles",
                "delivery_asset_roles must be an asset_id-to-responsibility object.",
            )
            role_overrides = {}
        responsibilities: List[str] = []
        for asset_id in delivery_ids:
            if asset_id not in selected_by_shot.get(shot_id, set()):
                add_issue(issues, "ERROR", "STORYBOARD_ASSET_NOT_SELECTED", f"{unit_path}.delivery_asset_ids", f"Asset {asset_id} is not selected in the canonical reuse decision for {shot_id}.")
            asset = inventory.get(asset_id)
            if asset is None:
                add_issue(issues, "ERROR", "STORYBOARD_ASSET_UNKNOWN", f"{unit_path}.delivery_asset_ids", f"Unknown delivery asset: {asset_id}")
                continue
            if asset.get("approval_status") not in {"approved", "user_approved"}:
                add_issue(issues, "ERROR", "DELIVERY_FRAME_NOT_APPROVED", f"{unit_path}.delivery_asset_ids", f"Asset {asset_id} is not approved.")
            provenance_ids = {
                str(value)
                for field in ("source_shot_ids", "inserted_shot_ids", "storyboard_unit_ids")
                for value in as_list(asset.get(field))
                if has_text(value)
            }
            if unit_id not in provenance_ids:
                add_issue(issues, "ERROR", "DELIVERY_FRAME_PROVENANCE_MISMATCH", f"{unit_path}.delivery_asset_ids", f"Asset {asset_id} provenance must explicitly include its owning storyboard unit {unit_id}.")
            asset_path = resolve_path(project_dir, asset.get("path"))
            if asset_path is None or not asset_path.is_file():
                add_issue(issues, "ERROR", "DELIVERY_FRAME_UNAVAILABLE", f"{unit_path}.delivery_asset_ids", f"Approved asset is unavailable: {asset_id}")
                actual_hash = None
                resolved_path = None
            else:
                resolved_path = str(asset_path.resolve())
                actual_hash = sha256_file(asset_path)
                if not has_text(asset.get("sha256")) or asset.get("sha256") != actual_hash:
                    add_issue(issues, "ERROR", "DELIVERY_FRAME_HASH_MISMATCH", f"{unit_path}.delivery_asset_ids", f"Asset {asset_id} SHA-256 must match its actual approved image bytes.")
            responsibility = role_overrides.get(asset_id) or asset.get("responsibility") or asset.get("frame_role")
            if not has_text(responsibility):
                add_issue(issues, "ERROR", "STORYBOARD_ASSET_RESPONSIBILITY_MISSING", f"{unit_path}.delivery_asset_roles", f"Every target frame owned by {unit_id} needs an explicit editable action-state responsibility; missing for {asset_id}.")
            if has_text(responsibility):
                responsibilities.append(str(responsibility).strip())
            prior_unit_id = assigned_delivery_assets.get(asset_id)
            if prior_unit_id and prior_unit_id != unit_id:
                add_issue(issues, "ERROR", "DELIVERY_FRAME_DUPLICATED", f"{unit_path}.delivery_asset_ids", f"Asset {asset_id} is reused for both {prior_unit_id} and {unit_id}; every source or inserted storyboard unit needs its own image.")
            assigned_delivery_assets[asset_id] = unit_id
            if resolved_path:
                prior_path_owner = assigned_delivery_paths.get(resolved_path)
                if prior_path_owner and prior_path_owner != unit_id:
                    add_issue(issues, "ERROR", "DELIVERY_FRAME_PATH_REUSED_ACROSS_UNITS", f"{unit_path}.delivery_asset_ids", f"The same image path is assigned to both {prior_path_owner} and {unit_id}.")
                assigned_delivery_paths[resolved_path] = unit_id
            if actual_hash:
                prior_hash_owner = assigned_delivery_hashes.get(actual_hash)
                if prior_hash_owner and prior_hash_owner != unit_id:
                    add_issue(issues, "ERROR", "DELIVERY_FRAME_HASH_REUSED_ACROSS_UNITS", f"{unit_path}.delivery_asset_ids", f"The same image bytes are assigned to both {prior_hash_owner} and {unit_id}.")
                assigned_delivery_hashes[actual_hash] = unit_id
        if len(delivery_ids) > 1 and len(responsibilities) == len(delivery_ids) and len(set(responsibilities)) != len(responsibilities):
            add_issue(issues, "ERROR", "STORYBOARD_ASSET_RESPONSIBILITY_DUPLICATED", f"{unit_path}.delivery_asset_roles", "Multiple target frames for one unit must describe distinct action-state responsibilities.")
        unit_delivery_assets[unit_id] = list(delivery_ids)
        unit_owner_shots[unit_id] = shot_id
        selected_delivery_assets_by_shot.setdefault(shot_id, []).extend(delivery_ids)

    for shot_index, shot in enumerate(shots):
        shot_id = str(shot.get("id") or f"index-{shot_index}")
        base = f"shots/shot_manifest.json.{shot_id}"
        units = shot.get("source_units") or []
        added_units = shot.get("inserted_units") or []
        if not isinstance(units, list):
            add_issue(issues, "ERROR", "INVALID_SOURCE_UNITS", f"{base}.source_units", "source_units must be a list, even when this is a purely inserted shot.")
            units = []
        if not isinstance(added_units, list):
            add_issue(issues, "ERROR", "INVALID_INSERTED_UNITS", f"{base}.inserted_units", "inserted_units must be a list.")
            added_units = []
        if not units and not added_units:
            add_issue(issues, "ERROR", "STORYBOARD_UNITS_MISSING", base, "Every generation clip must contain at least one source_units or inserted_units storyboard record.")
            continue
        unit_ids: List[str] = []
        unit_durations: List[float] = []
        unit_generation_timecodes: List[Tuple[float, float, str]] = []
        for unit_index, unit in enumerate(units):
            unit_path = f"{base}.source_units[{unit_index}]"
            if not isinstance(unit, dict):
                add_issue(issues, "ERROR", "INVALID_SOURCE_UNIT", unit_path, "Source unit must be an object.")
                continue
            source_id = str(unit.get("source_shot_id") or "").strip()
            if source_id not in source_by_id:
                add_issue(issues, "ERROR", "UNKNOWN_SOURCE_SHOT", f"{unit_path}.source_shot_id", f"Unknown source shot: {source_id or '<missing>'}.")
                continue
            unit_ids.append(source_id)
            flattened_ids.append(source_id)
            source_timecode = unit.get("source_timecode")
            if not timecode_matches(source_timecode, source_by_id[source_id].get("timecode"), tolerance=effective_tolerance):
                add_issue(issues, "ERROR", "SOURCE_TIMECODE_DIVERGENCE", f"{unit_path}.source_timecode", "The DOCX/source-unit timecode must exactly match the source-shot inventory.")
            else:
                unit_durations.append(float(source_timecode["duration"]))
            if not valid_timecode(unit.get("generation_timecode")):
                add_issue(issues, "ERROR", "GENERATION_UNIT_TIMECODE_MISSING", f"{unit_path}.generation_timecode", "Map this source shot to exact seconds inside the merged generation clip.")
            else:
                generated = unit["generation_timecode"]
                unit_generation_timecodes.append((float(generated["start"]), float(generated["end"]), unit_path))
            if not has_text(unit.get("storyboard_description")):
                add_issue(issues, "ERROR", "STORYBOARD_DESCRIPTION_MISSING", f"{unit_path}.storyboard_description", "Every source shot needs an editable storyboard description in the final Word document.")
            if not has_text(unit.get("script_text")):
                add_issue(issues, "ERROR", "SOURCE_SHOT_SCRIPT_MISSING", f"{unit_path}.script_text", "Every source shot needs its assigned revised spoken script in the DOCX; write '无' only for a genuinely silent beat.")
            source_frame = resolve_path(project_dir, unit.get("source_first_frame"))
            if source_frame is None:
                add_issue(issues, "ERROR", "SOURCE_FRAME_MISSING", f"{unit_path}.source_first_frame", "Extract an exact source frame for every atomic source shot.")
            elif not source_frame.is_file():
                add_issue(issues, "ERROR", "SOURCE_FRAME_UNAVAILABLE", f"{unit_path}.source_first_frame", f"Source frame is unavailable: {source_frame}")

            validate_performance_layers(project_dir, unit, unit_path, source_by_id, [source_id], issues)
            character = shot.get("character") or {}
            if shot.get("visual_type") in {"person_product_showcase", "person_eating"} or character.get("hands_only") is True:
                layer_statuses = {
                    str(record.get("status"))
                    for record in (unit.get("source_performance_layers") or {}).values()
                    if isinstance(record, dict)
                }
                if not layer_statuses.intersection({"observed", "audible"}):
                    add_issue(issues, "ERROR", "SIX_LAYER_HUMAN_EVIDENCE_EMPTY", f"{unit_path}.source_performance_layers", "A visible person/hand action needs at least one observed or audible source evidence layer; do not mark every layer not_applicable.")

            delivery_ids = [str(value) for value in as_list(unit.get("delivery_asset_ids")) if has_text(value)]
            if require_delivery_assets:
                validate_delivery_assets(
                    shot_id,
                    unit_path,
                    source_id,
                    unit,
                    delivery_ids,
                    "SOURCE_SHOT_APPROVED_IMAGE_MISSING",
                    "Every atomic source shot must map to at least one independently approved target frame.",
                )

        for unit_index, unit in enumerate(added_units):
            unit_path = f"{base}.inserted_units[{unit_index}]"
            if not isinstance(unit, dict):
                add_issue(issues, "ERROR", "INVALID_INSERTED_UNIT", unit_path, "Inserted storyboard unit must be an object.")
                continue
            inserted_id = str(unit.get("inserted_shot_id") or "").strip()
            if not re.fullmatch(r"ADD\d+", inserted_id) or inserted_id in inserted_ids:
                add_issue(issues, "ERROR", "INVALID_INSERTED_SHOT_ID", f"{unit_path}.inserted_shot_id", "Inserted-shot ids must be unique and use ADD followed by digits.")
            else:
                inserted_ids.append(inserted_id)
            if not valid_timecode(unit.get("generation_timecode")):
                add_issue(issues, "ERROR", "INSERTED_UNIT_TIMECODE_MISSING", f"{unit_path}.generation_timecode", "Map the inserted storyboard beat to exact seconds inside its generation clip.")
            else:
                generated = unit["generation_timecode"]
                unit_generation_timecodes.append((float(generated["start"]), float(generated["end"]), unit_path))
            if not has_text(unit.get("storyboard_description")):
                add_issue(issues, "ERROR", "INSERTED_STORYBOARD_DESCRIPTION_MISSING", f"{unit_path}.storyboard_description", "Every inserted shot needs an editable storyboard description derived from source rhythm and the revised script.")
            if not has_text(unit.get("script_text")):
                add_issue(issues, "ERROR", "INSERTED_SHOT_SCRIPT_MISSING", f"{unit_path}.script_text", "Every inserted shot needs its assigned revised spoken script; write '无' only for a genuinely silent beat.")
            if not has_text(unit.get("insertion_rationale")):
                add_issue(issues, "ERROR", "INSERTED_UNIT_RATIONALE_MISSING", f"{unit_path}.insertion_rationale", "Explain why this added shot is required instead of pretending it came from the source.")
            if not has_text(unit.get("rhythm_anchor")):
                add_issue(issues, "ERROR", "INSERTED_UNIT_RHYTHM_ANCHOR_MISSING", f"{unit_path}.rhythm_anchor", "Anchor the added shot to the source edit rhythm and revised spoken script.")
            reference_ids = [str(value) for value in as_list(unit.get("source_reference_shot_ids")) if has_text(value)]
            if not reference_ids or any(value not in source_by_id for value in reference_ids):
                add_issue(issues, "ERROR", "INSERTED_UNIT_SOURCE_BASIS_MISSING", f"{unit_path}.source_reference_shot_ids", "Bind every added shot to one or more real source-shot IDs so its performance and rhythm are source-derived.")
            reference_frame = resolve_path(project_dir, unit.get("source_reference_frame"))
            if reference_frame is None or not reference_frame.is_file():
                add_issue(issues, "ERROR", "INSERTED_UNIT_REFERENCE_FRAME_MISSING", f"{unit_path}.source_reference_frame", "Bind an exact source/reference frame for the added shot; templates may supplement but cannot replace source evidence.")
            validate_performance_layers(project_dir, unit, unit_path, source_by_id, reference_ids, issues)
            character = shot.get("character") or {}
            if shot.get("visual_type") in {"person_product_showcase", "person_eating"} or character.get("hands_only") is True:
                layer_statuses = {
                    str(record.get("status"))
                    for record in (unit.get("source_performance_layers") or {}).values()
                    if isinstance(record, dict)
                }
                if not layer_statuses.intersection({"observed", "audible"}):
                    add_issue(issues, "ERROR", "SIX_LAYER_HUMAN_EVIDENCE_EMPTY", f"{unit_path}.source_performance_layers", "A visible person/hand action needs at least one observed or audible source evidence layer; templates may only supplement a named gap.")
            delivery_ids = [str(value) for value in as_list(unit.get("delivery_asset_ids")) if has_text(value)]
            if require_delivery_assets:
                validate_delivery_assets(
                    shot_id,
                    unit_path,
                    inserted_id or f"{shot_id}/inserted-{unit_index}",
                    unit,
                    delivery_ids,
                    "INSERTED_SHOT_APPROVED_IMAGE_MISSING",
                    "Every inserted storyboard shot must map to at least one independently approved target frame.",
                )

        known_positions = [source_order.index(item) for item in unit_ids if item in source_order]
        if known_positions and known_positions != list(range(min(known_positions), max(known_positions) + 1)):
            add_issue(issues, "ERROR", "NON_ADJACENT_SOURCE_SHOTS_MERGED", f"{base}.source_units", "Only chronologically adjacent source shots may be merged into one generation clip.")
        clip_duration = (shot.get("timecode") or {}).get("duration")
        if unit_generation_timecodes and isinstance(clip_duration, (int, float)):
            timeline = sorted(unit_generation_timecodes, key=lambda item: (item[0], item[1]))
            if abs(timeline[0][0]) > 0.02 or abs(timeline[-1][1] - float(clip_duration)) > 0.08:
                add_issue(issues, "ERROR", "GENERATION_UNIT_COVERAGE_MISMATCH", f"{base}.source_units|inserted_units[].generation_timecode", "Source and inserted unit timing together must start at 0.00 and cover the full generation clip.")
            for left, right in zip(timeline, timeline[1:]):
                delta = right[0] - left[1]
                if abs(delta) > 0.08:
                    code = "GENERATION_UNIT_TIMELINE_GAP" if delta > 0 else "GENERATION_UNIT_TIMELINE_OVERLAP"
                    add_issue(issues, "ERROR", code, f"{base}.source_units|inserted_units[].generation_timecode", f"Storyboard units must be continuous without gaps or overlaps: {left[2]} → {right[2]}.")
        if isinstance(clip_duration, (int, float)) and clip_duration < float(minimum) - 0.02:
            add_issue(issues, "ERROR", "GENERATION_CLIP_TOO_SHORT", f"{base}.timecode.duration", f"Merge adjacent short source shots until the continuous generation clip is at least {float(minimum):.2f} seconds; do not delete any source shot.")
        if len(unit_ids) > 1:
            if not has_text(shot.get("merge_reason")):
                add_issue(issues, "ERROR", "SHORT_SHOT_MERGE_REASON_MISSING", f"{base}.merge_reason", "Explain that adjacent source shots were merged for the minimum continuous generation duration.")
            if unit_durations and not any(value < float(minimum) - 0.02 for value in unit_durations):
                add_issue(issues, "ERROR", "UNNECESSARY_SOURCE_SHOT_MERGE", f"{base}.source_units", "Do not merge already-long source shots merely to reduce work or Prompt count.")

    if require_delivery_assets:
        for shot in shots:
            shot_id = str(shot.get("id") or "")
            selected_ids = selected_order_by_shot.get(shot_id, [])
            mapped_ids = selected_delivery_assets_by_shot.get(shot_id, [])
            if selected_ids != mapped_ids:
                add_issue(
                    issues,
                    "ERROR",
                    "SHOT_SELECTED_ASSET_ORDER_MISMATCH",
                    f"planning/asset_reuse_plan.json.shot_decisions.{shot_id}.selected_asset_ids",
                    "selected_asset_ids must equal the ordered concatenation of every SRC/ADD delivery_asset_ids in this generation segment; no unowned or omitted target frame is allowed.",
                )

    shot_positions = {str(shot.get("id")): index for index, shot in enumerate(shots)}
    for shot_index, shot in enumerate(shots):
        shot_id = str(shot.get("id") or f"index-{shot_index}")
        references = shot.get("continuity_boundary_references") or []
        path = f"shots/shot_manifest.json.{shot_id}.continuity_boundary_references"
        if not isinstance(references, list):
            add_issue(issues, "ERROR", "CONTINUITY_BOUNDARY_REFERENCES_INVALID", path, "continuity_boundary_references must be an optional array.")
            continue
        if not require_delivery_assets:
            continue
        seen_references: set[tuple[str, str]] = set()
        for reference_index, reference in enumerate(references):
            reference_path = f"{path}[{reference_index}]"
            if not isinstance(reference, dict):
                add_issue(issues, "ERROR", "CONTINUITY_BOUNDARY_REFERENCE_INVALID", reference_path, "Continuity boundary reference must be an object.")
                continue
            owner_unit_id = str(reference.get("owner_unit_id") or "")
            asset_id = str(reference.get("asset_id") or "")
            owner_shot_id = unit_owner_shots.get(owner_unit_id)
            if reference.get("continuity_boundary_reference") is not True:
                add_issue(issues, "ERROR", "CONTINUITY_BOUNDARY_FLAG_MISSING", f"{reference_path}.continuity_boundary_reference", "A repeated display reference must explicitly declare continuity_boundary_reference=true.")
            if not owner_shot_id or asset_id not in unit_delivery_assets.get(owner_unit_id, []):
                add_issue(issues, "ERROR", "CONTINUITY_BOUNDARY_OWNER_MISMATCH", reference_path, "The asset must remain bound to its real owner unit and cannot count for another unit.")
            elif abs(shot_positions.get(owner_shot_id, -999) - shot_index) != 1:
                add_issue(issues, "ERROR", "CONTINUITY_BOUNDARY_NOT_ADJACENT", reference_path, "An owner-unit frame may be repeated only across an immediately adjacent generation-segment boundary.")
            if not has_text(reference.get("responsibility")):
                add_issue(issues, "ERROR", "CONTINUITY_BOUNDARY_RESPONSIBILITY_MISSING", f"{reference_path}.responsibility", "State the continuity-boundary duty shown by this repeated owner-unit frame.")
            pair = (owner_unit_id, asset_id)
            if pair in seen_references:
                add_issue(issues, "ERROR", "CONTINUITY_BOUNDARY_REFERENCE_DUPLICATED", reference_path, "Do not repeat the same owner asset twice in one boundary-reference block.")
            seen_references.add(pair)

    if flattened_ids != source_order:
        missing = [item for item in source_order if item not in flattened_ids]
        duplicates = sorted({item for item in flattened_ids if flattened_ids.count(item) > 1})
        add_issue(
            issues,
            "ERROR",
            "SOURCE_SHOT_COVERAGE_MISMATCH",
            "shots/shot_manifest.json.shots[].source_units",
            f"Source shots must appear exactly once and in order. Missing={missing or 'none'}; duplicated={duplicates or 'none'}.",
        )


def validate_revised_script_coverage(
    project: Dict[str, Any],
    story: Dict[str, Any],
    shots: List[Dict[str, Any]],
    issues: List[Dict[str, Any]],
) -> None:
    """Block shortened, duplicated or image-only spoken copy before Word export."""
    if (project.get("project_rules") or {}).get("require_revised_script_full_coverage") is not True:
        return
    script = story.get("subtitle_script") or {}
    if script.get("provided_by_user") is not True or not has_text(script.get("text")):
        add_issue(issues, "ERROR", "REVISED_SCRIPT_NOT_LOCKED", "planning/story_plan.json.subtitle_script", "Canonical Prompt compilation requires the user's locked revised spoken script.")
        return
    expected_text = str(script.get("text"))
    expected_key = spoken_text_key(expected_text)
    expected_count = spoken_char_count(expected_text)
    if script.get("effective_characters") != expected_count:
        add_issue(issues, "ERROR", "REVISED_SCRIPT_COUNT_MISMATCH", "planning/story_plan.json.subtitle_script.effective_characters", f"Store the computed Han/letter/digit count {expected_count}; do not estimate the word count.")

    ordered_shots = sorted(shots, key=lambda item: float((item.get("timecode") or {}).get("start", 0)))
    unit_parts: List[str] = []
    audio_parts: List[str] = []
    for shot in ordered_shots:
        units: List[Dict[str, Any]] = []
        units.extend(item for item in (shot.get("source_units") or []) if isinstance(item, dict))
        units.extend(item for item in (shot.get("inserted_units") or []) if isinstance(item, dict))
        units.sort(key=lambda item: float((item.get("generation_timecode") or {}).get("start", 0)))
        unit_parts.extend(spoken_text_key(item.get("script_text")) for item in units)
        audio = shot.get("audio") or {}
        if audio.get("delivery_mode") in {"voiceover", "on_screen_speech"}:
            audio_parts.append(spoken_text_key(audio.get("script_text")))

    unit_key = "".join(unit_parts)
    audio_key = "".join(audio_parts)
    if unit_key != expected_key:
        add_issue(issues, "ERROR", "WORD_SCRIPT_COVERAGE_MISMATCH", "shots/shot_manifest.json.shots[].source_units|inserted_units[].script_text", f"Editable per-unit Word script has {len(unit_key)} effective characters but the locked revised script has {len(expected_key)}; content must match exactly without loss or duplication.")
    if audio_key != expected_key:
        add_issue(issues, "ERROR", "PROMPT_SCRIPT_COVERAGE_MISMATCH", "shots/shot_manifest.json.shots[].audio.script_text", f"Prompt audio allocation has {len(audio_key)} effective characters but the locked revised script has {len(expected_key)}; content must match exactly without loss or duplication.")


def validate_eating_plan(
    project: Dict[str, Any],
    source: Dict[str, Any],
    story: Dict[str, Any],
    shots: List[Dict[str, Any]],
    issues: List[Dict[str, Any]],
) -> None:
    commercial_emotion_enabled = bool(((project.get("project_rules") or {}).get("commercial_emotion_contract") or {}).get("enabled"))
    duration = source.get("duration")
    if not isinstance(duration, (int, float)):
        duration = (story.get("eating_plan") or {}).get("source_duration_seconds")
    if not isinstance(duration, (int, float)):
        return

    plan = story.get("eating_plan") or {}
    if duration >= 30 and (project.get("project_rules") or {}).get("eating_occurrences_must_be_non_contiguous") is not True:
        add_issue(
            issues,
            "ERROR",
            "EATING_NON_CONTIGUOUS_RULE_DISABLED",
            "project.json.project_rules.eating_occurrences_must_be_non_contiguous",
            "For videos at least 30 seconds long, the three whole-video eating events must be separated rhythmically; this is not a three-image burst per event.",
        )
    if (project.get("project_rules") or {}).get("require_visible_swallow_or_post_bite_reaction") is True:
        add_issue(
            issues,
            "ERROR",
            "GLOBAL_SWALLOW_OR_REACTION_REQUIREMENT_FORBIDDEN",
            "project.json.project_rules.require_visible_swallow_or_post_bite_reaction",
            "Do not globally force a swallow or post-bite reaction. Preserve it only on occurrences with exact source evidence.",
        )
    occurrences = plan.get("occurrences")
    if duration >= 30 and (not isinstance(occurrences, list) or not occurrences):
        add_issue(issues, "ERROR", "EATING_PLAN_MISSING", "planning/story_plan.json.eating_plan", "For source videos of at least 30 seconds, inventory source eating shots and plan only the missing rhythmic insertions.")
        return
    if duration < 30 and isinstance(occurrences, list) and not occurrences:
        return
    if not isinstance(occurrences, list):
        return

    source_count = plan.get("source_eating_occurrence_count")
    inserted_count = plan.get("inserted_eating_occurrence_count")
    target_count = plan.get("target_eating_occurrence_count")
    if not all(isinstance(value, int) and value >= 0 for value in (source_count, inserted_count, target_count)):
        add_issue(issues, "ERROR", "EATING_PLAN_COUNTS_INVALID", "planning/story_plan.json.eating_plan", "Store non-negative source, inserted and target eating-occurrence counts.")
        return

    minimum = int((project.get("project_rules") or {}).get("minimum_eating_occurrences_when_source_duration_gte_30", 3))
    expected_target = max(source_count, minimum) if duration >= 30 else source_count + inserted_count
    expected_inserted = max(0, minimum - source_count) if duration >= 30 else inserted_count
    if target_count != expected_target or inserted_count != expected_inserted:
        add_issue(issues, "ERROR", "EATING_INSERT_COUNT_MISMATCH", "planning/story_plan.json.eating_plan", f"Source count {source_count} requires exactly {expected_inserted} insertion(s) and target {expected_target}; never add three more when the source already contains them.")

    shot_positions = {str(shot.get("id")): index for index, shot in enumerate(shots)}
    shot_by_id = {str(shot.get("id")): shot for shot in shots}
    origin_counts = {"source": 0, "inserted": 0}
    occurrence_positions: List[Tuple[int, str, str]] = []
    occurrence_ids: set[str] = set()
    for index, occurrence in enumerate(occurrences):
        path = f"planning/story_plan.json.eating_plan.occurrences[{index}]"
        if not isinstance(occurrence, dict):
            add_issue(issues, "ERROR", "INVALID_EATING_OCCURRENCE", path, "Eating occurrence must be an object.")
            continue
        occurrence_id = str(occurrence.get("id") or "").strip()
        if not occurrence_id or occurrence_id in occurrence_ids:
            add_issue(issues, "ERROR", "INVALID_EATING_OCCURRENCE_ID", f"{path}.id", "Eating occurrence ids must be unique.")
        occurrence_ids.add(occurrence_id)
        origin = occurrence.get("origin")
        if origin not in origin_counts:
            add_issue(issues, "ERROR", "INVALID_EATING_OCCURRENCE_ORIGIN", f"{path}.origin", "Use source or inserted.")
            continue
        origin_counts[origin] += 1
        shot_id = str(occurrence.get("shot_id") or "")
        bound_units: List[Dict[str, Any]] = []
        if shot_id not in shot_positions:
            add_issue(issues, "ERROR", "EATING_OCCURRENCE_SHOT_MISSING", f"{path}.shot_id", f"Unknown generation clip: {shot_id or '<missing>'}.")
        else:
            occurrence_positions.append((shot_positions[shot_id], origin, occurrence_id))
            shot = shot_by_id[shot_id]
            if origin == "source":
                occurrence_source_ids = [str(value) for value in as_list(occurrence.get("source_shot_ids") or occurrence.get("source_shot_id")) if has_text(value)]
                shot_source_ids = {str(unit.get("source_shot_id")) for unit in (shot.get("source_units") or []) if isinstance(unit, dict)}
                if not occurrence_source_ids or any(value not in shot_source_ids for value in occurrence_source_ids):
                    add_issue(issues, "ERROR", "SOURCE_EATING_UNIT_MISMATCH", f"{path}.source_shot_ids", "Bind every source eating occurrence to the real SRC unit(s) inside the named generation clip.")
                bound_units = [
                    unit for unit in (shot.get("source_units") or [])
                    if isinstance(unit, dict) and str(unit.get("source_shot_id")) in occurrence_source_ids
                ]
            else:
                inserted_id = str(occurrence.get("inserted_shot_id") or "")
                shot_inserted_ids = {str(unit.get("inserted_shot_id")) for unit in (shot.get("inserted_units") or []) if isinstance(unit, dict)}
                if not inserted_id or inserted_id not in shot_inserted_ids:
                    add_issue(issues, "ERROR", "INSERTED_EATING_UNIT_MISMATCH", f"{path}.inserted_shot_id", "Bind every inserted eating occurrence to its exact ADD storyboard unit inside the named generation clip.")
                bound_units = [
                    unit for unit in (shot.get("inserted_units") or [])
                    if isinstance(unit, dict) and str(unit.get("inserted_shot_id")) == inserted_id
                ]
        occurrence_timecode = occurrence.get("generation_timecode")
        if not valid_timecode(occurrence_timecode):
            add_issue(issues, "ERROR", "EATING_OCCURRENCE_TIMECODE_MISSING", f"{path}.generation_timecode", "Mark exact seconds for the bite/eating action inside its generation clip.")
        elif bound_units and not any(timecode_contains(unit.get("generation_timecode"), occurrence_timecode, 0.001) for unit in bound_units):
            add_issue(issues, "ERROR", "EATING_OCCURRENCE_OUTSIDE_BOUND_UNIT", f"{path}.generation_timecode", "The eating interval must lie inside the exact SRC/ADD storyboard unit named by the occurrence.")
        if not has_text(occurrence.get("rhythm_rationale")):
            add_issue(issues, "ERROR", "EATING_RHYTHM_RATIONALE_MISSING", f"{path}.rhythm_rationale", "Explain how this separated eating beat follows source rhythm and the revised script.")
        if origin == "source" and not flatten_text(occurrence.get("source_evidence")):
            add_issue(issues, "ERROR", "SOURCE_EATING_EVIDENCE_MISSING", f"{path}.source_evidence", "Record observable source-video evidence for every source eating occurrence.")
        if origin == "inserted" and not has_text(occurrence.get("insertion_rationale")):
            add_issue(issues, "ERROR", "INSERTED_EATING_RATIONALE_MISSING", f"{path}.insertion_rationale", "Explain why this is one of the missing eating shots and why it belongs at this rhythm point.")
        appetite = occurrence.get("appetite_evidence") or {}
        for field in ("bite_readability", "crisp_sound", "product_state_change", "source_performance_basis"):
            if not has_text(appetite.get(field)):
                add_issue(issues, "ERROR", "APPETITE_EVIDENCE_MISSING", f"{path}.appetite_evidence.{field}", "Eating prompts must derive observable appetizing performance from the source, with templates only filling a justified gap.")
        if occurrence.get("visible_swallow_required") is True:
            evidence = " ".join(flatten_text(occurrence.get("source_evidence")))
            if "吞咽" not in evidence and "swallow" not in evidence.lower():
                add_issue(issues, "ERROR", "UNSUPPORTED_SWALLOW_OR_REACTION", f"{path}.visible_swallow_required", "Do not invent a visible swallow or post-eating reaction when the source does not show it.")
        if occurrence.get("post_bite_reaction_required") is True:
            evidence = " ".join(flatten_text(occurrence.get("source_evidence"))).lower()
            reaction_terms = ("吃后反应", "咬后反应", "表情变化", "点头", "眼神变化", "reaction", "nod")
            if not any(term in evidence for term in reaction_terms):
                add_issue(issues, "ERROR", "UNSUPPORTED_SWALLOW_OR_REACTION", f"{path}.post_bite_reaction_required", "Do not invent a post-bite reaction when the source evidence does not show one.")
        speech = occurrence.get("speech_after_bite") or {}
        if speech.get("enabled") is True:
            allowed_triggers = {"bite_completed", "product_left_mouth", "chewing_finished", "swallow_completed"}
            if speech.get("start_trigger") not in allowed_triggers:
                add_issue(issues, "ERROR", "POST_BITE_SPEECH_TRIGGER_INVALID", f"{path}.speech_after_bite.start_trigger", "Speech may start immediately after the bite/product leaves the mouth; visible swallowing is optional unless source-supported.")
            if not has_text(speech.get("mouth_speakable_evidence")):
                add_issue(issues, "ERROR", "POST_BITE_SPEECH_EVIDENCE_MISSING", f"{path}.speech_after_bite.mouth_speakable_evidence", "Explain the visible boundary: bite completed, product left the mouth, lips/jaw restored to a speakable state.")
            if speech.get("start_trigger") == "swallow_completed":
                evidence = " ".join(flatten_text(occurrence.get("source_evidence"))).lower()
                if "吞咽" not in evidence and "swallow" not in evidence:
                    add_issue(issues, "ERROR", "UNSUPPORTED_SWALLOW_OR_REACTION", f"{path}.speech_after_bite.start_trigger", "Do not delay immediate speech to an invented swallow when the source has no swallow evidence.")
        if commercial_emotion_enabled and shot_id in shot_by_id:
            required_phases = [str(value) for value in as_list(occurrence.get("required_phases")) if has_text(value)]
            if not required_phases:
                add_issue(issues, "ERROR", "EATING_REQUIRED_PHASES_MISSING", f"{path}.required_phases", "按原片逐项列出实际可见的靠近、咬合、离嘴、闭口咀嚼等阶段；原片没有的吞咽/反应不要补。")
            else:
                phase_errors = occurrence_phase_binding_errors(
                    occurrence.get("phase_beat_ids"),
                    [beat for beat in as_list(shot_by_id[shot_id].get("action_beats")) if isinstance(beat, dict)],
                    required_phases,
                    allow_shared_adjacent=True,
                )
                for code in sorted(phase_errors):
                    add_issue(issues, "ERROR", f"EATING_{code}", f"{path}.phase_beat_ids", "吃食 occurrence 必须把原片实际阶段依次绑定到唯一 action beat；不能用一句“品尝”概括，也不能把阶段倒序或绑定不存在的节拍。")

    if origin_counts["source"] != source_count or origin_counts["inserted"] != inserted_count or len(occurrences) != target_count:
        add_issue(issues, "ERROR", "EATING_OCCURRENCE_COUNT_DIVERGENCE", "planning/story_plan.json.eating_plan.occurrences", "Occurrence rows must exactly match the stored source, inserted and target counts.")

    occurrence_positions.sort()
    for left, right in zip(occurrence_positions, occurrence_positions[1:]):
        if right[0] - left[0] <= 1:
            add_issue(issues, "ERROR", "EATING_OCCURRENCES_CONTIGUOUS", "planning/story_plan.json.eating_plan.occurrences", f"{left[2]} and {right[2]} are consecutive; the three whole-video eating events must be separated by at least one non-eating rhythm beat.")
            if "inserted" in {left[1], right[1]}:
                add_issue(issues, "ERROR", "INSERTED_EATING_OCCURRENCES_CONTIGUOUS", "planning/story_plan.json.eating_plan.occurrences", f"{left[2]} and {right[2]} are consecutive; a newly inserted eating event cannot be adjacent to another eating event.")


def validate_break_plan(
    project: Dict[str, Any],
    product: Dict[str, Any],
    story: Dict[str, Any],
    shots: List[Dict[str, Any]],
    issues: List[Dict[str, Any]],
) -> None:
    rules = project.get("project_rules") or {}
    commercial_emotion_enabled = bool((rules.get("commercial_emotion_contract") or {}).get("enabled"))
    required = rules.get("require_hands_only_break_showcase") is True or is_butter_crisp_project(project, product)
    if not required:
        return
    plan = story.get("break_plan") or {}
    occurrences = plan.get("occurrences")
    if not isinstance(occurrences, list) or not occurrences:
        add_issue(issues, "ERROR", "BREAK_PLAN_MISSING", "planning/story_plan.json.break_plan", "Butter-crisp delivery requires rhythmic break-open planning, including a no-person hands-only product showcase.")
        return
    shot_by_id = {str(shot.get("id")): shot for shot in shots}
    hands_only_count = 0
    person_present_count = 0
    occurrence_ids: set[str] = set()
    for index, occurrence in enumerate(occurrences):
        path = f"planning/story_plan.json.break_plan.occurrences[{index}]"
        if not isinstance(occurrence, dict):
            add_issue(issues, "ERROR", "INVALID_BREAK_OCCURRENCE", path, "Break occurrence must be an object.")
            continue
        occurrence_id = str(occurrence.get("id") or "").strip()
        if not occurrence_id or occurrence_id in occurrence_ids:
            add_issue(issues, "ERROR", "INVALID_BREAK_OCCURRENCE_ID", f"{path}.id", "Break occurrence ids must be non-empty and unique.")
        occurrence_ids.add(occurrence_id)
        mode = occurrence.get("mode")
        if mode not in {"person_present", "hands_only_product"}:
            add_issue(issues, "ERROR", "INVALID_BREAK_MODE", f"{path}.mode", "Use person_present or hands_only_product.")
            continue
        shot_id = str(occurrence.get("shot_id") or "")
        shot = shot_by_id.get(shot_id)
        bound_units: List[Dict[str, Any]] = []
        if shot is None:
            add_issue(issues, "ERROR", "BREAK_OCCURRENCE_SHOT_MISSING", f"{path}.shot_id", f"Unknown generation clip: {shot_id or '<missing>'}.")
        origin = occurrence.get("origin")
        if origin not in {"source", "inserted"}:
            add_issue(issues, "ERROR", "INVALID_BREAK_OCCURRENCE_ORIGIN", f"{path}.origin", "Use source or inserted.")
        elif shot is not None and origin == "source":
            occurrence_source_ids = [str(value) for value in as_list(occurrence.get("source_shot_ids") or occurrence.get("source_shot_id")) if has_text(value)]
            shot_source_ids = {str(unit.get("source_shot_id")) for unit in (shot.get("source_units") or []) if isinstance(unit, dict)}
            if not occurrence_source_ids or any(value not in shot_source_ids for value in occurrence_source_ids):
                add_issue(issues, "ERROR", "SOURCE_BREAK_UNIT_MISMATCH", f"{path}.source_shot_ids", "Bind every source break occurrence to the real SRC unit(s) inside the named generation clip.")
            bound_units = [
                unit for unit in (shot.get("source_units") or [])
                if isinstance(unit, dict) and str(unit.get("source_shot_id")) in occurrence_source_ids
            ]
            if not flatten_text(occurrence.get("source_evidence")):
                add_issue(issues, "ERROR", "SOURCE_BREAK_EVIDENCE_MISSING", f"{path}.source_evidence", "Record the exact source action/rhythm evidence for a source break occurrence.")
        elif shot is not None:
            inserted_id = str(occurrence.get("inserted_shot_id") or "")
            shot_inserted_ids = {str(unit.get("inserted_shot_id")) for unit in (shot.get("inserted_units") or []) if isinstance(unit, dict)}
            if not inserted_id or inserted_id not in shot_inserted_ids:
                add_issue(issues, "ERROR", "INSERTED_BREAK_UNIT_MISMATCH", f"{path}.inserted_shot_id", "Bind every inserted break occurrence to its exact ADD storyboard unit inside the named generation clip.")
            bound_units = [
                unit for unit in (shot.get("inserted_units") or [])
                if isinstance(unit, dict) and str(unit.get("inserted_shot_id")) == inserted_id
            ]
            if not has_text(occurrence.get("insertion_rationale")):
                add_issue(issues, "ERROR", "INSERTED_BREAK_RATIONALE_MISSING", f"{path}.insertion_rationale", "Explain why this break proof is inserted at this source/script rhythm point.")
        if mode == "hands_only_product":
            hands_only_count += 1
            character = (shot or {}).get("character") or {}
            if (shot or {}).get("visual_type") != "product_showcase" or character.get("present") is not False or character.get("hands_only") is not True:
                add_issue(issues, "ERROR", "HANDS_ONLY_BREAK_VISUAL_INVALID", path, "The hard-required break showcase must show only hands and product: no face/body/person, visual_type=product_showcase, character.present=false, hands_only=true.")
            product_state = (shot or {}).get("product_state") or {}
            count_text = str(product_state.get("count") or "").strip().lower()
            if count_text not in {"1", "1.0", "一", "一根", "单根", "one", "one stick"}:
                add_issue(issues, "ERROR", "HANDS_ONLY_BREAK_SINGLE_STICK_REQUIRED", f"{path}.shot_id", "The mandatory hands-only showcase must break one and only one stick; bind product_state.count=1.")
            if product_state.get("state") != "breaking":
                add_issue(issues, "ERROR", "HANDS_ONLY_BREAK_STATE_INVALID", f"{path}.shot_id", "The mandatory hands-only event must use the concrete breaking product state, not a generic held/showcase state.")
        elif mode == "person_present":
            person_present_count += 1
            character = (shot or {}).get("character") or {}
            if character.get("present") is not True or (shot or {}).get("visual_type") not in {"person_product_showcase", "person_eating"}:
                add_issue(issues, "ERROR", "PERSON_BREAK_VISUAL_INVALID", path, "person_present break occurrences require a visible person; use hands_only_product for the mandatory no-person showcase.")
        occurrence_timecode = occurrence.get("generation_timecode")
        if not valid_timecode(occurrence_timecode):
            add_issue(issues, "ERROR", "BREAK_OCCURRENCE_TIMECODE_MISSING", f"{path}.generation_timecode", "Mark exact rhythmic break seconds inside the generation clip.")
        elif bound_units and not any(timecode_contains(unit.get("generation_timecode"), occurrence_timecode, 0.001) for unit in bound_units):
            add_issue(issues, "ERROR", "BREAK_OCCURRENCE_OUTSIDE_BOUND_UNIT", f"{path}.generation_timecode", "The break interval must lie inside the exact SRC/ADD unit named by this occurrence.")
        if not has_text(occurrence.get("rhythm_rationale")):
            add_issue(issues, "ERROR", "BREAK_RHYTHM_RATIONALE_MISSING", f"{path}.rhythm_rationale", "Explain how the break proof fits the source rhythm and revised script.")
        break_terms = ("掰开", "掰断", "折断", "脆裂", "断面", "snap", "break", "fracture")
        fracture_terms = ("断面", "断裂面", "脆裂", "fracture")
        crumb_terms = ("碎屑", "掉渣", "酥渣", "crumb")
        same_stick_terms = ("同一根", "同根", "same stick")
        two_piece_terms = ("两段", "两截", "互补", "two pieces", "two-piece")
        snap_sound_terms = ("咔嚓", "脆响", "crack", "snap")
        unit_text = " ".join(str(unit.get("storyboard_description") or "") for unit in bound_units).lower()
        action_beats = [beat for beat in as_list((shot or {}).get("action_beats")) if isinstance(beat, dict)]
        if commercial_emotion_enabled and shot is not None:
            break_phases = ("prepare", "tension", "snap", "separate", "reveal")
            phase_errors = occurrence_phase_binding_errors(
                occurrence.get("phase_beat_ids"),
                action_beats,
                break_phases,
                allow_shared_adjacent=True,
            )
            for code in sorted(phase_errors):
                add_issue(issues, "ERROR", f"BREAK_{code}", f"{path}.phase_beat_ids", "掰断 occurrence 必须按准备受力→张力→单次脆断→两半分离→断面展示绑定 action beat；至少拆成两个节拍，不能几秒一笔带过。")
        action_text = " ".join(
            str(beat.get(field) or "")
            for beat in action_beats
            for field in ("action", "product_change", "foley_cue")
        ).lower()
        foley_text = str(((shot or {}).get("audio") or {}).get("foley") or "").lower()
        if bound_units and not any(term in unit_text for term in break_terms):
            add_issue(issues, "ERROR", "BREAK_UNIT_DESCRIPTION_MISSING", f"{path}.shot_id", "The bound SRC/ADD storyboard description must visibly describe the break-open action and fracture proof.")
        if shot is not None and not any(term in action_text for term in break_terms):
            add_issue(issues, "ERROR", "BREAK_ACTION_BEAT_MISSING", f"shots/shot_manifest.json.{shot_id}.action_beats", "A timed action/product-change beat must carry the actual break-open event.")
        if shot is not None and not any(term in foley_text for term in ("咔嚓", "脆裂", "碎屑", "snap", "crack")):
            add_issue(issues, "ERROR", "BREAK_FOLEY_NOT_BOUND", f"shots/shot_manifest.json.{shot_id}.audio.foley", "Bind a short synchronized crisp snap/crumb foley cue to the break action.")
        action_beat_id = str(occurrence.get("action_beat_id") or "").strip()
        bound_action_beats = [beat for beat in action_beats if str(beat.get("id") or "").strip() == action_beat_id]
        if not action_beat_id or len(bound_action_beats) != 1:
            add_issue(issues, "ERROR", "BREAK_ACTION_BEAT_BINDING_MISSING", f"{path}.action_beat_id", "Bind the occurrence to exactly one uniquely identified timed action beat; metadata alone cannot prove a break event.")
        else:
            bound_beat = bound_action_beats[0]
            beat_start, beat_end = bound_beat.get("start"), bound_beat.get("end")
            if not isinstance(beat_start, (int, float)) or not isinstance(beat_end, (int, float)) or beat_end <= beat_start:
                add_issue(issues, "ERROR", "BREAK_ACTION_BEAT_TIMECODE_INVALID", f"shots/shot_manifest.json.{shot_id}.action_beats.{action_beat_id}", "The bound break action beat needs a valid start/end interval.")
            else:
                beat_timecode = {"start": beat_start, "end": beat_end, "duration": beat_end - beat_start}
                if valid_timecode(occurrence_timecode) and not timecode_contains(beat_timecode, occurrence_timecode, 0.001):
                    add_issue(issues, "ERROR", "BREAK_ACTION_BEAT_TIMECODE_MISMATCH", f"{path}.generation_timecode", "The exact break occurrence interval must lie inside its bound action beat.")
            bound_beat_text = " ".join(
                str(bound_beat.get(field) or "")
                for field in ("action", "product_change", "foley_cue")
            ).lower()
            required_text_groups = (
                (break_terms, "BREAK_ACTION_BEAT_MISSING", "The bound beat must perform the actual break-open action."),
                (fracture_terms, "BREAK_FRACTURE_NOT_BOUND", "The bound beat must show the orange-gold fracture surface."),
                (crumb_terms, "BREAK_CRUMBS_NOT_BOUND", "The bound beat must emit a restrained non-zero amount of crumbs from the fracture point."),
                (same_stick_terms, "BREAK_SAME_STICK_NOT_BOUND", "The bound beat must state that the two pieces come from the same stick."),
                (two_piece_terms, "BREAK_TWO_PIECE_CONSERVATION_NOT_BOUND", "The bound beat must preserve exactly two complementary pieces."),
                (snap_sound_terms, "BREAK_SNAP_SOUND_NOT_BOUND", "The bound beat must carry the frame-synchronous crisp snap cue."),
            )
            for terms, code, message in required_text_groups:
                if not any(term in bound_beat_text for term in terms):
                    add_issue(issues, "ERROR", code, f"shots/shot_manifest.json.{shot_id}.action_beats.{action_beat_id}", message)
        proof = occurrence.get("crisp_proof") or {}
        if proof.get("action_beat_id") != action_beat_id:
            add_issue(issues, "ERROR", "CRISP_PROOF_ACTION_BEAT_MISMATCH", f"{path}.crisp_proof.action_beat_id", "Structured crisp proof must cross-reference the same concrete action beat as the occurrence.")
        for field in ("single_snap", "fracture_visible", "material_conservation_locked"):
            if proof.get(field) is not True:
                add_issue(issues, "ERROR", "CRISP_BREAK_PROOF_MISSING", f"{path}.crisp_proof.{field}", "Require one crisp snap, visible fracture and conservation of the same stick.")
        crumbs = proof.get("crumbs") or {}
        break_physics = product.get("break_physics") or {}
        required_crumb_min = break_physics.get("crumb_count_minimum", 3)
        required_crumb_max = break_physics.get("crumb_count_maximum", 8)
        if (
            not isinstance(required_crumb_min, int)
            or not isinstance(required_crumb_max, int)
            or required_crumb_min < 1
            or required_crumb_max < required_crumb_min
        ):
            add_issue(issues, "ERROR", "PRODUCT_CRUMB_CONTRACT_INVALID", "library/product_bible.json.break_physics", "Butter-crisp profile must define a restrained, non-zero crumb range.")
            required_crumb_min, required_crumb_max = 1, 8
        if (
            not isinstance(crumbs.get("minimum"), int)
            or not isinstance(crumbs.get("maximum"), int)
            or not required_crumb_min <= crumbs["minimum"] <= crumbs["maximum"] <= required_crumb_max
        ):
            add_issue(issues, "ERROR", "CRUMB_RANGE_MISSING", f"{path}.crisp_proof.crumbs", f"Lock restrained visible crumbs to the non-zero profile range {required_crumb_min}–{required_crumb_max}, emitted from the real fracture point.")
        for field, message in (
            ("complementary_orange_gold_fracture", "Describe the two complementary orange-gold flaky fracture faces."),
            ("same_stick_two_piece_conservation", "Prove both pieces come from the same stick and preserve total mass/length."),
            ("sound_sync", "Describe frame-synchronous snap and crumb foley timing."),
        ):
            if not has_text(proof.get(field)):
                add_issue(issues, "ERROR", "CRISP_BREAK_STRUCTURED_PROOF_MISSING", f"{path}.crisp_proof.{field}", message)
        if not has_text(proof.get("foley")):
            add_issue(issues, "ERROR", "CRISP_FOLEY_MISSING", f"{path}.crisp_proof.foley", "Describe the short crisp snap and crumb sound.")
    if hands_only_count < 1:
        add_issue(issues, "ERROR", "HANDS_ONLY_BREAK_SHOWCASE_MISSING", "planning/story_plan.json.break_plan.occurrences", "At least one rhythmic no-person, hands-only butter-crisp break showcase is mandatory.")
    if person_present_count < 1:
        add_issue(issues, "ERROR", "PERSON_PRESENT_BREAK_SHOWCASE_MISSING", "planning/story_plan.json.break_plan.occurrences", "Butter-crisp delivery also needs at least one rhythmically placed break event while a person is visibly present.")


def validate_package_artwork(
    project_dir: Path,
    project: Dict[str, Any],
    product: Dict[str, Any],
    shots: List[Dict[str, Any]],
    issues: List[Dict[str, Any]],
    reuse_plan: Optional[Dict[str, Any]] = None,
    *,
    require_candidate_qa: bool = True,
) -> None:
    rules = project.get("project_rules") or {}
    packaging_shots = [
        shot for shot in shots
        if (shot.get("product_state") or {}).get("packaging") not in (None, False, "none", "hidden")
    ]
    if not packaging_shots:
        return
    artwork = product.get("package_artwork") or {}
    if rules.get("package_artwork_policy") != "preserve_master_projection" or artwork.get("policy") != "preserve_master_projection":
        add_issue(issues, "ERROR", "PACKAGE_ARTWORK_POLICY_MISSING", "library/product_bible.json.package_artwork", "Lock approved package-face masters and project them onto the box; do not ask the image model to redraw print.")
    face_masters = artwork.get("face_masters") or {}
    legibility_threshold = artwork.get("minimum_legible_face_area_ratio", 0.08)
    if not isinstance(legibility_threshold, (int, float)) or not 0 <= float(legibility_threshold) <= 1:
        add_issue(issues, "ERROR", "PACKAGE_LEGIBILITY_THRESHOLD_INVALID", "library/product_bible.json.package_artwork.minimum_legible_face_area_ratio", "Set a 0–1 visible-area threshold for required text/pattern legibility.")
        legibility_threshold = 0.08
    inventory = {
        str(item.get("asset_id")): item
        for item in ((reuse_plan or {}).get("inventory") or [])
        if isinstance(item, dict) and has_text(item.get("asset_id"))
    }
    for shot in packaging_shots:
        shot_id = str(shot.get("id") or "<unknown>")
        state = shot.get("product_state") or {}
        shot_art = state.get("package_artwork") or {}
        faces = shot_art.get("visible_faces")
        if not isinstance(faces, list) or not faces:
            add_issue(issues, "ERROR", "PACKAGE_VISIBLE_FACE_INVENTORY_MISSING", f"shots/shot_manifest.json.{shot_id}.product_state.package_artwork.visible_faces", "Inventory every actually visible box face and separate visible regions from natural occlusion/off-frame regions.")
            continue
        if shot_art.get("artwork_scaled_or_relaid_out") is True:
            add_issue(issues, "ERROR", "PACKAGE_ARTWORK_RECOMPOSED", f"shots/shot_manifest.json.{shot_id}.product_state.package_artwork", "Never shrink, move or relayout package art merely to force all printing into frame.")
        box_faces: Dict[str, set[str]] = {}
        for index, face in enumerate(faces):
            path = f"shots/shot_manifest.json.{shot_id}.product_state.package_artwork.visible_faces[{index}]"
            if not isinstance(face, dict):
                add_issue(issues, "ERROR", "INVALID_PACKAGE_FACE", path, "Visible face record must be an object.")
                continue
            face_name = str(face.get("face") or "")
            if face_name not in {"front", "side", "top"}:
                add_issue(issues, "ERROR", "INVALID_PACKAGE_FACE_NAME", f"{path}.face", "Inventory front, side and top for every box; each may be visible, occluded or hidden.")
            box_id = str(face.get("box_id") or "").strip()
            if not box_id:
                add_issue(issues, "ERROR", "PACKAGE_BOX_ID_MISSING", f"{path}.box_id", "Assign every physical box a stable box_id so no face can be omitted or borrowed from another box.")
            else:
                box_faces.setdefault(box_id, set()).add(face_name)
            master_value = face.get("master_reference") or face_masters.get(face_name)
            master_path = resolve_path(project_dir, master_value)
            if master_path is None or not master_path.is_file():
                add_issue(issues, "ERROR", "PACKAGE_FACE_REFERENCE_MISSING", f"{path}.master_reference", f"Provide an approved master for the visible {face_name or 'unknown'} face.")
            visibility_state = face.get("visibility_state")
            if visibility_state not in {"visible", "occluded", "hidden"}:
                add_issue(issues, "ERROR", "PACKAGE_FACE_VISIBILITY_INVALID", f"{path}.visibility_state", "Classify each physical face as visible, occluded or hidden.")
            visible_extent = face.get("visible_extent")
            if visible_extent not in {"full", "partial", "none"}:
                add_issue(issues, "ERROR", "PACKAGE_FACE_EXTENT_INVALID", f"{path}.visible_extent", "Use full, partial or none for the visible extent.")
            if visibility_state == "hidden":
                if visible_extent != "none" or not has_text(face.get("not_applicable_reason")):
                    add_issue(issues, "ERROR", "PACKAGE_HIDDEN_FACE_REASON_MISSING", path, "A hidden face must use visible_extent=none and explain the source-locked occlusion/off-frame reason.")
                continue
            expected_regions = flatten_text(face.get("expected_visible_regions"))
            if not expected_regions:
                add_issue(issues, "ERROR", "PACKAGE_VISIBLE_REGION_EXPECTATION_MISSING", f"{path}.expected_visible_regions", "List every logo/text/pattern fragment that should remain visible on this physical face.")
            polygon = face.get("expected_visible_polygon")
            if not isinstance(polygon, list) or len(polygon) < 3 or any(not isinstance(point, list) or len(point) != 2 or not all(isinstance(value, (int, float)) for value in point) for point in polygon):
                add_issue(issues, "ERROR", "PACKAGE_VISIBLE_POLYGON_MISSING", f"{path}.expected_visible_polygon", "Store at least three image-space polygon points for the actually visible face region.")
            area_ratio = face.get("visible_area_ratio")
            if not isinstance(area_ratio, (int, float)) or isinstance(area_ratio, bool) or not 0 < float(area_ratio) <= 1:
                add_issue(issues, "ERROR", "PACKAGE_VISIBLE_AREA_INVALID", f"{path}.visible_area_ratio", "Store the visible face area ratio as a number in (0,1].")
            legibility_required = face.get("legibility_required")
            if not isinstance(legibility_required, bool):
                add_issue(issues, "ERROR", "PACKAGE_LEGIBILITY_FLAG_MISSING", f"{path}.legibility_required", "State whether this face is large enough for strict text/pattern legibility QA.")
            elif isinstance(area_ratio, (int, float)) and not isinstance(area_ratio, bool):
                expected_legibility = float(area_ratio) >= float(legibility_threshold)
                if legibility_required != expected_legibility:
                    add_issue(issues, "ERROR", "PACKAGE_LEGIBILITY_FLAG_MISMATCH", f"{path}.legibility_required", f"legibility_required must be {expected_legibility} at the configured visible-area threshold {float(legibility_threshold):.3f}.")
            if visible_extent == "partial" and face.get("natural_crop_or_occlusion") is not True:
                add_issue(issues, "ERROR", "PACKAGE_ARTWORK_VISIBLE_REGION_MISSING", f"{path}.natural_crop_or_occlusion", "A partial face is acceptable only when the missing region is explicitly accounted for by natural crop or occlusion.")
            if face.get("projection_method") not in {"homography", "deterministic_composite", "protected_master_projection"}:
                add_issue(issues, "ERROR", "PACKAGE_ARTWORK_PROJECTION_INVALID", f"{path}.projection_method", "Use a deterministic master projection for readable package print.")
            if not require_candidate_qa:
                continue
            evidence = face.get("qa_evidence") or {}
            if not isinstance(evidence, dict) or not evidence:
                add_issue(issues, "ERROR", "PACKAGE_ARTWORK_EVIDENCE_MISSING", f"{path}.qa_evidence", "Attach per-face crop/hash/checkpoint evidence; a bare qa_status label is not proof.")
            else:
                crop_path = resolve_path(project_dir, evidence.get("candidate_face_crop"))
                if crop_path is None or not crop_path.is_file():
                    add_issue(issues, "ERROR", "PACKAGE_ARTWORK_CANDIDATE_CROP_MISSING", f"{path}.qa_evidence.candidate_face_crop", "Save an original-resolution crop of the candidate visible face for QA.")
                elif evidence.get("candidate_face_crop_sha256") != sha256_file(crop_path):
                    add_issue(issues, "ERROR", "PACKAGE_ARTWORK_CANDIDATE_HASH_MISMATCH", f"{path}.qa_evidence.candidate_face_crop_sha256", "Candidate face-crop hash must match the file that was actually reviewed.")
                delivery_asset_id = str(evidence.get("delivery_asset_id") or "")
                unit_delivery_ids = {
                    str(asset_id)
                    for unit in [*(shot.get("source_units") or []), *(shot.get("inserted_units") or [])]
                    if isinstance(unit, dict)
                    for asset_id in as_list(unit.get("delivery_asset_ids"))
                    if has_text(asset_id)
                }
                if not delivery_asset_id or delivery_asset_id not in unit_delivery_ids:
                    add_issue(issues, "ERROR", "PACKAGE_CROP_ASSET_BINDING_MISSING", f"{path}.qa_evidence.delivery_asset_id", "Bind this crop to the exact approved delivery image used by a SRC/ADD card in this shot.")
                parent_asset = inventory.get(delivery_asset_id)
                parent_path = resolve_path(project_dir, (parent_asset or {}).get("path"))
                if parent_path is None or not parent_path.is_file() or evidence.get("parent_image_sha256") != sha256_file(parent_path):
                    add_issue(issues, "ERROR", "PACKAGE_PARENT_IMAGE_HASH_MISMATCH", f"{path}.qa_evidence.parent_image_sha256", "The reviewed crop must name the actual DOCX delivery image and match its parent SHA-256.")
                crop_rect = evidence.get("crop_rect_xywh")
                if not isinstance(crop_rect, list) or len(crop_rect) != 4 or any(not isinstance(value, int) or isinstance(value, bool) or value < 0 for value in crop_rect) or crop_rect[2] <= 0 or crop_rect[3] <= 0:
                    add_issue(issues, "ERROR", "PACKAGE_CROP_RECT_MISSING", f"{path}.qa_evidence.crop_rect_xywh", "Store non-negative integer [x,y,width,height] coordinates for the reviewed crop.")
                if master_path is not None and master_path.is_file() and evidence.get("master_sha256") != sha256_file(master_path):
                    add_issue(issues, "ERROR", "PACKAGE_ARTWORK_REFERENCE_HASH_MISMATCH", f"{path}.qa_evidence.master_sha256", "Master hash must match the approved face reference used for projection and review.")
                projection_manifest_path = resolve_path(project_dir, evidence.get("projection_manifest"))
                projection_manifest: Dict[str, Any] = {}
                if projection_manifest_path is None or not projection_manifest_path.is_file():
                    add_issue(issues, "ERROR", "PACKAGE_PROJECTION_MANIFEST_MISSING", f"{path}.qa_evidence.projection_manifest", "Attach the internal manifest produced by project_package_master.py for every visible or occluded package face.")
                elif evidence.get("projection_manifest_sha256") != sha256_file(projection_manifest_path):
                    add_issue(issues, "ERROR", "PACKAGE_PROJECTION_MANIFEST_HASH_MISMATCH", f"{path}.qa_evidence.projection_manifest_sha256", "Projection-manifest SHA-256 must match the exact QA evidence file.")
                else:
                    try:
                        projection_manifest = json.loads(projection_manifest_path.read_text(encoding="utf-8"))
                    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
                        add_issue(issues, "ERROR", "PACKAGE_PROJECTION_MANIFEST_INVALID", f"{path}.qa_evidence.projection_manifest", "Projection manifest must be readable UTF-8 JSON.")
                        projection_manifest = {}
                if projection_manifest:
                    manifest_master = projection_manifest.get("master") or {}
                    manifest_output = projection_manifest.get("output") or {}
                    target_quad = projection_manifest.get("target_quad_tl_tr_br_bl")
                    manifest_checks = (
                        projection_manifest.get("schema_version") == "package-master-projection-v1.0",
                        projection_manifest.get("face") == face_name,
                        projection_manifest.get("projection_method") == "homography",
                        projection_manifest.get("model_redraw_used") is False,
                        isinstance(target_quad, list) and len(target_quad) == 4,
                        master_path is not None
                        and master_path.is_file()
                        and manifest_master.get("sha256") == sha256_file(master_path),
                        parent_path is not None
                        and parent_path.is_file()
                        and manifest_output.get("sha256") == sha256_file(parent_path),
                    )
                    if not all(manifest_checks):
                        add_issue(issues, "ERROR", "PACKAGE_PROJECTION_MANIFEST_FACT_MISMATCH", f"{path}.qa_evidence.projection_manifest", "Projection manifest must bind this face, approved master and final owner image, preserve the four-corner homography, and report model_redraw_used=false.")
                checkpoints = evidence.get("visible_region_checkpoints")
                if not isinstance(checkpoints, list) or not checkpoints:
                    add_issue(issues, "ERROR", "PACKAGE_ARTWORK_REGION_CHECK_MISSING", f"{path}.qa_evidence.visible_region_checkpoints", "Record every expected visible logo/text/pattern region as a QA checkpoint.")
                else:
                    checkpoint_ids = [str(checkpoint.get("id")) for checkpoint in checkpoints if isinstance(checkpoint, dict) and has_text(checkpoint.get("id"))]
                    if set(checkpoint_ids) != set(expected_regions) or len(checkpoint_ids) != len(set(checkpoint_ids)):
                        add_issue(issues, "ERROR", "PACKAGE_REGION_SET_MISMATCH", f"{path}.qa_evidence.visible_region_checkpoints", "Checkpoint ids must equal the expected visible-region set exactly, with no omissions, extras or duplicates.")
                    for checkpoint_index, checkpoint in enumerate(checkpoints):
                        checkpoint_path = f"{path}.qa_evidence.visible_region_checkpoints[{checkpoint_index}]"
                        if not isinstance(checkpoint, dict) or not has_text(checkpoint.get("id")) or checkpoint.get("status") != "matched":
                            add_issue(issues, "ERROR", "PACKAGE_ARTWORK_FRAGMENT_MISSING", checkpoint_path, "Every expected visible artwork region must be explicitly matched; missing or uncertain fragments block approval.")
                status_checks = {
                    "text_legibility": ("PACKAGE_TEXT_GARBLED", "Visible package text must match the master or be explicitly not_applicable."),
                    "orientation": ("PACKAGE_ARTWORK_MIRRORED", "Artwork orientation must match the master and must not be mirrored."),
                    "cross_edge_registration": ("PACKAGE_EDGE_REGISTRATION_MISMATCH", "Artwork crossing a fold/edge must register correctly or be marked not_applicable."),
                    "occlusion_scope": ("PACKAGE_OCCLUSION_SCOPE_INVALID", "Only source/locked natural occlusion and off-frame regions may be excluded."),
                }
                for field, (code, message) in status_checks.items():
                    if evidence.get(field) not in {"matched", "not_applicable"}:
                        add_issue(issues, "ERROR", code, f"{path}.qa_evidence.{field}", message)
                    elif evidence.get(field) == "not_applicable" and not has_text(evidence.get(f"{field}_reason")):
                        add_issue(issues, "ERROR", "PACKAGE_QA_NOT_APPLICABLE_REASON_MISSING", f"{path}.qa_evidence.{field}_reason", "Every not_applicable QA status needs a concrete geometric/visibility reason.")
                if evidence.get("model_redraw_detected") is not False:
                    add_issue(issues, "ERROR", "PACKAGE_ARTWORK_REDRAWN", f"{path}.qa_evidence.model_redraw_detected", "Reject any candidate whose print was redrawn instead of protected/projected from the master.")
                if evidence.get("unexpected_missing_region") is not False:
                    add_issue(issues, "ERROR", "PACKAGE_ARTWORK_FRAGMENT_MISSING", f"{path}.qa_evidence.unexpected_missing_region", "Reject any visible-region loss not explained by locked natural crop or occlusion.")
            if face.get("qa_status") != "approved":
                add_issue(issues, "ERROR", "PACKAGE_ARTWORK_QA_NOT_APPROVED", f"{path}.qa_status", "Approve visible-region artwork, orientation, cross-edge alignment and legibility before the frame enters Word.")
        for box_id, inventoried_faces in box_faces.items():
            missing_faces = sorted({"front", "side", "top"} - inventoried_faces)
            if missing_faces:
                add_issue(issues, "ERROR", "PACKAGE_FACE_INVENTORY_INCOMPLETE", f"shots/shot_manifest.json.{shot_id}.product_state.package_artwork.visible_faces", f"Box {box_id} is missing face inventory records: {missing_faces}; hidden/occluded still need explicit rows.")


def validate_prompt_output_ownership(
    project_dir: Path,
    shots: Sequence[Dict[str, Any]],
    issues: List[Dict[str, Any]],
) -> None:
    """Reject Prompt deliverables that did not come from the canonical compiler."""
    prompt_dir = project_dir / "prompts"
    if not prompt_dir.is_dir():
        return
    expected = {
        f"{shot.get('id')}.md"
        for shot in shots
        if isinstance(shot, dict) and has_text(shot.get("id"))
    }
    allowed = expected | {PROMPT_ONLY_AGGREGATE}
    for path in sorted(prompt_dir.glob("*.md")):
        if path.name not in allowed:
            add_issue(
                issues,
                "ERROR",
                "NON_CANONICAL_PROMPT_BYPASS",
                str(path.relative_to(project_dir)),
                "Root Prompt markdown may only be compiler-owned Sxxx.md files or canonical_prompt_only.md; move drafts outside prompts/ and compile again.",
            )
    aggregate = prompt_dir / PROMPT_ONLY_AGGREGATE
    if aggregate.is_file() and not (prompt_dir / "generation_pack.json").is_file():
        add_issue(
            issues,
            "ERROR",
            "NON_CANONICAL_PROMPT_BYPASS",
            str(aggregate.relative_to(project_dir)),
            "The canonical aggregate is valid only when accompanied by its compiler-owned generation pack and delivery receipt.",
        )


def validate_project(project_dir: Path, bundle: Dict[str, Dict[str, Any]], issues: List[Dict[str, Any]]) -> None:
    project = bundle["project"]
    product = bundle["product"]
    style = bundle["style"]
    corrections = bundle["corrections"]
    shots = bundle["shots"]
    story = bundle["story"]
    knowledge = bundle["knowledge"]
    avatars = bundle["avatars"]
    product_library = bundle["product_library"]
    source = bundle["source"]
    reuse_plan = bundle["asset_reuse"]

    try:
        execution_tier = normalized_execution_tier(project)
    except ValueError as exc:
        execution_tier = "full_delivery"
        add_issue(issues, "ERROR", "EXECUTION_TIER_INVALID", "project.json.execution_tier", str(exc))
    delivery_assets_required = execution_tier == "full_delivery"
    pixel_preflight_required = execution_tier in {"first_frame_only", "full_delivery"}
    workflow_path = project_dir / "planning" / "workflow_state.json"
    if workflow_path.is_file():
        workflow = load_json(workflow_path)
        workflow_tier = workflow.get("execution_tier")
        if workflow_tier is not None and workflow_tier != execution_tier:
            add_issue(
                issues,
                "ERROR",
                "EXECUTION_TIER_MISMATCH",
                "planning/workflow_state.json.execution_tier",
                "Workflow state cannot override project.json.execution_tier; migrate both files together.",
            )

    for field in ("project_id", "project_name", "platform", "aspect_ratio", "generation_mode", "product_profile", "style_profile"):
        if not has_text(project.get(field)):
            add_issue(issues, "ERROR", "missing_project_field", f"project.json.{field}", "Required project field is empty.")

    if not isinstance(project.get("prompt_length_contract"), dict):
        add_issue(issues, "ERROR", "PROMPT_LENGTH_CONTRACT_MISSING", "project.json.prompt_length_contract", "Persist one project-owned Prompt length contract; enabled=false disables both bounds.")
    else:
        try:
            normalized_prompt_length_contract(project)
        except ValueError as exc:
            add_issue(issues, "ERROR", "PROMPT_LENGTH_CONTRACT_INVALID", "project.json.prompt_length_contract", str(exc))
    if not isinstance(project.get("skill_release_lock"), dict):
        add_issue(issues, "WARN", "SKILL_RELEASE_LOCK_MISSING", "project.json.skill_release_lock", "Legacy project is pinned as unmanaged-legacy; explicitly migrate before adopting a new Skill release.")
    else:
        try:
            release_lock = normalized_skill_release_lock(project)
            current_release = current_release_manifest()
            if execution_tier not in {"source_intake", "diagnose_only"} and release_lock.get("bundle_release_id") != current_release.get("bundle_release_id"):
                add_issue(
                    issues,
                    "ERROR",
                    "LEGACY_PROJECT_EXECUTION_BLOCKED",
                    "project.json.skill_release_lock.bundle_release_id",
                    "This project is read-only under the current Skill. Create a non-destructive explicit migration copy before first-frame generation or Prompt compilation.",
                )
        except ValueError as exc:
            add_issue(issues, "ERROR", "SKILL_RELEASE_LOCK_INVALID", "project.json.skill_release_lock", str(exc))
    if execution_tier not in {"source_intake", "diagnose_only"} and project.get("product_profile") == "durian-daifuku-v1":
        add_issue(
            issues,
            "ERROR",
            "LEGACY_PRODUCT_CONTRACT_BLOCKED",
            "project.json.product_profile",
            "durian-daifuku-v1 is available only for historical replay. Explicitly migrate to durian-daifuku-v2 before execution.",
        )
    migration_requirements = project.get("migration_requirements") or {}
    if migration_requirements.get("requires_manual_shot_map_rebuild") is True:
        add_issue(issues, "ERROR", "MIGRATED_SHOT_MAP_REBUILD_REQUIRED", "project.json.migration_requirements", str(migration_requirements.get("reason") or "Rebuild the atomic SRC/ADD map before compile/export."))

    if not project.get("source_video"):
        add_issue(issues, "ERROR", "missing_source_video", "project.json.source_video", "Set the source video path after import.")
    else:
        source_path = resolve_path(project_dir, project.get("source_video"))
        if source_path is not None and not source_path.exists():
            add_issue(issues, "WARN", "source_video_unavailable", "project.json.source_video", f"Source path is not currently accessible: {source_path}")

    if project.get("product_profile") != product.get("profile_id"):
        add_issue(
            issues,
            "ERROR",
            "product_profile_mismatch",
            "library/product_bible.json.profile_id",
            "Product profile does not match project.json.",
        )
    if project.get("style_profile") != style.get("profile_id"):
        add_issue(
            issues,
            "ERROR",
            "style_profile_mismatch",
            "library/style_bible.json.profile_id",
            "Style profile does not match project.json.",
        )

    project_rules = project.get("project_rules") or {}
    if project_rules.get("speech_strategy") not in {"adaptive_from_script_and_source", "manual"}:
        add_issue(issues, "ERROR", "invalid_speech_strategy", "project.json.project_rules.speech_strategy", "Use adaptive_from_script_and_source or manual.")
    if project_rules.get("allow_voiceover") is False and project_rules.get("allow_on_screen_speech") is False:
        add_issue(issues, "WARN", "no_narration_mode", "project.json.project_rules", "Both voice-over and on-screen speech are disabled.")

    rules = corrections.get("rules")
    if not isinstance(rules, list):
        add_issue(issues, "ERROR", "invalid_correction_rules", "library/correction_memory.json.rules", "rules must be a list.")
    else:
        seen_ids = set()
        for index, rule in enumerate(rules):
            path = f"library/correction_memory.json.rules[{index}]"
            if not isinstance(rule, dict):
                add_issue(issues, "ERROR", "invalid_correction_rule", path, "Rule must be an object.")
                continue
            rule_id = rule.get("id")
            if not has_text(rule_id):
                add_issue(issues, "ERROR", "missing_rule_id", path, "Rule id is required.")
            elif rule_id in seen_ids:
                add_issue(issues, "ERROR", "duplicate_rule_id", path, f"Duplicate rule id: {rule_id}")
            else:
                seen_ids.add(rule_id)
            if rule.get("scope") not in VALID_SCOPES:
                add_issue(issues, "ERROR", "invalid_rule_scope", path, f"scope must be one of {sorted(VALID_SCOPES)}.")
            priority = rule.get("priority")
            if not isinstance(priority, int) or not 1 <= priority <= 100:
                add_issue(issues, "ERROR", "invalid_rule_priority", path, "priority must be an integer from 1 to 100.")
            if not has_text(rule.get("instruction")):
                add_issue(issues, "ERROR", "missing_rule_instruction", path, "instruction is required.")

    validate_story_plan(story, issues)

    if not isinstance(knowledge.get("entries"), list):
        add_issue(issues, "ERROR", "invalid_knowledge_entries", "library/knowledge_index.json.entries", "entries must be a list.")
    if not isinstance(avatars.get("avatars"), list):
        add_issue(issues, "ERROR", "invalid_avatar_entries", "library/avatar_library.json.avatars", "avatars must be a list.")
    product_entries = product_library.get("products")
    if not isinstance(product_entries, list) or not product_entries:
        add_issue(issues, "ERROR", "invalid_product_library", "library/product_library.json.products", "Products must contain the selected project product.")
    elif not any(item.get("id") == project.get("product_profile") for item in product_entries if isinstance(item, dict)):
        add_issue(issues, "ERROR", "selected_product_missing_from_library", "library/product_library.json.products", "The project product_profile must exist in product_library.json.")

    shot_items = shots.get("shots")
    if not isinstance(shot_items, list):
        add_issue(issues, "ERROR", "invalid_shot_list", "shots/shot_manifest.json.shots", "shots must be a list.")
        return
    if not shot_items:
        add_issue(issues, "ERROR", "no_shots", "shots/shot_manifest.json.shots", "Add at least one analyzed shot.")
        return

    state_profiles = product.get("state_profiles") or {}
    seen_shot_ids = set()
    for index, shot in enumerate(shot_items):
        validate_shot(
            project_dir,
            project,
            product,
            style,
            state_profiles,
            story,
            shot,
            index,
            seen_shot_ids,
            issues,
            require_delivery_assets=delivery_assets_required,
            require_pixel_preflight=pixel_preflight_required,
        )

    story_segments = story.get("segments") or []
    segment_ids = {segment.get("id") for segment in story_segments if isinstance(segment, dict) and has_text(segment.get("id"))}
    shot_ids = {shot.get("id") for shot in shot_items if isinstance(shot, dict) and has_text(shot.get("id"))}
    for shot in shot_items:
        if not isinstance(shot, dict):
            continue
        for segment_id in as_list(shot.get("script_segment_ids")):
            if segment_id not in segment_ids:
                add_issue(issues, "ERROR", "unknown_script_segment", f"shots/shot_manifest.json.{shot.get('id')}.script_segment_ids", f"Unknown story segment: {segment_id}")
    for segment in story_segments:
        if not isinstance(segment, dict):
            continue
        for shot_id in as_list(segment.get("assigned_shots")):
            if shot_id not in shot_ids:
                add_issue(issues, "ERROR", "unknown_assigned_shot", f"planning/story_plan.json.segments.{segment.get('id')}.assigned_shots", f"Unknown shot: {shot_id}")

    validate_source_shot_contract(
        project_dir,
        project,
        source,
        shot_items,
        reuse_plan,
        issues,
        require_delivery_assets=delivery_assets_required,
    )
    validate_revised_script_coverage(project, story, shot_items, issues)
    validate_eating_plan(project, source, story, shot_items, issues)
    validate_break_plan(project, product, story, shot_items, issues)
    validate_package_artwork(
        project_dir,
        project,
        product,
        shot_items,
        issues,
        reuse_plan,
        require_candidate_qa=delivery_assets_required,
    )
    validate_prompt_output_ownership(project_dir, shot_items, issues)
    validate_mix_and_pacing(story, shot_items, issues)

    commercial = project.get("commercial") or {}
    intended_use = commercial.get("intended_use", "internal_test")
    if intended_use == "commercial_release":
        for field in COMMERCIAL_CLEARANCE_FIELDS:
            if commercial.get(field) is not True:
                add_issue(issues, "BLOCK", "commercial_clearance_missing", f"project.json.commercial.{field}", "Commercial release is blocked until this field is true.")
        if not has_text(commercial.get("reviewer")):
            add_issue(issues, "BLOCK", "commercial_reviewer_missing", "project.json.commercial.reviewer", "Name the release reviewer.")
    elif intended_use not in {"internal_test", "client_review"}:
        add_issue(issues, "ERROR", "invalid_intended_use", "project.json.commercial.intended_use", "Use internal_test, client_review, or commercial_release.")

    if project.get("status") == "approved" and any(issue["level"] in {"ERROR", "BLOCK"} for issue in issues):
        add_issue(issues, "ERROR", "invalid_approved_status", "project.json.status", "Project cannot remain approved while errors or commercial blocks exist.")


def validate_durian_daifuku_v2_shot(
    project_dir: Path,
    product: Dict[str, Any],
    shot: Dict[str, Any],
    issues: List[Dict[str, Any]],
    base: str,
    *,
    require_pixel_preflight: bool = False,
) -> None:
    if product.get("profile_id") != "durian-daifuku-v2":
        return
    state_data = shot.get("product_state") or {}
    state_id = state_data.get("state")
    required = (product.get("required_shot_contract") or {}).get("fields") or []
    for field in required:
        if not state_data.get(field):
            add_issue(
                issues,
                "ERROR",
                f"DAIFUKU_{str(field).upper()}_MISSING",
                f"{base}.product_state.{field}",
                f"Durian-daifuku-v2 requires structured {field}; prose alone cannot lock pixels or a terminal state.",
            )

    scale_lock = state_data.get("scale_lock") or {}
    if scale_lock:
        if scale_lock.get("mode") != "physical_consistency":
            add_issue(issues, "ERROR", "DAIFUKU_SCALE_MODE_INVALID", f"{base}.product_state.scale_lock.mode", "Use physical_consistency for durian-daifuku-v2.")
        if scale_lock.get("source_scale_role") not in {"compatible_scale_anchor", "pose_only_incompatible_scale"}:
            add_issue(issues, "ERROR", "DAIFUKU_SOURCE_SCALE_ROLE_MISSING", f"{base}.product_state.scale_lock.source_scale_role", "Declare whether the source food is a compatible scale anchor or pose-only incompatible scale.")
        anchor = scale_lock.get("anchor") or {}
        allowed_anchor_types = set((product.get("scale_contract") or {}).get("required_anchor_types") or [])
        if anchor.get("type") not in allowed_anchor_types or not has_text(anchor.get("evidence")):
            add_issue(issues, "ERROR", "DAIFUKU_SCALE_ANCHOR_INVALID", f"{base}.product_state.scale_lock.anchor", "Bind a reliable same-depth anchor type and observable evidence.")
        ratio = anchor.get("expected_ratio")
        if anchor.get("type") == "index_finger_mid" and ratio != [3.5, 4.0]:
            add_issue(issues, "ERROR", "DAIFUKU_FINGER_RATIO_INVALID", f"{base}.product_state.scale_lock.anchor.expected_ratio", "Index-finger scale must lock the reconstructable product width to 3.5–4.0 finger widths.")
        if state_id == "plated" and anchor.get("type") not in {"known_container_dimension", "approved_scene_scale_master"}:
            add_issue(issues, "ERROR", "DAIFUKU_PLATE_SCALE_UNPROVEN", f"{base}.product_state.scale_lock.anchor", "Plated shots require a known container dimension or an approved in-scene scale master; plate appearance alone is not a scale anchor.")

        pixel_plan = scale_lock.get("pixel_plan")
        pixel_base = f"{base}.product_state.scale_lock.pixel_plan"
        if require_pixel_preflight and not isinstance(pixel_plan, dict):
            add_issue(
                issues,
                "ERROR",
                "DAIFUKU_PIXEL_PREFLIGHT_MISSING",
                pixel_base,
                "Do not generate from prose-only scale. Measure a same-depth anchor, compute the target pixel bbox, and bind a deterministic geometry guide first.",
            )
        if isinstance(pixel_plan, dict):
            if pixel_plan.get("status") != "authorized":
                add_issue(issues, "ERROR", "DAIFUKU_PIXEL_PREFLIGHT_NOT_AUTHORIZED", f"{pixel_base}.status", "Pixel preflight must be authorized before image generation.")

            source_path = resolve_path(project_dir, pixel_plan.get("source_frame"))
            source_link = resolve_path(project_dir, (shot.get("asset_links") or {}).get("source_first_frame"))
            if not source_path or not source_path.is_file() or source_path != source_link:
                add_issue(issues, "ERROR", "DAIFUKU_PIXEL_SOURCE_MISMATCH", f"{pixel_base}.source_frame", "Pixel preflight must bind the exact source_first_frame for this shot.")
            else:
                if pixel_plan.get("source_frame_sha256") != sha256_file(source_path):
                    add_issue(issues, "ERROR", "DAIFUKU_PIXEL_SOURCE_HASH_MISMATCH", f"{pixel_base}.source_frame_sha256", "Source-frame bytes changed after pixel preflight; rebuild the plan instead of reusing stale geometry.")
                with Image.open(source_path) as source_image:
                    actual_size = list(source_image.size)
                if pixel_plan.get("frame_size_px") != actual_size:
                    add_issue(issues, "ERROR", "DAIFUKU_PIXEL_FRAME_SIZE_MISMATCH", f"{pixel_base}.frame_size_px", f"Recorded frame size must equal {actual_size}.")

            pixel_anchor = pixel_plan.get("anchor") or {}
            pixel_target = pixel_plan.get("target") or {}
            contract_binding = pixel_plan.get("contract_binding") or {}
            expected_binding = {
                "bundle_release_id": current_release_manifest().get("bundle_release_id"),
                "product_profile": product.get("profile_id"),
                "product_version": product.get("version"),
                "product_bible_sha256": sha256_file(project_dir / "library" / "product_bible.json"),
                "state": state_id,
                "anchor_type": anchor.get("type"),
                "anchor_expected_ratio": anchor.get("expected_ratio"),
            }
            if any(contract_binding.get(key) != value for key, value in expected_binding.items()):
                add_issue(
                    issues,
                    "ERROR",
                    "DAIFUKU_PIXEL_CONTRACT_STALE",
                    f"{pixel_base}.contract_binding",
                    "Pixel preflight must be rebuilt after any release, product bible, state or scale-anchor change.",
                )
            if pixel_anchor.get("type") != anchor.get("type") or pixel_anchor.get("expected_ratio") != anchor.get("expected_ratio"):
                add_issue(
                    issues,
                    "ERROR",
                    "DAIFUKU_PIXEL_ANCHOR_MISMATCH",
                    f"{pixel_base}.anchor",
                    "The measured pixel anchor must exactly match product_state.scale_lock.anchor.",
                )
            try:
                measured_width = float(pixel_anchor.get("measured_width_px"))
                selected_ratio = float(pixel_anchor.get("selected_ratio"))
                target_width = int(pixel_target.get("width_px"))
                target_height = int(pixel_target.get("height_px"))
                tolerance = [int(value) for value in pixel_target.get("width_tolerance_px")]
                bbox = [int(value) for value in pixel_target.get("bbox_xywh")]
                frame_size = [int(value) for value in pixel_plan.get("frame_size_px")]
                measurement_bbox = [int(value) for value in pixel_anchor.get("measurement_bbox_xywh")]
                arithmetic_valid = (
                    measured_width > 0
                    and selected_ratio > 0
                    and target_width == round(measured_width * selected_ratio)
                    and target_height > 0
                    and len(tolerance) == 2
                    and tolerance[0] <= target_width <= tolerance[1]
                )
                bbox_valid = (
                    len(bbox) == 4
                    and len(frame_size) == 2
                    and bbox[2] == target_width
                    and bbox[3] == target_height
                    and bbox[0] >= 0
                    and bbox[1] >= 0
                    and bbox[0] + bbox[2] <= frame_size[0]
                    and bbox[1] + bbox[3] <= frame_size[1]
                )
                anchor_measurement_valid = (
                    pixel_anchor.get("measurement_method") == "annotated_bbox"
                    and len(measurement_bbox) == 4
                    and measurement_bbox[2] == round(measured_width)
                    and measurement_bbox[0] >= 0
                    and measurement_bbox[1] >= 0
                    and measurement_bbox[2] > 0
                    and measurement_bbox[3] > 0
                    and measurement_bbox[0] + measurement_bbox[2] <= frame_size[0]
                    and measurement_bbox[1] + measurement_bbox[3] <= frame_size[1]
                )
            except (TypeError, ValueError):
                arithmetic_valid = False
                bbox_valid = False
                anchor_measurement_valid = False
            if not arithmetic_valid:
                add_issue(issues, "ERROR", "DAIFUKU_PIXEL_ARITHMETIC_INVALID", pixel_base, "Target pixel width must be the measured same-depth anchor width multiplied by the selected approved ratio, within its stored tolerance.")
            if not bbox_valid:
                add_issue(issues, "ERROR", "DAIFUKU_TARGET_BBOX_INVALID", f"{pixel_base}.target.bbox_xywh", "Target bbox must match the computed width and height and stay fully inside the exact source frame.")
            if not anchor_measurement_valid:
                add_issue(
                    issues,
                    "ERROR",
                    "DAIFUKU_ANCHOR_MEASUREMENT_UNPROVEN",
                    f"{pixel_base}.anchor.measurement_bbox_xywh",
                    "Anchor width must come from an annotated in-frame bbox whose pixel width can be recomputed.",
                )

            guide_path = resolve_path(project_dir, pixel_plan.get("guide_path"))
            if pixel_plan.get("guide_role") != "geometry_only_do_not_render_overlay":
                add_issue(issues, "ERROR", "DAIFUKU_SCALE_GUIDE_ROLE_INVALID", f"{pixel_base}.guide_role", "The guide is geometry-only; its cyan overlay, crosshair, labels and text must never be rendered into the result.")
            if not guide_path or not guide_path.is_file():
                add_issue(issues, "ERROR", "DAIFUKU_SCALE_GUIDE_MISSING", f"{pixel_base}.guide_path", "Create the deterministic scale guide before any image-generation call.")
            elif pixel_plan.get("guide_sha256") != sha256_file(guide_path):
                add_issue(issues, "ERROR", "DAIFUKU_SCALE_GUIDE_HASH_MISMATCH", f"{pixel_base}.guide_sha256", "Scale-guide bytes changed; rebuild and re-authorize pixel preflight.")
            linked_guide = resolve_path(project_dir, (shot.get("asset_links") or {}).get("scale_guide"))
            if guide_path != linked_guide:
                add_issue(issues, "ERROR", "DAIFUKU_SCALE_GUIDE_LINK_MISMATCH", f"{base}.asset_links.scale_guide", "The shot must link the exact guide validated by pixel preflight.")

            manifest_path = resolve_path(project_dir, pixel_plan.get("manifest_path"))
            manifest_matches = False
            if manifest_path and manifest_path.is_file():
                try:
                    preflight_manifest = load_json(manifest_path)
                    manifest_matches = all(
                        preflight_manifest.get(key) == pixel_plan.get(key)
                        for key in (
                            "status",
                            "source_frame",
                            "source_frame_sha256",
                            "frame_size_px",
                            "anchor",
                            "target",
                            "contract_binding",
                            "guide_path",
                            "guide_sha256",
                            "guide_role",
                        )
                    )
                except (OSError, ValueError, json.JSONDecodeError):
                    manifest_matches = False
            if not manifest_matches:
                add_issue(issues, "ERROR", "DAIFUKU_SCALE_GUIDE_MANIFEST_MISMATCH", f"{pixel_base}.manifest_path", "The scale plan in the shot must exactly match its persisted preflight manifest.")

    surface_lock = state_data.get("surface_lock") or {}
    if surface_lock and (
        surface_lock.get("rice_flour_haze") is not True
        or surface_lock.get("visible_in_oblique_light") is not True
        or surface_lock.get("individually_resolvable_particles") is not False
    ):
        add_issue(issues, "ERROR", "DAIFUKU_POWDER_SURFACE_INVALID", f"{base}.product_state.surface_lock", "Require an ultra-fine rice-flour haze visible as powder frost in oblique light, never countable particles or a powderless dough skin.")

    filling_lock = state_data.get("filling_lock") or {}
    if filling_lock and (
        float(filling_lock.get("continuous_puree_ratio") or 0) < 0.9
        or filling_lock.get("countable_lumps") is not False
        or filling_lock.get("holes_or_honeycomb") is not False
        or filling_lock.get("stringing") is not False
    ):
        add_issue(issues, "ERROR", "DAIFUKU_FILLING_TEXTURE_INVALID", f"{base}.product_state.filling_lock", "Lock at least 90% continuous puree with no countable lumps, holes, honeycomb or stringing.")

    endpoint_lock = state_data.get("endpoint_lock") or {}
    if endpoint_lock and (endpoint_lock.get("terminal_state") != state_id or endpoint_lock.get("single_endpoint") is not True):
        add_issue(issues, "ERROR", "DAIFUKU_STATE_ENDPOINT_AMBIGUOUS", f"{base}.product_state.endpoint_lock", "The terminal state must equal product_state.state and single_endpoint must be true.")
    if state_id == "opening_window_seed" and endpoint_lock:
        if float(endpoint_lock.get("max_visible_filling_area_ratio") or 1) > 0.05 or float(endpoint_lock.get("piece_air_gap_cm") or 0) != 0:
            add_issue(issues, "ERROR", "DAIFUKU_OPENING_OVERSHOOT", f"{base}.product_state.endpoint_lock", "opening_window_seed must stop at first reveal: filling area <=5% and no air gap between pieces.")

    approved_assets = {
        item.get("id"): item
        for item in product.get("reference_assets") or []
        if isinstance(item, dict) and item.get("approved") is True and has_text(item.get("id"))
    }
    role_bindings = state_data.get("reference_roles") or []
    bound_reference_paths: set[str] = set()
    for index, binding in enumerate(role_bindings if isinstance(role_bindings, list) else []):
        path = f"{base}.product_state.reference_roles[{index}]"
        if not isinstance(binding, dict):
            add_issue(issues, "ERROR", "DAIFUKU_REFERENCE_ROLE_INVALID", path, "Reference role binding must be an object.")
            continue
        asset = approved_assets.get(binding.get("asset_id"))
        if not asset:
            add_issue(issues, "ERROR", "DAIFUKU_REFERENCE_NOT_APPROVED", path, "Bind an approved reference asset from the selected product profile.")
            continue
        if binding.get("role") != asset.get("role"):
            add_issue(issues, "ERROR", "DAIFUKU_REFERENCE_ROLE_MISMATCH", path, "The shot role must exactly match the approved asset role.")
        allowed_states = asset.get("allowed_states") or []
        if "*" not in allowed_states and state_id not in allowed_states:
            add_issue(issues, "ERROR", "DAIFUKU_REFERENCE_STATE_POLLUTION", path, f"Reference {asset.get('id')} is not allowed for state {state_id}.")
        if not flatten_text(binding.get("allowed_inheritance")) or not flatten_text(binding.get("forbidden_inheritance")):
            add_issue(issues, "ERROR", "DAIFUKU_REFERENCE_BOUNDARY_MISSING", path, "Repeat the reference's allowed and forbidden inheritance at shot level.")
        target_relative = asset.get("target_path") or asset.get("path")
        if target_relative:
            bound_reference_paths.add(str(target_relative))
        if target_relative and not (project_dir / str(target_relative)).is_file():
            add_issue(issues, "ERROR", "DAIFUKU_REFERENCE_FILE_MISSING", path, f"Seeded reference file is unavailable: {target_relative}")

    linked_reference_paths = {
        str(value)
        for value in as_list((shot.get("asset_links") or {}).get("product_references"))
        if has_text(value)
    }
    if bound_reference_paths != linked_reference_paths:
        add_issue(
            issues,
            "ERROR",
            "DAIFUKU_REFERENCE_ROLE_COVERAGE_MISMATCH",
            f"{base}.asset_links.product_references",
            "Every linked product reference must have one exact shot-level role binding, and every role binding must be linked.",
        )

    conflict_text = " ".join(
        [
            *flatten_text(state_data.get("shot_specific_traits")),
            *flatten_text(shot.get("hard_constraints")),
            *flatten_text(shot.get("continuity")),
            *[str((beat or {}).get("product_change") or "") for beat in shot.get("action_beats") or [] if isinstance(beat, dict)],
        ]
    )
    legacy_terms = [term for term in ("沙沙颗粒", "轻微流心", "2至4条", "2到4条", "3至6厘米", "3到6厘米") if term in conflict_text]
    if legacy_terms:
        add_issue(issues, "ERROR", "DAIFUKU_LEGACY_CONTRACT_CONFLICT", f"{base}.product_state", f"Remove superseded v1 terms: {legacy_terms}")


def validate_shot(
    project_dir: Path,
    project: Dict[str, Any],
    product: Dict[str, Any],
    style: Dict[str, Any],
    state_profiles: Dict[str, Any],
    story: Dict[str, Any],
    shot: Any,
    index: int,
    seen_ids: set,
    issues: List[Dict[str, Any]],
    *,
    require_delivery_assets: bool = True,
    require_pixel_preflight: bool = False,
) -> None:
    base = f"shots/shot_manifest.json.shots[{index}]"
    if not isinstance(shot, dict):
        add_issue(issues, "ERROR", "invalid_shot", base, "Shot must be an object.")
        return

    shot_id = shot.get("id")
    if not has_text(shot_id):
        add_issue(issues, "ERROR", "missing_shot_id", f"{base}.id", "Shot id is required.")
    elif shot_id in seen_ids:
        add_issue(issues, "ERROR", "duplicate_shot_id", f"{base}.id", f"Duplicate shot id: {shot_id}")
    else:
        seen_ids.add(shot_id)

    validate_durian_daifuku_v2_shot(
        project_dir,
        product,
        shot,
        issues,
        base,
        require_pixel_preflight=require_pixel_preflight,
    )

    for field in ("title", "purpose", "narrative_role"):
        if not has_text(shot.get(field)):
            add_issue(issues, "ERROR", "missing_shot_field", f"{base}.{field}", "Required shot field is empty.")

    visual_type = shot.get("visual_type")
    if visual_type not in VALID_VISUAL_TYPES:
        add_issue(issues, "ERROR", "invalid_visual_type", f"{base}.visual_type", f"Use only {sorted(VALID_VISUAL_TYPES)}.")
    if not as_list(shot.get("script_segment_ids")):
        add_issue(issues, "ERROR", "missing_script_segment_links", f"{base}.script_segment_ids", "Bind the shot to one or more subtitle-script segments.")
    if not has_text(shot.get("scene_rationale")):
        add_issue(issues, "ERROR", "missing_scene_rationale", f"{base}.scene_rationale", "Explain why this scene supports the product, person or eating beat.")

    timecode = shot.get("timecode") or {}
    start, end, duration = timecode.get("start"), timecode.get("end"), timecode.get("duration")
    if not all(isinstance(value, (int, float)) for value in (start, end, duration)):
        add_issue(issues, "ERROR", "invalid_timecode", f"{base}.timecode", "start, end and duration must be numeric.")
    else:
        if end <= start or duration <= 0:
            add_issue(issues, "ERROR", "invalid_timecode_order", f"{base}.timecode", "end must be greater than start and duration must be positive.")
        if abs((end - start) - duration) > 0.08:
            add_issue(issues, "ERROR", "duration_mismatch", f"{base}.timecode", "duration must equal end - start within 0.08 seconds.")

    if not flatten_text(shot.get("source_facts")):
        add_issue(issues, "WARN", "missing_source_facts", f"{base}.source_facts", "Record what is directly observable in the source.")
    project_rules = project.get("project_rules") or {}
    if project_rules.get("preserve_source_composition") is True and not flatten_text(shot.get("source_locks")):
        add_issue(issues, "ERROR", "missing_source_locks", f"{base}.source_locks", "Source locks are required when preserving composition.")
    if not flatten_text(shot.get("allowed_changes")):
        add_issue(issues, "WARN", "missing_allowed_changes", f"{base}.allowed_changes", "Explicitly define what may change.")

    scene = shot.get("scene") or {}
    if not has_text(scene.get("location")):
        add_issue(issues, "ERROR", "missing_scene", f"{base}.scene.location", "Scene location is required.")
    if not flatten_text(scene.get("background")):
        add_issue(issues, "ERROR", "missing_background", f"{base}.scene.background", "Describe visible background elements.")

    character = shot.get("character") or {}
    emotion = shot.get("emotion") or {}
    if visual_type == "product_showcase":
        if character.get("present") is not False:
            add_issue(issues, "ERROR", "product_shot_has_character", f"{base}.character.present", "Product showcase shots must explicitly set character.present=false.")
        if character.get("hands_only") not in (None, False, True):
            add_issue(issues, "ERROR", "invalid_hands_only_flag", f"{base}.character.hands_only", "hands_only must be true or false.")
    else:
        if character.get("present") is not True:
            add_issue(issues, "ERROR", "person_shot_without_character", f"{base}.character.present", "Person showcase/eating shots require character.present=true.")
        for field in ("identity", "position", "gaze"):
            if not has_text(character.get(field)):
                add_issue(issues, "ERROR", "missing_character_field", f"{base}.character.{field}", "Character field is required for person shots.")
        if not flatten_text(character.get("micro_expressions")):
            add_issue(issues, "ERROR", "missing_micro_expression", f"{base}.character.micro_expressions", "Describe observable micro-expressions.")
        for field in ("start", "trigger", "inferred_intention", "end", "narrative_payoff"):
            if not has_text(emotion.get(field)):
                add_issue(
                    issues,
                    "ERROR",
                    "missing_emotion_reasoning",
                    f"{base}.emotion.{field}",
                    "Person shots need a source-grounded baseline, trigger, inferred intention, end state and narrative payoff before six-layer authoring.",
                )
        if not flatten_text(emotion.get("progression")):
            add_issue(issues, "ERROR", "missing_emotion_progression", f"{base}.emotion.progression", "Describe the emotional transition, not only a label.")
        if not flatten_text(emotion.get("evidence_basis")):
            add_issue(
                issues,
                "ERROR",
                "missing_emotion_evidence_basis",
                f"{base}.emotion.evidence_basis",
                "Ground the inferred intention and emotional arc in source time, gaze, action, expression or sound evidence.",
            )

    beats = shot.get("action_beats")
    if not isinstance(beats, list) or not beats:
        add_issue(issues, "ERROR", "missing_action_beats", f"{base}.action_beats", "Add at least one timed action beat.")
    elif isinstance(duration, (int, float)):
        previous_start = -1.0
        for beat_index, beat in enumerate(beats):
            beat_path = f"{base}.action_beats[{beat_index}]"
            if not isinstance(beat, dict):
                add_issue(issues, "ERROR", "invalid_action_beat", beat_path, "Action beat must be an object.")
                continue
            beat_start, beat_end = beat.get("start"), beat.get("end")
            if not isinstance(beat_start, (int, float)) or not isinstance(beat_end, (int, float)):
                add_issue(issues, "ERROR", "invalid_action_time", beat_path, "Action beat start/end must be numeric.")
                continue
            if beat_start < 0 or beat_end <= beat_start or beat_end > duration + 0.02:
                add_issue(issues, "ERROR", "action_out_of_range", beat_path, "Action beat must fit inside the shot duration.")
            if beat_start < previous_start:
                add_issue(issues, "ERROR", "action_not_ordered", beat_path, "Action beats must be sorted by start time.")
            previous_start = beat_start
            for field in ("actor", "action", "expression", "product_change", "camera_response"):
                if not has_text(beat.get(field)):
                    add_issue(issues, "ERROR", "missing_action_field", f"{beat_path}.{field}", "Action beat field is required.")

    commercial_emotion_contract = (project.get("project_rules") or {}).get("commercial_emotion_contract") or {}
    if visual_type != "product_showcase" and isinstance(duration, (int, float)):
        validate_commercial_emotion_rhythm(
            shot,
            float(duration),
            commercial_emotion_contract,
            issues,
            base,
            source_unit=bool(as_list(shot.get("source_units"))),
        )

    product_state = shot.get("product_state") or {}
    if product_state.get("profile") != project.get("product_profile"):
        add_issue(issues, "ERROR", "shot_product_profile_mismatch", f"{base}.product_state.profile", "Shot product profile must match project.json.")
    state = product_state.get("state")
    if not has_text(state) or state not in state_profiles:
        add_issue(issues, "ERROR", "unknown_product_state", f"{base}.product_state.state", f"Use a state defined in product_bible.json: {sorted(state_profiles)}")
    packaging = product_state.get("packaging")
    packaging_texts = [
        str(scene.get("location", "")),
        *flatten_text(scene.get("background")),
        *flatten_text(scene.get("foreground")),
        *flatten_text(shot.get("source_facts")),
        str(shot.get("purpose", "")),
        str(product_state.get("shot_specific_traits", "")),
    ]
    for beat in as_list(shot.get("action_beats")):
        if isinstance(beat, dict):
            packaging_texts.extend(str(beat.get(field, "")) for field in ("action", "expression", "product_change", "camera_response"))
    packaging_positive = ("包装", "纸盒", "纸箱", "包装袋", "独立包装", "box", "package", "packaging")
    packaging_negative = (
        "无包装", "不要包装", "不出现包装", "禁止包装", "不含包装", "不含独立包装",
        "packaging none", "no packaging", "without packaging",
    )
    if packaging in (None, False, "none", "hidden") and any(
        contains_positive_without_negative(text, packaging_positive, packaging_negative) for text in packaging_texts
    ):
        add_issue(issues, "ERROR", "PACKAGING_STATE_UNDECLARED", f"{base}.product_state.packaging", "Visible/source-described packaging cannot be self-declared as none/hidden to bypass per-face master QA.")
    if project_rules.get("packaging_visible") is False and packaging not in (None, False, "none", "hidden"):
        add_issue(issues, "ERROR", "packaging_conflict", f"{base}.product_state.packaging", "Project forbids visible packaging.")
    if project_rules.get("packaging_visible") is False:
        for text in packaging_texts:
            if contains_positive_without_negative(text, packaging_positive, packaging_negative):
                add_issue(
                    issues,
                    "ERROR",
                    "packaging_text_conflict",
                    f"{base}.scene_or_action_text",
                    f"Project forbids visible packaging, but shot text mentions packaging: {text}",
                )
                break

    camera = shot.get("camera") or {}
    for field in ("shot_size", "angle", "movement", "focus", "lens_feel"):
        if not has_text(camera.get(field)):
            add_issue(issues, "ERROR", "missing_camera_field", f"{base}.camera.{field}", "Camera field is required.")

    lighting = shot.get("lighting") or {}
    if not has_text(lighting.get("source")) or not has_text(lighting.get("temperature")):
        add_issue(issues, "ERROR", "missing_lighting", f"{base}.lighting", "Lighting source and temperature are required.")

    audio = shot.get("audio") or {}
    delivery_mode = audio.get("delivery_mode")
    if delivery_mode not in VALID_DELIVERY_MODES:
        add_issue(issues, "ERROR", "invalid_delivery_mode", f"{base}.audio.delivery_mode", f"Use one of {sorted(VALID_DELIVERY_MODES)}.")
    if not has_text(audio.get("delivery_rationale")):
        add_issue(issues, "ERROR", "missing_delivery_rationale", f"{base}.audio.delivery_rationale", "Explain how the subtitle script and source style determined this mode.")
    if delivery_mode in {"voiceover", "on_screen_speech"} and not has_text(audio.get("script_text")):
        add_issue(issues, "ERROR", "missing_script_text", f"{base}.audio.script_text", "Spoken shots require the exact assigned subtitle text.")
    if delivery_mode in {"voiceover", "on_screen_speech"} and not has_text(audio.get("voice_direction")):
        add_issue(issues, "ERROR", "missing_voice_direction", f"{base}.audio.voice_direction", "Describe tone, emphasis and pauses.")
    if delivery_mode == "voiceover" and project_rules.get("allow_voiceover") is False:
        add_issue(issues, "ERROR", "project_voiceover_conflict", f"{base}.audio.delivery_mode", "Project disables voice-over.")
    if delivery_mode == "on_screen_speech":
        if project_rules.get("allow_on_screen_speech") is False:
            add_issue(issues, "ERROR", "project_speech_conflict", f"{base}.audio.delivery_mode", "Project disables on-screen speech.")
        if visual_type == "product_showcase":
            add_issue(issues, "ERROR", "speech_without_visible_person", f"{base}.audio.delivery_mode", "A product-only shot cannot contain on-screen speech.")
        if not has_text(audio.get("speech_timing")):
            add_issue(issues, "ERROR", "missing_speech_timing", f"{base}.audio.speech_timing", "Define exactly when the person speaks.")
    if visual_type == "person_eating" and delivery_mode == "on_screen_speech":
        timing = str(audio.get("speech_timing", ""))
        safe_terms = (
            "咬前", "吃前", "咬合结束", "产品离嘴", "咬食结束", "吃完后", "咀嚼结束", "吞咽后",
            "before biting", "bite completed", "product left mouth", "after bite", "chewing finished", "after swallowing",
        )
        if not any(term in timing for term in safe_terms):
            add_issue(issues, "ERROR", "speech_while_eating_risk", f"{base}.audio.speech_timing", "Mark speech before the bite or immediately after the bite/product leaves the mouth. Do not require a visible swallow unless the source shows one.")

    if delivery_mode in {"voiceover", "on_screen_speech"}:
        capacity = audio.get("speech_capacity") or {}
        required_capacity = ("segment_count", "effective_characters", "speakable_seconds", "characters_per_second")
        if any(capacity.get(field) is None for field in required_capacity):
            add_issue(issues, "ERROR", "pacing_fields_missing", f"{base}.audio.speech_capacity", "Store segment_count, effective_characters, speakable_seconds and characters_per_second for every spoken shot.")
        else:
            segment_count = capacity.get("segment_count")
            effective_chars = capacity.get("effective_characters")
            speakable_seconds = capacity.get("speakable_seconds")
            stated_rate = capacity.get("characters_per_second")
            if not isinstance(segment_count, int) or segment_count < 1:
                add_issue(issues, "ERROR", "pacing_fields_missing", f"{base}.audio.speech_capacity.segment_count", "segment_count must be a positive integer.")
            elif segment_count > 3:
                add_issue(issues, "ERROR", "script_segment_overload", f"{base}.audio.speech_capacity.segment_count", "A shot may contain at most three spoken segments; split by semantics and action loop.")
            if not isinstance(effective_chars, int) or effective_chars < 1:
                add_issue(issues, "ERROR", "pacing_fields_missing", f"{base}.audio.speech_capacity.effective_characters", "effective_characters must be a positive integer.")
            elif effective_chars != spoken_char_count(str(audio.get("script_text", ""))):
                add_issue(issues, "ERROR", "speech_capacity_mismatch", f"{base}.audio.speech_capacity.effective_characters", "Effective character count must equal Han/letter/digit characters in script_text.")
            if not isinstance(speakable_seconds, (int, float)) or speakable_seconds <= 0:
                add_issue(issues, "ERROR", "speech_window_invalid", f"{base}.audio.speech_capacity.speakable_seconds", "speakable_seconds must be positive and exclude actual biting, any closed-mouth chewing shown, required breaths, pure foley and silent observation. Exclude swallowing only when it is actually present.")
            elif isinstance(effective_chars, int) and isinstance(stated_rate, (int, float)):
                calculated_rate = effective_chars / float(speakable_seconds)
                if abs(float(stated_rate) - calculated_rate) > 0.06:
                    add_issue(issues, "ERROR", "speech_capacity_mismatch", f"{base}.audio.speech_capacity.characters_per_second", f"Stored rate {stated_rate} does not match {effective_chars}/{speakable_seconds}={calculated_rate:.2f}.")
                pacing = story.get("pacing") or {}
                limit_field = "maximum_on_screen_chars_per_second" if delivery_mode == "on_screen_speech" else "maximum_voiceover_chars_per_second"
                limit = pacing.get(limit_field)
                if isinstance(limit, (int, float)) and calculated_rate > float(limit) + 0.01:
                    add_issue(issues, "ERROR", "speech_rate_exceeded", f"{base}.audio.speech_capacity.characters_per_second", f"Speech rate {calculated_rate:.2f} chars/s exceeds planned limit {float(limit):.2f}; split or extend instead of accelerating.")

    for field in ("hard_constraints", "prohibited", "continuity"):
        if not flatten_text(shot.get(field)):
            level = "ERROR" if field in {"hard_constraints", "prohibited"} else "WARN"
            add_issue(issues, level, f"missing_{field}", f"{base}.{field}", f"{field} must not be empty.")

    assets = shot.get("asset_links") or {}
    source_frame = resolve_path(project_dir, assets.get("source_first_frame"))
    has_source_units = bool(shot.get("source_units"))
    if source_frame is None and has_source_units:
        add_issue(issues, "ERROR", "missing_source_first_frame", f"{base}.asset_links.source_first_frame", "Extract the exact first temporal frame of every shot.")
    elif source_frame is not None and not source_frame.exists():
        add_issue(issues, "WARN", "source_first_frame_unavailable", f"{base}.asset_links.source_first_frame", f"File is not accessible: {source_frame}")
    beauty_frame = resolve_path(project_dir, assets.get("selected_beauty_keyframe"))
    if beauty_frame is None:
        add_issue(issues, "WARN", "missing_beauty_keyframe", f"{base}.asset_links.selected_beauty_keyframe", "Select a separate beauty keyframe for visual reference; do not substitute it for the shot's first frame.")
    elif not beauty_frame.exists():
        add_issue(issues, "WARN", "beauty_keyframe_unavailable", f"{base}.asset_links.selected_beauty_keyframe", f"File is not accessible: {beauty_frame}")
    if project.get("generation_mode") == "image_to_video" and require_delivery_assets:
        first_frame = resolve_path(project_dir, assets.get("approved_generation_first_frame"))
        if first_frame is None:
            add_issue(issues, "ERROR", "missing_approved_generation_first_frame", f"{base}.asset_links.approved_generation_first_frame", "Image-to-video delivery requires an approved, accessible generation first frame derived from the exact temporal first frame.")
        elif not first_frame.is_file():
            add_issue(issues, "ERROR", "approved_generation_first_frame_unavailable", f"{base}.asset_links.approved_generation_first_frame", f"Approved first-frame file is not accessible: {first_frame}")
        else:
            from image_generation_gate import validate_approved_result_binding

            for code, detail in validate_approved_result_binding(project_dir, project, shot):
                add_issue(issues, "ERROR", code, f"{base}.asset_links.image_generation_result_receipt", detail)
    product_references = [value for value in as_list(assets.get("product_references")) if has_text(value)]
    if not product_references:
        add_issue(issues, "ERROR", "missing_product_references", f"{base}.asset_links.product_references", "Bind one or more approved, accessible product reference files.")
    else:
        for reference_index, reference_value in enumerate(product_references):
            reference_path = resolve_path(project_dir, str(reference_value))
            if reference_path is None or not reference_path.is_file():
                add_issue(issues, "ERROR", "product_reference_unavailable", f"{base}.asset_links.product_references[{reference_index}]", f"Product reference file is not accessible: {reference_path or reference_value}")

    risk = shot.get("risk") or {}
    if risk.get("level") not in VALID_RISKS:
        add_issue(issues, "ERROR", "invalid_risk", f"{base}.risk.level", f"Risk must be one of {sorted(VALID_RISKS)}.")
    if risk.get("level") == "high" and not flatten_text(risk.get("reasons")):
        add_issue(issues, "ERROR", "missing_risk_reason", f"{base}.risk.reasons", "High-risk shots require reasons.")

    style_subtitles = (style.get("subtitle_policy") or {}).get("generate")
    if project_rules.get("subtitles_generated_by_model") is False and style_subtitles is True:
        add_issue(issues, "ERROR", "subtitle_policy_conflict", "library/style_bible.json.subtitle_policy.generate", "Project forbids model-generated subtitles.")


def determine_release_status(project: Dict[str, Any], issues: Sequence[Dict[str, Any]]) -> str:
    if any(issue["level"] in {"ERROR", "BLOCK"} for issue in issues):
        return "NOT CLEARED FOR RELEASE"
    intended_use = (project.get("commercial") or {}).get("intended_use", "internal_test")
    if intended_use == "commercial_release":
        return "CLEARED FOR RELEASE"
    if intended_use == "client_review":
        return "CLIENT REVIEW ONLY"
    return "INTERNAL TEST ONLY"


def lint_project(project_dir: Path, write_report: bool = True) -> Dict[str, Any]:
    project_dir = project_dir.expanduser().resolve()
    issues: List[Dict[str, Any]] = []
    if not project_dir.is_dir():
        add_issue(issues, "ERROR", "missing_project_directory", str(project_dir), "Project directory does not exist.")
        project: Dict[str, Any] = {}
    elif not validate_required_files(project_dir, issues):
        project = {}
    else:
        bundle = read_bundle(project_dir)
        project = bundle["project"]
        validate_project(project_dir, bundle, issues)
        if requires_delivery_assets(project):
            reuse_audit = subprocess.run(
                [
                    sys.executable,
                    str(Path(__file__).resolve().parent / "audit_asset_reuse.py"),
                    "--plan",
                    str(project_dir / REQUIRED_FILES["asset_reuse"]),
                    "--stage",
                    "pre-generation",
                ],
                capture_output=True,
                text=True,
                check=False,
            )
            if reuse_audit.returncode != 0:
                add_issue(
                    issues,
                    "ERROR",
                    "asset_reuse_audit_blocked",
                    str(REQUIRED_FILES["asset_reuse"]),
                    (reuse_audit.stdout or reuse_audit.stderr).strip(),
                )

    try:
        report_execution_tier = normalized_execution_tier(project) if project else None
    except ValueError:
        report_execution_tier = None
    counts = {level: sum(issue["level"] == level for issue in issues) for level in ("ERROR", "BLOCK", "WARN")}
    report = {
        "schema_version": "1.0",
        "project_dir": str(project_dir),
        "generated_at": now_iso(),
        "execution_tier": report_execution_tier,
        "counts": counts,
        "release_status": determine_release_status(project, issues),
        "issues": issues,
    }
    if write_report and project_dir.is_dir():
        write_json(project_dir / "review" / "lint_report.json", report)
    return report


def applicable_corrections(bundle: Dict[str, Dict[str, Any]], shot: Dict[str, Any]) -> List[Dict[str, Any]]:
    project = bundle["project"]
    rules = bundle["corrections"].get("rules") or []
    matching: List[Dict[str, Any]] = []
    for rule in rules:
        if not isinstance(rule, dict) or rule.get("active") is not True:
            continue
        scope, target = rule.get("scope"), rule.get("target")
        expected = {
            "shot": shot.get("id"),
            "project": project.get("project_id"),
            "product": project.get("product_profile"),
            "style": project.get("style_profile"),
        }.get(scope)
        if target in (None, "*", expected):
            matching.append(rule)
    return sorted(
        matching,
        key=lambda item: (int(item.get("priority", 0)), item.get("updated_at", "")),
        reverse=True,
    )


def applicable_knowledge(bundle: Dict[str, Dict[str, Any]], shot: Dict[str, Any]) -> List[Dict[str, Any]]:
    context = {
        "product_profile": bundle["project"].get("product_profile"),
        "visual_type": shot.get("visual_type"),
        "product_state": (shot.get("product_state") or {}).get("state"),
        "delivery_mode": (shot.get("audio") or {}).get("delivery_mode"),
        "narrative_role": shot.get("narrative_role"),
    }
    matches = []
    for entry in bundle["knowledge"].get("entries") or []:
        if not isinstance(entry, dict) or entry.get("approved") is not True:
            continue
        applies_to = entry.get("applies_to") or {}
        if all(
            expected in (None, "*", context.get(key)) or context.get(key) in as_list(expected)
            for key, expected in applies_to.items()
            if key in context
        ):
            matches.append(entry)
    return sorted(matches, key=lambda item: int(item.get("priority", 0)), reverse=True)


def action_text(beats: Iterable[Dict[str, Any]]) -> str:
    lines = []
    for beat in beats:
        lines.append(
            f"{beat.get('id', '未编号')}｜{beat.get('start', 0):.2f}–{beat.get('end', 0):.2f}秒："
            f"{beat.get('actor')}执行“{beat.get('action')}”；"
            f"由“{beat.get('trigger')}”触发，人物处在“{join_cn(beat.get('emotion_terms'))}”；"
            f"可见变化为“{beat.get('visible_change') or beat.get('expression')}”；声音变化为“{beat.get('voice_change')}”；"
            f"产品变化为“{beat.get('product_change')}”；"
            f"镜头配合为“{beat.get('camera_response')}”；随后自然接到“{beat.get('next_action')}”。"
        )
    return "\n".join(lines)


def performance_layer_text(unit: Dict[str, Any]) -> str:
    labels = {
        "emotion_trigger": "情绪与触发",
        "gaze": "视线",
        "facial_microreaction": "五官微反应",
        "body_hand_preparation": "身体/手部准备",
        "breath_pause": "呼吸/停顿",
        "voice_speech": "声音/口语",
    }
    lines: List[str] = []
    layers = unit.get("source_performance_layers") or {}
    for key in PERFORMANCE_LAYER_KEYS:
        record = layers.get(key) or {}
        source_time = record.get("source_timecode") or {}
        interval = "无源片区间" if not source_time else f"源片{source_time.get('start', 0):.3f}–{source_time.get('end', 0):.3f}秒"
        gap = f"；模板补充原因：{record.get('gap_reason')}" if has_text(record.get("gap_reason")) else ""
        lines.append(f"{labels[key]}={record.get('status')}（{interval}）：{record.get('observable_evidence')}{gap}")
    return "；".join(lines)


def performance_directing_text(unit: Dict[str, Any]) -> str:
    """Translate the six-layer evidence ledger into positive, shootable direction.

    Audit states, confidence and gap explanations belong in project evidence and
    Word cards, not in the generation Prompt. Invisible/inapplicable dimensions
    are omitted instead of being padded with negative language.
    """
    labels = {
        "emotion_trigger": "情绪触发",
        "gaze": "视线推进",
        "facial_microreaction": "五官反馈",
        "body_hand_preparation": "身体与手部准备",
        "breath_pause": "呼吸与停顿",
        "voice_speech": "声音与口语",
    }
    lines: List[str] = []
    layers = unit.get("source_performance_layers") or {}
    for key in PERFORMANCE_LAYER_KEYS:
        record = layers.get(key) or {}
        if record.get("status") not in {"observed", "audible", "template_supplement"}:
            continue
        evidence = record.get("observable_evidence")
        if has_text(evidence) and not is_negative_prompt_rule(evidence):
            lines.append(f"{labels[key]}：{str(evidence).strip()}")
    return "；".join(lines)


def source_unit_text(units: Iterable[Dict[str, Any]]) -> str:
    lines = []
    for unit in units:
        source = unit.get("source_timecode") or {}
        generated = unit.get("generation_timecode") or {}
        lines.append(
            f"{unit.get('source_shot_id')}｜原片{source.get('start', 0):.2f}–{source.get('end', 0):.2f}秒"
            f" → 生成镜内{generated.get('start', 0):.2f}–{generated.get('end', 0):.2f}秒："
            f"{unit.get('storyboard_description')}；本单元口播：{unit.get('script_text')}；"
            f"原片表演复原：{performance_directing_text(unit) or '本单元以可见物体动作和镜头节奏推进'}"
        )
    return "\n".join(lines)


def inserted_unit_text(units: Iterable[Dict[str, Any]]) -> str:
    lines = []
    for unit in units:
        generated = unit.get("generation_timecode") or {}
        lines.append(
            f"{unit.get('inserted_shot_id')}｜新增镜头（无原片秒数）"
            f" → 生成镜内{generated.get('start', 0):.2f}–{generated.get('end', 0):.2f}秒："
            f"{unit.get('storyboard_description')}；节奏锚点：{unit.get('rhythm_anchor')}；"
            f"新增理由：{unit.get('insertion_rationale')}；源片表演依据：{join_cn(unit.get('source_reference_shot_ids'))}；"
            f"本单元口播：{unit.get('script_text')}；表演设计依据：{performance_directing_text(unit) or '沿用绑定源片的可见动作与节奏'}"
        )
    return "\n".join(lines)


def break_occurrence_text(occurrences: Iterable[Dict[str, Any]]) -> str:
    lines: List[str] = []
    for occurrence in occurrences:
        timecode = occurrence.get("generation_timecode") or {}
        proof = occurrence.get("crisp_proof") or {}
        crumbs = proof.get("crumbs") or {}
        lines.append(
            f"{occurrence.get('id')}｜{occurrence.get('mode')}｜生成镜内{timecode.get('start', 0):.3f}–{timecode.get('end', 0):.3f}秒；"
            f"节奏依据：{occurrence.get('rhythm_rationale')}；一次脆断={proof.get('single_snap')}；"
            f"碎屑={crumbs.get('minimum')}–{crumbs.get('maximum')}片；"
            f"互补橙金断面：{proof.get('complementary_orange_gold_fracture')}；"
            f"同一根两段守恒：{proof.get('same_stick_two_piece_conservation')}；音画同步：{proof.get('sound_sync')}"
        )
    return "\n".join(lines)


def compile_shot(bundle: Dict[str, Dict[str, Any]], shot: Dict[str, Any]) -> Tuple[str, Dict[str, Any]]:
    project = bundle["project"]
    product = bundle["product"]
    style = bundle["style"]
    story = bundle["story"]
    product_state = shot.get("product_state") or {}
    state_id = product_state.get("state")
    state = (product.get("state_profiles") or {}).get(state_id, {})
    corrections = applicable_corrections(bundle, shot)
    correction_text = [rule.get("instruction", "") for rule in corrections if has_text(rule.get("instruction"))]
    knowledge = applicable_knowledge(bundle, shot)
    knowledge_instructions = [
        entry.get("positive_instruction") or entry.get("instruction", "")
        for entry in knowledge
        if entry.get("type") in {"prompt", "rule"}
        and has_text(entry.get("positive_instruction") or entry.get("instruction"))
    ]
    knowledge_negative_instructions = [
        entry.get("negative_instruction", "")
        for entry in knowledge
        if entry.get("type") in {"prompt", "rule"} and has_text(entry.get("negative_instruction"))
    ]
    knowledge_images = [entry.get("path") for entry in knowledge if entry.get("type") == "image" and has_text(entry.get("path"))]
    knowledge_reference_roles = [
        (
            f"参考资产{entry.get('id')}只承担“{entry.get('reference_role')}”；"
            f"只继承：{join_cn(entry.get('allowed_inheritance'))}；"
            f"不继承：{join_cn(entry.get('forbidden_inheritance'))}"
        )
        for entry in knowledge
        if entry.get("type") == "image" and has_text(entry.get("reference_role"))
    ]

    product_traits = flatten_text(product.get("immutable_traits"))
    product_traits.extend(flatten_text(state.get("required")))
    product_traits.extend(flatten_text((shot.get("product_state") or {}).get("shot_specific_traits")))

    structured_product_locks: List[str] = []
    if product.get("profile_id") == "durian-daifuku-v2":
        scale_lock = product_state.get("scale_lock") or {}
        anchor = scale_lock.get("anchor") or {}
        surface_lock = product_state.get("surface_lock") or {}
        filling_lock = product_state.get("filling_lock") or {}
        endpoint_lock = product_state.get("endpoint_lock") or {}
        pixel_plan = scale_lock.get("pixel_plan") or {}
        pixel_anchor = pixel_plan.get("anchor") or {}
        pixel_target = pixel_plan.get("target") or {}
        pixel_instruction = ""
        if pixel_plan:
            pixel_instruction = (
                f"像素尺度计划：原帧={pixel_plan.get('frame_size_px')}px；"
                f"同景深锚点实测={pixel_anchor.get('measured_width_px')}px，选定比例={pixel_anchor.get('selected_ratio')}；"
                f"目标大福={pixel_target.get('width_px')}×{pixel_target.get('height_px')}px，"
                f"允许宽度={pixel_target.get('width_tolerance_px')}px，替换框xywh={pixel_target.get('bbox_xywh')}；"
                "几何引导图只约束尺寸与位置，绝不把青色轮廓、十字、标签或文字渲染进成品"
            )
        structured_product_locks = [
            f"尺度模式={scale_lock.get('mode')}；原食品尺度角色={scale_lock.get('source_scale_role')}；同景深锚点={anchor.get('type')}，目标比例={anchor.get('expected_ratio')}，可见证据={anchor.get('evidence')}",
            pixel_instruction,
            f"表皮锁：细糯米粉雾层={surface_lock.get('rice_flour_haze')}，侧向柔光可见={surface_lock.get('visible_in_oblique_light')}，粉粒不可逐粒辨认={surface_lock.get('individually_resolvable_particles') is False}",
            f"内馅锁：连续果泥比例={filling_lock.get('continuous_puree_ratio')}，可数疙瘩={filling_lock.get('countable_lumps')}，孔洞蜂窝={filling_lock.get('holes_or_honeycomb')}，拉丝={filling_lock.get('stringing')}",
            f"唯一终点={endpoint_lock.get('terminal_state')}，单终点={endpoint_lock.get('single_endpoint')}；到达终点后立即停止",
            *knowledge_reference_roles,
        ]

    globally_forbidden = flatten_text(product.get("global_negative_constraints"))
    state_forbidden = flatten_text(state.get("forbidden"))
    shot_forbidden = flatten_text(shot.get("prohibited"))

    scene = shot.get("scene") or {}
    character = shot.get("character") or {}
    emotion = shot.get("emotion") or {}
    camera = shot.get("camera") or {}
    lighting = shot.get("lighting") or {}
    audio = shot.get("audio") or {}
    timecode = shot.get("timecode") or {}
    source_units = shot.get("source_units") or []
    inserted_units = shot.get("inserted_units") or []
    break_occurrences = [
        item for item in ((story.get("break_plan") or {}).get("occurrences") or [])
        if isinstance(item, dict) and str(item.get("shot_id")) == str(shot.get("id"))
    ]

    visual_type = shot.get("visual_type")
    if visual_type == "product_showcase":
        if character.get("hands_only") is True:
            visual_instruction = "本镜头以完成动作所需的双手与产品为全部可见主体，双手受力、产品变化和声音共同承担叙事。"
        else:
            visual_instruction = "本镜头以产品及其所在真实场景为全部可见主体，通过材质、状态变化、光线和镜头节奏完成叙事。"
        character_instruction = "原片叙事复原：本镜由物体状态、动作因果、声音和摄影变化推进。"
    else:
        visual_label = "人物展示产品" if visual_type == "person_product_showcase" else "人物吃产品"
        visual_instruction = f"本镜头以“{visual_label}”为核心，人物、产品和镜头响应共同服务本镜叙事职责。"
        character_instruction = (
            f"原片叙事复原：人物为{character.get('identity')}，位于{character.get('position')}。"
            f"人物此刻不是在做表情展示，而是被“{emotion.get('persona_drive')}”推着行动。"
            f"主情绪为“{emotion.get('primary_emotion')}”，同时夹着“{join_cn(emotion.get('secondary_emotions'))}”；"
            f"底下压着“{emotion.get('undertone')}”，动作结束后仍留下“{emotion.get('residue')}”。"
            f"整镜带货情绪转化为：{emotion.get('commercial_turn')}。可用情绪与感受词：{join_cn(emotion.get('emotion_vocabulary'))}。"
            f"起始心理状态是“{emotion.get('start')}”；来自原片的触发是“{emotion.get('trigger')}”；"
            f"人物此刻想完成“{emotion.get('inferred_intention')}”。情绪随“{join_cn(emotion.get('progression'))}”转折，"
            f"最终落到“{emotion.get('end')}”，让观众感受到“{emotion.get('narrative_payoff')}”。"
            f"导演推断依据：{join_cn(emotion.get('evidence_basis'))}。"
            f"正向表演执行：视线按“{character.get('gaze')}”推进，五官反馈呈现“{join_cn(character.get('micro_expressions'))}”，"
            f"强度为{emotion.get('intensity', 'natural')}，每个表情变化都由前一动作或声音触发并推动下一动作。"
            f"创作增强状态={((emotion.get('creative_enhancement') or {}).get('status') or '未声明')}；"
            f"只有标记为 user_authorized 的增强才可加入，且不得写成原片事实。"
        )

    delivery_mode = audio.get("delivery_mode")
    if delivery_mode == "on_screen_speech":
        audio_instruction = (
            f"人物在镜头内说出：“{audio.get('script_text')}”，只在“{audio.get('speech_timing')}”讲话并自然对口型。"
            f"若同时吃产品，口播落在咬前，或咬合完成、产品离嘴且原片可见闭口咀嚼结束后的第一个可说节拍；声音指导：{audio.get('voice_direction')}。"
        )
    elif delivery_mode == "voiceover":
        audio_instruction = (
            f"口播在本镜头作为后期画外音：“{audio.get('script_text')}”。画面人物的嘴部专注执行原片可见的表情、咬下和闭口咀嚼动作；"
            f"声音指导：{audio.get('voice_direction')}。"
        )
    else:
        audio_instruction = "本镜头无口播和画外音；只保留与动作一致的自然拟音和环境声。"

    package_artwork = product_state.get("package_artwork") or {}
    package_instruction = ""
    if package_artwork.get("visible_faces"):
        face_summaries = []
        for face in package_artwork.get("visible_faces") or []:
            face_summaries.append(
                f"盒{face.get('box_id')}的{face.get('face')}面状态={face.get('visibility_state')}、范围={face.get('visible_extent')}，实际应见区域：{join_cn(face.get('expected_visible_regions'))}；"
                f"自然遮挡/出框区域：{join_cn(face.get('occluded_or_offframe_regions'), '无')}；"
                f"使用{face.get('projection_method')}投射该盒面的批准母版（内部路径与哈希已锁定）"
            )
        package_instruction = (
            "包装印刷锁：" + "；".join(face_summaries) + "。包装文字与图案由批准母版确定性投射，"
            "生成模型负责盒体透视、折边、场景光照、反光、接触影和边缘融合；实际可见区域与母版保持一致，自然遮挡和出框按原构图保留。"
        )

    execution_rules = unique_text(
        [
            *flatten_text(shot.get("hard_constraints")),
            *correction_text,
            *knowledge_instructions,
        ]
    )
    positive_execution_rules = [rule for rule in execution_rules if not is_negative_prompt_rule(rule)]
    negative_candidates = [
        "禁止生成自动字幕和水印",
        *knowledge_negative_instructions,
        *[rule for rule in execution_rules if is_negative_prompt_rule(rule)],
        *shot_forbidden,
        *state_forbidden,
        *globally_forbidden,
    ]
    minimal_negative_rules = compact_negative_constraints(negative_candidates)

    prompt_body = f"""【生成目标与叙事职责】
生成一段约{timecode.get('duration')}秒、{project.get('aspect_ratio')}比例的{style.get('name')}视频片段，镜头编号{shot.get('id')}。本镜承担“{shot.get('narrative_role')}”，叙事目的是“{shot.get('purpose')}”。{visual_instruction}

【口播原文与声源】
声音方式为“{delivery_mode}”，依据是：{audio.get('delivery_rationale')}。{audio_instruction}

【原片叙事复原】
原片可确认事实：{join_cn(shot.get('source_facts'))}。原片身份、空间、构图和动作连续性锚点：{join_cn(shot.get('source_locks'))}。本次改款范围：{join_cn(shot.get('allowed_changes'))}。

{character_instruction}

本生成片段完整保留下列原片分镜，短分镜按相邻连续关系组成同一叙事段：
{source_unit_text(source_units) or '本片段为纯新增镜头。'}

本生成片段中的新增镜头按原片节奏与新版口播落位：
{inserted_unit_text(inserted_units) or '无新增镜头。'}

本镜掰开叙事点：
{break_occurrence_text(break_occurrences) or '本镜不承担掰开动作。'}

【原片逐时动作】
场景位于{scene.get('location')}。选择该场景的理由是：{shot.get('scene_rationale')}。背景包括：{join_cn(scene.get('background'))}。前景包括：{join_cn(scene.get('foreground'))}。前景、人物、产品和背景保持真实遮挡与空间层次。

按照以下镜头内时间轴执行动作；适用六层在动作发生的位置共同推进，不拆成六段通用尾注：
{action_text(shot.get('action_beats') or [])}

【产品与动作物理】
产品使用“{product.get('name')}”规范，当前主要状态为“{state_id}”：{state.get('description', '')}。数量为{product_state.get('count')}，包装状态为{product_state.get('packaging')}。产品执行重点：{join_cn(product_traits)}。结构化产品锁：{join_cn(structured_product_locks, '按当前产品规范执行')}。{package_instruction}

本镜正向执行重点：{join_cn(positive_execution_rules, '以原片逐时动作、产品状态和人物情绪弧为准')}。连续性：{join_cn(shot.get('continuity'))}。

【摄影、灯光与声音】
摄影采用{camera.get('shot_size')}，机位为{camera.get('angle')}，运镜为{camera.get('movement')}，焦点为{camera.get('focus')}，镜头质感为{camera.get('lens_feel')}。灯光来源为{lighting.get('source')}，色温为{lighting.get('temperature')}，补充要求：{join_cn(lighting.get('notes'))}。

保留或生成的拟音包括：{join_cn(audio.get('foley'))}。音乐要求：{audio.get('music')}。声音、镜头响应与人物动作在同一节拍上完成情绪和叙事落点。

【最小纠错附录】
{join_cn(minimal_negative_rules, '无额外纠错项')}。"""

    metadata = {
        "shot_id": shot.get("id"),
        "title": shot.get("title"),
        "timecode": timecode,
        "visual_type": visual_type,
        "narrative_role": shot.get("narrative_role"),
        "delivery_mode": delivery_mode,
        "script_segment_ids": as_list(shot.get("script_segment_ids")),
        "risk": shot.get("risk"),
        "product_profile": product.get("profile_id"),
        "product_version": product.get("version"),
        "source_shot_ids": [unit.get("source_shot_id") for unit in source_units],
        "inserted_shot_ids": [unit.get("inserted_shot_id") for unit in inserted_units],
        "style_profile": style.get("profile_id"),
        "style_version": style.get("version"),
        "correction_rule_ids": [rule.get("id") for rule in corrections],
        "source_first_frame": (shot.get("asset_links") or {}).get("source_first_frame"),
        "selected_beauty_keyframe": (shot.get("asset_links") or {}).get("selected_beauty_keyframe"),
        "approved_generation_first_frame": (shot.get("asset_links") or {}).get("approved_generation_first_frame"),
        "product_references": as_list((shot.get("asset_links") or {}).get("product_references")),
        "scale_guide": (shot.get("asset_links") or {}).get("scale_guide"),
        "pixel_plan_manifest": ((((shot.get("product_state") or {}).get("scale_lock") or {}).get("pixel_plan") or {}).get("manifest_path")),
        "image_generation_authorization": (shot.get("asset_links") or {}).get("image_generation_authorization"),
        "image_generation_result_receipt": (shot.get("asset_links") or {}).get("image_generation_result_receipt"),
        "knowledge_entry_ids": [entry.get("id") for entry in knowledge],
        "knowledge_image_references": knowledge_images,
        "prompt_authoring_contract": normalized_skill_release_lock(project)["prompt_authoring_contract"],
        "skill_release_lock": normalized_skill_release_lock(project),
        "prompt_file": f"prompts/{shot.get('id')}.md",
        "source_units": source_units,
        "inserted_units": inserted_units,
    }
    markdown = f"""# {shot.get('id')}｜{shot.get('title')}

- 时间：{timecode.get('start')}–{timecode.get('end')} 秒；独立生成时长 {timecode.get('duration')} 秒
- 风险：{(shot.get('risk') or {}).get('level')} — {join_cn((shot.get('risk') or {}).get('reasons'))}
- 产品规范：{product.get('profile_id')} v{product.get('version')}
- 风格规范：{style.get('profile_id')} v{style.get('version')}
- 纠错规则：{join_cn(metadata['correction_rule_ids'], '无')}

## 可直接提交的完整 Prompt

```text
{prompt_body}
```
"""
    return markdown, metadata


def build_shot_cards(bundle: Dict[str, Dict[str, Any]], report: Dict[str, Any]) -> str:
    project = bundle["project"]
    shots = bundle["shots"].get("shots") or []
    total_duration = sum(float((shot.get("timecode") or {}).get("duration", 0) or 0) for shot in shots)
    visual_seconds = {key: 0.0 for key in VALID_VISUAL_TYPES}
    delivery_seconds = {key: 0.0 for key in VALID_DELIVERY_MODES}
    for shot in shots:
        duration = float((shot.get("timecode") or {}).get("duration", 0) or 0)
        if shot.get("visual_type") in visual_seconds:
            visual_seconds[shot.get("visual_type")] += duration
        delivery_mode = (shot.get("audio") or {}).get("delivery_mode")
        if delivery_mode in delivery_seconds:
            delivery_seconds[delivery_mode] += duration

    ratio = lambda seconds: f"{seconds / total_duration:.0%}" if total_duration else "0%"
    lines = [
        f"# {project.get('project_name')}｜分镜确认卡",
        "",
        f"- 项目：`{project.get('project_id')}`",
        f"- 平台：{project.get('platform')}；模式：{project.get('generation_mode')}；比例：{project.get('aspect_ratio')}",
        f"- 发布状态：**{report.get('release_status')}**",
        f"- 检查：ERROR {report['counts']['ERROR']} / BLOCK {report['counts']['BLOCK']} / WARN {report['counts']['WARN']}",
        f"- 画面占比：产品展示 {ratio(visual_seconds['product_showcase'])} / 人物展示产品 {ratio(visual_seconds['person_product_showcase'])} / 人物吃产品 {ratio(visual_seconds['person_eating'])}",
        f"- 声音占比：画外音 {ratio(delivery_seconds['voiceover'])} / 人物讲话 {ratio(delivery_seconds['on_screen_speech'])} / 无口播 {ratio(delivery_seconds['silent'])}",
        "",
        "| 镜头 | 时间 | 画面类型 | 叙事职责 | 声音 | 情绪变化 | 产品状态 | 首帧/美观帧 |",
        "|---|---:|---|---|---|---|---|---|",
    ]
    for shot in shots:
        timecode = shot.get("timecode") or {}
        emotion = shot.get("emotion") or {}
        product_state = shot.get("product_state") or {}
        risk = shot.get("risk") or {}
        assets = shot.get("asset_links") or {}
        frame_status = f"{'已提取' if assets.get('source_first_frame') else '缺首帧'} / {'已选' if assets.get('selected_beauty_keyframe') else '待选'}"
        emotion_summary = "不适用" if shot.get("visual_type") == "product_showcase" else f"{emotion.get('start')} → {join_cn(emotion.get('progression'), '')} → {emotion.get('end')}"
        lines.append(
            "| {id} | {duration}s | {visual} | {role} | {delivery} | {emotion} | {state} | {frames} |".format(
                id=table_escape(shot.get("id")),
                duration=table_escape(timecode.get("duration")),
                visual=table_escape(shot.get("visual_type")),
                role=table_escape(shot.get("narrative_role")),
                delivery=table_escape((shot.get("audio") or {}).get("delivery_mode")),
                emotion=table_escape(emotion_summary),
                state=table_escape(product_state.get("state")),
                frames=frame_status,
            )
        )

    high_risk = [shot for shot in shots if (shot.get("risk") or {}).get("level") == "high"]
    lines.extend(["", "## 建议生成顺序", ""])
    if high_risk:
        first = high_risk[0]
        lines.append(f"先测试 `{first.get('id')}`：{join_cn((first.get('risk') or {}).get('reasons'))}。该镜头通过后再批量生成。")
    else:
        lines.append("未标记高风险镜头；仍建议先测试一个最能代表人物和产品质感的镜头。")

    blockers = [issue for issue in report.get("issues", []) if issue.get("level") in {"ERROR", "BLOCK"}]
    lines.extend(["", "## 必须处理", ""])
    if blockers:
        for issue in blockers:
            lines.append(f"- [{issue['level']}] `{issue['code']}` {issue['path']}：{issue['message']}")
    else:
        lines.append("- 没有结构错误或商业阻断项。")
    return "\n".join(lines) + "\n"


def extract_prompt_text(markdown: str) -> Optional[str]:
    match = re.search(r"```text\s*\n(.*?)\n```", markdown, re.S)
    return match.group(1).strip() if match else None


def build_prompt_only_aggregate(
    project: Dict[str, Any],
    entries: Sequence[Dict[str, Any]],
    prompt_dir: Path,
    compile_id: str,
) -> str:
    lines = [
        f"# {project.get('project_name')}｜Canonical Prompt-only 交付",
        "",
        f"- 项目：`{project.get('project_id')}`",
        f"- 执行档位：`prompt_only`",
        f"- 编译批次：`{compile_id}`",
        "- 权威来源：以下内容由编译器逐字汇总自 `prompts/Sxxx.md`；禁止手写替换。",
    ]
    for entry in entries:
        shot_id = str(entry.get("shot_id") or "")
        prompt_path = prompt_dir / f"{shot_id}.md"
        prompt_text = extract_prompt_text(prompt_path.read_text(encoding="utf-8"))
        if prompt_text is None:
            raise RuntimeError(f"Canonical per-shot Prompt is missing its text block: {prompt_path}")
        lines.extend(
            [
                "",
                f"## {shot_id}｜{entry.get('title')}",
                "",
                f"- Prompt SHA-256：`{entry.get('prompt_sha256')}`",
                "",
                "```text",
                prompt_text,
                "```",
            ]
        )
    return "\n".join(lines) + "\n"


def prompt_delivery_receipt(
    project_dir: Path,
    generation_pack: Dict[str, Any],
    aggregate_path: Optional[Path],
) -> Dict[str, Any]:
    prompt_dir = project_dir / "prompts"
    history_dir = project_dir / str(generation_pack.get("history_dir") or "")
    history_evidence = {
        str(path.relative_to(project_dir)): sha256_file(path)
        for path in sorted(history_dir.rglob("*"))
        if path.is_file() and path.name != "prompt_delivery_receipt.json"
    }
    receipt: Dict[str, Any] = {
        "schema_version": "1.0",
        "status": "delivery_authorized",
        "project_id": generation_pack.get("project_id"),
        "execution_tier": generation_pack.get("execution_tier"),
        "compile_id": generation_pack.get("compile_id"),
        "compile_input_sha256": generation_pack.get("compile_input_sha256"),
        "canonical_input_hashes": generation_pack.get("canonical_input_hashes"),
        "generation_pack_sha256": sha256_file(prompt_dir / "generation_pack.json"),
        "compiler_entrypoint_sha256": generation_pack.get("compiler_entrypoint_sha256"),
        "prompt_authoring_contract": "narrative-six-layer-v1",
        "required_prompt_headers": list(REQUIRED_PROMPT_HEADERS),
        "history_evidence_sha256": history_evidence,
        "generated_at": now_iso(),
        "shots": [
            {
                "shot_id": entry.get("shot_id"),
                "prompt_file": entry.get("prompt_file"),
                "prompt_sha256": entry.get("prompt_sha256"),
                "prompt_file_sha256": entry.get("prompt_file_sha256"),
            }
            for entry in generation_pack.get("shots") or []
        ],
        "aggregate": None,
    }
    if aggregate_path is not None:
        receipt["aggregate"] = {
            "path": str(aggregate_path.relative_to(project_dir)),
            "sha256": sha256_file(aggregate_path),
        }
    return receipt


def workflow_authorization_claim(workflow: Dict[str, Any]) -> Dict[str, Any]:
    compile_receipt = workflow.get("compile_receipt") or {}
    return {
        "execution_tier": workflow.get("execution_tier"),
        "current_stage": workflow.get("current_stage"),
        "status": workflow.get("status"),
        "prompt_delivery_authorized": workflow.get("prompt_delivery_authorized"),
        "completed_stages": list(workflow.get("completed_stages") or []),
        "blocked_by": list(workflow.get("blocked_by") or []),
        "next_allowed_actions": list(workflow.get("next_allowed_actions") or []),
        "compile_receipt": {
            "status": compile_receipt.get("status"),
            "compile_id": compile_receipt.get("compile_id"),
            "execution_tier": compile_receipt.get("execution_tier"),
            "generation_pack_sha256": compile_receipt.get("generation_pack_sha256"),
            "compiler_entrypoint_sha256": compile_receipt.get("compiler_entrypoint_sha256"),
            "receipt_path": compile_receipt.get("receipt_path"),
        },
    }


def verify_prompt_delivery(project_dir: Path, *, check_workflow: bool = True) -> Dict[str, Any]:
    """Verify that every deliverable is fresh, canonical and compiler-authorized."""
    project_dir = project_dir.expanduser().resolve()
    errors: List[Dict[str, str]] = []

    def fail(code: str, path: str, message: str) -> None:
        errors.append({"code": code, "path": path, "message": message})

    prompt_dir = project_dir / "prompts"
    pack_path = prompt_dir / "generation_pack.json"
    receipt_path = project_dir / "review" / "prompt_delivery_receipt.json"
    if not pack_path.is_file():
        fail("PROMPT_DELIVERY_PACK_MISSING", "prompts/generation_pack.json", "Compile the project before delivery.")
    if not receipt_path.is_file():
        fail("PROMPT_DELIVERY_RECEIPT_MISSING", "review/prompt_delivery_receipt.json", "Only the compiler may authorize Prompt delivery.")
    if errors:
        return {"status": "blocked", "error_count": len(errors), "errors": errors}

    project = load_json(project_dir / "project.json")
    shots_manifest = load_json(project_dir / "shots" / "shot_manifest.json")
    pack = load_json(pack_path)
    receipt = load_json(receipt_path)
    try:
        tier = normalized_execution_tier(project)
    except ValueError as exc:
        tier = None
        fail("EXECUTION_TIER_INVALID", "project.json.execution_tier", str(exc))
    if tier not in PROMPT_COMPILE_TIERS:
        fail("PROMPT_DELIVERY_TIER_NOT_COMPILABLE", "project.json.execution_tier", "Only prompt_only or full_delivery may authorize Prompt delivery.")
    if pack.get("execution_tier") != tier or receipt.get("execution_tier") != tier:
        fail("PROMPT_DELIVERY_TIER_MISMATCH", "prompts/generation_pack.json", "Project, generation pack and receipt execution tiers must match.")
    try:
        current_hashes = canonical_input_hashes(project_dir)
    except FileNotFoundError as exc:
        current_hashes = {}
        fail("PROMPT_DELIVERY_INPUT_MISSING", str(exc), "A canonical compile input is missing.")
    if current_hashes != pack.get("canonical_input_hashes") or current_hashes != receipt.get("canonical_input_hashes"):
        fail("PROMPT_DELIVERY_STALE", "prompts/generation_pack.json", "Canonical project inputs changed after compilation; recompile before delivery.")
    if pack.get("compile_input_sha256") != canonical_json_sha256(current_hashes):
        fail("PROMPT_DELIVERY_INPUT_HASH_MISMATCH", "prompts/generation_pack.json", "Compile input hash does not match the canonical input map.")
    if receipt.get("generation_pack_sha256") != sha256_file(pack_path):
        fail("PROMPT_DELIVERY_PACK_HASH_MISMATCH", "review/prompt_delivery_receipt.json", "The generation pack changed after authorization.")
    current_compiler_sha256 = sha256_file(Path(__file__).resolve())
    if (
        pack.get("compiler_entrypoint_sha256") != current_compiler_sha256
        or receipt.get("compiler_entrypoint_sha256") != current_compiler_sha256
    ):
        fail("PROMPT_DELIVERY_COMPILER_MISMATCH", "review/prompt_delivery_receipt.json", "Receipt was not produced by the currently installed canonical compiler; recompile.")
    if receipt.get("compile_id") != pack.get("compile_id") or receipt.get("status") != "delivery_authorized":
        fail("PROMPT_DELIVERY_RECEIPT_INVALID", "review/prompt_delivery_receipt.json", "Receipt must authorize the same compile batch.")
    history_dir = project_dir / str(pack.get("history_dir") or "")
    current_history_evidence = {
        str(path.relative_to(project_dir)): sha256_file(path)
        for path in sorted(history_dir.rglob("*"))
        if path.is_file() and path.name != "prompt_delivery_receipt.json"
    } if history_dir.is_dir() else {}
    if not current_history_evidence or current_history_evidence != receipt.get("history_evidence_sha256"):
        fail("PROMPT_DELIVERY_HISTORY_EVIDENCE_MISMATCH", str(pack.get("history_dir") or "prompts/history"), "Compiler history evidence is missing or changed after authorization.")

    canonical_shots = {
        str(shot.get("id")): shot
        for shot in shots_manifest.get("shots") or []
        if isinstance(shot, dict) and has_text(shot.get("id"))
    }
    pack_entries = {
        str(entry.get("shot_id")): entry
        for entry in pack.get("shots") or []
        if isinstance(entry, dict) and has_text(entry.get("shot_id"))
    }
    if set(canonical_shots) != set(pack_entries):
        fail("PROMPT_DELIVERY_SHOT_SET_MISMATCH", "prompts/generation_pack.json.shots", "Compiled Prompt shot ids must exactly match shot_manifest.json.")
    for shot_id, shot in canonical_shots.items():
        entry = pack_entries.get(shot_id) or {}
        relative_path = entry.get("prompt_file") or f"prompts/{shot_id}.md"
        path = project_dir / str(relative_path)
        if not path.is_file():
            fail("PROMPT_DELIVERY_FILE_MISSING", str(relative_path), "Canonical per-shot Prompt file is missing.")
            continue
        markdown = path.read_text(encoding="utf-8")
        prompt_text = extract_prompt_text(markdown)
        if prompt_text is None:
            fail("PROMPT_DELIVERY_TEXT_BLOCK_MISSING", str(relative_path), "Canonical Prompt text block is missing.")
            continue
        if sha256_file(path) != entry.get("prompt_file_sha256") or sha256_text(prompt_text) != entry.get("prompt_sha256"):
            fail("PROMPT_DELIVERY_PROMPT_HASH_MISMATCH", str(relative_path), "Per-shot Prompt changed after compilation.")
        positions = [prompt_text.find(header) for header in REQUIRED_PROMPT_HEADERS]
        if any(position < 0 for position in positions) or positions != sorted(positions):
            fail("PROMPT_DELIVERY_SECTION_CONTRACT_FAILED", str(relative_path), "All seven canonical Prompt sections must appear once and in order.")
        elif any(prompt_text.count(header) != 1 for header in REQUIRED_PROMPT_HEADERS):
            fail("PROMPT_DELIVERY_SECTION_CONTRACT_FAILED", str(relative_path), "Canonical Prompt section headings must appear exactly once.")
        normalized = re.sub(r"\s+", "", prompt_text).lower()
        missing_beats = [
            str(beat.get("id"))
            for beat in shot.get("action_beats") or []
            if isinstance(beat, dict) and has_text(beat.get("id")) and str(beat.get("id")).lower() not in normalized
        ]
        if missing_beats:
            fail("PROMPT_DELIVERY_ACTION_BEAT_MISSING", str(relative_path), f"Prompt omits canonical action beats: {missing_beats}")
        if compiled_prompt_quality_errors(prompt_text, shot):
            fail("PROMPT_DELIVERY_QUALITY_GATE_FAILED", str(relative_path), "Prompt fails semantic anti-placeholder or anti-padding checks.")

    ownership_issues: List[Dict[str, Any]] = []
    validate_prompt_output_ownership(project_dir, list(canonical_shots.values()), ownership_issues)
    for issue in ownership_issues:
        fail(str(issue.get("code")), str(issue.get("path")), str(issue.get("message")))

    aggregate = receipt.get("aggregate")
    if tier == "prompt_only":
        aggregate_path = prompt_dir / PROMPT_ONLY_AGGREGATE
        if not aggregate_path.is_file() or not isinstance(aggregate, dict):
            fail("PROMPT_ONLY_AGGREGATE_MISSING", f"prompts/{PROMPT_ONLY_AGGREGATE}", "Prompt-only delivery requires the compiler-owned aggregate.")
        elif aggregate.get("path") != f"prompts/{PROMPT_ONLY_AGGREGATE}" or aggregate.get("sha256") != sha256_file(aggregate_path):
            fail("PROMPT_ONLY_AGGREGATE_HASH_MISMATCH", f"prompts/{PROMPT_ONLY_AGGREGATE}", "Aggregate changed after compilation.")
    elif aggregate is not None:
        fail("PROMPT_ONLY_AGGREGATE_UNEXPECTED", "review/prompt_delivery_receipt.json.aggregate", "Full delivery must not claim a prompt-only aggregate.")

    if check_workflow:
        workflow_path = project_dir / "planning" / "workflow_state.json"
        if not workflow_path.is_file():
            fail("PROMPT_DELIVERY_WORKFLOW_MISSING", "planning/workflow_state.json", "Workflow state must carry the compile authorization.")
        else:
            workflow = load_json(workflow_path)
            workflow_receipt = workflow.get("compile_receipt") or {}
            if (
                workflow.get("execution_tier") != tier
                or workflow_receipt.get("status") != "delivery_authorized"
                or workflow_receipt.get("compile_id") != pack.get("compile_id")
                or workflow_receipt.get("generation_pack_sha256") != sha256_file(pack_path)
                or workflow_receipt.get("compiler_entrypoint_sha256") != current_compiler_sha256
            ):
                fail("PROMPT_DELIVERY_WORKFLOW_RECEIPT_MISMATCH", "planning/workflow_state.json.compile_receipt", "Workflow authorization does not match the latest compiler receipt.")
            claim = workflow_authorization_claim(workflow)
            if (
                claim != receipt.get("workflow_authorization_claim")
                or canonical_json_sha256(claim) != receipt.get("workflow_authorization_sha256")
            ):
                fail("PROMPT_DELIVERY_WORKFLOW_CLAIM_MISMATCH", "planning/workflow_state.json", "The complete compiler-owned workflow authorization state changed or was replaced.")

    return {
        "status": "authorized" if not errors else "blocked",
        "error_count": len(errors),
        "execution_tier": tier,
        "compile_id": pack.get("compile_id"),
        "errors": errors,
    }


def compile_project(project_dir: Path) -> Dict[str, Any]:
    project_dir = project_dir.expanduser().resolve()
    project_preflight = load_json(project_dir / "project.json")
    execution_tier = normalized_execution_tier(project_preflight)
    if execution_tier not in PROMPT_COMPILE_TIERS:
        raise RuntimeError(
            f"Compilation blocked: execution_tier={execution_tier!r}; only prompt_only or full_delivery may compile deliverable Prompts."
        )
    report = lint_project(project_dir, write_report=True)
    if report["counts"]["ERROR"]:
        raise RuntimeError(f"Compilation blocked by {report['counts']['ERROR']} structural error(s). Review review/lint_report.json.")
    bundle = read_bundle(project_dir)
    intended_use = (bundle["project"].get("commercial") or {}).get("intended_use", "internal_test")
    if intended_use == "commercial_release" and report["counts"]["BLOCK"]:
        raise RuntimeError(
            f"Compilation blocked for commercial_release by {report['counts']['BLOCK']} commercial clearance block(s). "
            "Set intended_use to internal_test/client_review while preparing, or clear the commercial gate first."
        )

    prompt_length_contract = normalized_prompt_length_contract(bundle["project"])
    prompt_dir = project_dir / "prompts"
    compile_id = datetime.now().strftime("%Y%m%dT%H%M%S") + "-" + uuid.uuid4().hex[:6]
    history_dir = prompt_dir / "history" / compile_id
    entries = []
    for shot in bundle["shots"].get("shots") or []:
        markdown, metadata = compile_shot(bundle, shot)
        prompt_path = prompt_dir / f"{shot.get('id')}.md"
        prompt_match = re.search(r"```text\s*\n(.*?)\n```", markdown, re.S)
        if not prompt_match:
            raise RuntimeError(f"Compiled prompt for {shot.get('id')} has no canonical text block.")
        quality_errors = compiled_prompt_quality_errors(prompt_match.group(1).strip(), shot)
        if quality_errors:
            raise RuntimeError(
                f"Compilation blocked for {shot.get('id')}: " + ", ".join(sorted(quality_errors))
            )
        write_text(prompt_path, markdown)
        write_text(history_dir / f"{shot.get('id')}.md", markdown)
        metadata["prompt_sha256"] = sha256_text(prompt_match.group(1).strip())
        metadata["prompt_file_sha256"] = sha256_file(prompt_path)
        metadata["prompt_non_whitespace_characters"] = len(re.sub(r"\s+", "", prompt_match.group(1).strip()))
        if prompt_length_contract["enabled"]:
            count = metadata["prompt_non_whitespace_characters"]
            minimum = prompt_length_contract["minimum_non_whitespace_characters"]
            maximum = prompt_length_contract["maximum_non_whitespace_characters"]
            if count < minimum or count > maximum:
                raise RuntimeError(
                    f"Compilation blocked: {shot.get('id')} Prompt has {count} non-whitespace characters; "
                    f"enabled project contract requires {minimum}–{maximum}."
                )
        entries.append(metadata)

    # Persist compile-owned project status before hashing canonical inputs.  If
    # this write happened after pack creation, every fresh pack would be stale
    # immediately because project.json itself is a canonical input.
    project = bundle["project"]
    if project.get("status") in {"draft", "analyzed"}:
        project["status"] = "prompt_ready"
    project["updated_at"] = now_iso()
    write_json(project_dir / "project.json", project)

    source = bundle["source"]
    input_hashes = canonical_input_hashes(project_dir)
    compile_input_sha256 = canonical_json_sha256(input_hashes)
    canonical_shots = {
        str(shot.get("id")): shot
        for shot in (bundle["shots"].get("shots") or [])
        if isinstance(shot, dict) and has_text(shot.get("id"))
    }
    for entry in entries:
        shot_id = str(entry.get("shot_id") or "")
        entry["compile_id"] = compile_id
        entry["canonical_input_hashes"] = dict(input_hashes)
        entry["compile_input_sha256"] = compile_input_sha256
        entry["shot_input_sha256"] = canonical_json_sha256(canonical_shots.get(shot_id, {}))
    generation_pack = {
        "schema_version": "1.2",
        "project_id": bundle["project"].get("project_id"),
        "execution_tier": execution_tier,
        "compile_id": compile_id,
        "generated_at": now_iso(),
        "source_sha256": source.get("sha256"),
        "compiler_entrypoint_sha256": sha256_file(Path(__file__).resolve()),
        "canonical_input_hashes": input_hashes,
        "compile_input_sha256": compile_input_sha256,
        "prompt_length_contract": prompt_length_contract,
        "skill_release_lock": normalized_skill_release_lock(bundle["project"]),
        "release_status": report.get("release_status"),
        "history_dir": str(history_dir.relative_to(project_dir)),
        "shots": entries,
    }
    write_json(prompt_dir / "generation_pack.json", generation_pack)
    write_json(history_dir / "generation_pack.json", generation_pack)
    input_snapshot = {
        "schema_version": "1.1",
        "compile_id": compile_id,
        "captured_at": now_iso(),
        "canonical_input_hashes": input_hashes,
        "compile_input_sha256": compile_input_sha256,
        "prompt_length_contract": prompt_length_contract,
        "skill_release_lock": normalized_skill_release_lock(bundle["project"]),
        "project": bundle["project"],
        "product_bible": bundle["product"],
        "product_library": bundle["product_library"],
        "style_bible": bundle["style"],
        "correction_memory": bundle["corrections"],
        "knowledge_index": bundle["knowledge"],
        "avatar_library": bundle["avatars"],
        "story_plan": bundle["story"],
        "asset_reuse_plan": bundle["asset_reuse"],
        "source_manifest": bundle["source"],
        "shot_manifest": bundle["shots"],
    }
    write_json(history_dir / "input_snapshot.json", input_snapshot)
    shot_cards = build_shot_cards(bundle, report)
    write_text(project_dir / "review" / "shot_cards.md", shot_cards)
    write_text(history_dir / "shot_cards.md", shot_cards)

    aggregate_path: Optional[Path] = None
    if execution_tier == "prompt_only":
        aggregate_path = prompt_dir / PROMPT_ONLY_AGGREGATE
        aggregate_text = build_prompt_only_aggregate(bundle["project"], entries, prompt_dir, compile_id)
        write_text(aggregate_path, aggregate_text)
        write_text(history_dir / PROMPT_ONLY_AGGREGATE, aggregate_text)
    else:
        stale_aggregate = prompt_dir / PROMPT_ONLY_AGGREGATE
        if stale_aggregate.is_file():
            stale_aggregate.unlink()

    receipt = prompt_delivery_receipt(project_dir, generation_pack, aggregate_path)
    receipt_path = project_dir / "review" / "prompt_delivery_receipt.json"

    workflow_path = project_dir / "planning" / "workflow_state.json"
    if workflow_path.is_file():
        workflow = load_json(workflow_path)
        workflow["execution_tier"] = execution_tier
        workflow["current_stage"] = "prompt_compile"
        workflow["status"] = "in_progress"
        workflow["blocked_by"] = []
        workflow["prompt_delivery_authorized"] = True
        workflow["compile_receipt"] = {
            "status": receipt["status"],
            "compile_id": compile_id,
            "execution_tier": execution_tier,
            "generation_pack_sha256": receipt["generation_pack_sha256"],
            "compiler_entrypoint_sha256": receipt["compiler_entrypoint_sha256"],
            "receipt_path": "review/prompt_delivery_receipt.json",
        }
        workflow["next_allowed_actions"] = (
            ["deliver_compiled_prompt_only", "verify_prompt_delivery"]
            if execution_tier == "prompt_only"
            else ["export_docx", "run_alignment_check", "verify_prompt_delivery"]
        )
        completed = workflow.setdefault("completed_stages", [])
        if execution_tier == "full_delivery" and "first_frame_approval" not in completed:
            completed.append("first_frame_approval")
        if "prompt_compile" not in completed:
            completed.append("prompt_compile")
        workflow["updated_at"] = now_iso()
        write_json(workflow_path, workflow)
        receipt["workflow_authorization_claim"] = workflow_authorization_claim(workflow)
        receipt["workflow_authorization_sha256"] = canonical_json_sha256(receipt["workflow_authorization_claim"])
    write_json(receipt_path, receipt)
    write_json(history_dir / "prompt_delivery_receipt.json", receipt)
    verification = verify_prompt_delivery(project_dir)
    if verification["status"] != "authorized":
        if workflow_path.is_file():
            workflow = load_json(workflow_path)
            workflow["prompt_delivery_authorized"] = False
            workflow["status"] = "blocked"
            workflow["blocked_by"] = [item["code"] for item in verification["errors"]]
            workflow["next_allowed_actions"] = ["fix_canonical_inputs", "compile"]
            workflow["updated_at"] = now_iso()
            write_json(workflow_path, workflow)
        raise RuntimeError(
            "Compilation produced outputs but delivery authorization failed: "
            + ", ".join(sorted({item["code"] for item in verification["errors"]}))
        )
    return {
        "shot_count": len(entries),
        "execution_tier": execution_tier,
        "release_status": report.get("release_status"),
        "compile_id": compile_id,
        "history_dir": str(history_dir),
        "generation_pack": str(prompt_dir / "generation_pack.json"),
        "delivery_receipt": str(receipt_path),
        "aggregate": str(aggregate_path) if aggregate_path else None,
        "delivery_status": verification["status"],
        "shot_cards": str(project_dir / "review" / "shot_cards.md"),
    }


def print_lint(report: Dict[str, Any]) -> None:
    print(f"Release status: {report['release_status']}")
    print(f"ERROR={report['counts']['ERROR']} BLOCK={report['counts']['BLOCK']} WARN={report['counts']['WARN']}")
    for issue in report.get("issues", []):
        print(f"[{issue['level']}] {issue['code']} {issue['path']}: {issue['message']}")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Operate a Jimeng video-remix project.")
    subparsers = parser.add_subparsers(dest="command", required=True)

    lint_parser = subparsers.add_parser("lint", help="Validate project completeness, conflicts and release clearance.")
    lint_parser.add_argument("--project-dir", required=True, type=Path)

    compile_parser = subparsers.add_parser("compile", help="Compile prompts, generation pack and review cards.")
    compile_parser.add_argument("--project-dir", required=True, type=Path)

    verify_parser = subparsers.add_parser(
        "verify-prompt-delivery",
        help="Verify that Prompt files are fresh compiler-owned outputs with a matching delivery receipt.",
    )
    verify_parser.add_argument("--project-dir", required=True, type=Path)

    return parser


def main() -> int:
    args = build_parser().parse_args()
    try:
        if args.command == "lint":
            report = lint_project(args.project_dir, write_report=True)
            print_lint(report)
            return 1 if report["counts"]["ERROR"] or report["counts"]["BLOCK"] else 0
        if args.command == "compile":
            print(json.dumps(compile_project(args.project_dir), ensure_ascii=False, indent=2))
            return 0
        if args.command == "verify-prompt-delivery":
            verification = verify_prompt_delivery(args.project_dir)
            print(json.dumps(verification, ensure_ascii=False, indent=2))
            return 0 if verification["status"] == "authorized" else 1
    except (FileNotFoundError, ValueError, RuntimeError, json.JSONDecodeError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
