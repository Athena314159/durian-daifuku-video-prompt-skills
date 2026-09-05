#!/usr/bin/env python3
"""Normalize legacy correction rules into the generation contract.

Older projects stored semantic scopes such as ``all_frames`` and the rule
text under ``rule``.  The generation compiler only understands the canonical
``shot/project/product/style`` scopes and ``instruction``.  Keeping this
conversion in one module prevents the compiler, image gate and migration code
from silently disagreeing about which feedback is active.
"""

from __future__ import annotations

import copy
import hashlib
from datetime import datetime
from typing import Any


CANONICAL_SCOPES = {"shot", "project", "product", "style"}
LEGACY_SCOPE_MAP = {
    "all_frames": ("project", 95),
    "face_required_frames": ("project", 94),
    "packaging_visible_frames": ("project", 93),
    "whole_or_held_daifuku": ("product", 92),
    "all_daifuku_states": ("product", 91),
    "delivery": ("project", 90),
}


def now_iso() -> str:
    return datetime.now().astimezone().replace(microsecond=0).isoformat()


def _stable_legacy_id(rule: dict[str, Any]) -> str:
    existing = str(rule.get("id") or "").strip()
    if existing:
        return existing
    payload = "|".join(
        str(rule.get(key) or "")
        for key in ("scope", "trigger", "rule", "instruction")
    )
    return "LEGACY-FEEDBACK-" + hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]


def normalize_rule(
    rule: Any,
    *,
    project_id: str | None,
    product_profile: str | None,
    style_profile: str | None,
) -> dict[str, Any] | None:
    """Return one canonical active rule, or ``None`` for untranslatable data."""
    if not isinstance(rule, dict):
        return None
    value = copy.deepcopy(rule)
    scope = str(value.get("scope") or "").strip()
    instruction = str(value.get("instruction") or value.get("rule") or "").strip()
    if not instruction:
        return None
    if scope in CANONICAL_SCOPES:
        target = value.get("target")
        priority = value.get("priority")
        if not isinstance(priority, int) or not 1 <= priority <= 100:
            priority = 100
        value.update(
            {
                "id": _stable_legacy_id(value),
                "scope": scope,
                "target": target if target not in ("",) else None,
                "priority": priority,
                "instruction": instruction,
                "active": value.get("active") is True,
            }
        )
        return value

    mapped = LEGACY_SCOPE_MAP.get(scope)
    if mapped is None:
        return None
    mapped_scope, default_priority = mapped
    target: str | None
    if mapped_scope == "product":
        target = product_profile or "*"
    elif mapped_scope == "style":
        target = style_profile or "*"
    else:
        target = project_id or "*"
    value.update(
        {
            "id": _stable_legacy_id(value),
            "scope": mapped_scope,
            "target": value.get("target") or target,
            "priority": default_priority,
            "instruction": instruction,
            "active": True,
            "origin": value.get("origin") or "legacy_migrated",
            "legacy_scope": scope,
        }
    )
    # Do not invent a new wall-clock value on every in-memory read: the
    # normalized rule is also hashed into compile/authorization receipts. If
    # an older persisted timestamp exists, preserve it; otherwise ``origin``
    # is the deterministic migration evidence.
    if not value.get("migrated_at"):
        value.pop("migrated_at", None)
    return value


def normalize_memory(
    memory: Any,
    *,
    project_id: str | None,
    product_profile: str | None,
    style_profile: str | None,
) -> tuple[dict[str, Any], bool]:
    """Normalize all usable rules and report whether the persisted shape changed."""
    value = copy.deepcopy(memory) if isinstance(memory, dict) else {}
    # An explicitly empty/placeholder correction file is already semantically
    # empty. Preserve its byte-level shape for fixtures and old projects that
    # have not written any feedback yet; only files that actually carry rules
    # need schema normalization.
    if isinstance(memory, dict) and "rules" not in memory:
        return value, False
    raw_rules = value.get("rules") if isinstance(value.get("rules"), list) else []
    normalized: list[dict[str, Any]] = []
    for raw in raw_rules:
        rule = normalize_rule(
            raw,
            project_id=project_id,
            product_profile=product_profile,
            style_profile=style_profile,
        )
        if rule is not None:
            normalized.append(rule)
    value["schema_version"] = "1.1"
    value["version"] = int(value.get("version") or 0) + (1 if normalized != raw_rules else 0)
    value["rules"] = normalized
    changed = value.get("rules") != raw_rules or memory.get("schema_version") != "1.1" if isinstance(memory, dict) else True
    # Normalization is called by lint, compile, authorization and export. It
    # must be a pure/read-stable transform; stamping ``now()`` here would make
    # the same legacy file hash differently on every read and would create a
    # permanent stale-snapshot loop. Migration/writeback code may add its own
    # timestamp when it persists the canonical memory.
    return value, bool(changed)
