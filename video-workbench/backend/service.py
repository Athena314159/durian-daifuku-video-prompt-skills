"""Project, knowledge-library and approval services for the local workbench."""

from __future__ import annotations

import io
import fcntl
import hashlib
import json
import math
import os
import re
import shutil
import tempfile
import threading
from pathlib import Path
from typing import Any, BinaryIO, Dict, Iterable, List, Optional, Tuple

from .adapters import Toolchain
from .errors import ApiError
from .storage import (
    atomic_write_json,
    copy_stream_atomic,
    new_id,
    now_iso,
    quoted_path,
    read_json,
    safe_filename,
    safe_join,
    sha256_file,
    slugify,
    validate_identifier,
)


VALID_VIDEO_EXTENSIONS = {".mp4", ".mov", ".m4v", ".webm", ".mkv", ".avi"}
VALID_IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp", ".gif"}
VALID_KNOWLEDGE_EXTENSIONS = {
    ".jpg",
    ".jpeg",
    ".png",
    ".webp",
    ".gif",
    ".mp4",
    ".mov",
    ".pdf",
    ".json",
    ".txt",
    ".md",
    ".docx",
}
VALID_EXECUTION_TIERS = {"source_intake", "diagnose_only", "first_frame_only", "prompt_only", "full_delivery"}
VALID_PRODUCT_MODES = {"preserve", "replace"}
VALID_CHARACTER_MODES = {"preserve", "head_replace", "full_replace"}
VALID_TASK_MODES = {"single", "dual"}
VALID_AVATAR_USAGE_SCOPES = {"head_only", "full_only", "head_and_full"}
PACKAGING_LAYERS = ("individual_package", "retail_box", "inner_tray", "shipping_carton")
PACKAGING_LAYER_SET = set(PACKAGING_LAYERS)
PACKAGING_CONTRACT_FIELDS = {
    "present",
    "dimensions_cm",
    "quantity",
    "topology",
    "contains",
    "text_layout",
    "material",
    "attributes",
    "notes",
}


def _disabled_prompt_length_contract() -> Dict[str, Any]:
    return {
        "enabled": False,
        "minimum_non_whitespace_characters": 0,
        "maximum_non_whitespace_characters": 0,
    }


def _default_config(execution_tier: str = "source_intake") -> Dict[str, Any]:
    return {
        "product_mode": "preserve",
        "product_id": None,
        "character_mode": "preserve",
        "avatar_id": None,
        "source_person_id": None,
        "shot_scope": {"mode": "all"},
        "execution_tier": execution_tier,
        "task_mode": "single",
        "script_locked": False,
        "prompt_length_contract": _disabled_prompt_length_contract(),
        "codex": {"enabled": False, "model": None},
    }


class WorkbenchService:
    schema_version = "workbench-local-v1"

    def __init__(
        self,
        data_root: Optional[Path] = None,
        projects_root: Optional[Path] = None,
        skill_dir: Optional[Path] = None,
        python_bin: Optional[str] = None,
        ffmpeg_bin: Optional[str] = None,
        ffprobe_bin: Optional[str] = None,
        codex_bin: Optional[str] = None,
        maximum_video_bytes: int = 8 * 1024 * 1024 * 1024,
        maximum_knowledge_bytes: int = 512 * 1024 * 1024,
    ) -> None:
        default_root = Path(__file__).resolve().parent.parent / "data"
        self.data_root = (data_root or Path(os.environ.get("VIDEO_WORKBENCH_DATA", str(default_root)))).resolve()
        configured_projects = projects_root or Path(os.environ.get("VIDEO_WORKBENCH_PROJECTS", str(self.data_root / "projects")))
        self.projects_root = configured_projects.resolve()
        self.knowledge_root = self.data_root / "knowledge"
        self.tasks_root = self.data_root / "tasks"
        for directory in (self.projects_root, self.knowledge_root / "products", self.knowledge_root / "avatars", self.tasks_root):
            directory.mkdir(parents=True, exist_ok=True)
        self._instance_lock_handle: Optional[Any] = None
        self._acquire_instance_lock()
        self.maximum_video_bytes = maximum_video_bytes
        self.maximum_knowledge_bytes = maximum_knowledge_bytes
        self.toolchain = Toolchain(skill_dir, python_bin, ffmpeg_bin, ffprobe_bin, codex_bin)
        self._lock = threading.RLock()
        from .tasks import TaskManager

        self.tasks = TaskManager(self)

    def _acquire_instance_lock(self) -> None:
        lock_path = self.data_root / "workbench.lock"
        handle = lock_path.open("a+", encoding="utf-8")
        try:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except (BlockingIOError, OSError):
            try:
                handle.seek(0)
                owner = json.loads(handle.read() or "{}")
            except (OSError, json.JSONDecodeError):
                owner = {}
            handle.close()
            raise ApiError(
                409,
                "WORKBENCH_INSTANCE_ALREADY_RUNNING",
                "Another workbench instance already owns this data/project root",
                {"lock_path": str(lock_path), "owner": owner},
            )
        record = {
            "schema_version": "workbench-instance-lock-v1",
            "pid": os.getpid(),
            "data_root": str(self.data_root),
            "projects_root": str(self.projects_root),
            "acquired_at": now_iso(),
        }
        handle.seek(0)
        handle.truncate(0)
        json.dump(record, handle, ensure_ascii=False, indent=2)
        handle.write("\n")
        handle.flush()
        os.fsync(handle.fileno())
        self._instance_lock_handle = handle

    def close(self) -> None:
        tasks = getattr(self, "tasks", None)
        if tasks is not None:
            tasks.shutdown()
        handle = self._instance_lock_handle
        self._instance_lock_handle = None
        if handle is not None:
            try:
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
            finally:
                handle.close()

    # ---- bootstrap and projects -------------------------------------------------

    def bootstrap(self) -> Dict[str, Any]:
        capabilities = self.toolchain.capabilities()
        capabilities.update(
            {
                "version": "0.1.0",
                "bind_policy": "loopback_only_by_default",
                "maximum_video_bytes": self.maximum_video_bytes,
                "task_modes": sorted(VALID_TASK_MODES),
                "execution_tiers": sorted(VALID_EXECUTION_TIERS),
                "character_modes": sorted(VALID_CHARACTER_MODES),
                "projects_root": str(self.projects_root),
            }
        )
        knowledge = self.list_knowledge()
        return {
            "ok": True,
            "server": capabilities,
            "adapters": capabilities["adapters"],
            "projects": self.list_projects(),
            "knowledge": {"products": knowledge["products"], "avatars": knowledge["avatars"]},
        }

    def _new_project_id(self, name: str) -> str:
        base = "%s-%s" % (now_iso()[:10].replace("-", ""), slugify(name, "project"))
        candidate = base[:100]
        index = 2
        while (self.projects_root / candidate).exists():
            candidate = (base[:94] + "-%d" % index)[:100]
            index += 1
        return candidate

    def _workbench_path(self, project_dir: Path) -> Path:
        return project_dir / "workbench" / "state.json"

    def _minimal_project(self, project_dir: Path, project_id: str, name: str, execution_tier: str) -> None:
        for relative in (
            "source/uploads",
            "source/thumbnails",
            "source/analysis",
            "planning",
            "shots",
            "library",
            "review",
            "prompts",
            "exports",
            "workbench",
        ):
            (project_dir / relative).mkdir(parents=True, exist_ok=True)
        project_json = project_dir / "project.json"
        if not project_json.exists():
            atomic_write_json(
                project_json,
                {
                    "schema_version": "1.1",
                    "project_id": project_id,
                    "project_name": name,
                    "execution_tier": execution_tier,
                    "product_mode": "preserve_source_product",
                    "prompt_length_contract": _disabled_prompt_length_contract(),
                    "created_at": now_iso(),
                    "updated_at": now_iso(),
                },
            )
        else:
            raw = read_json(project_json, {})
            if isinstance(raw, dict) and "prompt_length_contract" not in raw:
                raw["prompt_length_contract"] = _disabled_prompt_length_contract()
                atomic_write_json(project_json, raw)

    def create_project(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        name = str(payload.get("name") or "").strip()
        if not name or len(name) > 120:
            raise ApiError(400, "INVALID_PROJECT_NAME", "Project name must contain 1-120 characters")
        execution_tier = str(payload.get("execution_tier") or "source_intake")
        if execution_tier not in VALID_EXECUTION_TIERS:
            raise ApiError(400, "INVALID_EXECUTION_TIER", "Unsupported execution tier")
        project_id = str(payload.get("id") or self._new_project_id(name))
        validate_identifier(project_id, "project id")
        project_dir = self.projects_root / project_id
        if project_dir.exists():
            raise ApiError(409, "PROJECT_EXISTS", "A project with this id already exists")

        product_mode = str(payload.get("product_mode") or "preserve")
        product_id = payload.get("product_id")
        if product_mode not in VALID_PRODUCT_MODES:
            raise ApiError(400, "INVALID_PRODUCT_MODE", "product_mode must be preserve or replace")
        if product_id is not None:
            product_id = str(product_id)
            validate_identifier(product_id, "product id")

        built_in_profiles = {item["id"] for item in self._built_in_products()}
        product_profile = product_id if product_mode == "replace" and product_id in built_in_profiles else None
        command = self.toolchain.initialize_project_command(
            name,
            self.projects_root,
            project_id,
            execution_tier,
            product_profile,
        )
        if command is None:
            initialization = {
                "status": "blocked",
                "code": "DIRECTOR_INIT_SCRIPT_NOT_AVAILABLE",
                "message": "The installed jimeng-video-remix-director init script was not found.",
            }
            project_dir.mkdir(parents=True, exist_ok=False)
        else:
            result = self.toolchain.run_sync(command, timeout=180)
            if result["ok"] and project_dir.is_dir():
                initialization = {"status": "ready", "command": "init_project.py"}
            else:
                project_dir.mkdir(parents=True, exist_ok=True)
                initialization = {
                    "status": "blocked",
                    "code": "DIRECTOR_INIT_FAILED",
                    "message": "The director project initializer did not complete.",
                    "details": {
                        "returncode": result.get("returncode"),
                        "stderr": (result.get("stderr") or "")[-3000:],
                    },
                }

        self._minimal_project(project_dir, project_id, name, execution_tier)
        config = _default_config(execution_tier)
        config["product_mode"] = product_mode
        config["product_id"] = product_id
        if isinstance(payload.get("character_mode"), str):
            config["character_mode"] = payload["character_mode"]
        if payload.get("avatar_id") is not None:
            config["avatar_id"] = str(payload["avatar_id"])
        if payload.get("source_person_id") is not None:
            config["source_person_id"] = str(payload["source_person_id"])
        if isinstance(payload.get("task_mode"), str):
            config["task_mode"] = payload["task_mode"]
        config = self._validated_config(config)
        binding_status = "ready"
        if product_mode == "replace" and product_id and product_profile is None:
            binding_status = "waiting_for_custom_product_binding"
        state = {
            "schema_version": self.schema_version,
            "id": project_id,
            "name": name,
            "created_at": now_iso(),
            "updated_at": now_iso(),
            "initialization": initialization,
            "canonical_hydrated": True,
            "config": config,
            "product_binding_status": binding_status,
            "product_binding": {
                "status": binding_status,
                "selected_id": product_id,
                "applied_id": product_profile,
                "updated_at": now_iso(),
            },
            "avatar_binding": {
                "status": "ready" if config["character_mode"] == "preserve" else "waiting_for_apply",
                "selected_id": config.get("avatar_id"),
                "applied_id": None,
                "updated_at": now_iso(),
            },
            "video": None,
        }
        atomic_write_json(self._workbench_path(project_dir), state)
        return self.get_project(project_id)

    def _load_state(self, project_dir: Path) -> Dict[str, Any]:
        path = self._workbench_path(project_dir)
        if not path.is_file():
            raw = read_json(project_dir / "project.json", {})
            state = {
                "schema_version": self.schema_version,
                "id": project_dir.name,
                "name": raw.get("project_name") or project_dir.name,
                "created_at": raw.get("created_at") or now_iso(),
                "updated_at": raw.get("updated_at") or now_iso(),
                "initialization": {"status": "ready"},
                "config": _default_config(str(raw.get("execution_tier") or "source_intake")),
                "product_binding_status": "ready",
                "product_binding": {
                    "status": "ready",
                    "selected_id": raw.get("product_profile"),
                    "applied_id": raw.get("product_profile"),
                    "updated_at": raw.get("updated_at") or now_iso(),
                },
                "avatar_binding": {
                    "status": "ready",
                    "selected_id": None,
                    "applied_id": None,
                    "updated_at": raw.get("updated_at") or now_iso(),
                },
                "video": None,
            }
            atomic_write_json(path, state)
        value = read_json(path)
        if not isinstance(value, dict):
            raise ApiError(500, "INVALID_PROJECT_STATE", "Project workbench state is not a JSON object")
        if value.get("canonical_hydrated") is not True:
            raw = read_json(project_dir / "project.json", {})
            avatar_plan = read_json(project_dir / "planning" / "avatar_binding.json", {})
            script_lock = read_json(project_dir / "planning" / "revised_script_lock.json", {})
            config = dict(value.get("config") or _default_config(str(raw.get("execution_tier") or "source_intake")))
            config["execution_tier"] = str(raw.get("execution_tier") or config.get("execution_tier") or "source_intake")
            if raw.get("product_mode") == "replace_product" or raw.get("product_profile"):
                config["product_mode"] = "replace"
                config["product_id"] = raw.get("product_profile")
            if isinstance(avatar_plan, dict) and avatar_plan.get("target_avatar_id"):
                operation = str(avatar_plan.get("operation") or "")
                config["character_mode"] = "full_replace" if operation in {"replace_person", "replace_full", "full_replace"} else "head_replace"
                config["avatar_id"] = avatar_plan.get("target_avatar_id")
                config["source_person_id"] = avatar_plan.get("source_person_id") or avatar_plan.get("source_speaker_id")
            config["script_locked"] = isinstance(script_lock, dict) and str(script_lock.get("status") or "").lower() == "locked"
            value["config"] = self._validated_config(config)
            product_id = value["config"].get("product_id")
            if value["config"].get("product_mode") == "replace" and product_id:
                value["product_binding_status"] = "ready"
                value["product_binding"] = {
                    **(value.get("product_binding") or {}),
                    "status": "ready",
                    "selected_id": product_id,
                    "applied_id": product_id,
                    "source": "canonical_project_import",
                    "updated_at": raw.get("updated_at") or now_iso(),
                }
            if value["config"].get("character_mode") != "preserve":
                avatar_id = value["config"].get("avatar_id")
                source_person_id = value["config"].get("source_person_id")
                value["avatar_binding"] = {
                    **(value.get("avatar_binding") or {}),
                    "status": "ready" if str((avatar_plan or {}).get("status") or "").lower() in {"ready", "locked", "approved"} else "waiting_for_apply",
                    "selected_id": avatar_id,
                    "applied_id": avatar_id,
                    "source_person_id": source_person_id,
                    "source": "canonical_project_import",
                    "updated_at": (avatar_plan or {}).get("updated_at") or raw.get("updated_at") or now_iso(),
                }
            value["canonical_hydrated"] = True
            atomic_write_json(path, value)
        # project.json is the only Prompt-length fact source used by the paired
        # Skills. Project reads only *project* it into the UI: they must never
        # rewrite canonical project.json or invalidate a compile just by opening
        # an old project.
        raw = read_json(project_dir / "project.json", {})
        config = dict(value.get("config") or _default_config(str(raw.get("execution_tier") or "source_intake")))
        raw_contract = raw.get("prompt_length_contract")
        contract_source = raw_contract if "prompt_length_contract" in raw else config.get("prompt_length_contract")
        try:
            normalized_contract = self._normalize_prompt_length_contract(contract_source)
        except ApiError as exc:
            normalized_contract = _disabled_prompt_length_contract()
            value["prompt_length_contract_issue"] = {
                "code": exc.code,
                "message": exc.message,
                "recoverable_in_project_settings": True,
            }
        else:
            value.pop("prompt_length_contract_issue", None)
        config["prompt_length_contract"] = normalized_contract
        value["config"] = config
        return value

    def get_project_dir(self, project_id: str) -> Path:
        validate_identifier(project_id, "project id")
        project_dir = safe_join(self.projects_root, project_id)
        if not project_dir.is_dir() or not (project_dir / "project.json").is_file():
            raise ApiError(404, "PROJECT_NOT_FOUND", "Project was not found")
        return project_dir

    def _video_public(self, project_id: str, video: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
        if not video:
            return None
        value = dict(video)
        relative_path = value.get("path")
        thumbnail_path = value.get("thumbnail_path")
        if relative_path:
            value["video_url"] = "/api/v1/projects/%s/media/%s" % (project_id, quoted_path(relative_path))
        if thumbnail_path:
            value["thumbnail_url"] = "/api/v1/projects/%s/media/%s" % (project_id, quoted_path(thumbnail_path))
        else:
            value["thumbnail_url"] = None
        return value

    def _project_source_people(self, project_dir: Path) -> List[Dict[str, Any]]:
        """Return canonical source-person choices without inventing identities."""
        role_lock = read_json(project_dir / "planning" / "role_lock.json", {})
        if not isinstance(role_lock, dict):
            return []
        people: List[Dict[str, Any]] = []
        seen: set = set()
        for key in ("speakers", "people"):
            collection = role_lock.get(key)
            if isinstance(collection, dict):
                values = [(str(map_key), item) for map_key, item in collection.items()]
            elif isinstance(collection, list):
                values = [(None, item) for item in collection]
            else:
                continue
            for map_key, item in values:
                if isinstance(item, str):
                    record: Dict[str, Any] = {"id": item, "label": item}
                elif isinstance(item, dict):
                    person_id = next(
                        (
                            item.get(candidate)
                            for candidate in ("source_person_id", "person_id", "source_identity", "speaker_id", "id")
                            if item.get(candidate) not in (None, "")
                        ),
                        map_key,
                    )
                    if person_id in (None, ""):
                        continue
                    label = next(
                        (
                            item.get(candidate)
                            for candidate in ("user_label", "label", "name", "display_name", "source_identity")
                            if item.get(candidate) not in (None, "")
                        ),
                        person_id,
                    )
                    record = {
                        "id": str(person_id),
                        "label": str(label),
                        "speaker_id": item.get("speaker_id"),
                        "person_id": item.get("person_id"),
                        "source_identity": item.get("source_identity"),
                        "visible_scope": item.get("visible_scope"),
                        "camera_holder": item.get("camera_holder"),
                        "aliases": list(
                            dict.fromkeys(
                                str(item.get(candidate)).strip()
                                for candidate in ("source_person_id", "person_id", "source_identity", "speaker_id", "id")
                                if item.get(candidate) not in (None, "")
                            )
                        ),
                    }
                else:
                    continue
                identifier = str(record["id"]).strip()
                if not identifier or identifier in seen:
                    continue
                record["id"] = identifier
                seen.add(identifier)
                people.append(record)
        return people

    def _known_source_person_ids(self, project_dir: Path) -> List[str]:
        known = {str(item["id"]) for item in self._project_source_people(project_dir)}

        def walk(value: Any) -> None:
            if isinstance(value, dict):
                for key, child in value.items():
                    if key in {"source_person_id", "person_id", "speaker_id", "source_identity"} and isinstance(child, (str, int)):
                        text = str(child).strip()
                        if text:
                            known.add(text)
                    else:
                        walk(child)
            elif isinstance(value, list):
                for child in value:
                    walk(child)

        walk(read_json(project_dir / "planning" / "role_lock.json", {}))
        walk(read_json(project_dir / "source" / "source_manifest.json", {}))
        return sorted(known)

    def _selected_source_person(self, project_dir: Path, config: Dict[str, Any]) -> Tuple[Optional[str], List[str]]:
        selected = config.get("source_person_id")
        selected = str(selected).strip() if selected not in (None, "") else None
        people = self._project_source_people(project_dir)
        known = self._known_source_person_ids(project_dir)
        if selected and known and selected not in known:
            raise ApiError(
                400,
                "INVALID_SOURCE_PERSON_ID",
                "source_person_id does not match a person in role_lock/source_manifest",
                {"selected": selected, "allowed": known},
            )
        if selected:
            for person in people:
                aliases = {str(person.get("id"))} | {str(value) for value in person.get("aliases") or []}
                if selected in aliases:
                    return str(person["id"]), known
            return selected, known
        if len(people) == 1:
            return str(people[0]["id"]), known
        if not people and len(known) == 1:
            return known[0], known
        return None, known

    def _pending_inputs(self, state: Dict[str, Any], project_dir: Optional[Path] = None) -> Tuple[List[str], List[str]]:
        config = state.get("config") or {}
        pending: List[str] = []
        if not state.get("video"):
            pending.append("source_video")
        if config.get("product_mode") == "replace":
            product_id = config.get("product_id")
            product_record = self.find_knowledge("products", str(product_id), required=False) if product_id else None
            if not product_record or not (product_record.get("media_urls") or []):
                pending.append("target_product_reference")
            if product_record and not product_record.get("dimensions_cm"):
                pending.append("product_dimensions_cm")
            product_roles = {
                str(item.get("role") or "").lower()
                for item in ((product_record or {}).get("references") or [])
                if isinstance(item, dict)
            }
            references = [value for value in ((product_record or {}).get("references") or []) if isinstance(value, dict)]
            packaging_contracts = (product_record or {}).get("packaging_contracts")
            layered_packaging = isinstance(packaging_contracts, dict) or any(
                self._reference_declares_layered_packaging(reference) for reference in references
            )
            if layered_packaging:
                packaging_contracts = packaging_contracts if isinstance(packaging_contracts, dict) else {}
                asset_ids_by_layer = self._packaging_asset_ids(references)
                for layer in PACKAGING_LAYERS:
                    layer_contract = packaging_contracts.get(layer)
                    layer_assets = asset_ids_by_layer.get(layer) or []
                    if layer_assets and not isinstance(layer_contract, dict):
                        pending.append("packaging_%s_contract" % layer)
                    if isinstance(layer_contract, dict) and layer_contract.get("present", True) is True and not layer_assets:
                        pending.append("packaging_%s_reference" % layer)
                    if isinstance(layer_contract, dict) and layer_contract.get("present") is False and layer_assets:
                        pending.append("packaging_%s_absent_but_referenced" % layer)
            else:
                package_expected = bool((product_record or {}).get("requires_package_spec")) or any(
                    any(token in role for token in ("package", "packaging", "box", "pouch", "包装", "外盒", "袋"))
                    for role in product_roles
                )
                package_spec = (product_record or {}).get("package_spec")
                if product_record and package_expected and not package_spec:
                    pending.append("product_package_spec")
                if isinstance(package_spec, dict) and package_spec.get("present") is not False:
                    if self._first_contract_value(package_spec, (("quantity",), ("unit_count",), ("units_per_package",), ("数量",))) is None:
                        pending.append("package_quantity")
                    if self._first_contract_value(package_spec, (("box_topology",), ("topology",), ("package_topology",), ("structure",), ("盒体拓扑",))) is None:
                        pending.append("box_topology")
                    if self._first_contract_value(package_spec, (("text_layout",), ("artwork_layout",), ("label_layout",), ("文字版面",))) is None:
                        pending.append("text_layout")
                    if not any(role in {"package_front", "packaging_front", "box_front", "retail_box_front", "包装正面", "外盒正面"} for role in product_roles):
                        pending.append("package_front_reference")
            product_binding = state.get("product_binding") or {}
            if product_binding.get("status") not in {"ready", "bound_missing_approved_reference"} or product_binding.get("applied_id") != product_id:
                pending.append("apply_selected_product_binding")
        if config.get("character_mode") in {"head_replace", "full_replace"} and not config.get("avatar_id"):
            pending.append("avatar_reference")
        if config.get("character_mode") in {"head_replace", "full_replace"} and project_dir is not None:
            try:
                selected_source_person, known_source_people = self._selected_source_person(project_dir, config)
            except ApiError:
                selected_source_person, known_source_people = None, self._known_source_person_ids(project_dir)
                pending.append("source_person_id_invalid")
            if not selected_source_person:
                pending.append("source_person_id")
        avatar_id = config.get("avatar_id")
        if avatar_id:
            avatar = self.find_knowledge("avatars", str(avatar_id), required=False)
            if not avatar or avatar.get("authorized") is not True:
                pending.append("portrait_authorization")
            if avatar and avatar.get("source") == "custom":
                usage_scope = avatar.get("usage_scope")
                scope_allowed = (
                    usage_scope == "head_and_full"
                    or (usage_scope == "head_only" and config.get("character_mode") == "head_replace")
                    or (usage_scope == "full_only" and config.get("character_mode") == "full_replace")
                )
                if not scope_allowed:
                    pending.append("avatar_usage_scope_mismatch")
            avatar_roles = {
                str(item.get("role") or "").lower().replace("-", "_")
                for item in ((avatar or {}).get("references") or [])
                if isinstance(item, dict)
            }
            if config.get("character_mode") == "full_replace" and not any(
                role in {"full_body", "fullbody", "body", "turnaround", "全身", "三视图"} for role in avatar_roles
            ):
                pending.append("avatar_full_body_reference")
            if config.get("character_mode") in {"head_replace", "full_replace"} and not any(
                role in {"front", "frontal", "front_face", "face_front", "head_front", "portrait_front", "正脸"}
                for role in avatar_roles
            ):
                pending.append("avatar_head_reference")
            avatar_binding = state.get("avatar_binding") or {}
            if avatar_binding.get("status") != "ready" or avatar_binding.get("applied_id") != avatar_id:
                pending.append("apply_selected_avatar_binding")
            elif avatar and avatar.get("source") == "custom" and project_dir is not None:
                avatar_validation = self.validate_custom_avatar_binding(project_dir.name)
                if avatar_validation.get("status") != "ready":
                    pending.append("apply_selected_avatar_binding")
        if state.get("prompt_length_contract_issue"):
            pending.append("prompt_length_contract_invalid")
        tier = config.get("execution_tier")
        script_locked = self._project_script(project_dir).get("locked") is True if project_dir is not None else config.get("script_locked") is True
        if tier in {"prompt_only", "full_delivery"} and not script_locked:
            pending.append("locked_revised_script")
        blocking = ["source_video"] if "source_video" in pending else []
        if tier != "source_intake":
            blocking.extend(item for item in pending if item not in blocking)
        return pending, blocking

    def _serialize_project(self, project_dir: Path) -> Dict[str, Any]:
        state = self._load_state(project_dir)
        raw = read_json(project_dir / "project.json", {})
        pending, blocking = self._pending_inputs(state, project_dir)
        initialization = state.get("initialization") or {}
        active_tasks = self.tasks.list_tasks(project_id=project_dir.name, active_only=True)
        if active_tasks:
            status = "running" if any(item["status"] == "running" for item in active_tasks) else active_tasks[0]["status"]
        elif initialization.get("status") == "blocked":
            status = "blocked"
        elif blocking or pending:
            status = "waiting"
        else:
            status = "ready"
        avatar_binding_plan = read_json(project_dir / "planning" / "avatar_binding.json", {})
        current_source_person_id = (
            (state.get("config") or {}).get("source_person_id")
            or (state.get("avatar_binding") or {}).get("source_person_id")
            or (avatar_binding_plan.get("source_person_id") if isinstance(avatar_binding_plan, dict) else None)
        )
        return {
            "id": project_dir.name,
            "name": state.get("name") or raw.get("project_name") or project_dir.name,
            "status": status,
            "created_at": state.get("created_at"),
            "updated_at": state.get("updated_at"),
            "config": state.get("config") or _default_config(),
            "video": self._video_public(project_dir.name, state.get("video")),
            "pending_inputs": pending,
            "blocking_inputs": blocking,
            "initialization": initialization,
            "product_binding_status": state.get("product_binding_status", "unknown"),
            "product_binding": state.get("product_binding") or {"status": state.get("product_binding_status", "unknown")},
            "avatar_binding": state.get("avatar_binding") or {"status": "unknown"},
            "source_people": self._project_source_people(project_dir),
            "source_person_id": current_source_person_id,
            "binding_actions": ["apply"] if any(item in pending for item in ("apply_selected_product_binding", "apply_selected_avatar_binding")) else [],
            "skill_project": {
                "execution_tier": raw.get("execution_tier"),
                "product_mode": raw.get("product_mode"),
                "product_profile": raw.get("product_profile"),
                "prompt_length_contract": raw.get("prompt_length_contract"),
                "prompt_length_contract_issue": state.get("prompt_length_contract_issue"),
                "skill_release_lock": raw.get("skill_release_lock"),
            },
            "active_task_ids": [task["id"] for task in active_tasks],
        }

    def _project_script(self, project_dir: Path) -> Dict[str, Any]:
        workbench_path = project_dir / "planning" / "workbench_script.json"
        if workbench_path.is_file():
            return read_json(workbench_path)
        canonical_lock = read_json(project_dir / "planning" / "revised_script_lock.json", {})
        canonical_text = str(canonical_lock.get("text") or "") if isinstance(canonical_lock, dict) else ""
        canonical_locked = isinstance(canonical_lock, dict) and str(canonical_lock.get("status") or "").lower() == "locked"
        return {
                "schema_version": "workbench-script-v1",
                "source_text": "",
                "revised_text": canonical_text,
                "active_source": "revised" if canonical_text else "source",
                "locked": canonical_locked,
                "language": str((canonical_lock or {}).get("language") or "zh-CN"),
                "shot_mapping": {},
                "effective_characters": len(re.sub(r"[\s\W_]+", "", canonical_text, flags=re.UNICODE)),
                "confirmed_sha256": hashlib.sha256(canonical_text.encode("utf-8")).hexdigest() if canonical_locked and canonical_text else None,
                "confirmed_at": (canonical_lock or {}).get("locked_at") if canonical_locked else None,
                "confirmed_active_source": "revised" if canonical_locked and canonical_text else None,
                "updated_at": (canonical_lock or {}).get("updated_at"),
            }

    def _project_markers(self, project_dir: Path) -> List[Dict[str, Any]]:
        value = read_json(
            project_dir / "planning" / "manual_markers.json",
            {"schema_version": "workbench-manual-markers-v1", "markers": []},
        )
        return value.get("markers") or [] if isinstance(value, dict) else []

    def _registered_asset_hashes(self, project_dir: Path, shot_manifest: Optional[Dict[str, Any]] = None) -> Dict[str, str]:
        registered: Dict[str, str] = {}

        def walk(value: Any) -> None:
            if isinstance(value, dict):
                digest = value.get("sha256") or value.get("asset_sha256")
                if isinstance(digest, str) and re.fullmatch(r"[a-fA-F0-9]{64}", digest):
                    for key in ("path", "image_path", "asset_path", "source_path"):
                        if isinstance(value.get(key), str) and value.get(key):
                            registered[str(value[key])] = digest.lower()
                for child in value.values():
                    walk(child)
            elif isinstance(value, list):
                for child in value:
                    walk(child)

        walk(read_json(project_dir / "planning" / "asset_reuse_plan.json", {}))
        walk(shot_manifest if shot_manifest is not None else read_json(project_dir / "shots" / "shot_manifest.json", {}))
        review_root = project_dir / "review"
        if review_root.is_dir():
            for index, path in enumerate(sorted(review_root.glob("*generation*pack*.json"))):
                if index >= 20:
                    break
                walk(read_json(path, {}))
        return registered

    def _relocated_registered_project_file(
        self,
        project_dir: Path,
        raw_path: str,
        expected_sha256: Optional[str],
    ) -> Optional[Path]:
        if not expected_sha256 or not re.fullmatch(r"[a-fA-F0-9]{64}", expected_sha256):
            return None
        original = Path(raw_path)
        parts = original.parts
        suffix: Optional[Path] = None
        for index in range(len(parts) - 1):
            if parts[index] == "work" and parts[index + 1] == "branches":
                suffix = Path(*parts[index:])
                break
        candidates: List[Path] = []
        if suffix is not None:
            try:
                direct = safe_join(project_dir, suffix.as_posix())
            except ApiError:
                direct = None
            if direct is not None and direct.is_file():
                candidates.append(direct)
        if not candidates:
            bounded_root = project_dir / "work" / "branches" / "image"
            if bounded_root.is_dir():
                for candidate in bounded_root.rglob(original.name):
                    if candidate.is_file() and sha256_file(candidate).lower() == expected_sha256.lower():
                        candidates.append(candidate)
                        if len(candidates) > 1:
                            break
        matching = [candidate for candidate in candidates if sha256_file(candidate).lower() == expected_sha256.lower()]
        return matching[0] if len(matching) == 1 else None

    def _safe_project_or_skill_media_url(
        self,
        project_dir: Path,
        raw_path: Any,
        registered_hashes: Optional[Dict[str, str]] = None,
    ) -> Tuple[Optional[str], str]:
        """Resolve canonical media references without exposing arbitrary files."""
        if not isinstance(raw_path, str) or not raw_path.strip():
            return None, "missing"
        source = Path(raw_path.strip())
        project_root = project_dir.resolve(strict=False)
        skill_assets_root = (self.toolchain.skill_dir / "assets").resolve(strict=False)

        if source.is_absolute():
            candidate = source.resolve(strict=False)
        else:
            try:
                candidate = safe_join(project_dir, source.as_posix())
            except ApiError:
                return None, "external_unresolved"

        if candidate == project_root or project_root in candidate.parents:
            if candidate.is_file():
                relative = candidate.relative_to(project_root).as_posix()
                return "/api/v1/projects/%s/media/%s" % (project_dir.name, quoted_path(relative)), "ready"
            if source.is_absolute():
                return None, "missing"
        if candidate == skill_assets_root or skill_assets_root in candidate.parents:
            if not candidate.is_file():
                return None, "missing"
            relative = candidate.relative_to(self.toolchain.skill_dir.resolve(strict=False)).as_posix()
            return "/api/v1/skill-media/%s" % quoted_path(relative), "ready"

        # A relative canonical reference may intentionally point at `assets/...`
        # in the installed Skill rather than the project.
        if not source.is_absolute():
            try:
                skill_candidate = safe_join(self.toolchain.skill_dir, source.as_posix())
            except ApiError:
                return None, "external_unresolved"
            if skill_candidate == skill_assets_root or skill_assets_root in skill_candidate.parents:
                if not skill_candidate.is_file():
                    return None, "missing"
                relative = skill_candidate.relative_to(self.toolchain.skill_dir.resolve(strict=False)).as_posix()
                return "/api/v1/skill-media/%s" % quoted_path(relative), "ready"
        if source.is_absolute():
            expected = (registered_hashes or {}).get(raw_path.strip())
            relocated = self._relocated_registered_project_file(project_dir, raw_path.strip(), expected)
            if relocated is not None:
                relative = relocated.relative_to(project_root).as_posix()
                return "/api/v1/projects/%s/media/%s" % (project_dir.name, quoted_path(relative)), "relocated_verified"
            return None, "external_unresolved"
        return None, "missing"

    def _project_shot_data(self, project_dir: Path) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
        value = read_json(project_dir / "shots" / "shot_manifest.json", {"shots": []})
        registered_hashes = self._registered_asset_hashes(project_dir, value if isinstance(value, dict) else None)
        result_manifest = read_json(project_dir / "shots" / "results" / "result_manifest.json", {"records": []})
        results_by_unit: Dict[str, List[Dict[str, Any]]] = {}
        for result_record in result_manifest.get("records") or [] if isinstance(result_manifest, dict) else []:
            if not isinstance(result_record, dict) or not result_record.get("unit_id"):
                continue
            public_result = dict(result_record)
            if public_result.get("path"):
                public_result["media_url"] = "/api/v1/projects/%s/media/%s" % (
                    project_dir.name,
                    quoted_path(str(public_result["path"])),
                )
            results_by_unit.setdefault(str(result_record["unit_id"]), []).append(public_result)
        for values in results_by_unit.values():
            values.sort(key=lambda item: str(item.get("created_at") or ""), reverse=True)
        lint_report = read_json(project_dir / "review" / "lint_report.json", {})
        lint_counts = lint_report.get("counts") or {} if isinstance(lint_report, dict) else {}
        lint_ran = bool(lint_report)
        overall_lint_blocked = int(lint_counts.get("ERROR") or 0) > 0 or int(lint_counts.get("BLOCK") or 0) > 0
        groups = value.get("shots") if isinstance(value, dict) else []
        if not isinstance(groups, list):
            groups = []
        flattened: List[Dict[str, Any]] = []
        inherited_fields = ("title", "visual_type", "character", "emotion", "action_beats", "product_state", "asset_links")
        for group_index, group in enumerate(groups):
            if not isinstance(group, dict):
                continue
            parent_id = str(group.get("id") or "")
            prompt_path = project_dir / "prompts" / (parent_id + ".md") if parent_id else None
            prompt_text = None
            prompt_relative = None
            prompt_hash = None
            if prompt_path is not None and prompt_path.is_file():
                markdown = prompt_path.read_text(encoding="utf-8", errors="replace")
                match = re.search(r"```text\s*\n(.*?)\n```", markdown, flags=re.DOTALL)
                prompt_text = match.group(1).strip() if match else None
                prompt_relative = prompt_path.relative_to(project_dir).as_posix()
                prompt_hash = sha256_file(prompt_path)
            group_issue_prefix = "shots/shot_manifest.json.shots[%d]" % group_index
            group_issues = [
                issue
                for issue in (lint_report.get("issues") or [] if isinstance(lint_report, dict) else [])
                if isinstance(issue, dict) and str(issue.get("path") or "").startswith(group_issue_prefix)
            ]
            lint_status = "not_run" if not lint_ran else ("blocked" if overall_lint_blocked else "passed")
            for collection, unit_type, identifier_key in (
                ("source_units", "source", "source_shot_id"),
                ("inserted_units", "inserted", "inserted_shot_id"),
            ):
                units = group.get(collection) or []
                if not isinstance(units, list):
                    continue
                for unit in units:
                    if not isinstance(unit, dict):
                        continue
                    flat = dict(unit)
                    identifier = unit.get(identifier_key) or unit.get("id")
                    if not identifier:
                        # Missing canonical ids stay visibly blocked instead of being silently invented.
                        continue
                    flat["id"] = str(identifier)
                    flat["unit_type"] = unit_type
                    flat["parent_shot_id"] = group.get("id")
                    for field in inherited_fields:
                        if unit.get("semantic_reset_after_split") is True and field in {
                            "title",
                            "visual_type",
                            "emotion",
                            "action_beats",
                            "product_state",
                            "asset_links",
                        }:
                            continue
                        if flat.get(field) is None:
                            flat[field] = group.get(field)
                    if not unit.get("title"):
                        flat["title"] = "%s · %s" % (identifier, group.get("title") or group.get("id") or "")
                    source_timecode = unit.get("source_timecode") or {}
                    generation_timecode = unit.get("generation_timecode") or {}
                    flat["timecode"] = source_timecode or generation_timecode or group.get("timecode")
                    flat["generation_timecode"] = unit.get("generation_timecode")
                    group_start = float((group.get("timecode") or {}).get("start") or 0.0)
                    if unit_type == "source" and source_timecode:
                        # SRC units are anchored to the source video's absolute
                        # child timecode. A parent S timecode must never expand
                        # both split children back to the full parent duration.
                        timeline_timecode = dict(source_timecode)
                        try:
                            timeline_timecode.setdefault(
                                "duration",
                                float(source_timecode.get("end")) - float(source_timecode.get("start")),
                            )
                        except (TypeError, ValueError):
                            pass
                    elif generation_timecode:
                        timeline_timecode = {
                            "start": round(group_start + float(generation_timecode.get("start") or 0.0), 6),
                            "end": round(group_start + float(generation_timecode.get("end") or 0.0), 6),
                            "duration": float(generation_timecode.get("duration") or (float(generation_timecode.get("end") or 0.0) - float(generation_timecode.get("start") or 0.0))),
                        }
                    else:
                        timeline_timecode = group.get("timecode") or {}
                    flat["timeline_timecode"] = timeline_timecode
                    flat["start"] = timeline_timecode.get("start")
                    flat["end"] = timeline_timecode.get("end")
                    flat["delivery_asset_ids"] = list(unit.get("delivery_asset_ids") or [])
                    flat["delivery_asset_roles"] = dict(unit.get("delivery_asset_roles") or {})
                    if unit.get("semantic_reset_after_split") is True:
                        flat["narrative_role"] = unit.get("narrative_role")
                        flat["script_segment_ids"] = unit.get("script_segment_ids") or []
                    else:
                        flat["narrative_role"] = unit.get("narrative_role") or group.get("narrative_role")
                        flat["script_segment_ids"] = unit.get("script_segment_ids") or group.get("script_segment_ids") or []
                    flat["semantic_tags"] = self._shot_semantic_tags(group, flat)
                    flat["prompt"] = prompt_text
                    flat["prompt_path"] = prompt_relative
                    flat["prompt_hash"] = prompt_hash
                    if unit.get("requires_regeneration") is True:
                        # A split child must never inherit the old parent Prompt
                        # merely because the parent's markdown still exists for audit.
                        flat["prompt"] = None
                        flat["prompt_path"] = None
                        flat["prompt_hash"] = None
                        flat["prompt_status"] = "stale_requires_regeneration"
                    flat["lint_status"] = lint_status
                    flat["lint_issues"] = group_issues
                    flat["results"] = results_by_unit.get(str(identifier), [])
                    flat["latest_result"] = flat["results"][0] if flat["results"] else None
                    for frame_field in ("source_first_frame", "approved_generation_first_frame"):
                        frame_value = flat.get(frame_field)
                        if frame_value is None:
                            frame_value = group.get(frame_field)
                        if frame_value is None:
                            frame_value = (flat.get("asset_links") or {}).get(frame_field)
                        if frame_value is None:
                            frame_value = (group.get("asset_links") or {}).get(frame_field)
                        frame_url, frame_status = self._safe_project_or_skill_media_url(project_dir, frame_value, registered_hashes)
                        flat[frame_field + "_url"] = frame_url
                        flat[frame_field + "_status"] = frame_status
                    flattened.append(flat)
        # Old drafts may have one UI-level shot per record and no nested units.
        if not flattened:
            for group in groups:
                if isinstance(group, dict):
                    flat = dict(group)
                    flat.setdefault("parent_shot_id", group.get("id"))
                    flat.setdefault("delivery_asset_ids", [])
                    flat["semantic_tags"] = self._shot_semantic_tags(group, flat)
                    flat["results"] = results_by_unit.get(str(group.get("id") or ""), [])
                    flat["latest_result"] = flat["results"][0] if flat["results"] else None
                    flattened.append(flat)
        return flattened, groups

    @staticmethod
    def _shot_semantic_tags(group: Dict[str, Any], unit: Dict[str, Any]) -> List[str]:
        """Compile stable UI/detector tags from canonical visible-action facts."""
        split_reset = unit.get("semantic_reset_after_split") is True
        existing: List[str] = []
        semantic_sources = (unit.get("semantic_tags"),) if split_reset else (group.get("semantic_tags"), unit.get("semantic_tags"))
        for raw in semantic_sources:
            if isinstance(raw, list):
                existing.extend(str(value).strip().lower() for value in raw if str(value).strip())
        evidence_fields = {
            # Split labels are user-facing names, not fresh visual evidence.
            "title": None if split_reset else (unit.get("title") or group.get("title")),
            "visual_type": None if split_reset else (unit.get("visual_type") or group.get("visual_type")),
            "action_beats": None if split_reset else (unit.get("action_beats") or group.get("action_beats")),
            "product_state": unit.get("product_state") if split_reset else (unit.get("product_state") or group.get("product_state")),
            "narrative_role": unit.get("narrative_role") if split_reset else (unit.get("narrative_role") or group.get("narrative_role")),
            "character": unit.get("character") or group.get("character"),
            "emotion": unit.get("emotion") if split_reset else (unit.get("emotion") or group.get("emotion")),
            "asset_links": unit.get("asset_links") if split_reset else (unit.get("asset_links") or group.get("asset_links")),
        }
        # Search values only. Field names such as `character` and
        # `product_state` would otherwise make every unit a false positive.
        text = json.dumps(list(evidence_fields.values()), ensure_ascii=False, sort_keys=True).lower()
        eating_terms = ("吃", "咬", "咀嚼", "入口", "含住", "吞咽", "bite", "chew", "eating", "taste")
        breaking_terms = ("掰", "掰开", "掰断", "脆断", "折断", "断裂", "撕开", "裂开", "break", "breaking", "snap", "fracture", "split")
        person_terms = ("人物", "人像", "主播", "脸", "嘴", "头部", "身体", "全身", "character", "person", "portrait", "face", "head", "body", "full_body")
        product_terms = ("产品", "包装", "盒", "食物", "横截面", "product", "package", "showcase", "food")
        tags = set(existing)
        if any(term in text for term in eating_terms):
            tags.add("eating")
        if any(term in text for term in breaking_terms):
            tags.add("breaking")
        character = unit.get("character") if unit.get("character") is not None else group.get("character")
        hands_only = False
        explicit_person = False
        if isinstance(character, dict):
            hands_only = character.get("hands_only") is True or str(character.get("visibility") or "").lower() in {
                "hands_only",
                "hands",
            }
            if character.get("present") is not False:
                explicit_person = character.get("present") is True or any(
                    character.get(key)
                    for key in ("id", "owner", "person_id", "source_person_id", "avatar_id", "face", "head", "body", "full_body")
                )
                visibility = str(character.get("visibility") or "").lower()
                explicit_person = explicit_person or visibility in {"face", "head", "upper_body", "body", "full_body", "full"}
        elif isinstance(character, str):
            explicit_person = bool(character.strip())
        if hands_only:
            tags.add("hands_only")
        inherited_emotion = unit.get("emotion") if split_reset else (unit.get("emotion") or group.get("emotion"))
        if explicit_person or ((not hands_only) and (inherited_emotion or any(term in text for term in person_terms))):
            tags.add("person")
        if (
            unit.get("product_state") is not None
            or ((not split_reset) and group.get("product_state") is not None)
            or any(tag in tags for tag in ("eating", "breaking"))
            or any(term in text for term in product_terms)
        ):
            tags.add("product")
        return [tag for tag in ("eating", "breaking", "person", "product") if tag in tags] + sorted(
            tag for tag in tags if tag not in {"eating", "breaking", "person", "product"}
        )

    def _active_workbench_revocations(self, project_dir: Path) -> List[Dict[str, Any]]:
        """Return unresolved user revocations without trusting a derived asset status."""
        root = project_dir / "review" / "workbench-revocations"
        if not root.is_dir():
            return []
        active_statuses = {"active", "invalidated", "blocked"}
        records: List[Dict[str, Any]] = []
        for path in sorted(root.glob("*.json"))[-1000:]:
            value = read_json(path, {})
            if not isinstance(value, dict):
                continue
            status = str(value.get("status") or "active").lower()
            if status not in active_statuses:
                continue
            record = dict(value)
            record["path"] = path.relative_to(project_dir).as_posix()
            records.append(record)
        records.sort(key=lambda value: (str(value.get("created_at") or ""), str(value.get("path") or "")))
        return records

    def _project_assets(self, project_dir: Path, flat_shots: Optional[List[Dict[str, Any]]] = None) -> List[Dict[str, Any]]:
        extensions = {
            ".jpg", ".jpeg", ".png", ".webp", ".gif", ".mp4", ".mov", ".m4v", ".webm", ".wav", ".mp3", ".m4a", ".docx", ".pdf"
        }
        reuse_plan = read_json(project_dir / "planning" / "asset_reuse_plan.json", {})
        current_generation_input_sha256 = self._generation_input_snapshot(project_dir)["contract_sha256"]
        dependency_state = read_json(project_dir / "workbench" / "dependency_state.json", {})
        has_material_invalidation = isinstance(dependency_state, dict) and int(dependency_state.get("revision") or 0) > 0
        inventory_by_path: Dict[str, Dict[str, Any]] = {}
        for entry in reuse_plan.get("inventory") or [] if isinstance(reuse_plan, dict) else []:
            if not isinstance(entry, dict) or not entry.get("path"):
                continue
            raw_path = Path(str(entry["path"]))
            resolved = raw_path.resolve(strict=False) if raw_path.is_absolute() else (project_dir / raw_path).resolve(strict=False)
            inventory_by_path[str(resolved)] = entry
            relocated = self._relocated_registered_project_file(project_dir, str(entry["path"]), entry.get("sha256"))
            if relocated is not None:
                inventory_by_path[str(relocated.resolve(strict=False))] = entry
        result_manifest = read_json(project_dir / "shots" / "results" / "result_manifest.json", {"records": []})
        for record in result_manifest.get("records") or [] if isinstance(result_manifest, dict) else []:
            if not isinstance(record, dict) or not record.get("path"):
                continue
            try:
                result_path = safe_join(project_dir, str(record["path"]))
            except ApiError:
                continue
            inventory_by_path[str(result_path.resolve(strict=False))] = {
                "asset_id": record.get("asset_id"),
                "path": record.get("path"),
                "sha256": record.get("sha256"),
                "purpose": "%s result for %s" % (record.get("kind"), record.get("unit_id")),
                "approval_status": "unreviewed",
                "owner_unit_ids": record.get("owner_unit_ids") or [record.get("unit_id")],
                "result_kind": record.get("kind"),
                "version": record.get("version"),
                "notes": record.get("notes"),
                "unit_id": record.get("unit_id"),
                "created_at": record.get("created_at"),
                "generation_input_sha256": record.get("generation_input_sha256"),
            }
        approval_ledger = read_json(project_dir / "workbench" / "approvals.json", {"records": []})
        latest_by_path: Dict[str, Dict[str, Any]] = {}
        latest_by_id: Dict[str, Dict[str, Any]] = {}
        for approval in approval_ledger.get("records") or [] if isinstance(approval_ledger, dict) else []:
            if not isinstance(approval, dict):
                continue
            if approval.get("asset_path"):
                latest_by_path[str(approval["asset_path"])] = approval
            if approval.get("asset_id"):
                latest_by_id[str(approval["asset_id"])] = approval
        revoked_ids: set = set()
        revoked_paths: set = set()
        revocation_documents: List[Dict[str, Any]] = []
        cascade = read_json(project_dir / "review" / "delivery_revocation_cascade.json", {})
        if isinstance(cascade, dict):
            revocation_documents.append(cascade)
        revocation_root = project_dir / "review" / "workbench-revocations"
        if revocation_root.is_dir():
            for revocation_path in revocation_root.glob("*.json"):
                value = read_json(revocation_path, {})
                if isinstance(value, dict):
                    revocation_documents.append(value)
        for revocation in revocation_documents:
            if str(revocation.get("status") or "").lower() in {"cleared", "resolved", "superseded", "inactive"}:
                continue
            revoked_ids.update(str(value) for value in revocation.get("revoked_asset_ids") or [] if value not in (None, ""))
            for revoked in revocation.get("revoked_assets") or []:
                if isinstance(revoked, dict) and revoked.get("path"):
                    revoked_paths.add(str(revoked["path"]))
        owners_by_asset: Dict[str, List[str]] = {}
        for unit in flat_shots or []:
            for asset_id in unit.get("delivery_asset_ids") or []:
                owners_by_asset.setdefault(str(asset_id), []).append(str(unit.get("id")))
        result: List[Dict[str, Any]] = []
        seen_paths: set = set()

        def append_asset(path: Path, inventory: Dict[str, Any]) -> None:
            relative = path.relative_to(project_dir).as_posix()
            if relative in seen_paths:
                return
            seen_paths.add(relative)
            asset_id = str(inventory.get("asset_id") or path.stem)
            latest_approval = latest_by_path.get(relative) or latest_by_id.get(asset_id)
            approval_status = inventory.get("approval_status")
            if latest_approval:
                approval_status = "user_approved" if latest_approval.get("decision") == "approve" else "revoked"
            ledger_decision = latest_approval.get("decision") if latest_approval else None
            canonical_user_approved = str(inventory.get("approval_status") or "").lower() in {
                "user_approved",
                "approved_by_user",
                "user-approved",
            }
            if latest_approval:
                expected_sha256 = latest_approval.get("asset_sha256")
                approval_source = "workbench_ledger"
                receipt_valid = bool(
                    latest_approval.get("id")
                    and latest_approval.get("created_at")
                    and latest_approval.get("asset_path") == relative
                    and ledger_decision in {"approve", "revoke"}
                )
            else:
                expected_sha256 = inventory.get("sha256") if canonical_user_approved else None
                approval_source = "canonical_user_approval" if canonical_user_approved else "none"
                receipt_valid = canonical_user_approved
            approval_input_sha256 = latest_approval.get("input_contract_sha256") if latest_approval else None
            if isinstance(approval_input_sha256, str):
                approval_input_current = approval_input_sha256 == current_generation_input_sha256
            else:
                approval_input_current = not has_material_invalidation
            asset_generation_input_sha256 = inventory.get("generation_input_sha256")
            if isinstance(asset_generation_input_sha256, str):
                asset_input_current = asset_generation_input_sha256 == current_generation_input_sha256
            else:
                # A user may explicitly approve a canonical asset in the current
                # input revision even when its historical inventory lacks this field.
                asset_input_current = approval_input_current if latest_approval else not has_material_invalidation
            dependency_current = approval_input_current and asset_input_current
            sha256_valid = False
            if isinstance(expected_sha256, str) and re.fullmatch(r"[a-fA-F0-9]{64}", expected_sha256):
                sha256_valid = sha256_file(path).lower() == expected_sha256.lower()
            revocation_clear = asset_id not in revoked_ids and relative not in revoked_paths and str(path) not in revoked_paths
            if ledger_decision == "revoke" or not revocation_clear:
                effective_decision = "revoke"
            elif (ledger_decision == "approve" or canonical_user_approved) and sha256_valid and receipt_valid and dependency_current:
                effective_decision = "approve"
            else:
                effective_decision = "pending"
            suffix = path.suffix.lower()
            if suffix in VALID_VIDEO_EXTENSIONS:
                media_kind = "video"
            elif suffix in {".wav", ".mp3", ".m4a"}:
                media_kind = "audio"
            elif suffix in {".docx", ".pdf"}:
                media_kind = "document"
            else:
                media_kind = "image"
            owner_unit_ids = owners_by_asset.get(asset_id) or [str(value) for value in inventory.get("owner_unit_ids") or [] if value]
            if inventory.get("result_kind"):
                asset_class = "shot_result"
            elif media_kind == "document":
                asset_class = "document"
            elif relative.startswith("source/") or inventory.get("asset_type") in {"source_frame", "source_video", "source_audio"}:
                asset_class = "source"
            elif relative.startswith("review/"):
                asset_class = "review"
            elif relative.startswith("exports/") or owner_unit_ids:
                asset_class = "delivery"
            else:
                asset_class = "candidate"
            result.append(
                {
                    "id": asset_id,
                    "asset_id": asset_id,
                    "path": relative,
                    "filename": path.name,
                    "kind": media_kind,
                    "asset_class": asset_class,
                    "size": path.stat().st_size,
                    "media_url": "/api/v1/projects/%s/media/%s" % (project_dir.name, quoted_path(relative)),
                    "approval_status": approval_status or "unreviewed",
                    "effective_approval": {
                        "decision": effective_decision,
                        "source": approval_source,
                        "sha256_valid": sha256_valid,
                        "revocation_clear": revocation_clear,
                        "receipt_valid": receipt_valid,
                        "dependency_current": dependency_current,
                        "approval_input_current": approval_input_current,
                        "asset_input_current": asset_input_current,
                        "current_generation_input_sha256": current_generation_input_sha256,
                    },
                    "latest_approval": latest_approval,
                    "owner_unit_ids": owner_unit_ids,
                    "unit_id": inventory.get("unit_id") or (owner_unit_ids[0] if len(owner_unit_ids) == 1 else None),
                    "result_kind": inventory.get("result_kind"),
                    "version": inventory.get("version"),
                    "notes": inventory.get("notes"),
                    "created_at": inventory.get("created_at"),
                    "sha256": inventory.get("sha256"),
                    "generation_input_sha256": asset_generation_input_sha256,
                    "purpose": inventory.get("purpose"),
                }
            )

        for base_name in ("source", "shots", "review", "exports"):
            base = project_dir / base_name
            if not base.is_dir():
                continue
            for path in base.rglob("*"):
                if not path.is_file() or path.suffix.lower() not in extensions:
                    continue
                inventory = inventory_by_path.get(str(path.resolve(strict=False))) or {}
                append_asset(path, inventory)
        # Historical handoffs under work/branches are not recursively scanned.
        # Only hash-registered, relocation-verified files are surfaced.
        project_root = project_dir.resolve(strict=False)
        for resolved_path, inventory in inventory_by_path.items():
            path = Path(resolved_path)
            if path.is_file() and (path == project_root or project_root in path.parents) and path.suffix.lower() in extensions:
                append_asset(path, inventory)
        result.sort(key=lambda item: item["path"])
        return result[:5000]

    def get_project(self, project_id: str) -> Dict[str, Any]:
        project_dir = self.get_project_dir(project_id)
        project = self._serialize_project(project_dir)
        flat_shots, shot_groups = self._project_shot_data(project_dir)
        workflow = read_json(project_dir / "planning" / "workflow_state.json", {})
        story_plan = read_json(project_dir / "planning" / "story_plan.json", {})
        alignment = read_json(project_dir / "review" / "alignment_manifest.json", {})
        asset_reuse_plan = read_json(project_dir / "planning" / "asset_reuse_plan.json", {})
        active_revocation = read_json(project_dir / "review" / "delivery_revocation_cascade.json", {})
        active_workbench_revocations = self._active_workbench_revocations(project_dir)
        project["script"] = self._project_script(project_dir)
        project["markers"] = self._project_markers(project_dir)
        project["split_plans"] = [
            value
            for path in sorted((project_dir / "planning" / "split-plans").glob("*.json"))[-100:]
            for value in [read_json(path, {})]
            if isinstance(value, dict) and value
        ]
        project["shots"] = flat_shots
        project["shot_groups"] = shot_groups
        project["assets"] = self._project_assets(project_dir, flat_shots)
        project["workflow"] = workflow
        project["story_plan"] = story_plan
        project["alignment"] = alignment
        project["asset_reuse_plan"] = asset_reuse_plan
        project["active_revocation"] = active_revocation
        project["active_workbench_revocations"] = active_workbench_revocations
        project["detection_results"] = self._detection_results_with_freshness(project_dir)
        project["docx_export_authorized"] = workflow.get("docx_export_authorized") is True
        visual_status = str(workflow.get("docx_visual_qa_status") or "").lower()
        render_state = self._docx_render_state(project_dir)
        receipt_state = self._docx_qa_receipt_state(project_dir, workflow, render_state)
        if active_revocation and str(active_revocation.get("status") or "").lower() not in {"", "cleared", "resolved", "superseded", "inactive"}:
            docx_qa = {"status": "blocked", "code": "ACTIVE_DELIVERY_REVOCATION", "message": "存在未清除的交付撤销，旧 Word 不可交付。"}
        elif active_workbench_revocations:
            docx_qa = {"status": "blocked", "code": "ACTIVE_DELIVERY_REVOCATION", "message": "存在尚未由新版本替代的已撤销素材，旧 Word 不可交付。"}
        elif workflow.get("docx_export_authorized") is not True:
            docx_qa = {"status": "blocked", "code": "DOCX_EXPORT_NOT_AUTHORIZED", "message": "canonical workflow 尚未明确授权 Word 导出。"}
        elif visual_status in {"passed", "approved", "complete", "completed"}:
            if render_state.get("status") != "ready":
                docx_qa = {
                    "status": "blocked",
                    "code": "DOCX_QA_RENDER_STALE",
                    "message": "Word 或逐页渲染已变化，旧视觉 QA 不再有效。",
                }
            elif receipt_state.get("status") != "ready":
                docx_qa = {
                    "status": "blocked",
                    "code": str(receipt_state.get("code") or "DOCX_QA_RECEIPT_STALE"),
                    "message": "当前 Word 与逐页视觉 QA 回执不再完全一致，必须重新审核。",
                }
            else:
                docx_qa = {"status": "passed", "code": None, "message": "当前 Word、全部渲染页与视觉 QA 回执哈希完全一致。"}
        else:
            docx_qa = {"status": "waiting", "code": "DOCX_VISUAL_QA_REQUIRED", "message": "Word 是否存在不等于交付通过；仍需逐页渲染视觉 QA。"}
        docx_qa.update(
            {
                "page_count": workflow.get("final_render_page_count"),
                "alignment_status": workflow.get("final_alignment_status"),
                "authorized_by_explicit_workflow_boolean": workflow.get("docx_export_authorized") is True,
            }
        )
        docx_qa["render_status"] = render_state.get("status")
        docx_qa["render_code"] = render_state.get("code")
        docx_qa["document"] = render_state.get("document")
        docx_qa["render_pages"] = render_state.get("pages") or []
        docx_qa["receipt_path"] = workflow.get("docx_visual_qa_receipt_path")
        docx_qa["receipt_status"] = receipt_state.get("status")
        docx_qa["receipt_code"] = receipt_state.get("code")
        project["docx_qa"] = docx_qa
        project["generation_status"] = self.generation_artifact_status(project_dir.name)
        return project

    def list_projects(self) -> List[Dict[str, Any]]:
        result = []
        for directory in self.projects_root.iterdir():
            if directory.is_dir() and (directory / "project.json").is_file():
                try:
                    result.append(self._serialize_project(directory))
                except ApiError:
                    continue
        result.sort(key=lambda item: item.get("updated_at") or "", reverse=True)
        return result

    def _revision_token(self, project_dir: Path, relatives: Iterable[str]) -> str:
        facts: List[str] = []
        for relative in relatives:
            if any(token in relative for token in ("*", "?", "[")):
                matches = sorted(project_dir.glob(relative))[:200]
                if not matches:
                    facts.append(relative + ":missing")
                for path in matches:
                    try:
                        stat = path.stat()
                        facts.append("%s:%d:%d:%d" % (path.relative_to(project_dir).as_posix(), stat.st_size, stat.st_mtime_ns, stat.st_ino))
                    except OSError:
                        continue
            else:
                path = safe_join(project_dir, relative)
                try:
                    stat = path.stat()
                    facts.append("%s:%d:%d:%d" % (relative, stat.st_size, stat.st_mtime_ns, stat.st_ino))
                except OSError:
                    facts.append(relative + ":missing")
        return hashlib.sha256("\n".join(facts).encode("utf-8")).hexdigest()[:20]

    def project_status(self, project_id: str) -> Dict[str, Any]:
        """Return polling state without enumerating project media."""
        project_dir = self.get_project_dir(project_id)
        summary = self._serialize_project(project_dir)
        revisions = {
            "project": self._revision_token(project_dir, ["project.json", "workbench/state.json"]),
            "shots": self._revision_token(
                project_dir,
                ["shots/shot_manifest.json", "prompts", "prompts/*.md", "prompts/prompt_manifest.json"],
            ),
            "assets": self._revision_token(
                project_dir,
                [
                    "planning/asset_reuse_plan.json",
                    "shots/results/result_manifest.json",
                    "review/*handoff*.json",
                    "review/*gallery*.json",
                    "review/*manifest*.json",
                    "work/branches/*/*handoff*.json",
                    "work/branches/image",
                    "source",
                    "shots",
                    "review",
                    "exports",
                ],
            ),
            "workflow": self._revision_token(
                project_dir,
                [
                    "planning/workflow_state.json",
                    "planning/split-plans/*.json",
                    "review/alignment_manifest.json",
                    "review/workbench_detection.json",
                ],
            ),
            "script": self._revision_token(
                project_dir,
                ["planning/workbench_script.json", "planning/revised_script_lock.json"],
            ),
            "approvals": self._revision_token(
                project_dir,
                ["workbench/approvals.json", "review/delivery_revocation_cascade.json", "review/workbench-revocations"],
            ),
        }
        tasks = self.tasks.list_tasks(project_id=project_id, active_only=True)
        task_summaries = [
            {
                "id": task.get("id"),
                "operation": task.get("operation"),
                "status": task.get("status"),
                "phase": task.get("phase"),
                "progress": task.get("progress"),
                "message": task.get("message"),
                "lanes": task.get("lanes"),
                "updated_at": task.get("updated_at"),
            }
            for task in tasks
        ]
        return {
            "ok": True,
            "project": {
                "id": summary["id"],
                "name": summary["name"],
                "status": summary["status"],
                "updated_at": summary.get("updated_at"),
                "pending_inputs": summary.get("pending_inputs") or [],
                "blocking_inputs": summary.get("blocking_inputs") or [],
                "revisions": revisions,
                "active_tasks": task_summaries,
            },
            "tasks": task_summaries,
        }

    def _content_set_sha256(self, project_dir: Path, patterns: Iterable[str]) -> Optional[str]:
        records: List[str] = []
        seen: set = set()
        for pattern in patterns:
            for path in sorted(project_dir.glob(pattern))[:1000]:
                if not path.is_file():
                    continue
                relative = path.relative_to(project_dir).as_posix()
                if relative in seen:
                    continue
                seen.add(relative)
                records.append("%s:%s" % (relative, sha256_file(path)))
        if not records:
            return None
        return hashlib.sha256("\n".join(records).encode("utf-8")).hexdigest()

    def _generation_input_snapshot(self, project_dir: Path) -> Dict[str, Any]:
        """Hash only facts that a generated shot must be based on."""
        state = self._load_state(project_dir)
        config = state.get("config") or _default_config()
        raw_project = read_json(project_dir / "project.json", {})
        raw_prompt_length_contract = raw_project.get("prompt_length_contract")
        try:
            prompt_length_contract: Dict[str, Any] = self._normalize_prompt_length_contract(raw_prompt_length_contract)
        except ApiError as exc:
            # Keep invalid legacy bytes represented in the hash so an explicit
            # repair changes the receipt; task preflight separately blocks use.
            prompt_length_contract = {
                "status": "invalid",
                "code": exc.code,
                "raw": raw_prompt_length_contract,
            }
        script = self._project_script(project_dir)
        active_text = script.get("revised_text") if script.get("active_source") == "revised" else script.get("source_text")
        product_contract = project_dir / "library" / "product_immutable_contract.json"
        avatar_contract = project_dir / "planning" / "avatar_binding_lock.json"
        shot_manifest = project_dir / "shots" / "shot_manifest.json"
        markers = project_dir / "planning" / "manual_markers.json"
        role_lock = project_dir / "planning" / "role_lock.json"
        story_plan = project_dir / "planning" / "story_plan.json"
        source_manifest = project_dir / "source" / "source_manifest.json"
        product_bible = project_dir / "library" / "product_bible.json"
        product_library = project_dir / "library" / "product_library.json"
        avatar_library = project_dir / "library" / "avatar_library.json"
        material_config = {
            "product_mode": config.get("product_mode"),
            "product_id": config.get("product_id"),
            "character_mode": config.get("character_mode"),
            "avatar_id": config.get("avatar_id"),
            "source_person_id": config.get("source_person_id"),
            "shot_scope": config.get("shot_scope") or {"mode": "all"},
        }
        product_binding = state.get("product_binding") or {}
        avatar_binding = state.get("avatar_binding") or {}
        value: Dict[str, Any] = {
            "schema_version": "workbench-generation-input-v1",
            "source_video_sha256": (state.get("video") or {}).get("sha256"),
            "material_config": material_config,
            "prompt_length_contract": prompt_length_contract,
            "prompt_length_contract_canonical_field_present": "prompt_length_contract" in raw_project,
            "prompt_length_contract_raw_sha256": hashlib.sha256(
                json.dumps(raw_prompt_length_contract, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")
            ).hexdigest(),
            "active_script_sha256": hashlib.sha256(str(active_text or "").encode("utf-8")).hexdigest(),
            "script_locked": script.get("locked") is True,
            "script_confirmed_sha256": script.get("confirmed_sha256"),
            "shot_manifest_sha256": sha256_file(shot_manifest) if shot_manifest.is_file() else None,
            "manual_markers_sha256": sha256_file(markers) if markers.is_file() else None,
            "role_lock_sha256": sha256_file(role_lock) if role_lock.is_file() else None,
            "story_plan_sha256": sha256_file(story_plan) if story_plan.is_file() else None,
            "source_manifest_sha256": sha256_file(source_manifest) if source_manifest.is_file() else None,
            "product_contract_sha256": sha256_file(product_contract) if product_contract.is_file() else None,
            "avatar_contract_sha256": sha256_file(avatar_contract) if avatar_contract.is_file() else None,
            "product_bible_sha256": sha256_file(product_bible) if product_bible.is_file() else None,
            "product_library_sha256": sha256_file(product_library) if product_library.is_file() else None,
            "avatar_library_sha256": sha256_file(avatar_library) if avatar_library.is_file() else None,
            "product_binding": {
                "status": product_binding.get("status"),
                "selected_id": product_binding.get("selected_id"),
                "applied_id": product_binding.get("applied_id"),
                "immutable_contract_sha256": product_binding.get("immutable_contract_sha256"),
            },
            "avatar_binding": {
                "status": avatar_binding.get("status"),
                "selected_id": avatar_binding.get("selected_id"),
                "applied_id": avatar_binding.get("applied_id"),
                "source_person_id": avatar_binding.get("source_person_id"),
                "immutable_contract_sha256": avatar_binding.get("immutable_contract_sha256"),
            },
        }
        canonical = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)
        value["contract_sha256"] = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
        return value

    def _delivery_input_snapshot(self, project_dir: Path) -> Dict[str, Any]:
        """Hash the current generation facts plus prompts, findings and approvals."""
        generation = self._generation_input_snapshot(project_dir)
        detection_path = project_dir / "review" / "workbench_detection.json"
        approvals_path = project_dir / "workbench" / "approvals.json"
        results_path = project_dir / "shots" / "results" / "result_manifest.json"
        reuse_plan_path = project_dir / "planning" / "asset_reuse_plan.json"
        value: Dict[str, Any] = {
            "schema_version": "workbench-delivery-input-v1",
            "generation_input_sha256": generation["contract_sha256"],
            "prompt_content_sha256": self._content_set_sha256(
                project_dir,
                ("prompts/*.md", "prompts/prompt_manifest.json"),
            ),
            "detection_artifact_sha256": sha256_file(detection_path) if detection_path.is_file() else None,
            "approvals_sha256": sha256_file(approvals_path) if approvals_path.is_file() else None,
            "result_manifest_sha256": sha256_file(results_path) if results_path.is_file() else None,
            "asset_reuse_plan_sha256": sha256_file(reuse_plan_path) if reuse_plan_path.is_file() else None,
        }
        canonical = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)
        value["contract_sha256"] = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
        return value

    def _invalidate_derived_dependencies(
        self,
        project_dir: Path,
        reason: str,
        changed_fields: Iterable[str],
        previous_input: Optional[Dict[str, Any]] = None,
        invalidate_script: bool = False,
        invalidate_shots: bool = False,
    ) -> Dict[str, Any]:
        """Persist one fail-closed invalidation transaction without deleting audit artifacts."""
        timestamp = now_iso()
        state = self._load_state(project_dir)
        if invalidate_script:
            script_path = project_dir / "planning" / "workbench_script.json"
            script = self._project_script(project_dir)
            if isinstance(script, dict):
                script.update(
                    {
                        "locked": False,
                        "stale": True,
                        "stale_reason": reason,
                        "confirmed_sha256": None,
                        "confirmed_at": None,
                        "confirmed_active_source": None,
                        "updated_at": timestamp,
                    }
                )
                atomic_write_json(script_path, script)
            canonical_lock_path = project_dir / "planning" / "revised_script_lock.json"
            canonical_lock = read_json(canonical_lock_path, {})
            if not isinstance(canonical_lock, dict):
                canonical_lock = {}
            canonical_lock.update({"status": "stale", "stale_reason": reason, "updated_at": timestamp})
            atomic_write_json(canonical_lock_path, canonical_lock)
            config = dict(state.get("config") or _default_config())
            config["script_locked"] = False
            state["config"] = self._validated_config(config)
        if invalidate_shots:
            manifest_path = project_dir / "shots" / "shot_manifest.json"
            manifest = read_json(manifest_path, {})
            if not isinstance(manifest, dict):
                manifest = {"shots": []}
            manifest.update(
                {
                    "analysis_status": "stale",
                    "stale_reason": reason,
                    "requires_source_reanalysis": True,
                    "updated_at": timestamp,
                }
            )
            atomic_write_json(manifest_path, manifest)
        prompt_root = project_dir / "prompts"
        prompt_manifest_path = prompt_root / "prompt_manifest.json"
        if prompt_manifest_path.is_file() or any(prompt_root.glob("*.md")):
            prompt_manifest = read_json(prompt_manifest_path, {})
            if not isinstance(prompt_manifest, dict):
                prompt_manifest = {}
            prompt_manifest.update(
                {
                    "status": "stale",
                    "stale_reason": reason,
                    "stale_at": timestamp,
                }
            )
            atomic_write_json(prompt_manifest_path, prompt_manifest)
        detection_path = project_dir / "review" / "workbench_detection.json"
        if detection_path.is_file():
            detection = read_json(detection_path, {})
            if isinstance(detection, dict):
                forced = [str(value) for value in detection.get("forced_stale_reasons") or []]
                if reason not in forced:
                    forced.append(reason)
                detection.update({"forced_stale_reasons": forced, "forced_stale_at": timestamp})
                atomic_write_json(detection_path, detection)
        alignment_path = project_dir / "review" / "alignment_manifest.json"
        alignment = read_json(alignment_path, {})
        if not isinstance(alignment, dict):
            alignment = {}
        alignment.update({"status": "stale", "stale_reason": reason, "updated_at": timestamp})
        atomic_write_json(alignment_path, alignment)
        workflow_path = project_dir / "planning" / "workflow_state.json"
        workflow = read_json(workflow_path, {})
        if not isinstance(workflow, dict):
            workflow = {}
        pending_inputs = [str(value) for value in workflow.get("pending_inputs") or []]
        for value in ("recompile_current_inputs", "regenerate_current_delivery_assets", "rerun_detectors", "document_visual_qa"):
            if value not in pending_inputs:
                pending_inputs.append(value)
        workflow_status = "inputs_changed_pending_regeneration"
        if reason == "ACTIVE_SCRIPT_CHANGED":
            current_script = self._project_script(project_dir)
            workflow_status = "script_changed_pending_recompile" if current_script.get("locked") is True else "script_changed_pending_relock"
            if current_script.get("locked") is not True and "locked_revised_script" not in pending_inputs:
                pending_inputs.append("locked_revised_script")
        workflow.update(
            {
                "status": workflow_status,
                "docx_export_authorized": False,
                "docx_visual_qa_status": "invalidated",
                "docx_visual_qa_blocked_reason": reason,
                "delivery_preflight_status": "stale",
                "delivery_preflight_stale_reason": reason,
                "pending_inputs": pending_inputs,
                "updated_at": timestamp,
            }
        )
        atomic_write_json(workflow_path, workflow)
        state["updated_at"] = timestamp
        atomic_write_json(self._workbench_path(project_dir), state)
        current_input = self._generation_input_snapshot(project_dir)
        dependency_path = project_dir / "workbench" / "dependency_state.json"
        dependency = read_json(dependency_path, {})
        if not isinstance(dependency, dict):
            dependency = {}
        revision = int(dependency.get("revision") or 0) + 1
        receipt = {
            "schema_version": "workbench-input-invalidation-v1",
            "id": new_id("invalidation"),
            "project_id": project_dir.name,
            "revision": revision,
            "reason": reason,
            "changed_fields": list(dict.fromkeys(str(value) for value in changed_fields if str(value))),
            "previous_generation_input": previous_input,
            "current_generation_input": current_input,
            "invalidates": ["prompts", "detections", "alignment", "approvals", "delivery_assets", "docx_authorization", "docx_visual_qa"],
            "created_at": timestamp,
        }
        receipt_path = project_dir / "workbench" / "input-invalidations" / (receipt["id"] + ".json")
        atomic_write_json(receipt_path, receipt)
        dependency.update(
            {
                "schema_version": "workbench-dependency-state-v1",
                "revision": revision,
                "status": "stale_pending_regeneration",
                "active_invalidation_path": receipt_path.relative_to(project_dir).as_posix(),
                "current_generation_input_sha256": current_input["contract_sha256"],
                "updated_at": timestamp,
            }
        )
        atomic_write_json(dependency_path, dependency)
        return receipt

    def mark_delivery_preflight_verified(self, project_id: str) -> Dict[str, Any]:
        """Bind a successful deterministic verify step to the exact current inputs."""
        project_dir = self.get_project_dir(project_id)
        snapshot = self._delivery_input_snapshot(project_dir)
        receipt = {
            "schema_version": "workbench-delivery-preflight-v1",
            "id": new_id("preflight"),
            "project_id": project_id,
            "status": "verified",
            "delivery_input": snapshot,
            "created_at": now_iso(),
        }
        receipt_path = project_dir / "review" / "delivery-preflight-receipts" / (receipt["id"] + ".json")
        atomic_write_json(receipt_path, receipt)
        workflow_path = project_dir / "planning" / "workflow_state.json"
        workflow = read_json(workflow_path, {})
        if not isinstance(workflow, dict):
            workflow = {}
        workflow.update(
            {
                "delivery_preflight_status": "verified",
                "delivery_preflight_receipt_path": receipt_path.relative_to(project_dir).as_posix(),
                "delivery_input_contract_sha256": snapshot["contract_sha256"],
                "delivery_preflight_verified_at": receipt["created_at"],
                "updated_at": now_iso(),
            }
        )
        atomic_write_json(workflow_path, workflow)
        dependency_path = project_dir / "workbench" / "dependency_state.json"
        dependency = read_json(dependency_path, {})
        if isinstance(dependency, dict) and dependency:
            dependency.update(
                {
                    "status": "verified_current_inputs",
                    "verified_delivery_input_sha256": snapshot["contract_sha256"],
                    "verified_at": receipt["created_at"],
                }
            )
            atomic_write_json(dependency_path, dependency)
        return receipt

    def validate_docx_export_preflight(self, project_id: str) -> Dict[str, Any]:
        """Fail closed unless workflow authorization is bound to current bytes."""
        project_dir = self.get_project_dir(project_id)
        workflow = read_json(project_dir / "planning" / "workflow_state.json", {})
        if not isinstance(workflow, dict) or workflow.get("docx_export_authorized") is not True:
            return {"status": "waiting", "code": "DOCX_EXPORT_NOT_AUTHORIZED", "pending_inputs": ["canonical_docx_export_authorization"]}
        relative = str(workflow.get("delivery_preflight_receipt_path") or "")
        if not relative.startswith("review/delivery-preflight-receipts/") or not relative.endswith(".json"):
            return {"status": "blocked", "code": "DELIVERY_PREFLIGHT_RECEIPT_REQUIRED", "pending_inputs": ["run_verify_on_current_inputs"]}
        try:
            receipt_path = safe_join(project_dir, relative)
        except ApiError:
            return {"status": "blocked", "code": "DELIVERY_PREFLIGHT_RECEIPT_INVALID"}
        receipt = read_json(receipt_path, {}) if receipt_path.is_file() else {}
        current = self._delivery_input_snapshot(project_dir)
        saved = receipt.get("delivery_input") if isinstance(receipt, dict) else None
        if (
            not isinstance(saved, dict)
            or receipt.get("project_id") != project_id
            or receipt.get("status") != "verified"
            or saved.get("contract_sha256") != current.get("contract_sha256")
            or workflow.get("delivery_input_contract_sha256") != current.get("contract_sha256")
        ):
            return {
                "status": "blocked",
                "code": "DELIVERY_PREFLIGHT_INPUTS_STALE",
                "pending_inputs": ["run_verify_on_current_inputs"],
                "saved_delivery_input_sha256": (saved or {}).get("contract_sha256") if isinstance(saved, dict) else None,
                "current_delivery_input_sha256": current.get("contract_sha256"),
            }
        script = self._project_script(project_dir)
        if script.get("locked") is not True or not script.get("confirmed_sha256"):
            return {"status": "waiting", "code": "LOCKED_REVISED_SCRIPT_REQUIRED", "pending_inputs": ["locked_revised_script"]}
        analysis_contract = self.validate_analysis_contract(project_id)
        if analysis_contract.get("status") != "ready":
            return {"status": "blocked", "code": "ANALYSIS_CONTRACT_STALE", "analysis_contract": analysis_contract}
        manifest = read_json(project_dir / "shots" / "shot_manifest.json", {})
        if isinstance(manifest, dict) and (manifest.get("analysis_status") == "stale" or manifest.get("requires_source_reanalysis") is True):
            return {"status": "blocked", "code": "SHOT_MANIFEST_STALE", "pending_inputs": ["rerun_source_analysis"]}
        prompt_manifest = read_json(project_dir / "prompts" / "prompt_manifest.json", {})
        if isinstance(prompt_manifest, dict) and prompt_manifest.get("status") == "stale":
            return {"status": "blocked", "code": "PROMPTS_STALE", "pending_inputs": ["recompile_current_inputs"]}
        detection = self._detection_results_with_freshness(project_dir)
        if detection and detection.get("findings_are_effective") is not True:
            return {"status": "blocked", "code": "DETECTION_RESULTS_STALE", "stale_reasons": detection.get("stale_reasons") or []}
        return {"status": "ready", "code": None, "receipt_path": relative, "delivery_input": current}

    # ---- uploads and project configuration -------------------------------------

    def _verified_media_metadata(self, path: Path, media_kind: str, invalid_code: str) -> Dict[str, Any]:
        metadata = self.toolchain.inspect_image(path) if media_kind == "image" else self.toolchain.inspect_video(path)
        if metadata.get("status") == "ready":
            return metadata
        probe_code = str(metadata.get("error_code") or "MEDIA_VALIDATION_FAILED")
        if probe_code in {"FFPROBE_NOT_AVAILABLE", "FFMPEG_NOT_AVAILABLE"}:
            raise ApiError(
                503,
                "MEDIA_VALIDATOR_NOT_AVAILABLE",
                "ffprobe and ffmpeg are required before media can enter a workbench manifest",
                metadata,
            )
        raise ApiError(
            422,
            invalid_code,
            "The uploaded file could not be decoded as the selected media kind",
            metadata,
        )

    def upload_shot_result(
        self,
        project_id: str,
        unit_id: str,
        stream: BinaryIO,
        filename: str,
        fields: Dict[str, Any],
    ) -> Dict[str, Any]:
        project_dir = self.get_project_dir(project_id)
        validate_identifier(unit_id, "unit id")
        flat_shots, _ = self._project_shot_data(project_dir)
        if unit_id not in {str(item.get("id")) for item in flat_shots}:
            raise ApiError(404, "SHOT_UNIT_NOT_FOUND", "Result owner unit was not found")
        kind = str(fields.get("kind") or "")
        if kind not in {"first_frame", "video"}:
            raise ApiError(400, "INVALID_RESULT_KIND", "kind must be first_frame or video")
        clean_name = safe_filename(filename, "result.png" if kind == "first_frame" else "result.mp4")
        suffix = Path(clean_name).suffix.lower()
        allowed = {".jpg", ".jpeg", ".png", ".webp"} if kind == "first_frame" else VALID_VIDEO_EXTENSIONS
        if suffix not in allowed:
            raise ApiError(415, "UNSUPPORTED_RESULT_TYPE", "Uploaded file does not match the selected result kind")
        version = str(fields.get("version") or "v1").strip()
        if not version or len(version) > 100 or any(ord(character) < 32 for character in version):
            raise ApiError(400, "INVALID_RESULT_VERSION", "version must contain 1-100 visible characters")
        notes = str(fields.get("notes") or "").strip()
        if len(notes) > 4000:
            raise ApiError(400, "RESULT_NOTES_TOO_LONG", "notes cannot exceed 4000 characters")
        record_id = new_id("result")
        destination = project_dir / "shots" / "results" / unit_id / kind / (record_id + "-" + clean_name)
        size, digest = copy_stream_atomic(
            stream,
            destination,
            self.maximum_video_bytes if kind == "video" else self.maximum_knowledge_bytes,
        )
        try:
            media_kind = "image" if kind == "first_frame" else "video"
            media_metadata = self._verified_media_metadata(destination, media_kind, "INVALID_SHOT_RESULT_MEDIA")
            warnings: List[Dict[str, Any]] = []
            if media_kind == "image":
                aspect_ratio = float(media_metadata["width"]) / float(media_metadata["height"])
                media_metadata["aspect_ratio"] = round(aspect_ratio, 6)
                if abs(aspect_ratio - (9.0 / 16.0)) > 0.03:
                    warnings.append(
                        {
                            "code": "ASPECT_RATIO_NOT_9_16",
                            "message": "首帧不是约 9:16；已保留供源片横屏等人工判断，不自动拒绝。",
                            "observed": "%sx%s" % (media_metadata["width"], media_metadata["height"]),
                        }
                    )
            asset_id = "RESULT-%s-%s-%s" % (unit_id, "FF" if kind == "first_frame" else "VIDEO", digest[:12].upper())
            relative = destination.relative_to(project_dir).as_posix()
            generation_input = self._generation_input_snapshot(project_dir)
            manifest_path = project_dir / "shots" / "results" / "result_manifest.json"
            with self._lock:
                manifest = read_json(manifest_path, {"schema_version": "workbench-shot-results-v1", "records": []})
                if not isinstance(manifest, dict) or not isinstance(manifest.get("records", []), list):
                    raise ApiError(500, "INVALID_RESULT_MANIFEST", "Stored shot result manifest is invalid")
                for prior in manifest.get("records") or []:
                    if (
                        isinstance(prior, dict)
                        and prior.get("unit_id") == unit_id
                        and prior.get("kind") == kind
                        and prior.get("version") == version
                        and prior.get("sha256") == digest
                    ):
                        destination.unlink(missing_ok=True)
                        public_prior = dict(prior)
                        public_prior["media_url"] = "/api/v1/projects/%s/media/%s" % (project_id, quoted_path(str(prior["path"])))
                        return {"ok": True, "result": public_prior, "duplicate": True, "project": self.get_project(project_id)}
                record = {
                    "id": record_id,
                    "asset_id": asset_id,
                    "project_id": project_id,
                    "unit_id": unit_id,
                    "owner_unit_ids": [unit_id],
                    "kind": kind,
                    "version": version,
                    "notes": notes or None,
                    "original_filename": clean_name,
                    "path": relative,
                    "size": size,
                    "sha256": digest,
                    "generation_input_sha256": generation_input["contract_sha256"],
                    "media_metadata": media_metadata,
                    "warnings": warnings,
                    "approval_status": "unreviewed",
                    "created_at": now_iso(),
                }
                manifest.setdefault("records", []).append(record)
                manifest["updated_at"] = now_iso()
                atomic_write_json(manifest_path, manifest)
            public = dict(record)
            public["media_url"] = "/api/v1/projects/%s/media/%s" % (project_id, quoted_path(relative))
            return {"ok": True, "result": public, "duplicate": False, "project": self.get_project(project_id)}
        except Exception:
            # A result is not an artifact until both media truth checks and the
            # atomic manifest write succeed. Never strand an unregistered file.
            try:
                destination.unlink(missing_ok=True)
            except OSError:
                pass
            raise

    def upload_video(self, project_id: str, stream: BinaryIO, filename: str) -> Dict[str, Any]:
        project_dir = self.get_project_dir(project_id)
        previous_input = self._generation_input_snapshot(project_dir)
        clean_name = safe_filename(filename, "source.mp4")
        suffix = Path(clean_name).suffix.lower()
        if suffix not in VALID_VIDEO_EXTENSIONS:
            raise ApiError(415, "UNSUPPORTED_VIDEO_TYPE", "Supported videos: %s" % ", ".join(sorted(VALID_VIDEO_EXTENSIONS)))
        upload_dir = project_dir / "source" / "uploads"
        target = upload_dir / (new_id("video") + "-" + clean_name)
        size, digest = copy_stream_atomic(stream, target, self.maximum_video_bytes)
        try:
            metadata = self._verified_media_metadata(target, "video", "INVALID_VIDEO")
        except Exception:
            target.unlink(missing_ok=True)
            raise

        thumbnail = project_dir / "source" / "thumbnails" / (digest[:16] + ".jpg")
        thumbnail_result = self.toolchain.make_thumbnail(target, thumbnail, metadata.get("duration"))
        relative_target = target.relative_to(project_dir).as_posix()
        relative_thumbnail = thumbnail.relative_to(project_dir).as_posix() if thumbnail_result.get("status") == "ready" else None
        video = {
            "filename": clean_name,
            "stored_filename": target.name,
            "path": relative_target,
            "size": size,
            "sha256": digest,
            "metadata": metadata,
            "thumbnail_path": relative_thumbnail,
            "thumbnail_status": thumbnail_result,
            "uploaded_at": now_iso(),
            "analysis_status": "not_started",
        }
        with self._lock:
            state = self._load_state(project_dir)
            old_video = state.get("video")
            if old_video:
                video["replaced_video_sha256"] = old_video.get("sha256")
            state["video"] = video
            state["updated_at"] = now_iso()
            atomic_write_json(self._workbench_path(project_dir), state)
        # Existing uploaded source is retained for audit; replacing never deletes user material.
        invalidation = None
        if old_video and old_video.get("sha256") != digest:
            invalidation = self._invalidate_derived_dependencies(
                project_dir,
                "SOURCE_VIDEO_REPLACED",
                ("source_video_sha256",),
                previous_input=previous_input,
                invalidate_script=True,
                invalidate_shots=True,
            )
        public = self._video_public(project_id, video)
        return {"ok": True, "project": self.get_project(project_id), "video": public, "invalidation": invalidation}

    @staticmethod
    def _normalize_prompt_length_contract(raw: Any) -> Dict[str, Any]:
        if raw is None:
            return _disabled_prompt_length_contract()
        if not isinstance(raw, dict):
            raise ApiError(
                400,
                "INVALID_PROMPT_LENGTH_CONTRACT",
                "prompt_length_contract must be an object",
            )
        enabled = raw.get("enabled")
        if not isinstance(enabled, bool):
            raise ApiError(
                400,
                "INVALID_PROMPT_LENGTH_CONTRACT",
                "prompt_length_contract.enabled must be true or false",
            )
        if not enabled:
            return _disabled_prompt_length_contract()
        minimum = raw.get("minimum_non_whitespace_characters")
        maximum = raw.get("maximum_non_whitespace_characters")
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
            raise ApiError(
                400,
                "INVALID_PROMPT_LENGTH_CONTRACT",
                "An enabled Prompt length contract requires positive integer minimum/maximum bounds with maximum >= minimum",
            )
        return {
            "enabled": True,
            "minimum_non_whitespace_characters": minimum,
            "maximum_non_whitespace_characters": maximum,
        }

    def _validated_config(self, config: Dict[str, Any]) -> Dict[str, Any]:
        product_mode = config.get("product_mode")
        character_mode = config.get("character_mode")
        tier = config.get("execution_tier")
        task_mode = config.get("task_mode")
        if product_mode not in VALID_PRODUCT_MODES:
            raise ApiError(400, "INVALID_PRODUCT_MODE", "product_mode must be preserve or replace")
        if character_mode not in VALID_CHARACTER_MODES:
            raise ApiError(400, "INVALID_CHARACTER_MODE", "character_mode must be preserve, head_replace or full_replace")
        if tier not in VALID_EXECUTION_TIERS:
            raise ApiError(400, "INVALID_EXECUTION_TIER", "Unsupported execution tier")
        if task_mode not in VALID_TASK_MODES:
            raise ApiError(400, "INVALID_TASK_MODE", "task_mode must be single or dual")
        for key in ("product_id", "avatar_id"):
            if config.get(key) is not None:
                config[key] = validate_identifier(str(config[key]), key)
        source_person_id = config.get("source_person_id")
        if source_person_id is not None:
            source_person_id = str(source_person_id).strip()
            if not source_person_id or len(source_person_id) > 200 or any(ord(character) < 32 for character in source_person_id):
                raise ApiError(400, "INVALID_SOURCE_PERSON_ID", "source_person_id must be a non-empty string of at most 200 characters")
        config["source_person_id"] = source_person_id
        shot_scope = config.get("shot_scope")
        if not isinstance(shot_scope, dict) or shot_scope.get("mode") not in {"all", "range", "selected"}:
            raise ApiError(400, "INVALID_SHOT_SCOPE", "shot_scope.mode must be all, range or selected")
        if shot_scope["mode"] == "range":
            try:
                start = float(shot_scope.get("start"))
                end = float(shot_scope.get("end"))
            except (TypeError, ValueError):
                raise ApiError(400, "INVALID_SHOT_RANGE", "Range requires numeric start and end")
            if start < 0 or end <= start:
                raise ApiError(400, "INVALID_SHOT_RANGE", "Range end must be greater than start")
            config["shot_scope"] = {"mode": "range", "start": start, "end": end}
        elif shot_scope["mode"] == "selected":
            shot_ids = shot_scope.get("shot_ids")
            if not isinstance(shot_ids, list) or not shot_ids:
                raise ApiError(400, "INVALID_SHOT_SELECTION", "Selected scope requires shot_ids")
            clean_ids = [validate_identifier(str(value), "shot id") for value in shot_ids]
            config["shot_scope"] = {"mode": "selected", "shot_ids": list(dict.fromkeys(clean_ids))}
        else:
            config["shot_scope"] = {"mode": "all"}
        codex = config.get("codex")
        if not isinstance(codex, dict) or not isinstance(codex.get("enabled"), bool):
            raise ApiError(400, "INVALID_CODEX_CONFIG", "codex.enabled must be true or false")
        model = codex.get("model")
        if model is not None:
            model = str(model).strip()
            if not re.fullmatch(r"[A-Za-z0-9._:-]{1,100}", model):
                raise ApiError(400, "INVALID_MODEL_NAME", "Codex model contains unsafe characters")
        config["codex"] = {"enabled": codex["enabled"], "model": model or None}
        config["script_locked"] = config.get("script_locked") is True
        config["prompt_length_contract"] = self._normalize_prompt_length_contract(config.get("prompt_length_contract"))
        if product_mode == "preserve":
            config["product_id"] = None
        if character_mode == "preserve":
            config["avatar_id"] = None
            config["source_person_id"] = None
        return config

    def save_config(self, project_id: str, payload: Dict[str, Any]) -> Dict[str, Any]:
        project_dir = self.get_project_dir(project_id)
        previous_input = self._generation_input_snapshot(project_dir)
        state = self._load_state(project_dir)
        previous_config = dict(state.get("config") or _default_config())
        current = dict(previous_config)
        allowed = {
            "product_mode",
            "product_id",
            "character_mode",
            "avatar_id",
            "source_person_id",
            "shot_scope",
            "execution_tier",
            "task_mode",
            "script_locked",
            "prompt_length_contract",
            "codex",
        }
        unknown = sorted(set(payload) - allowed)
        if unknown:
            raise ApiError(400, "UNKNOWN_CONFIG_FIELDS", "Config contains unsupported fields", {"fields": unknown})
        for key, value in payload.items():
            current[key] = value
        current = self._validated_config(current)
        # Script confirmation belongs to the script receipt, never to a generic
        # config checkbox supplied by the UI.
        current["script_locked"] = self._project_script(project_dir).get("locked") is True
        if current["character_mode"] in {"head_replace", "full_replace"} and current.get("source_person_id"):
            # Validate against canonical source identities before persisting a
            # selector value; unknown ids must never silently become a new role.
            canonical_source_person, _ = self._selected_source_person(project_dir, current)
            current["source_person_id"] = canonical_source_person
        state["config"] = current
        state["updated_at"] = now_iso()

        raw_path = project_dir / "project.json"
        raw = read_json(raw_path, {})
        execution_tier_raw_changed = "execution_tier" in payload and raw.get("execution_tier") != current["execution_tier"]
        prompt_length_raw_changed = "prompt_length_contract" in payload and raw.get("prompt_length_contract") != current["prompt_length_contract"]
        original_profile = raw.get("product_profile")
        if current["product_mode"] == "preserve" and original_profile is None:
            state["product_binding_status"] = "ready"
        elif current["product_mode"] == "replace" and current.get("product_id") == original_profile:
            state["product_binding_status"] = "ready"
        elif current["product_mode"] == "replace" and current.get("product_id"):
            state["product_binding_status"] = "waiting_for_product_rebind"
        previous_product_binding = state.get("product_binding") or {}
        if current["product_mode"] == "preserve":
            desired_product_status = "ready" if raw.get("product_mode") == "preserve_source_product" else "waiting_for_apply"
            selected_product_id = None
        elif current.get("product_id") == previous_product_binding.get("applied_id"):
            desired_product_status = previous_product_binding.get("status") or "ready"
            selected_product_id = current.get("product_id")
        else:
            desired_product_status = "waiting_for_apply"
            selected_product_id = current.get("product_id")
        next_product_binding = {
            **previous_product_binding,
            "status": desired_product_status,
            "selected_id": selected_product_id,
            "applied_id": previous_product_binding.get("applied_id"),
        }
        product_semantics_changed = any(
            next_product_binding.get(key) != previous_product_binding.get(key)
            for key in ("status", "selected_id", "applied_id")
        )
        next_product_binding["updated_at"] = now_iso() if product_semantics_changed else previous_product_binding.get("updated_at")
        state["product_binding"] = next_product_binding
        previous_avatar_binding = state.get("avatar_binding") or {}
        if current["character_mode"] == "preserve":
            avatar_status = "ready" if previous_avatar_binding.get("applied_id") is None else "waiting_for_apply"
            selected_avatar_id = None
        elif (
            current.get("avatar_id") == previous_avatar_binding.get("applied_id")
            and current.get("source_person_id") == previous_avatar_binding.get("source_person_id")
        ):
            avatar_status = previous_avatar_binding.get("status") or "ready"
            selected_avatar_id = current.get("avatar_id")
        else:
            avatar_status = "waiting_for_apply"
            selected_avatar_id = current.get("avatar_id")
        next_avatar_binding = {
            **previous_avatar_binding,
            "status": avatar_status,
            "selected_id": selected_avatar_id,
            "applied_id": previous_avatar_binding.get("applied_id"),
            "source_person_id": current.get("source_person_id"),
        }
        avatar_semantics_changed = any(
            next_avatar_binding.get(key) != previous_avatar_binding.get(key)
            for key in ("status", "selected_id", "applied_id", "source_person_id")
        )
        next_avatar_binding["updated_at"] = now_iso() if avatar_semantics_changed else previous_avatar_binding.get("updated_at")
        state["avatar_binding"] = next_avatar_binding
        state["product_binding_status"] = state["product_binding"]["status"]
        if execution_tier_raw_changed or prompt_length_raw_changed:
            if execution_tier_raw_changed:
                raw["execution_tier"] = current["execution_tier"]
            if prompt_length_raw_changed:
                raw["prompt_length_contract"] = current["prompt_length_contract"]
            raw["updated_at"] = now_iso()
            atomic_write_json(raw_path, raw)
        atomic_write_json(self._workbench_path(project_dir), state)
        material_fields = (
            "product_mode",
            "product_id",
            "character_mode",
            "avatar_id",
            "source_person_id",
            "shot_scope",
            "execution_tier",
            "prompt_length_contract",
        )
        changed_fields = [key for key in material_fields if previous_config.get(key) != current.get(key)]
        if execution_tier_raw_changed and "execution_tier" not in changed_fields:
            changed_fields.append("execution_tier")
        if prompt_length_raw_changed and "prompt_length_contract" not in changed_fields:
            # Explicitly migrating a missing/noncanonical raw field changes the
            # Skill's project.json hash even when the UI projection was already
            # disabled; it still needs a canonical invalidation receipt.
            changed_fields.append("prompt_length_contract")
        invalidation = None
        if changed_fields:
            invalidation = self._invalidate_derived_dependencies(
                project_dir,
                "MATERIAL_CONFIG_CHANGED",
                changed_fields,
                previous_input=previous_input,
            )
        project = self.get_project(project_id)
        return {"ok": True, "project": project, "config": project["config"], "invalidation": invalidation}

    def _binding_backup(self, project_dir: Path, transaction_id: str, relatives: Iterable[str]) -> Path:
        backup_root = project_dir / "workbench" / "binding-backups" / transaction_id
        for relative in relatives:
            source = safe_join(project_dir, relative)
            if source.is_file():
                destination = backup_root / relative
                destination.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(source, destination)
        return backup_root

    def _apply_preserve_product(self, project_dir: Path, state: Dict[str, Any], transaction_id: str) -> Dict[str, Any]:
        raw_path = project_dir / "project.json"
        raw = read_json(raw_path, {})
        self._binding_backup(project_dir, transaction_id, ["project.json", "library/product_bible.json", "library/product_library.json", "library/knowledge_index.json"])
        raw["product_mode"] = "preserve_source_product"
        raw["product_profile"] = None
        binding = {
            "status": "ready",
            "selected_id": None,
            "applied_id": None,
            "transaction_id": transaction_id,
            "source": "preserve_source_product",
            "updated_at": now_iso(),
        }
        raw["product_binding"] = binding
        raw["updated_at"] = now_iso()
        atomic_write_json(raw_path, raw)
        state["product_binding"] = binding
        state["product_binding_status"] = "ready"
        return binding

    def _apply_builtin_product(self, project_dir: Path, state: Dict[str, Any], product_id: str, transaction_id: str) -> Dict[str, Any]:
        profile_path = safe_join(self.toolchain.skill_dir / "assets" / "profiles", product_id + ".json")
        if not profile_path.is_file():
            raise ApiError(404, "PRODUCT_PROFILE_NOT_FOUND", "Built-in product profile was not found")
        profile = read_json(profile_path)
        if profile.get("profile_id") != product_id:
            raise ApiError(409, "PRODUCT_PROFILE_ID_MISMATCH", "Built-in product profile id does not match the selection")
        backup_root = self._binding_backup(
            project_dir,
            transaction_id,
            ["project.json", "library/product_bible.json", "library/product_library.json", "library/knowledge_index.json"],
        )
        copied_references: List[Dict[str, Any]] = []
        image_entries: List[Dict[str, Any]] = []
        for asset in profile.get("reference_assets") or []:
            if not isinstance(asset, dict) or asset.get("approved") is not True:
                continue
            source_relative = asset.get("source_path")
            target_relative = asset.get("target_path")
            if not source_relative or not target_relative:
                raise ApiError(409, "PRODUCT_REFERENCE_CONTRACT_INVALID", "Approved profile reference lacks source_path or target_path")
            source = safe_join(self.toolchain.skill_dir, str(source_relative))
            target = safe_join(project_dir, str(target_relative))
            if not source.is_file():
                raise ApiError(409, "PRODUCT_REFERENCE_MISSING", "Approved built-in product reference file is missing", {"path": str(source_relative)})
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, target)
            copied = dict(asset)
            copied.pop("source_path", None)
            copied["path"] = target.relative_to(project_dir).as_posix()
            copied["sha256"] = sha256_file(target)
            copied_references.append(copied)
            applies_to: Dict[str, Any] = {"product_profile": product_id}
            states = [str(value) for value in asset.get("allowed_states") or [] if str(value)]
            if states and "*" not in states:
                applies_to["product_state"] = states
            image_entries.append(
                {
                    "id": "KB-%s" % asset.get("id"),
                    "type": "image",
                    "title": asset.get("id"),
                    "path": copied["path"],
                    "sha256": copied["sha256"],
                    "reference_role": asset.get("role"),
                    "allowed_inheritance": asset.get("allowed_inheritance") or [],
                    "forbidden_inheritance": asset.get("forbidden_inheritance") or [],
                    "applies_to": applies_to,
                    "priority": 95,
                    "approved": True,
                    "version": int(profile.get("version") or 1),
                }
            )

        product_library_path = project_dir / "library" / "product_library.json"
        product_library = read_json(product_library_path, {"schema_version": "1.1", "version": 1, "products": []})
        products = [item for item in product_library.get("products") or [] if isinstance(item, dict) and item.get("id") != product_id]
        for item in products:
            item["active"] = False
        products.append(
            {
                "id": product_id,
                "name": profile.get("name") or product_id,
                "active": True,
                "rights_cleared": False,
                "usage_scope": "internal_test",
                "profile_path": "library/product_bible.json",
                "version": profile.get("version") or 1,
                "states": sorted((profile.get("state_profiles") or {}).keys()),
                "reference_assets": copied_references,
                "approved_result_assets": [],
            }
        )
        product_library["products"] = products

        knowledge_path = project_dir / "library" / "knowledge_index.json"
        knowledge = read_json(knowledge_path, {"schema_version": "1.1", "version": 1, "entries": []})
        retained_entries = []
        new_ids = {str(item.get("id")) for item in (profile.get("knowledge_seed") or []) + image_entries if isinstance(item, dict)}
        for entry in knowledge.get("entries") or []:
            if not isinstance(entry, dict):
                continue
            applies = entry.get("applies_to") or {}
            if applies.get("product_profile") and applies.get("product_profile") != product_id:
                continue
            if str(entry.get("id")) in new_ids:
                continue
            retained_entries.append(entry)
        knowledge["entries"] = retained_entries + list(profile.get("knowledge_seed") or []) + image_entries
        knowledge["version"] = max(int(knowledge.get("version") or 1), int(profile.get("version") or 1))

        raw_path = project_dir / "project.json"
        raw = read_json(raw_path, {})
        # Product-specific rule overrides from any installed profile are removed before the new profile is applied.
        project_rules = raw.setdefault("project_rules", {})
        profiles_root = self.toolchain.skill_dir / "assets" / "profiles"
        for other_path in profiles_root.glob("*.json"):
            try:
                other = read_json(other_path)
            except ApiError:
                continue
            for key in (other.get("project_rule_overrides") or {}).keys():
                project_rules.pop(key, None)
        overrides = profile.get("project_rule_overrides") or {}
        project_rules.update(overrides)
        status = "ready" if copied_references else "bound_missing_approved_reference"
        binding = {
            "status": status,
            "selected_id": product_id,
            "applied_id": product_id,
            "transaction_id": transaction_id,
            "source": "builtin",
            "reference_count": len(copied_references),
            "backup_path": backup_root.relative_to(project_dir).as_posix(),
            "applied_rule_keys": sorted(overrides.keys()),
            "updated_at": now_iso(),
        }
        raw.update({"product_mode": "replace_product", "product_profile": product_id, "product_binding": binding, "updated_at": now_iso()})
        atomic_write_json(project_dir / "library" / "product_bible.json", profile)
        atomic_write_json(product_library_path, product_library)
        atomic_write_json(knowledge_path, knowledge)
        atomic_write_json(raw_path, raw)
        state["product_binding"] = binding
        state["product_binding_status"] = status
        return binding

    def _raw_custom_knowledge(self, kind: str, asset_id: str) -> Optional[Dict[str, Any]]:
        record_path = self.knowledge_root / kind / asset_id / "record.json"
        if not record_path.is_file():
            return None
        value = read_json(record_path)
        return value if isinstance(value, dict) else None

    @staticmethod
    def _first_contract_value(value: Any, paths: Iterable[Tuple[str, ...]]) -> Any:
        for path in paths:
            current = value
            for key in path:
                if not isinstance(current, dict) or key not in current:
                    current = None
                    break
                current = current[key]
            if current not in (None, "", [], {}, 0):
                return current
        return None

    @staticmethod
    def _reference_packaging_layer(reference: Dict[str, Any]) -> Optional[str]:
        explicit = reference.get("packaging_layer")
        if reference.get("_packaging_layer_explicit") is True:
            if explicit in (None, ""):
                return None
            value = str(explicit).strip().lower().replace("-", "_").replace(" ", "_")
            return value if value in PACKAGING_LAYER_SET else None
        if explicit not in (None, ""):
            value = str(explicit).strip().lower().replace("-", "_").replace(" ", "_")
            return value if value in PACKAGING_LAYER_SET else None
        role = str(reference.get("role") or "").strip().lower().replace("-", "_").replace(" ", "_")
        for layer in PACKAGING_LAYERS:
            if role == layer or role.startswith(layer + "_"):
                return layer
        chinese_prefixes = {
            "独立包装": "individual_package",
            "单包": "individual_package",
            "零售盒": "retail_box",
            "包装盒": "retail_box",
            "内托": "inner_tray",
            "托盘": "inner_tray",
            "运输箱": "shipping_carton",
            "运输纸箱": "shipping_carton",
            "外箱": "shipping_carton",
        }
        for prefix, layer in chinese_prefixes.items():
            if role.startswith(prefix):
                return layer
        # Legacy generic box roles are exposed as retail-box assets, but they
        # do not by themselves switch an old record to the layered v2 contract.
        if role in {
            "package_front",
            "package_back",
            "package_side",
            "packaging_front",
            "packaging_back",
            "packaging_side",
            "box_front",
            "box_back",
            "box_side",
            "包装正面",
            "包装背面",
            "外盒正面",
        }:
            return "retail_box"
        return None

    @staticmethod
    def _reference_declares_layered_packaging(reference: Dict[str, Any]) -> bool:
        if reference.get("_packaging_layer_explicit") is True:
            return WorkbenchService._reference_packaging_layer(reference) is not None
        if reference.get("packaging_layer") not in (None, ""):
            return True
        role = str(reference.get("role") or "").strip().lower().replace("-", "_").replace(" ", "_")
        # `retail_box_front` was already accepted by the v1 flat package
        # contract. Do not silently migrate historical records merely because
        # their old role happens to share a v2 layer prefix. New uploads can
        # opt in unambiguously with `packaging_layer=retail_box`.
        if role == "retail_box_front":
            return False
        if any(role == layer or role.startswith(layer + "_") for layer in PACKAGING_LAYERS):
            return True
        return any(
            role.startswith(prefix)
            for prefix in ("独立包装", "单包", "零售盒", "包装盒", "内托", "托盘", "运输箱", "运输纸箱", "外箱")
        )

    @staticmethod
    def _packaging_asset_ids(references: Iterable[Dict[str, Any]]) -> Dict[str, List[str]]:
        result: Dict[str, List[str]] = {}
        for reference in references:
            if not isinstance(reference, dict):
                continue
            layer = WorkbenchService._reference_packaging_layer(reference)
            reference_id = reference.get("id")
            if layer and reference_id not in (None, ""):
                values = result.setdefault(layer, [])
                if str(reference_id) not in values:
                    values.append(str(reference_id))
        return {layer: result[layer] for layer in PACKAGING_LAYERS if result.get(layer)}

    @staticmethod
    def _validate_packaging_contracts(value: Any) -> Optional[Dict[str, Dict[str, Any]]]:
        if value is None:
            return None
        if not isinstance(value, dict):
            raise ApiError(400, "INVALID_PACKAGING_CONTRACTS", "packaging_contracts must be a JSON object")
        unknown_layers = sorted(set(value) - PACKAGING_LAYER_SET)
        if unknown_layers:
            raise ApiError(
                400,
                "UNKNOWN_PACKAGING_LAYER",
                "packaging_contracts contains an unsupported packaging layer",
                {"layers": unknown_layers, "allowed": list(PACKAGING_LAYERS)},
            )
        normalized: Dict[str, Dict[str, Any]] = {}
        for layer in PACKAGING_LAYERS:
            if layer not in value:
                continue
            raw_contract = value[layer]
            if not isinstance(raw_contract, dict):
                raise ApiError(
                    400,
                    "INVALID_PACKAGING_LAYER_CONTRACT",
                    "Each packaging layer contract must be an object",
                    {"layer": layer},
                )
            unknown_fields = sorted(set(raw_contract) - PACKAGING_CONTRACT_FIELDS)
            if unknown_fields:
                raise ApiError(
                    400,
                    "UNKNOWN_PACKAGING_CONTRACT_FIELDS",
                    "Packaging layer contract contains unsupported fields",
                    {"layer": layer, "fields": unknown_fields, "allowed": sorted(PACKAGING_CONTRACT_FIELDS)},
                )
            contract = dict(raw_contract)
            present = contract.get("present", True)
            if not isinstance(present, bool):
                raise ApiError(400, "INVALID_PACKAGING_PRESENT", "present must be true or false", {"layer": layer})
            contract["present"] = present
            if "notes" in contract:
                notes = str(contract.get("notes") or "").strip()
                if len(notes) > 2000:
                    raise ApiError(400, "PACKAGING_NOTES_TOO_LONG", "Packaging layer notes cannot exceed 2000 characters", {"layer": layer})
                contract["notes"] = notes or None
            if not present:
                forbidden_when_absent = sorted(
                    key for key in ("dimensions_cm", "quantity", "topology", "contains", "text_layout", "material", "attributes") if key in contract
                )
                if forbidden_when_absent:
                    raise ApiError(
                        400,
                        "ABSENT_PACKAGING_HAS_PHYSICAL_FACTS",
                        "A packaging layer marked present=false cannot also declare physical facts",
                        {"layer": layer, "fields": forbidden_when_absent},
                    )
                try:
                    json.dumps(contract, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)
                except (TypeError, ValueError) as exc:
                    raise ApiError(
                        400,
                        "INVALID_PACKAGING_CONTRACT_NUMBER",
                        "Packaging contracts must contain finite JSON values",
                        {"layer": layer, "reason": str(exc)},
                    )
                normalized[layer] = contract
                continue
            dimensions = contract.get("dimensions_cm")
            if not isinstance(dimensions, dict) or not dimensions:
                raise ApiError(
                    400,
                    "PACKAGING_DIMENSIONS_REQUIRED",
                    "A present packaging layer requires dimensions_cm",
                    {"layer": layer},
                )
            for dimension_name, dimension_value in dimensions.items():
                if (
                    not isinstance(dimension_name, str)
                    or not dimension_name.strip()
                    or isinstance(dimension_value, bool)
                    or not isinstance(dimension_value, (int, float))
                    or not math.isfinite(float(dimension_value))
                    or float(dimension_value) <= 0
                ):
                    raise ApiError(
                        400,
                        "INVALID_PACKAGING_DIMENSION",
                        "Every dimensions_cm value must be a positive finite number",
                        {"layer": layer, "dimension": str(dimension_name)},
                    )
            quantity = contract.get("quantity")
            if isinstance(quantity, bool) or not isinstance(quantity, int) or quantity <= 0:
                raise ApiError(
                    400,
                    "PACKAGING_QUANTITY_REQUIRED",
                    "A present packaging layer requires a positive integer quantity",
                    {"layer": layer},
                )
            topology = contract.get("topology")
            if not (
                (isinstance(topology, str) and bool(topology.strip()) and len(topology.strip()) <= 500)
                or (isinstance(topology, dict) and bool(topology))
            ):
                raise ApiError(
                    400,
                    "PACKAGING_TOPOLOGY_REQUIRED",
                    "A present packaging layer requires a non-empty topology string or object",
                    {"layer": layer},
                )
            if isinstance(topology, str):
                contract["topology"] = topology.strip()
            contains = contract.get("contains")
            if contains not in (None, ""):
                contains = str(contains).strip().lower().replace("-", "_").replace(" ", "_")
                allowed_contains = {"product_body"} | PACKAGING_LAYER_SET
                if contains not in allowed_contains or contains == layer:
                    raise ApiError(
                        400,
                        "INVALID_PACKAGING_CONTAINS",
                        "contains must name product_body or a different supported packaging layer",
                        {"layer": layer, "contains": contains, "allowed": sorted(allowed_contains - {layer})},
                    )
                contract["contains"] = contains
            if "text_layout" in contract and not isinstance(contract["text_layout"], (dict, list)):
                raise ApiError(400, "INVALID_PACKAGING_TEXT_LAYOUT", "text_layout must be an object or array", {"layer": layer})
            if "material" in contract and not isinstance(contract["material"], (str, dict, list)):
                raise ApiError(400, "INVALID_PACKAGING_MATERIAL", "material must be text, an object or an array", {"layer": layer})
            if "attributes" in contract and not isinstance(contract["attributes"], dict):
                raise ApiError(400, "INVALID_PACKAGING_ATTRIBUTES", "attributes must be an object", {"layer": layer})
            try:
                json.dumps(contract, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)
            except (TypeError, ValueError) as exc:
                raise ApiError(
                    400,
                    "INVALID_PACKAGING_CONTRACT_NUMBER",
                    "Packaging contracts must contain finite JSON values",
                    {"layer": layer, "reason": str(exc)},
                )
            normalized[layer] = contract
        return normalized

    def _build_custom_product_contract(self, project_dir: Path, product_id: str) -> Dict[str, Any]:
        """Rebuild the immutable product facts from the knowledge source of truth."""
        record_path = self.knowledge_root / "products" / product_id / "record.json"
        record = self._raw_custom_knowledge("products", product_id)
        if not record:
            raise ApiError(409, "CUSTOM_PRODUCT_RECORD_MISSING", "Custom product knowledge record is missing")
        dimensions = record.get("dimensions_cm")
        package_spec = record.get("package_spec")
        raw_references = [value for value in record.get("references") or [] if isinstance(value, dict)]
        layered_packaging = isinstance(record.get("packaging_contracts"), dict) or any(
            self._reference_declares_layered_packaging(reference) for reference in raw_references
        )
        packaging_contracts: Optional[Dict[str, Dict[str, Any]]] = None
        if layered_packaging:
            try:
                packaging_contracts = self._validate_packaging_contracts(record.get("packaging_contracts"))
            except ApiError as exc:
                raise ApiError(
                    409,
                    "CUSTOM_PRODUCT_PACKAGING_CONTRACT_INVALID",
                    "Stored layered packaging contracts are invalid",
                    {"source_code": exc.code, "source_details": exc.details},
                )
            packaging_contracts = packaging_contracts or {}
        missing: List[str] = []
        if dimensions in (None, {}, []):
            missing.append("dimensions_cm")
        if layered_packaging:
            present_layers = [
                layer
                for layer in PACKAGING_LAYERS
                if isinstance((packaging_contracts or {}).get(layer), dict)
                and (packaging_contracts or {})[layer].get("present", True) is True
            ]
            package_present = bool(present_layers)
            primary_layer = next(
                (layer for layer in ("retail_box", "individual_package", "inner_tray", "shipping_carton") if layer in present_layers),
                None,
            )
            primary_contract = (packaging_contracts or {}).get(primary_layer, {}) if primary_layer else {}
            quantity = primary_contract.get("quantity")
            topology = primary_contract.get("topology")
            text_layout = primary_contract.get("text_layout")
            if not isinstance(package_spec, dict) or not package_spec:
                package_spec = {
                    "present": package_present,
                    "source": "packaging_contracts",
                    "primary_layer": primary_layer,
                }
                if primary_layer:
                    package_spec.update(
                        {
                            "dimensions_cm": primary_contract.get("dimensions_cm"),
                            "quantity": quantity,
                            "topology": topology,
                        }
                    )
                    if text_layout is not None:
                        package_spec["text_layout"] = text_layout
        else:
            if not isinstance(package_spec, dict) or not package_spec:
                missing.append("package_spec")
                package_spec = package_spec if isinstance(package_spec, dict) else {}
            package_present = package_spec.get("present") is not False
            quantity = self._first_contract_value(
                package_spec,
                (
                    ("quantity",),
                    ("unit_count",),
                    ("units_per_package",),
                    ("product_count",),
                    ("pack_count",),
                    ("contents", "quantity"),
                    ("contents", "count"),
                    ("数量",),
                ),
            )
            topology = self._first_contract_value(
                package_spec,
                (
                    ("box_topology",),
                    ("topology",),
                    ("package_topology",),
                    ("structure",),
                    ("box", "topology"),
                    ("盒体拓扑",),
                ),
            )
            text_layout = self._first_contract_value(
                package_spec,
                (
                    ("text_layout",),
                    ("artwork_layout",),
                    ("label_layout",),
                    ("typography_layout",),
                    ("copy_layout",),
                    ("artwork", "text_layout"),
                    ("文字版面",),
                ),
            )
            if package_present:
                if quantity is None:
                    missing.append("package_quantity")
                if topology is None:
                    missing.append("box_topology")
                if text_layout is None:
                    missing.append("text_layout")
        references: List[Dict[str, Any]] = []
        for index, reference in enumerate(raw_references):
            if not isinstance(reference, dict) or not reference.get("filename"):
                continue
            filename = str(reference["filename"])
            source = safe_join(self.knowledge_root / "products" / product_id, filename)
            if not source.is_file():
                raise ApiError(
                    409,
                    "CUSTOM_PRODUCT_REFERENCE_MISSING",
                    "A knowledge reference listed by the product record is missing",
                    {"filename": filename},
                )
            actual_sha256 = sha256_file(source)
            recorded_sha256 = str(reference.get("sha256") or "")
            if not recorded_sha256 or recorded_sha256 != actual_sha256:
                raise ApiError(
                    409,
                    "CUSTOM_PRODUCT_REFERENCE_HASH_MISMATCH",
                    "A product reference no longer matches its registered SHA-256",
                    {"filename": filename, "registered_sha256": recorded_sha256, "actual_sha256": actual_sha256},
                )
            contract_reference = {
                    "id": str(reference.get("id") or "ref-%d" % (index + 1)),
                    "source_filename": filename,
                    "project_path": (Path("source") / "references" / "products" / product_id / filename).as_posix(),
                    "sha256": actual_sha256,
                    "size": int(reference.get("size") or source.stat().st_size),
                    "role": reference.get("role"),
                    "product_state": reference.get("product_state"),
                }
            if layered_packaging:
                contract_reference.update(
                    {
                        "packaging_layer": self._reference_packaging_layer(reference),
                        "label": reference.get("label"),
                        "angle": reference.get("angle"),
                    }
                )
            references.append(contract_reference)
        if not references:
            missing.append("reference_sha256s")
        packaging_assets: Dict[str, List[Dict[str, Any]]] = {}
        if layered_packaging:
            for reference in references:
                layer = reference.get("packaging_layer")
                if layer in PACKAGING_LAYER_SET:
                    packaging_assets.setdefault(str(layer), []).append(reference)
            for layer in PACKAGING_LAYERS:
                layer_contract = (packaging_contracts or {}).get(layer)
                layer_assets = packaging_assets.get(layer) or []
                if layer_assets and not isinstance(layer_contract, dict):
                    missing.append("packaging_%s_contract" % layer)
                if isinstance(layer_contract, dict) and layer_contract.get("present", True) is True and not layer_assets:
                    missing.append("packaging_%s_reference" % layer)
                if isinstance(layer_contract, dict) and layer_contract.get("present") is False and layer_assets:
                    missing.append("packaging_%s_absent_but_referenced" % layer)
        else:
            package_front_roles = {
                "package_front",
                "packaging_front",
                "box_front",
                "retail_box_front",
                "包装正面",
                "外盒正面",
            }
            if package_present and not any(str(reference.get("role") or "").strip().lower().replace("-", "_") in package_front_roles for reference in references):
                missing.append("package_front_reference")
        if missing:
            raise ApiError(
                409,
                "CUSTOM_PRODUCT_CONTRACT_INCOMPLETE",
                (
                    "Custom product dimensions plus per-layer packaging contracts/references are required"
                    if layered_packaging
                    else "Custom product dimensions, package quantity/topology/text layout and hashed references are required"
                ),
                {"pending_inputs": list(dict.fromkeys(missing)), "product_id": product_id},
            )
        contract = {
            "schema_version": "custom-product-immutable-contract-v2" if layered_packaging else "custom-product-immutable-contract-v1",
            "product_id": product_id,
            "source_record_path": "knowledge/products/%s/record.json" % product_id,
            "source_record_sha256": sha256_file(record_path),
            "dimensions_cm": dimensions,
            "package_spec": package_spec,
            "package_present": package_present,
            "package_quantity": quantity,
            "box_topology": topology,
            "text_layout": text_layout,
            "reference_count": len(references),
            "references": references,
        }
        if layered_packaging:
            contract.update(
                {
                    "packaging_contracts": {
                        layer: (packaging_contracts or {})[layer]
                        for layer in PACKAGING_LAYERS
                        if layer in (packaging_contracts or {})
                    },
                    "packaging_assets": {
                        layer: packaging_assets[layer]
                        for layer in PACKAGING_LAYERS
                        if packaging_assets.get(layer)
                    },
                    "packaging_layers_present": present_layers,
                    "primary_packaging_layer": primary_layer,
                }
            )
        try:
            canonical = json.dumps(contract, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)
        except (TypeError, ValueError) as exc:
            raise ApiError(
                409,
                "CUSTOM_PRODUCT_CONTRACT_INVALID_NUMBER",
                "Product contract values must be finite JSON values",
                {"reason": str(exc)},
            )
        contract["contract_sha256"] = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
        return contract

    @staticmethod
    def _locked_product_bible_fields(contract: Dict[str, Any]) -> Dict[str, Any]:
        fields = {
            "immutable_contract_path": "library/product_immutable_contract.json",
            "immutable_contract_sha256": contract["contract_sha256"],
            "physical_dimensions_cm": contract["dimensions_cm"],
            "packaging_contract": contract["package_spec"],
            "package_present": contract["package_present"],
            "package_quantity": contract["package_quantity"],
            "box_topology": contract["box_topology"],
            "text_layout": contract["text_layout"],
            "reference_assets": contract["references"],
        }
        if contract.get("schema_version") == "custom-product-immutable-contract-v2":
            fields.update(
                {
                    "packaging_contracts": contract.get("packaging_contracts") or {},
                    "packaging_assets": contract.get("packaging_assets") or {},
                    "packaging_layers_present": contract.get("packaging_layers_present") or [],
                    "primary_packaging_layer": contract.get("primary_packaging_layer"),
                }
            )
        return fields

    def _materialize_custom_product_contract(
        self,
        project_dir: Path,
        product_id: str,
        transaction_id: str,
    ) -> Dict[str, Any]:
        contract = self._build_custom_product_contract(project_dir, product_id)
        self._binding_backup(
            project_dir,
            transaction_id,
            [
                "library/product_immutable_contract.json",
                "library/product_bible.json",
                "library/product_library.json",
            ],
        )
        knowledge_root = self.knowledge_root / "products" / product_id
        for reference in contract["references"]:
            source = safe_join(knowledge_root, str(reference["source_filename"]))
            destination = safe_join(project_dir, str(reference["project_path"]))
            with source.open("rb") as handle:
                _, copied_sha256 = copy_stream_atomic(handle, destination, self.maximum_knowledge_bytes)
            if copied_sha256 != reference["sha256"]:
                destination.unlink(missing_ok=True)
                raise ApiError(500, "CUSTOM_PRODUCT_COPY_HASH_MISMATCH", "Copied product reference failed SHA-256 verification")

        contract_path = project_dir / "library" / "product_immutable_contract.json"
        atomic_write_json(contract_path, contract)
        bible_path = project_dir / "library" / "product_bible.json"
        bible = read_json(bible_path, {})
        if not isinstance(bible, dict):
            bible = {}
        bible.update(
            {
                "schema_version": bible.get("schema_version") or "custom-product-bible-v1",
                "profile_id": product_id,
                "name": bible.get("name") or (self._raw_custom_knowledge("products", product_id) or {}).get("name") or product_id,
                **self._locked_product_bible_fields(contract),
            }
        )
        bible.setdefault("immutable_traits", [])
        bible.setdefault("state_profiles", {})
        atomic_write_json(bible_path, bible)

        library_path = project_dir / "library" / "product_library.json"
        library = read_json(library_path, {"schema_version": "1.1", "version": 1, "products": []})
        if not isinstance(library, dict):
            library = {"schema_version": "1.1", "version": 1, "products": []}
        products = [item for item in library.get("products") or [] if isinstance(item, dict) and item.get("id") != product_id]
        for item in products:
            item["active"] = False
        products.append(
            {
                "id": product_id,
                "name": bible.get("name") or product_id,
                "active": True,
                "source": "custom",
                "immutable_contract_path": "library/product_immutable_contract.json",
                "immutable_contract_sha256": contract["contract_sha256"],
                "reference_assets": contract["references"],
                "states": sorted((bible.get("state_profiles") or {}).keys()),
                **(
                    {
                        "packaging_contracts": contract.get("packaging_contracts") or {},
                        "packaging_assets": contract.get("packaging_assets") or {},
                        "packaging_layers_present": contract.get("packaging_layers_present") or [],
                        "primary_packaging_layer": contract.get("primary_packaging_layer"),
                    }
                    if contract.get("schema_version") == "custom-product-immutable-contract-v2"
                    else {}
                ),
            }
        )
        library["products"] = products
        library["version"] = max(int(library.get("version") or 1), 1)
        atomic_write_json(library_path, library)
        return contract

    def commit_custom_product_guidance(
        self,
        project_id: str,
        guidance: Any,
        expected_contract_sha256: Optional[str],
    ) -> Dict[str, Any]:
        """Whitelist-merge model-derived prose while reasserting locked facts."""
        if not isinstance(guidance, dict):
            raise ApiError(409, "INVALID_DERIVED_PRODUCT_GUIDANCE", "Codex must return a derived_guidance object")
        unknown = sorted(set(guidance) - {"immutable_traits", "state_profiles", "non_authoritative_prompt_guidance"})
        if unknown:
            raise ApiError(
                409,
                "DERIVED_GUIDANCE_CONTAINS_LOCKED_FIELDS",
                "Derived product guidance contains unsupported fields",
                {"fields": unknown},
            )
        immutable_traits = guidance.get("immutable_traits")
        state_profiles = guidance.get("state_profiles")
        prompt_guidance = guidance.get("non_authoritative_prompt_guidance") or []
        if (
            not isinstance(immutable_traits, list)
            or not immutable_traits
            or not all(isinstance(value, str) and value.strip() for value in immutable_traits)
            or not isinstance(state_profiles, dict)
            or not state_profiles
            or not isinstance(prompt_guidance, list)
            or not all(isinstance(value, str) for value in prompt_guidance)
        ):
            raise ApiError(
                409,
                "INVALID_DERIVED_PRODUCT_GUIDANCE",
                "Derived guidance requires non-empty immutable_traits and state_profiles plus an optional text list",
            )
        prohibited = {
            "dimensions_cm",
            "physical_dimensions_cm",
            "package_spec",
            "packaging_contract",
            "packaging_contracts",
            "packaging_assets",
            "packaging_layer",
            "packaging_layers_present",
            "primary_packaging_layer",
            "quantity",
            "package_quantity",
            "quantity_contract",
            "topology",
            "box_topology",
            "text_layout",
            "artwork_layout",
            "reference_assets",
            "references",
            "reference_sha256",
            "contract_sha256",
            "product_id",
            "profile_id",
        }

        def forbidden_path(value: Any, path: str = "derived_guidance") -> Optional[str]:
            if isinstance(value, dict):
                for key, child in value.items():
                    normalized = str(key).strip().lower()
                    child_path = "%s.%s" % (path, key)
                    if normalized in prohibited:
                        return child_path
                    found = forbidden_path(child, child_path)
                    if found:
                        return found
            elif isinstance(value, list):
                for index, child in enumerate(value):
                    found = forbidden_path(child, "%s[%d]" % (path, index))
                    if found:
                        return found
            return None

        conflict_path = forbidden_path(guidance)
        if conflict_path:
            raise ApiError(
                409,
                "DERIVED_GUIDANCE_CONTAINS_LOCKED_FIELDS",
                "Codex attempted to redefine an immutable product fact",
                {"path": conflict_path},
            )

        project_dir = self.get_project_dir(project_id)
        state = self._load_state(project_dir)
        product_id = str((state.get("config") or {}).get("product_id") or "")
        if not product_id:
            raise ApiError(409, "PRODUCT_REFERENCE_REQUIRED", "No custom product is selected")
        contract = self._build_custom_product_contract(project_dir, product_id)
        if not expected_contract_sha256 or contract["contract_sha256"] != expected_contract_sha256:
            raise ApiError(
                409,
                "PRODUCT_BINDING_SOURCE_CHANGED",
                "The knowledge product changed after this binding task was created; apply it again",
                {
                    "expected_contract_sha256": expected_contract_sha256,
                    "current_contract_sha256": contract["contract_sha256"],
                },
            )
        stored = read_json(project_dir / "library" / "product_immutable_contract.json", {})
        if stored != contract:
            raise ApiError(
                409,
                "CUSTOM_PRODUCT_IMMUTABLE_CONTRACT_VIOLATION",
                "The staged immutable product contract was changed before commit",
            )
        bible_path = project_dir / "library" / "product_bible.json"
        bible = read_json(bible_path, {})
        if not isinstance(bible, dict):
            bible = {}
        bible.update(self._locked_product_bible_fields(contract))
        bible.update(
            {
                "schema_version": bible.get("schema_version") or "custom-product-bible-v1",
                "profile_id": product_id,
                "immutable_traits": [value.strip() for value in immutable_traits],
                "state_profiles": state_profiles,
                "non_authoritative_prompt_guidance": prompt_guidance,
                "derived_guidance_updated_at": now_iso(),
            }
        )
        atomic_write_json(bible_path, bible)
        library_path = project_dir / "library" / "product_library.json"
        library = read_json(library_path, {})
        if not isinstance(library, dict):
            library = {"schema_version": "1.1", "version": 1, "products": []}
        entries = []
        found = False
        for item in library.get("products") or []:
            if not isinstance(item, dict):
                continue
            if item.get("id") == product_id:
                item = dict(item)
                item.update(
                    {
                        "active": True,
                        "immutable_contract_sha256": contract["contract_sha256"],
                        "immutable_contract_path": "library/product_immutable_contract.json",
                        "reference_assets": contract["references"],
                        "states": sorted(state_profiles.keys()),
                    }
                )
                if contract.get("schema_version") == "custom-product-immutable-contract-v2":
                    item.update(
                        {
                            "packaging_contracts": contract.get("packaging_contracts") or {},
                            "packaging_assets": contract.get("packaging_assets") or {},
                            "packaging_layers_present": contract.get("packaging_layers_present") or [],
                            "primary_packaging_layer": contract.get("primary_packaging_layer"),
                        }
                    )
                found = True
            entries.append(item)
        if not found:
            raise ApiError(409, "CUSTOM_PRODUCT_LIBRARY_ENTRY_MISSING", "Deterministic product library entry is missing")
        library["products"] = entries
        atomic_write_json(library_path, library)
        return self.validate_custom_product_binding(project_id)

    def _write_avatar_binding_plan(self, project_dir: Path, binding: Dict[str, Any]) -> None:
        plan_path = project_dir / "planning" / "avatar_binding.json"
        plan = read_json(plan_path, {})
        if not isinstance(plan, dict):
            plan = {}
        plan.update(
            {
                "schema_version": plan.get("schema_version") or "avatar-binding-v1.0",
                "status": binding.get("status"),
                "source_person_id": binding.get("source_person_id"),
                "target_avatar_id": binding.get("applied_id") or binding.get("selected_id"),
                "character_mode": binding.get("character_mode"),
                "transaction_id": binding.get("transaction_id"),
                "immutable_contract_path": binding.get("immutable_contract_path"),
                "immutable_contract_sha256": binding.get("immutable_contract_sha256"),
                "reference_set_sha256": binding.get("reference_set_sha256"),
                "updated_at": binding.get("updated_at") or now_iso(),
            }
        )
        atomic_write_json(plan_path, plan)

    def _build_custom_avatar_contract(
        self,
        project_dir: Path,
        avatar_id: str,
        source_person_id: str,
        character_mode: str,
    ) -> Dict[str, Any]:
        record_path = self.knowledge_root / "avatars" / avatar_id / "record.json"
        record = self._raw_custom_knowledge("avatars", avatar_id)
        if not record:
            raise ApiError(409, "CUSTOM_AVATAR_RECORD_MISSING", "Custom avatar storage record is missing")
        if record.get("authorized") is not True:
            raise ApiError(409, "PORTRAIT_AUTHORIZATION_REQUIRED", "Portrait authorization must be explicitly recorded")
        usage_scope = record.get("usage_scope")
        allowed = (
            usage_scope == "head_and_full"
            or (usage_scope == "head_only" and character_mode == "head_replace")
            or (usage_scope == "full_only" and character_mode == "full_replace")
        )
        if not allowed:
            raise ApiError(409, "AVATAR_USAGE_SCOPE_MISMATCH", "Avatar usage scope does not permit the selected replacement mode")
        references: List[Dict[str, Any]] = []
        reference_ids: set = set()
        for index, reference in enumerate(record.get("references") or []):
            if not isinstance(reference, dict) or not reference.get("filename"):
                continue
            reference_id = str(reference.get("id") or "ref-%d" % (index + 1))
            if reference_id in reference_ids:
                raise ApiError(409, "DUPLICATE_AVATAR_REFERENCE_ID", "Avatar reference IDs must be unique")
            reference_ids.add(reference_id)
            filename = str(reference["filename"])
            source = safe_join(self.knowledge_root / "avatars" / avatar_id, filename)
            if not source.is_file():
                raise ApiError(409, "CUSTOM_AVATAR_REFERENCE_MISSING", "A registered avatar reference file is missing", {"filename": filename})
            actual_sha256 = sha256_file(source)
            actual_size = source.stat().st_size
            if reference.get("sha256") != actual_sha256 or int(reference.get("size") or -1) != actual_size:
                raise ApiError(
                    409,
                    "CUSTOM_AVATAR_REFERENCE_HASH_MISMATCH",
                    "Avatar reference bytes no longer match the registered SHA-256 and size",
                    {"reference_id": reference_id, "filename": filename},
                )
            role = str(reference.get("role") or "").strip().lower().replace("-", "_")
            angle = str(reference.get("angle") or "").strip().lower().replace("-", "_")
            references.append(
                {
                    "id": reference_id,
                    "role": role or None,
                    "angle": angle or None,
                    "source_filename": filename,
                    "project_path": (Path("source") / "references" / "avatars" / avatar_id / filename).as_posix(),
                    "sha256": actual_sha256,
                    "size": actual_size,
                }
            )
        if not references:
            raise ApiError(409, "AVATAR_REFERENCE_FILES_MISSING", "No verified avatar references are available")
        head_roles = {"front", "frontal", "front_face", "face_front", "head_front", "portrait_front", "正脸"}
        full_roles = {"full_body", "fullbody", "body_front", "full", "全身"}
        has_head = any(reference.get("role") in head_roles or reference.get("angle") in {"front", "frontal", "正面"} for reference in references)
        has_full = any(reference.get("role") in full_roles for reference in references)
        if not has_head:
            raise ApiError(
                409,
                "AVATAR_HEAD_REFERENCE_REQUIRED",
                "A verified frontal face/head reference is required; a full-body-only file cannot stand in for it",
                {"pending_inputs": ["avatar_head_reference"]},
            )
        if character_mode == "full_replace" and not has_full:
            raise ApiError(
                409,
                "AVATAR_FULL_BODY_REFERENCE_REQUIRED",
                "Full replacement requires both a frontal face reference and a verified full-body reference",
                {"pending_inputs": ["avatar_full_body_reference"]},
            )
        reference_json = json.dumps(references, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)
        contract = {
            "schema_version": "custom-avatar-immutable-contract-v1",
            "avatar_id": avatar_id,
            "source_person_id": source_person_id,
            "character_mode": character_mode,
            "usage_scope": usage_scope,
            "authorized": True,
            "authorization_scope": record.get("authorization_scope"),
            "source_record_sha256": sha256_file(record_path),
            "reference_count": len(references),
            "reference_set_sha256": hashlib.sha256(reference_json.encode("utf-8")).hexdigest(),
            "references": references,
        }
        canonical = json.dumps(contract, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)
        contract["contract_sha256"] = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
        return contract

    def validate_custom_avatar_binding(self, project_id: str) -> Dict[str, Any]:
        project_dir = self.get_project_dir(project_id)
        state = self._load_state(project_dir)
        config = state.get("config") or {}
        avatar_id = str(config.get("avatar_id") or "")
        source_person_id = str(config.get("source_person_id") or "")
        character_mode = str(config.get("character_mode") or "")
        if not avatar_id or not source_person_id or character_mode not in {"head_replace", "full_replace"}:
            return {"status": "blocked", "code": "CUSTOM_AVATAR_BINDING_SELECTION_INCOMPLETE"}
        stored = read_json(project_dir / "planning" / "avatar_binding_lock.json", {})
        required_contract_fields = {
            "contract_sha256",
            "avatar_id",
            "source_person_id",
            "character_mode",
            "references",
        }
        if not isinstance(stored, dict) or not required_contract_fields.issubset(stored):
            return {
                "status": "blocked",
                "code": "CUSTOM_AVATAR_IMMUTABLE_CONTRACT_VIOLATION",
                "checks": {"immutable_contract_present": False},
            }
        # A project validates the exact snapshot copied when the user applied
        # the avatar. Later knowledge-library edits affect only a future apply;
        # they must not rewrite or redefine this project's frozen contract.
        expected = stored
        library = read_json(project_dir / "library" / "avatar_library.json", {})
        entry = next((item for item in library.get("avatars") or [] if isinstance(item, dict) and item.get("id") == avatar_id), None)
        binding = state.get("avatar_binding") or {}
        checks = {
            "contract_exact": self._contract_self_hash_matches(expected),
            "contract_avatar_exact": expected.get("avatar_id") == avatar_id,
            "contract_source_person_exact": expected.get("source_person_id") == source_person_id,
            "contract_character_mode_exact": expected.get("character_mode") == character_mode,
            "library_contract_sha_exact": bool(entry and entry.get("immutable_contract_sha256") == expected["contract_sha256"]),
            "library_reference_set_exact": bool(entry and entry.get("reference_records") == expected["references"]),
            "binding_contract_sha_exact": binding.get("immutable_contract_sha256") == expected["contract_sha256"],
            "binding_source_person_exact": binding.get("source_person_id") == source_person_id,
            "binding_character_mode_exact": binding.get("character_mode") == character_mode,
        }
        for reference in expected["references"]:
            try:
                copied = safe_join(project_dir, str(reference["project_path"]))
                valid = copied.is_file() and copied.stat().st_size == reference["size"] and sha256_file(copied) == reference["sha256"]
            except (ApiError, OSError):
                valid = False
            checks["reference_%s_exact" % reference["id"]] = valid
        immutable_valid = all(checks.values())
        current_record = self._raw_custom_knowledge("avatars", avatar_id)
        authorization_current = bool(current_record and current_record.get("authorized") is True)
        if immutable_valid and not authorization_current:
            return {
                "status": "blocked",
                "code": "PORTRAIT_AUTHORIZATION_REVOKED",
                "contract_sha256": expected["contract_sha256"],
                "checks": checks,
                "authorization_current": False,
            }
        return {
            "status": "ready" if immutable_valid else "blocked",
            "code": None if immutable_valid else "CUSTOM_AVATAR_IMMUTABLE_CONTRACT_VIOLATION",
            "contract_sha256": expected["contract_sha256"],
            "checks": checks,
            "authorization_current": authorization_current,
        }

    def _apply_avatar_binding(self, project_dir: Path, state: Dict[str, Any], avatar_id: Optional[str], transaction_id: str) -> Dict[str, Any]:
        config = state.get("config") or {}
        raw_path = project_dir / "project.json"
        raw = read_json(raw_path, {})
        if config.get("character_mode") == "preserve":
            binding = {
                "status": "ready",
                "selected_id": None,
                "applied_id": None,
                "transaction_id": transaction_id,
                "source": "preserve_source_character",
                "source_person_id": None,
                "character_mode": "preserve",
                "updated_at": now_iso(),
            }
            raw["avatar_binding"] = binding
            atomic_write_json(raw_path, raw)
            self._write_avatar_binding_plan(project_dir, binding)
            state["avatar_binding"] = binding
            return binding

        try:
            source_person_id, known_source_people = self._selected_source_person(project_dir, config)
        except ApiError as exc:
            binding = {
                "status": "waiting",
                "code": exc.code,
                "selected_id": avatar_id,
                "applied_id": None,
                "source_person_id": config.get("source_person_id"),
                "allowed_source_person_ids": self._known_source_person_ids(project_dir),
                "character_mode": config.get("character_mode"),
                "transaction_id": transaction_id,
                "updated_at": now_iso(),
            }
            self._write_avatar_binding_plan(project_dir, binding)
            return binding
        if not source_person_id and len(known_source_people) > 1:
            binding = {
                "status": "waiting",
                "code": "SOURCE_PERSON_SELECTION_REQUIRED",
                "selected_id": avatar_id,
                "applied_id": None,
                "source_person_id": None,
                "allowed_source_person_ids": known_source_people,
                "character_mode": config.get("character_mode"),
                "transaction_id": transaction_id,
                "updated_at": now_iso(),
            }
            self._write_avatar_binding_plan(project_dir, binding)
            return binding
        if not source_person_id:
            binding = {
                "status": "waiting",
                "code": "SOURCE_PERSON_SELECTION_REQUIRED",
                "selected_id": avatar_id,
                "applied_id": None,
                "source_person_id": None,
                "allowed_source_person_ids": known_source_people,
                "character_mode": config.get("character_mode"),
                "transaction_id": transaction_id,
                "updated_at": now_iso(),
            }
            self._write_avatar_binding_plan(project_dir, binding)
            return binding
        if source_person_id and not config.get("source_person_id"):
            config["source_person_id"] = source_person_id
            state["config"] = config
        if not avatar_id:
            return {"status": "waiting", "code": "AVATAR_REFERENCE_REQUIRED", "selected_id": None, "applied_id": None, "source_person_id": source_person_id, "updated_at": now_iso()}
        public = self.find_knowledge("avatars", avatar_id, required=False)
        if not public:
            return {"status": "waiting", "code": "AVATAR_REFERENCE_NOT_FOUND", "selected_id": avatar_id, "applied_id": None, "source_person_id": source_person_id, "updated_at": now_iso()}
        if public.get("authorized") is not True:
            return {"status": "waiting", "code": "PORTRAIT_AUTHORIZATION_REQUIRED", "selected_id": avatar_id, "applied_id": None, "source_person_id": source_person_id, "updated_at": now_iso()}
        if public.get("source") == "custom":
            usage_scope = public.get("usage_scope")
            scope_allowed = (
                usage_scope == "head_and_full"
                or (usage_scope == "head_only" and config.get("character_mode") == "head_replace")
                or (usage_scope == "full_only" and config.get("character_mode") == "full_replace")
            )
            if not scope_allowed:
                binding = {
                    "status": "waiting",
                    "code": "AVATAR_USAGE_SCOPE_MISMATCH",
                    "selected_id": avatar_id,
                    "applied_id": None,
                    "source_person_id": source_person_id,
                    "usage_scope": usage_scope,
                    "character_mode": config.get("character_mode"),
                    "transaction_id": transaction_id,
                    "updated_at": now_iso(),
                }
                self._write_avatar_binding_plan(project_dir, binding)
                return binding

        destination_root = project_dir / "source" / "references" / "avatars" / avatar_id
        destination_root.mkdir(parents=True, exist_ok=True)
        reference_assets: Dict[str, Any] = {}
        avatar_contract: Optional[Dict[str, Any]] = None
        source_name = str(public.get("source") or "custom")
        if source_name == "builtin":
            source_library = read_json(self.toolchain.skill_dir / "assets" / "project-template" / "library" / "avatar_library.json", {})
            source_avatar = next((item for item in source_library.get("avatars") or [] if item.get("id") == avatar_id), None)
            if not source_avatar:
                raise ApiError(409, "BUILTIN_AVATAR_RECORD_MISSING", "Built-in avatar metadata is missing")
            for role, raw_paths in (source_avatar.get("reference_assets") or {}).items():
                paths = raw_paths if isinstance(raw_paths, list) else [raw_paths]
                copied_paths = []
                for index, raw_relative in enumerate(paths):
                    if not isinstance(raw_relative, str) or not raw_relative:
                        continue
                    source = safe_join(self.toolchain.skill_dir, raw_relative)
                    if not source.is_file():
                        continue
                    destination = destination_root / ("%s-%02d%s" % (slugify(role, "reference"), index + 1, source.suffix.lower()))
                    shutil.copy2(source, destination)
                    copied_paths.append(destination.relative_to(project_dir).as_posix())
                reference_assets[role] = copied_paths if len(copied_paths) > 1 else (copied_paths[0] if copied_paths else None)
            entry = dict(source_avatar)
            entry["reference_assets"] = reference_assets
            entry["source_person_id"] = source_person_id
        else:
            custom = self._raw_custom_knowledge("avatars", avatar_id)
            if not custom:
                raise ApiError(409, "CUSTOM_AVATAR_RECORD_MISSING", "Custom avatar storage record is missing")
            try:
                avatar_contract = self._build_custom_avatar_contract(
                    project_dir,
                    avatar_id,
                    source_person_id,
                    str(config.get("character_mode")),
                )
            except ApiError as exc:
                binding = {
                    "status": "waiting",
                    "code": exc.code,
                    "selected_id": avatar_id,
                    "applied_id": None,
                    "source_person_id": source_person_id,
                    "character_mode": config.get("character_mode"),
                    "transaction_id": transaction_id,
                    "details": exc.details,
                    "pending_inputs": list((exc.details or {}).get("pending_inputs") or []),
                    "updated_at": now_iso(),
                }
                self._write_avatar_binding_plan(project_dir, binding)
                return binding
            for reference in avatar_contract["references"]:
                source = safe_join(self.knowledge_root / "avatars" / avatar_id, str(reference["source_filename"]))
                destination = safe_join(project_dir, str(reference["project_path"]))
                with source.open("rb") as handle:
                    copied_size, copied_sha256 = copy_stream_atomic(handle, destination, self.maximum_knowledge_bytes)
                if copied_size != reference["size"] or copied_sha256 != reference["sha256"]:
                    destination.unlink(missing_ok=True)
                    raise ApiError(500, "CUSTOM_AVATAR_COPY_HASH_MISMATCH", "Copied avatar reference failed size/SHA verification")
                role = slugify(str(reference.get("role") or reference.get("angle") or "reference"), "reference")
                relative = str(reference["project_path"])
                current = reference_assets.get(role)
                if current is None:
                    reference_assets[role] = relative
                elif isinstance(current, list):
                    current.append(relative)
                else:
                    reference_assets[role] = [current, relative]
            entry = {
                "id": avatar_id,
                "name": custom.get("name") or avatar_id,
                "active": True,
                "portrait_rights_cleared": custom.get("authorized") is True,
                "authorization_basis": custom.get("authorization_scope") or "workbench_custom_upload",
                "usage_scope": custom.get("usage_scope"),
                "identity_traits": [],
                "forbidden_changes": ["不得继承人物参考背景、服装、身体、场景、构图和光线"],
                "reference_assets": reference_assets,
                "reference_limitations": [custom.get("notes")] if custom.get("notes") else [],
                "source_person_id": source_person_id,
                "immutable_contract_path": "planning/avatar_binding_lock.json",
                "immutable_contract_sha256": avatar_contract["contract_sha256"],
                "reference_set_sha256": avatar_contract["reference_set_sha256"],
                "reference_records": avatar_contract["references"],
            }
            atomic_write_json(project_dir / "planning" / "avatar_binding_lock.json", avatar_contract)
        if not any(value for value in reference_assets.values()):
            return {"status": "waiting", "code": "AVATAR_REFERENCE_FILES_MISSING", "selected_id": avatar_id, "applied_id": None, "source_person_id": source_person_id, "updated_at": now_iso()}

        library_path = project_dir / "library" / "avatar_library.json"
        library = read_json(library_path, {"schema_version": "1.1", "version": 1, "avatars": []})
        avatars = [item for item in library.get("avatars") or [] if isinstance(item, dict) and item.get("id") != avatar_id]
        avatars.append(entry)
        library["avatars"] = avatars
        library["version"] = max(int(library.get("version") or 1), 1)
        binding = {
            "status": "ready",
            "selected_id": avatar_id,
            "applied_id": avatar_id,
            "transaction_id": transaction_id,
            "source": source_name,
            "character_mode": config.get("character_mode"),
            "source_person_id": source_person_id,
            "reference_count": len(public.get("media_urls") or []),
            "updated_at": now_iso(),
        }
        if avatar_contract is not None:
            binding.update(
                {
                    "immutable_contract_path": "planning/avatar_binding_lock.json",
                    "immutable_contract_sha256": avatar_contract["contract_sha256"],
                    "reference_set_sha256": avatar_contract["reference_set_sha256"],
                    "reference_ids": [reference["id"] for reference in avatar_contract["references"]],
                }
            )
        raw["avatar_binding"] = binding
        raw["source_person_id"] = source_person_id
        raw["updated_at"] = now_iso()
        atomic_write_json(library_path, library)
        atomic_write_json(raw_path, raw)
        self._write_avatar_binding_plan(project_dir, binding)
        state["avatar_binding"] = binding
        return binding

    def apply_bindings(self, project_id: str) -> Dict[str, Any]:
        project_dir = self.get_project_dir(project_id)
        with self._lock:
            state = self._load_state(project_dir)
            config = state.get("config") or _default_config()
            transaction_id = new_id("binding")
            product_result: Dict[str, Any]
            task: Optional[Dict[str, Any]] = None
            if config.get("product_mode") == "preserve":
                product_result = self._apply_preserve_product(project_dir, state, transaction_id)
            else:
                product_id = config.get("product_id")
                if not product_id:
                    product_result = {"status": "waiting", "code": "PRODUCT_REFERENCE_REQUIRED", "selected_id": None, "applied_id": None, "updated_at": now_iso()}
                else:
                    product = self.find_knowledge("products", str(product_id), required=False)
                    if not product:
                        product_result = {"status": "waiting", "code": "PRODUCT_REFERENCE_NOT_FOUND", "selected_id": product_id, "applied_id": None, "updated_at": now_iso()}
                    elif product.get("source") == "builtin":
                        product_result = self._apply_builtin_product(project_dir, state, str(product_id), transaction_id)
                    else:
                        try:
                            immutable_contract = self._materialize_custom_product_contract(
                                project_dir,
                                str(product_id),
                                transaction_id,
                            )
                        except ApiError as exc:
                            product_result = {
                                "status": "waiting",
                                "code": exc.code,
                                "selected_id": product_id,
                                "applied_id": None,
                                "transaction_id": transaction_id,
                                "source": "custom",
                                "pending_inputs": list((exc.details or {}).get("pending_inputs") or []),
                                "details": exc.details,
                                "updated_at": now_iso(),
                            }
                        else:
                            if (config.get("codex") or {}).get("enabled") is True:
                                product_result = {
                                    "status": "applying_with_codex",
                                    "selected_id": product_id,
                                    "applied_id": None,
                                    "transaction_id": transaction_id,
                                    "source": "custom",
                                    "immutable_contract_path": "library/product_immutable_contract.json",
                                    "immutable_contract_sha256": immutable_contract["contract_sha256"],
                                    "updated_at": now_iso(),
                                }
                            else:
                                product_result = {
                                    "status": "waiting",
                                    "code": "CUSTOM_PRODUCT_REQUIRES_CODEX_BINDING",
                                    "selected_id": product_id,
                                    "applied_id": None,
                                    "transaction_id": transaction_id,
                                    "source": "custom",
                                    "immutable_contract_path": "library/product_immutable_contract.json",
                                    "immutable_contract_sha256": immutable_contract["contract_sha256"],
                                    "pending_inputs": ["enable_project_codex"],
                                    "updated_at": now_iso(),
                                }
                        state["product_binding"] = product_result
                        state["product_binding_status"] = product_result["status"]
            avatar_result = self._apply_avatar_binding(project_dir, state, config.get("avatar_id"), transaction_id)
            state["avatar_binding"] = avatar_result
            state["updated_at"] = now_iso()
            atomic_write_json(self._workbench_path(project_dir), state)
            raw_path = project_dir / "project.json"
            raw = read_json(raw_path, {})
            raw["product_binding"] = state.get("product_binding") or product_result
            raw["avatar_binding"] = avatar_result
            raw["updated_at"] = now_iso()
            atomic_write_json(raw_path, raw)
        if product_result.get("status") == "applying_with_codex":
            task = self.tasks.create_task({"project_id": project_id, "operation": "apply_binding"})
            task = self.tasks.start(task["id"])
        project = self.get_project(project_id)
        overall = (
            "applying"
            if task and task.get("status") in {"queued", "running"}
            else ("ready" if not task and product_result.get("status") == "ready" and avatar_result.get("status") == "ready" else "waiting")
        )
        return {
            "ok": True,
            "binding": {"status": overall, "transaction_id": transaction_id, "product": product_result, "avatar": avatar_result},
            "task": task,
            "project": project,
        }

    def validate_custom_product_binding(self, project_id: str) -> Dict[str, Any]:
        project_dir = self.get_project_dir(project_id)
        state = self._load_state(project_dir)
        product_id = (state.get("config") or {}).get("product_id")
        if not product_id:
            raise ApiError(409, "PRODUCT_REFERENCE_REQUIRED", "No custom product is selected")
        stored_contract = read_json(project_dir / "library" / "product_immutable_contract.json", {})
        required_contract_fields = {
            "contract_sha256",
            "product_id",
            "dimensions_cm",
            "package_spec",
            "package_present",
            "package_quantity",
            "box_topology",
            "text_layout",
            "references",
        }
        if not isinstance(stored_contract, dict) or not required_contract_fields.issubset(stored_contract):
            return {
                "status": "blocked",
                "code": "CUSTOM_PRODUCT_IMMUTABLE_CONTRACT_VIOLATION",
                "contract_path": "library/product_immutable_contract.json",
                "checks": {"immutable_contract_present": False},
            }
        # The stored contract and project-local copied assets are the immutable
        # authority after apply. Rebuilding expected facts from a newly edited
        # library record would make an old project silently follow the edit (or
        # fail on a harmless rename), defeating snapshot isolation.
        expected_contract = stored_contract
        product_bible = read_json(project_dir / "library" / "product_bible.json", {})
        product_library = read_json(project_dir / "library" / "product_library.json", {})
        library_entry = next(
            (item for item in product_library.get("products") or [] if isinstance(item, dict) and item.get("id") == product_id),
            None,
        )
        locked_fields = self._locked_product_bible_fields(expected_contract)
        binding_before_validation = state.get("product_binding") or {}
        checks = {
            "immutable_contract_exact": self._contract_self_hash_matches(expected_contract),
            "contract_product_id_exact": expected_contract.get("product_id") == product_id,
            "binding_contract_sha_exact": binding_before_validation.get("immutable_contract_sha256")
            == expected_contract["contract_sha256"],
            "profile_id_matches": product_bible.get("profile_id") == product_id,
            "dimensions_exact": product_bible.get("physical_dimensions_cm") == expected_contract["dimensions_cm"],
            "package_spec_exact": product_bible.get("packaging_contract") == expected_contract["package_spec"],
            "package_present_exact": product_bible.get("package_present") == expected_contract["package_present"],
            "package_quantity_exact": product_bible.get("package_quantity") == expected_contract["package_quantity"],
            "box_topology_exact": product_bible.get("box_topology") == expected_contract["box_topology"],
            "text_layout_exact": product_bible.get("text_layout") == expected_contract["text_layout"],
            "reference_contract_exact": product_bible.get("reference_assets") == expected_contract["references"],
            "contract_sha_exact": product_bible.get("immutable_contract_sha256") == expected_contract["contract_sha256"],
            "contract_path_exact": product_bible.get("immutable_contract_path") == locked_fields["immutable_contract_path"],
            "immutable_traits_present": isinstance(product_bible.get("immutable_traits"), list) and bool(product_bible.get("immutable_traits")),
            "state_profiles_present": isinstance(product_bible.get("state_profiles"), dict) and bool(product_bible.get("state_profiles")),
            "product_library_entry_present": library_entry is not None,
            "product_library_contract_exact": bool(
                library_entry
                and library_entry.get("immutable_contract_sha256") == expected_contract["contract_sha256"]
                and library_entry.get("reference_assets") == expected_contract["references"]
            ),
        }
        layered_packaging = expected_contract.get("schema_version") == "custom-product-immutable-contract-v2"
        if layered_packaging:
            checks.update(
                {
                    "packaging_contracts_exact": product_bible.get("packaging_contracts")
                    == expected_contract.get("packaging_contracts"),
                    "packaging_assets_exact": product_bible.get("packaging_assets")
                    == expected_contract.get("packaging_assets"),
                    "packaging_layers_present_exact": product_bible.get("packaging_layers_present")
                    == expected_contract.get("packaging_layers_present"),
                    "primary_packaging_layer_exact": product_bible.get("primary_packaging_layer")
                    == expected_contract.get("primary_packaging_layer"),
                    "product_library_packaging_exact": bool(
                        library_entry
                        and library_entry.get("packaging_contracts") == expected_contract.get("packaging_contracts")
                        and library_entry.get("packaging_assets") == expected_contract.get("packaging_assets")
                        and library_entry.get("packaging_layers_present") == expected_contract.get("packaging_layers_present")
                        and library_entry.get("primary_packaging_layer") == expected_contract.get("primary_packaging_layer")
                    ),
                }
            )
        for reference in expected_contract["references"]:
            try:
                copied = safe_join(project_dir, str(reference["project_path"]))
                valid = copied.is_file() and sha256_file(copied) == reference["sha256"]
            except (ApiError, OSError):
                valid = False
            checks["project_reference_%s_exact" % reference["id"]] = valid
        immutable_check_names = {
            "immutable_contract_exact",
            "contract_product_id_exact",
            "binding_contract_sha_exact",
            "dimensions_exact",
            "package_spec_exact",
            "package_present_exact",
            "package_quantity_exact",
            "box_topology_exact",
            "text_layout_exact",
            "reference_contract_exact",
            "contract_sha_exact",
            "contract_path_exact",
            "product_library_contract_exact",
            *[name for name in checks if name.startswith("project_reference_")],
        }
        if layered_packaging:
            immutable_check_names.update(
                {
                    "packaging_contracts_exact",
                    "packaging_assets_exact",
                    "packaging_layers_present_exact",
                    "primary_packaging_layer_exact",
                    "product_library_packaging_exact",
                }
            )
        if not all(checks.get(name) for name in immutable_check_names):
            return {
                "status": "blocked",
                "code": "CUSTOM_PRODUCT_IMMUTABLE_CONTRACT_VIOLATION",
                "contract_path": "library/product_immutable_contract.json",
                "expected_contract_sha256": expected_contract["contract_sha256"],
                "checks": checks,
            }
        if not all(checks.values()):
            return {"status": "blocked", "code": "CUSTOM_PRODUCT_BINDING_NOT_MATERIALIZED", "checks": checks}
        transaction_id = (state.get("product_binding") or {}).get("transaction_id") or new_id("binding")
        binding = {
            "status": "ready",
            "selected_id": product_id,
            "applied_id": product_id,
            "transaction_id": transaction_id,
            "source": "custom_codex_compiled",
            "immutable_contract_path": "library/product_immutable_contract.json",
            "immutable_contract_sha256": expected_contract["contract_sha256"],
            "updated_at": now_iso(),
        }
        state["product_binding"] = binding
        state["product_binding_status"] = "ready"
        state["updated_at"] = now_iso()
        raw_path = project_dir / "project.json"
        raw = read_json(raw_path, {})
        raw.update({"product_mode": "replace_product", "product_profile": product_id, "product_binding": binding, "updated_at": now_iso()})
        atomic_write_json(raw_path, raw)
        atomic_write_json(self._workbench_path(project_dir), state)
        return {"status": "ready", "binding": binding, "checks": checks}

    def get_script(self, project_id: str) -> Dict[str, Any]:
        project_dir = self.get_project_dir(project_id)
        return {"ok": True, "script": self._project_script(project_dir)}

    def save_script_payload(self, project_id: str, payload: Dict[str, Any]) -> Dict[str, Any]:
        project_dir = self.get_project_dir(project_id)
        previous_input = self._generation_input_snapshot(project_dir)
        if not isinstance(payload, dict):
            raise ApiError(400, "INVALID_SCRIPT", "Script payload must be a JSON object")
        allowed = {"source_text", "revised_text", "active_source", "locked", "language", "shot_mapping"}
        unknown = sorted(set(payload) - allowed)
        if unknown:
            raise ApiError(400, "UNKNOWN_SCRIPT_FIELDS", "Script contains unsupported fields", {"fields": unknown})
        previous = dict(self._project_script(project_dir))
        record = dict(previous)
        for key, value in payload.items():
            record[key] = value
        for key in ("source_text", "revised_text"):
            if not isinstance(record.get(key), str):
                raise ApiError(400, "INVALID_SCRIPT_TEXT", "%s must be text" % key)
        if record.get("active_source") == "source_text":
            record["active_source"] = "source"
        elif record.get("active_source") == "revised_text":
            record["active_source"] = "revised"
        if record.get("active_source") not in {"source", "revised"}:
            raise ApiError(400, "INVALID_ACTIVE_SCRIPT", "active_source must be source or revised")
        if not isinstance(record.get("locked"), bool):
            raise ApiError(400, "INVALID_SCRIPT_LOCK", "locked must be true or false")
        language = str(record.get("language") or "zh-CN").strip()
        if not re.fullmatch(r"[A-Za-z]{2,3}(?:-[A-Za-z0-9]{2,8})?", language):
            raise ApiError(400, "INVALID_SCRIPT_LANGUAGE", "language must be a BCP-47-like language tag")
        record["language"] = language
        if not isinstance(record.get("shot_mapping"), (dict, list)):
            raise ApiError(400, "INVALID_SHOT_MAPPING", "shot_mapping must be an object or array")
        material_fields = ("source_text", "revised_text", "active_source", "shot_mapping")
        material_changed = any(previous.get(key) != record.get(key) for key in material_fields)
        explicitly_relocked = payload.get("locked") is True
        if material_changed and not explicitly_relocked:
            # A prior true value inherited during merge must not survive edits.
            record["locked"] = False
        active_text = record["revised_text"] if record["active_source"] == "revised" else record["source_text"]
        if record["locked"] and not active_text.strip():
            raise ApiError(400, "EMPTY_LOCKED_SCRIPT", "The active script cannot be empty when locked")
        active_sha256 = hashlib.sha256(active_text.encode("utf-8")).hexdigest()
        if record["locked"]:
            record["confirmed_sha256"] = active_sha256
            record["confirmed_at"] = now_iso()
            record["confirmed_active_source"] = record["active_source"]
        else:
            record["confirmed_sha256"] = None
            record["confirmed_at"] = None
            record["confirmed_active_source"] = None
        record["schema_version"] = "workbench-script-v1"
        record["effective_characters"] = len(re.sub(r"[\s\W_]+", "", active_text, flags=re.UNICODE))
        record["updated_at"] = now_iso()
        atomic_write_json(project_dir / "planning" / "workbench_script.json", record)
        canonical_lock_path = project_dir / "planning" / "revised_script_lock.json"
        canonical_lock = read_json(canonical_lock_path, {})
        if not isinstance(canonical_lock, dict):
            canonical_lock = {}
        canonical_lock.update(
            {
                "schema_version": canonical_lock.get("schema_version") or "revised-script-lock-v1.0",
                "status": "locked" if record["locked"] else "pending_relock",
                "active_source": record["active_source"],
                "text": active_text,
                "text_sha256": active_sha256,
                "shot_mapping": record["shot_mapping"],
                "confirmed_at": record["confirmed_at"],
                "updated_at": now_iso(),
            }
        )
        atomic_write_json(canonical_lock_path, canonical_lock)
        if (previous.get("locked") is True and (material_changed or record["locked"] is not True)):
            workflow_path = project_dir / "planning" / "workflow_state.json"
            workflow = read_json(workflow_path, {})
            if not isinstance(workflow, dict):
                workflow = {}
            pending_inputs = [str(value) for value in workflow.get("pending_inputs") or []]
            if "locked_revised_script" not in pending_inputs:
                pending_inputs.append("locked_revised_script")
            workflow.update(
                {
                    "status": "script_changed_pending_recompile" if record["locked"] else "script_changed_pending_relock",
                    "docx_export_authorized": False,
                    "script_lock_stale": True,
                    "pending_inputs": pending_inputs,
                    "updated_at": now_iso(),
                }
            )
            atomic_write_json(workflow_path, workflow)
            alignment_path = project_dir / "review" / "alignment_manifest.json"
            alignment = read_json(alignment_path, {})
            if not isinstance(alignment, dict):
                alignment = {}
            alignment.update({"status": "stale", "stale_reason": "script_lock_changed", "updated_at": now_iso()})
            atomic_write_json(alignment_path, alignment)
        state = self._load_state(project_dir)
        config = dict(state.get("config") or _default_config())
        config["script_locked"] = record["locked"]
        state["config"] = self._validated_config(config)
        state["updated_at"] = now_iso()
        atomic_write_json(self._workbench_path(project_dir), state)
        invalidation = None
        if material_changed:
            invalidation = self._invalidate_derived_dependencies(
                project_dir,
                "ACTIVE_SCRIPT_CHANGED",
                [key for key in material_fields if previous.get(key) != record.get(key)],
                previous_input=previous_input,
            )
        return {"ok": True, "script": record, "project": self.get_project(project_id), "invalidation": invalidation}

    def save_script(self, project_id: str, text: str, locked: bool = False) -> Dict[str, Any]:
        return self.save_script_payload(
            project_id,
            {"revised_text": text, "active_source": "revised", "locked": bool(locked)},
        )

    def add_marker(self, project_id: str, payload: Dict[str, Any]) -> Dict[str, Any]:
        project_dir = self.get_project_dir(project_id)
        previous_input = self._generation_input_snapshot(project_dir)
        kind = str(payload.get("kind") or "")
        if kind not in {"eating", "breaking"}:
            raise ApiError(400, "INVALID_MARKER_KIND", "kind must be eating or breaking")
        try:
            timestamp = float(payload.get("time"))
        except (TypeError, ValueError):
            raise ApiError(400, "INVALID_MARKER_TIME", "time must be a number of seconds")
        if timestamp < 0:
            raise ApiError(400, "INVALID_MARKER_TIME", "time cannot be negative")
        state = self._load_state(project_dir)
        duration = (((state.get("video") or {}).get("metadata") or {}).get("duration"))
        if duration is not None and timestamp > float(duration) + 0.001:
            raise ApiError(400, "MARKER_OUTSIDE_VIDEO", "Marker time is beyond the uploaded video duration")
        marker_shot_id = validate_identifier(str(payload["shot_id"]), "shot id") if payload.get("shot_id") else None
        if marker_shot_id:
            flat_shots, _ = self._project_shot_data(project_dir)
            owner = next((value for value in flat_shots if str(value.get("id") or "") == marker_shot_id), None)
            if owner is None:
                raise ApiError(400, "MARKER_SHOT_NOT_FOUND", "Marker shot_id is not a current delivery unit")
            timecode = owner.get("timeline_timecode") or owner.get("timecode") or {}
            try:
                owner_start = float(timecode.get("start"))
                owner_end = float(timecode.get("end"))
            except (TypeError, ValueError):
                raise ApiError(409, "MARKER_SHOT_TIMECODE_MISSING", "Marker shot has no deterministic time range")
            if timestamp < owner_start - 0.001 or timestamp > owner_end + 0.001:
                raise ApiError(
                    400,
                    "MARKER_OUTSIDE_SHOT",
                    "Marker time must fall inside its selected delivery unit",
                    {"shot_id": marker_shot_id, "start": owner_start, "end": owner_end},
                )
        marker = {
            "id": new_id("marker"),
            "kind": kind,
            "time": round(timestamp, 3),
            "shot_id": marker_shot_id,
            "note": str(payload.get("note") or "").strip()[:1000] or None,
            "created_at": now_iso(),
        }
        path = project_dir / "planning" / "manual_markers.json"
        value = read_json(path, {"schema_version": "workbench-manual-markers-v1", "markers": []})
        value.setdefault("markers", []).append(marker)
        value["updated_at"] = now_iso()
        atomic_write_json(path, value)
        invalidation = self._invalidate_derived_dependencies(
            project_dir,
            "MANUAL_MARKER_CHANGED",
            ("manual_markers",),
            previous_input=previous_input,
        )
        return {"ok": True, "marker": marker, "markers": value["markers"], "invalidation": invalidation}

    def get_markers(self, project_id: str) -> Dict[str, Any]:
        project_dir = self.get_project_dir(project_id)
        return {"ok": True, "markers": self._project_markers(project_dir)}

    def create_shot_split_plan(self, project_id: str, unit_id: str, payload: Dict[str, Any]) -> Dict[str, Any]:
        project_dir = self.get_project_dir(project_id)
        validate_identifier(unit_id, "unit id")
        flat_shots, groups = self._project_shot_data(project_dir)
        flat = next((item for item in flat_shots if str(item.get("id")) == unit_id), None)
        if not flat:
            raise ApiError(404, "SHOT_UNIT_NOT_FOUND", "Shot unit was not found")
        try:
            cursor_time = float(payload.get("cursor_time"))
        except (TypeError, ValueError):
            raise ApiError(400, "INVALID_SPLIT_CURSOR", "cursor_time must be a number of seconds")
        timeline = flat.get("timeline_timecode") or flat.get("timecode") or {}
        try:
            timeline_start = float(timeline.get("start"))
            timeline_end = float(timeline.get("end"))
        except (TypeError, ValueError):
            raise ApiError(409, "SHOT_TIMECODE_MISSING", "The selected unit has no deterministic time range")
        if cursor_time <= timeline_start + 0.001 or cursor_time >= timeline_end - 0.001:
            raise ApiError(
                400,
                "SPLIT_CURSOR_OUTSIDE_UNIT",
                "cursor_time must stay strictly inside the selected unit",
                {"start": timeline_start, "end": timeline_end},
            )
        labels = payload.get("labels") or ["前半段", "后半段"]
        if not isinstance(labels, list) or len(labels) != 2:
            raise ApiError(400, "INVALID_SPLIT_LABELS", "labels must contain exactly two labels")
        clean_labels = [str(value).strip() for value in labels]
        if any(not value or len(value) > 120 for value in clean_labels):
            raise ApiError(400, "INVALID_SPLIT_LABELS", "Each split label must contain 1-120 characters")
        reason = str(payload.get("reason") or "").strip()
        if len(reason) > 2000:
            raise ApiError(400, "SPLIT_REASON_TOO_LONG", "reason cannot exceed 2000 characters")

        parent_id = str(flat.get("parent_shot_id") or "")
        parent = next((item for item in groups if isinstance(item, dict) and str(item.get("id")) == parent_id), None)
        if not parent:
            raise ApiError(409, "SHOT_PARENT_NOT_FOUND", "The canonical parent shot was not found")
        collection = "source_units" if flat.get("unit_type") == "source" else "inserted_units"
        identifier_key = "source_shot_id" if collection == "source_units" else "inserted_shot_id"
        canonical_unit = next(
            (item for item in parent.get(collection) or [] if isinstance(item, dict) and str(item.get(identifier_key) or item.get("id")) == unit_id),
            None,
        )
        if not canonical_unit:
            raise ApiError(409, "CANONICAL_SHOT_UNIT_NOT_FOUND", "The selected flattened unit no longer exists in the manifest")
        timecode_key = "source_timecode" if collection == "source_units" else "generation_timecode"
        raw_timecode = canonical_unit.get(timecode_key) or {}
        try:
            raw_start = float(raw_timecode.get("start"))
            raw_end = float(raw_timecode.get("end"))
        except (TypeError, ValueError):
            raise ApiError(409, "SHOT_TIMECODE_MISSING", "The canonical unit has no splittable time range")
        raw_cursor = cursor_time if collection == "source_units" else raw_start + (cursor_time - timeline_start)
        existing_ids = {str(item.get("id")) for item in flat_shots if item.get("id")}
        base = unit_id[:115]
        suffix_index = 1
        while True:
            first_id = "%s.a%s" % (base, "" if suffix_index == 1 else suffix_index)
            second_id = "%s.b%s" % (base, "" if suffix_index == 1 else suffix_index)
            if first_id not in existing_ids and second_id not in existing_ids:
                break
            suffix_index += 1
        validate_identifier(first_id, "proposed unit id")
        validate_identifier(second_id, "proposed unit id")
        manifest_path = project_dir / "shots" / "shot_manifest.json"
        unit_json = json.dumps(canonical_unit, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)
        plan_id = new_id("split")
        plan = {
            "schema_version": "workbench-shot-split-plan-v1",
            "id": plan_id,
            "project_id": project_id,
            "unit_id": unit_id,
            "parent_shot_id": parent_id,
            "collection": collection,
            "identifier_key": identifier_key,
            "timecode_key": timecode_key,
            "cursor_time": round(cursor_time, 6),
            "timeline_start": round(timeline_start, 6),
            "timeline_end": round(timeline_end, 6),
            "raw_cursor_time": round(raw_cursor, 6),
            "labels": clean_labels,
            "reason": reason or None,
            "input_shot_manifest_sha256": sha256_file(manifest_path),
            "input_unit_sha256": hashlib.sha256(unit_json.encode("utf-8")).hexdigest(),
            "proposed_units": [
                {"id": first_id, "label": clean_labels[0], "timecode": {"start": raw_start, "end": raw_cursor}},
                {"id": second_id, "label": clean_labels[1], "timecode": {"start": raw_cursor, "end": raw_end}},
            ],
            "status": "pending_confirmation",
            "created_at": now_iso(),
        }
        atomic_write_json(project_dir / "planning" / "split-plans" / (plan_id + ".json"), plan)
        return {"ok": True, "split_plan": plan, "canonical_changed": False}

    def confirm_shot_split_plan(self, project_id: str, unit_id: str, plan_id: str) -> Dict[str, Any]:
        project_dir = self.get_project_dir(project_id)
        previous_input = self._generation_input_snapshot(project_dir)
        validate_identifier(unit_id, "unit id")
        validate_identifier(plan_id, "split plan id")
        plan_path = project_dir / "planning" / "split-plans" / (plan_id + ".json")
        plan = read_json(plan_path)
        if not isinstance(plan, dict) or plan.get("unit_id") != unit_id or plan.get("project_id") != project_id:
            raise ApiError(409, "SPLIT_PLAN_OWNER_MISMATCH", "Split plan does not belong to this project unit")
        if plan.get("status") != "pending_confirmation":
            raise ApiError(409, "SPLIT_PLAN_NOT_PENDING", "Only a pending split plan can be confirmed")
        manifest_path = project_dir / "shots" / "shot_manifest.json"
        if sha256_file(manifest_path) != plan.get("input_shot_manifest_sha256"):
            raise ApiError(409, "SPLIT_PLAN_STALE", "Shot manifest changed after this split plan was created")
        manifest = read_json(manifest_path)
        groups = manifest.get("shots") if isinstance(manifest, dict) else None
        if not isinstance(groups, list):
            raise ApiError(500, "INVALID_SHOT_MANIFEST", "Shot manifest does not contain a shots array")
        parent = next((item for item in groups if isinstance(item, dict) and str(item.get("id")) == plan.get("parent_shot_id")), None)
        if not parent:
            raise ApiError(409, "SHOT_PARENT_NOT_FOUND", "The split parent shot no longer exists")
        collection = str(plan.get("collection"))
        identifier_key = str(plan.get("identifier_key"))
        timecode_key = str(plan.get("timecode_key"))
        units = parent.get(collection)
        if not isinstance(units, list):
            raise ApiError(409, "CANONICAL_SHOT_UNIT_NOT_FOUND", "The split unit collection no longer exists")
        unit_index = next(
            (index for index, item in enumerate(units) if isinstance(item, dict) and str(item.get(identifier_key) or item.get("id")) == unit_id),
            None,
        )
        if unit_index is None:
            raise ApiError(409, "CANONICAL_SHOT_UNIT_NOT_FOUND", "The split unit no longer exists")
        original = units[unit_index]
        original_json = json.dumps(original, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)
        if hashlib.sha256(original_json.encode("utf-8")).hexdigest() != plan.get("input_unit_sha256"):
            raise ApiError(409, "SPLIT_PLAN_STALE", "The selected unit changed after this split plan was created")
        replacements = []
        for proposed in plan.get("proposed_units") or []:
            value = dict(original)
            archived_source_context: Dict[str, Any] = {}
            for context_key in (
                "storyboard_description",
                "script_text",
                "transcript",
                "dialogue",
                "voice_over",
                "voiceover",
                "speech_text",
                "spoken_text",
            ):
                if context_key in value:
                    archived_source_context[context_key] = value.pop(context_key)
            for prompt_key in ("prompt", "prompt_text", "prompt_path", "prompt_hash", "compiled_prompt", "jimeng_prompt"):
                value.pop(prompt_key, None)
            for stale_semantic_key in (
                "emotion",
                "product_state",
                "narrative_role",
                "speech_transition",
                "speech_mode",
                "dialogue_status",
                "asset_links",
            ):
                value.pop(stale_semantic_key, None)
            value["semantic_tags"] = []
            value["action_beats"] = []
            value["script_segment_ids"] = []
            value["visual_type"] = "pending_semantic_reanalysis"
            value["semantic_reset_after_split"] = True
            value["requires_semantic_reanalysis"] = True
            value["script_mapping_status"] = "pending_reanalysis"
            value[identifier_key] = proposed["id"]
            value[timecode_key] = dict(proposed["timecode"])
            if archived_source_context:
                value["split_source_context"] = {
                    "status": "archived_evidence_not_active_direction",
                    "from_unit_id": unit_id,
                    "original_timecode": dict(original.get(timecode_key) or {}),
                    "fields": archived_source_context,
                }
            if collection == "source_units":
                value.pop("generation_timecode", None)
                value["generation_timecode_status"] = "pending_recompile_after_split"
            value["title"] = str(proposed["label"])
            value["delivery_asset_ids"] = []
            value["delivery_asset_roles"] = {}
            value["split_from_unit_id"] = unit_id
            value["split_plan_id"] = plan_id
            value["requires_regeneration"] = True
            replacements.append(value)
        if len(replacements) != 2:
            raise ApiError(500, "INVALID_SPLIT_PLAN", "Split plan must contain exactly two replacement units")
        backup = project_dir / "workbench" / "split-backups" / plan_id / "shot_manifest.json"
        backup.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(manifest_path, backup)
        state_path = self._workbench_path(project_dir)
        markers_path = project_dir / "planning" / "manual_markers.json"
        if state_path.is_file():
            shutil.copy2(state_path, backup.parent / "workbench_state.json")
        if markers_path.is_file():
            shutil.copy2(markers_path, backup.parent / "manual_markers.json")
        units[unit_index : unit_index + 1] = replacements
        manifest.setdefault("split_history", []).append(
            {"plan_id": plan_id, "source_unit_id": unit_id, "new_unit_ids": [item[identifier_key] for item in replacements], "confirmed_at": now_iso()}
        )
        manifest["updated_at"] = now_iso()
        atomic_write_json(manifest_path, manifest)
        new_unit_ids = [str(item[identifier_key]) for item in replacements]
        state = self._load_state(project_dir)
        config = dict(state.get("config") or _default_config())
        scope = dict(config.get("shot_scope") or {"mode": "all"})
        scope_rewrite = None
        if scope.get("mode") == "selected" and unit_id in {str(value) for value in scope.get("shot_ids") or []}:
            rewritten: List[str] = []
            for selected in scope.get("shot_ids") or []:
                if str(selected) == unit_id:
                    rewritten.extend(new_unit_ids)
                else:
                    rewritten.append(str(selected))
            scope_rewrite = {"from": unit_id, "to": new_unit_ids}
            config["shot_scope"] = {"mode": "selected", "shot_ids": list(dict.fromkeys(rewritten))}
            state["config"] = self._validated_config(config)
            state["updated_at"] = now_iso()
            atomic_write_json(state_path, state)
        marker_rebindings: List[Dict[str, Any]] = []
        semantic_markers_by_unit: Dict[str, List[Dict[str, Any]]] = {value: [] for value in new_unit_ids}
        marker_document = read_json(markers_path, {"schema_version": "workbench-manual-markers-v1", "markers": []})
        if isinstance(marker_document, dict) and isinstance(marker_document.get("markers"), list):
            for marker in marker_document["markers"]:
                if not isinstance(marker, dict):
                    continue
                try:
                    marker_time = float(marker.get("time"))
                except (TypeError, ValueError):
                    continue
                if marker_time < float(plan["cursor_time"]):
                    rebound_id = new_unit_ids[0]
                else:
                    rebound_id = new_unit_ids[1]
                marker_timeline_start = float(plan.get("timeline_start", (plan.get("proposed_units") or [{}])[0].get("timecode", {}).get("start", 0.0)))
                marker_timeline_end = float(plan.get("timeline_end", (plan.get("proposed_units") or [{}, {}])[-1].get("timecode", {}).get("end", marker_timeline_start)))
                within_original = marker_timeline_start - 0.001 <= marker_time <= marker_timeline_end + 0.001
                if within_original and (marker.get("shot_id") in {None, unit_id}):
                    prior_id = marker.get("shot_id")
                    marker["shot_id"] = rebound_id
                    marker["rebound_by_split_plan"] = plan_id
                    marker["updated_at"] = now_iso()
                    evidence = {
                        "marker_id": marker.get("id"),
                        "kind": marker.get("kind"),
                        "time": marker.get("time"),
                    }
                    semantic_markers_by_unit[rebound_id].append(evidence)
                    marker_rebindings.append({"marker_id": marker.get("id"), "kind": marker.get("kind"), "time": marker.get("time"), "from": prior_id, "to": rebound_id})
            if marker_rebindings:
                marker_document["updated_at"] = now_iso()
                atomic_write_json(markers_path, marker_document)
        for replacement in replacements:
            replacement_id = str(replacement[identifier_key])
            semantic_evidence = semantic_markers_by_unit.get(replacement_id) or []
            replacement["semantic_tags"] = list(
                dict.fromkeys(
                    str(value.get("kind"))
                    for value in semantic_evidence
                    if value.get("kind") in {"eating", "breaking"}
                )
            )
            replacement["semantic_marker_evidence"] = semantic_evidence
        # Re-write after marker-driven semantic assignment. The first write
        # established the structural split; this write binds only observable
        # marker evidence and never restores inherited parent action prose.
        atomic_write_json(manifest_path, manifest)
        invalidation = self._invalidate_derived_dependencies(
            project_dir,
            "SHOT_UNIT_SPLIT",
            ("shot_manifest", "shot_scope", "manual_markers"),
            previous_input=previous_input,
        )
        plan.update(
            {
                "status": "confirmed",
                "confirmed_at": now_iso(),
                "backup_path": backup.relative_to(project_dir).as_posix(),
                "output_shot_manifest_sha256": sha256_file(manifest_path),
                "scope_rewrite": scope_rewrite,
                "marker_rebindings": marker_rebindings,
                "invalidation_receipt_path": "workbench/input-invalidations/%s.json" % invalidation["id"],
            }
        )
        atomic_write_json(plan_path, plan)
        workflow_path = project_dir / "planning" / "workflow_state.json"
        workflow = read_json(workflow_path, {})
        if not isinstance(workflow, dict):
            workflow = {}
        pending_inputs = [str(value) for value in workflow.get("pending_inputs") or []]
        for value in ("recompile_split_shots", "regenerate_split_shot_assets", "rerun_detectors"):
            if value not in pending_inputs:
                pending_inputs.append(value)
        workflow.update(
            {
                "status": "shot_split_pending_regeneration",
                "docx_export_authorized": False,
                "pending_inputs": pending_inputs,
                "updated_at": now_iso(),
            }
        )
        atomic_write_json(workflow_path, workflow)
        return {"ok": True, "split_plan": plan, "shots": self._project_shot_data(project_dir)[0]}

    def save_story_plan(self, project_id: str, payload: Dict[str, Any]) -> Dict[str, Any]:
        if not isinstance(payload, dict):
            raise ApiError(400, "INVALID_STORY_PLAN", "Story plan must be a JSON object")
        project_dir = self.get_project_dir(project_id)
        previous_input = self._generation_input_snapshot(project_dir)
        previous_story = read_json(project_dir / "planning" / "story_plan.json", {})
        payload = dict(payload)
        payload["updated_at"] = now_iso()
        atomic_write_json(project_dir / "planning" / "story_plan.json", payload)
        previous_material = dict(previous_story) if isinstance(previous_story, dict) else {}
        previous_material.pop("updated_at", None)
        current_material = dict(payload)
        current_material.pop("updated_at", None)
        invalidation = None
        if previous_material != current_material:
            invalidation = self._invalidate_derived_dependencies(
                project_dir,
                "STORY_PLAN_CHANGED",
                ("story_plan",),
                previous_input=previous_input,
            )
        return {"ok": True, "story_plan": payload, "invalidation": invalidation}

    # ---- knowledge libraries ----------------------------------------------------

    def _built_in_products(self) -> List[Dict[str, Any]]:
        profiles_root = self.toolchain.skill_dir / "assets" / "profiles"
        records = []
        if not profiles_root.is_dir():
            return records
        for path in sorted(profiles_root.glob("*.json")):
            try:
                value = read_json(path)
            except ApiError:
                continue
            if not isinstance(value, dict) or not value.get("profile_id"):
                continue
            if not any(key in value for key in ("state_profiles", "physical_dimensions_cm", "scale_contract")):
                continue
            references = []
            for asset in value.get("reference_assets") or []:
                relative = asset.get("source_path") if isinstance(asset, dict) else None
                if not relative:
                    continue
                try:
                    available = safe_join(self.toolchain.skill_dir, str(relative)).is_file()
                except ApiError:
                    available = False
                if available:
                    references.append(
                        {
                            "id": str(asset.get("id") or "ref-%d" % (len(references) + 1)),
                            "role": asset.get("role"),
                            "media_url": "/api/v1/skill-media/%s" % quoted_path(str(relative)),
                        }
                    )
            media_urls = [item["media_url"] for item in references]
            preview_url = media_urls[0] if media_urls else None
            records.append(
                {
                    "id": str(value["profile_id"]),
                    "kind": "product",
                    "name": value.get("name") or value["profile_id"],
                    "version": value.get("version"),
                    "source": "builtin",
                    "authorized": True,
                    "preview_url": preview_url,
                    "media_url": preview_url,
                    "media_urls": media_urls,
                    "references": references,
                    "dimensions_cm": value.get("physical_dimensions_cm") or value.get("scale_contract"),
                    "package_spec": value.get("package_artwork") or value.get("packaging_contract"),
                    "requires_package_spec": bool((value.get("project_rule_overrides") or {}).get("packaging_visible")),
                    "usage_scope": "project_product_profile",
                    "supersedes_profile": value.get("supersedes_profile"),
                    "selectable": not (str(value["profile_id"]).endswith("-v1") and value.get("supersedes_profile") is None and "durian" in str(value["profile_id"])),
                }
            )
        return records

    def _built_in_avatars(self) -> List[Dict[str, Any]]:
        library_path = self.toolchain.skill_dir / "assets" / "project-template" / "library" / "avatar_library.json"
        if not library_path.is_file():
            return []
        value = read_json(library_path, {})
        result = []
        for avatar in value.get("avatars") or []:
            if not isinstance(avatar, dict) or not avatar.get("id"):
                continue
            refs = avatar.get("reference_assets") or {}
            references = []
            seen_paths = set()
            for role, item in refs.items():
                paths = item if isinstance(item, list) else [item]
                for path_value in paths:
                    if not isinstance(path_value, str) or not path_value or path_value in seen_paths:
                        continue
                    seen_paths.add(path_value)
                    references.append(
                        {
                            "id": "ref-%d" % (len(references) + 1),
                            "role": role,
                            "media_url": "/api/v1/skill-media/%s" % quoted_path(path_value),
                        }
                    )
            media_urls = [item["media_url"] for item in references]
            preview = media_urls[0] if media_urls else None
            result.append(
                {
                    "id": str(avatar["id"]),
                    "kind": "avatar",
                    "name": avatar.get("name") or avatar["id"],
                    "source": "builtin",
                    "authorized": avatar.get("portrait_rights_cleared") is True,
                    "active": avatar.get("active") is True,
                    "preview_url": preview,
                    "media_url": preview,
                    "media_urls": media_urls,
                    "references": references,
                    "identity_traits": avatar.get("identity_traits") or [],
                    "forbidden_changes": avatar.get("forbidden_changes") or [],
                    "reference_limitations": avatar.get("reference_limitations") or [],
                    "usage_scope": avatar.get("usage_scope"),
                }
            )
        return result

    def _custom_records(self, kind: str) -> List[Dict[str, Any]]:
        directory = self.knowledge_root / kind
        result = []
        for record_path in directory.glob("*/record.json"):
            try:
                record = read_json(record_path)
            except ApiError:
                continue
            if isinstance(record, dict):
                result.append(self._public_knowledge_record(record))
        result.sort(key=lambda item: item.get("created_at") or "", reverse=True)
        return result

    def _public_knowledge_record(self, record: Dict[str, Any]) -> Dict[str, Any]:
        value = dict(record)
        kind = str(value["storage_kind"])
        asset_id = str(value["id"])
        value["revision"] = int(value.get("revision") or 1)
        references = list(value.get("references") or [])
        if not references and value.get("filename"):
            references = [
                {
                    "id": "ref-legacy",
                    "filename": value.get("filename"),
                    "size": value.get("size"),
                    "sha256": value.get("sha256"),
                    "role": None,
                    "label": None,
                }
            ]
        public_references = []
        for reference in references:
            reference_value = dict(reference)
            filename = str(reference_value["filename"])
            reference_value["media_url"] = "/api/v1/knowledge/%s/%s/media/%s" % (kind, asset_id, quoted_path(filename))
            reference_value.pop("_packaging_layer_explicit", None)
            public_references.append(reference_value)
        value["references"] = public_references
        value["media_urls"] = [item["media_url"] for item in public_references]
        value["media_url"] = value["media_urls"][0] if value["media_urls"] else None
        value["preview_url"] = value["media_url"]
        if kind == "products":
            public_packaging_assets: Dict[str, List[Dict[str, Any]]] = {}
            for raw_reference, reference in zip(references, public_references):
                layer = self._reference_packaging_layer(raw_reference)
                if layer:
                    public_packaging_assets.setdefault(layer, []).append(dict(reference))
            value["packaging_assets"] = {
                layer: public_packaging_assets[layer]
                for layer in PACKAGING_LAYERS
                if public_packaging_assets.get(layer)
            }
            contracts = value.get("packaging_contracts")
            value["packaging_contracts"] = contracts if isinstance(contracts, dict) else None
            value["packaging_layers"] = [
                layer
                for layer in PACKAGING_LAYERS
                if (isinstance(contracts, dict) and layer in contracts) or public_packaging_assets.get(layer)
            ]
        value["kind"] = "product" if kind == "products" else "avatar"
        value.pop("storage_kind", None)
        return value

    def list_knowledge(self) -> Dict[str, Any]:
        with self._lock:
            return {
                "ok": True,
                "products": self._built_in_products() + self._custom_records("products"),
                "avatars": self._built_in_avatars() + self._custom_records("avatars"),
            }

    def find_knowledge(self, kind: str, asset_id: str, required: bool = True) -> Optional[Dict[str, Any]]:
        validate_identifier(asset_id, "knowledge id")
        collection = self.list_knowledge()[kind]
        result = next((item for item in collection if item.get("id") == asset_id), None)
        if result is None and required:
            raise ApiError(404, "KNOWLEDGE_ASSET_NOT_FOUND", "Knowledge asset was not found")
        return result

    @staticmethod
    def _validate_knowledge_dimensions(value: Any) -> Optional[Dict[str, Any]]:
        if value is None:
            return None
        if not isinstance(value, dict) or not value:
            raise ApiError(400, "INVALID_PRODUCT_DIMENSIONS", "dimensions_cm must be a non-empty JSON object or null")
        normalized: Dict[str, Any] = {}
        for raw_name, raw_value in value.items():
            name = str(raw_name).strip()
            if (
                not name
                or len(name) > 100
                or isinstance(raw_value, bool)
                or not isinstance(raw_value, (int, float))
                or not math.isfinite(float(raw_value))
                or float(raw_value) <= 0
            ):
                raise ApiError(
                    400,
                    "INVALID_PRODUCT_DIMENSION",
                    "Every dimensions_cm value must be a positive finite number",
                    {"dimension": name or str(raw_name)},
                )
            normalized[name] = raw_value
        return normalized

    @staticmethod
    def _knowledge_text(value: Any, field: str, maximum: int, allow_empty: bool = True) -> Optional[str]:
        if value is None:
            if not allow_empty:
                raise ApiError(400, "INVALID_KNOWLEDGE_TEXT", "%s cannot be empty" % field, {"field": field})
            return None
        if not isinstance(value, str):
            raise ApiError(400, "INVALID_KNOWLEDGE_TEXT", "%s must be text or null" % field, {"field": field})
        normalized = value.strip()
        if len(normalized) > maximum or any(ord(character) < 32 and character not in "\n\r\t" for character in normalized):
            raise ApiError(
                400,
                "INVALID_KNOWLEDGE_TEXT",
                "%s exceeds its length limit or contains control characters" % field,
                {"field": field, "maximum": maximum},
            )
        if not allow_empty and not normalized:
            raise ApiError(400, "INVALID_KNOWLEDGE_TEXT", "%s cannot be empty" % field, {"field": field})
        return normalized or None

    @staticmethod
    def _contract_self_hash_matches(contract: Dict[str, Any]) -> bool:
        expected = str(contract.get("contract_sha256") or "")
        if not re.fullmatch(r"[a-f0-9]{64}", expected):
            return False
        material = dict(contract)
        material.pop("contract_sha256", None)
        try:
            canonical = json.dumps(material, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)
        except (TypeError, ValueError):
            return False
        return hashlib.sha256(canonical.encode("utf-8")).hexdigest() == expected

    def _knowledge_binding_usage(self, kind: str, asset_id: str) -> List[Dict[str, Any]]:
        usages: List[Dict[str, Any]] = []
        selection_field = "product_id" if kind == "products" else "avatar_id"
        binding_field = "product_binding" if kind == "products" else "avatar_binding"
        for project_dir in sorted(self.projects_root.iterdir()) if self.projects_root.is_dir() else []:
            if not project_dir.is_dir():
                continue
            state_path = self._workbench_path(project_dir)
            if not state_path.is_file():
                continue
            try:
                state = read_json(state_path, {})
            except ApiError:
                continue
            if not isinstance(state, dict):
                continue
            config = state.get("config") or {}
            binding = state.get(binding_field) or {}
            selected = config.get(selection_field) == asset_id or binding.get("selected_id") == asset_id
            applied = binding.get("applied_id") == asset_id
            staged = binding.get("immutable_contract_sha256") not in (None, "") and binding.get("selected_id") == asset_id
            if selected or applied or staged:
                usages.append(
                    {
                        "project_id": str(state.get("id") or project_dir.name),
                        "project_name": state.get("name") or project_dir.name,
                        "binding_status": binding.get("status"),
                        "selected": bool(selected),
                        "applied": bool(applied),
                        "immutable_snapshot_preserved": bool(applied or staged),
                    }
                )
        return usages

    def update_knowledge(self, kind: str, asset_id: str, payload: Dict[str, Any]) -> Dict[str, Any]:
        """Patch editable metadata without mutating registered media bytes or frozen project contracts."""
        if kind not in {"products", "avatars"}:
            raise ApiError(404, "UNKNOWN_KNOWLEDGE_KIND", "Knowledge kind must be products or avatars")
        validate_identifier(asset_id, "knowledge id")
        if not isinstance(payload, dict):
            raise ApiError(400, "INVALID_KNOWLEDGE_UPDATE", "Knowledge update must be a JSON object")
        common_fields = {
            "name",
            "version",
            "notes",
            "authorized",
            "authorization_scope",
            "usage_scope",
            "reference_metadata",
            "expected_revision",
        }
        product_fields = {"dimensions_cm", "package_spec", "packaging_contracts"}
        allowed = common_fields | (product_fields if kind == "products" else set())
        unknown = sorted(set(payload) - allowed)
        if unknown:
            raise ApiError(
                400,
                "UNKNOWN_KNOWLEDGE_UPDATE_FIELDS",
                "Knowledge update contains unsupported fields",
                {"fields": unknown, "allowed": sorted(allowed)},
            )
        if not (set(payload) - {"expected_revision"}):
            raise ApiError(400, "EMPTY_KNOWLEDGE_UPDATE", "At least one editable knowledge field is required")

        directory = self.knowledge_root / kind / asset_id
        record_path = directory / "record.json"
        with self._lock:
            if not record_path.is_file():
                existing_public = self.find_knowledge(kind, asset_id, required=False)
                raise ApiError(
                    409 if existing_public is not None else 404,
                    "BUILTIN_KNOWLEDGE_READ_ONLY" if existing_public is not None else "KNOWLEDGE_ASSET_NOT_FOUND",
                    "Built-in knowledge entries are read-only; create a custom entry to edit it"
                    if existing_public is not None
                    else "Knowledge asset was not found",
                )
            record = read_json(record_path)
            if not isinstance(record, dict) or record.get("storage_kind") != kind or str(record.get("id")) != asset_id:
                raise ApiError(500, "INVALID_KNOWLEDGE_RECORD", "Stored knowledge record identity is invalid")
            current_revision = int(record.get("revision") or 1)
            if "expected_revision" in payload:
                expected_revision = payload.get("expected_revision")
                if isinstance(expected_revision, bool) or not isinstance(expected_revision, int) or expected_revision < 1:
                    raise ApiError(400, "INVALID_KNOWLEDGE_REVISION", "expected_revision must be a positive integer")
                if expected_revision != current_revision:
                    raise ApiError(
                        409,
                        "KNOWLEDGE_REVISION_CONFLICT",
                        "The knowledge entry changed after it was opened; reload it before saving",
                        {"expected_revision": expected_revision, "current_revision": current_revision},
                    )

            try:
                candidate = json.loads(json.dumps(record, ensure_ascii=False, allow_nan=False))
            except (TypeError, ValueError) as exc:
                raise ApiError(500, "INVALID_KNOWLEDGE_RECORD", "Stored knowledge record is not finite JSON", {"reason": str(exc)})
            changed_fields: List[str] = []

            def replace(field: str, value: Any) -> None:
                if candidate.get(field) != value:
                    candidate[field] = value
                    changed_fields.append(field)

            if "name" in payload:
                replace("name", self._knowledge_text(payload.get("name"), "name", 120, allow_empty=False))
            if "version" in payload:
                version = self._knowledge_text(payload.get("version"), "version", 100, allow_empty=False)
                if version is None or any(ord(character) < 32 or ord(character) == 127 for character in version):
                    raise ApiError(
                        400,
                        "INVALID_KNOWLEDGE_VERSION",
                        "version must contain 1-100 visible characters on one line",
                    )
                replace("version", version)
            if "notes" in payload:
                replace("notes", self._knowledge_text(payload.get("notes"), "notes", 4000))
            if "authorized" in payload:
                if not isinstance(payload.get("authorized"), bool):
                    raise ApiError(400, "INVALID_KNOWLEDGE_AUTHORIZATION", "authorized must be true or false")
                replace("authorized", payload["authorized"])
            if "authorization_scope" in payload:
                replace(
                    "authorization_scope",
                    self._knowledge_text(payload.get("authorization_scope"), "authorization_scope", 1000),
                )
            if "usage_scope" in payload:
                usage_scope = self._knowledge_text(payload.get("usage_scope"), "usage_scope", 200)
                if kind == "avatars" and usage_scope not in {None, *VALID_AVATAR_USAGE_SCOPES}:
                    raise ApiError(
                        400,
                        "INVALID_AVATAR_USAGE_SCOPE",
                        "Avatar usage_scope must be head_only, full_only or head_and_full",
                    )
                replace("usage_scope", usage_scope)
            if kind == "products" and "dimensions_cm" in payload:
                replace("dimensions_cm", self._validate_knowledge_dimensions(payload.get("dimensions_cm")))
            if kind == "products" and "package_spec" in payload:
                package_spec = payload.get("package_spec")
                if package_spec is not None and not isinstance(package_spec, dict):
                    raise ApiError(400, "INVALID_KNOWLEDGE_STRUCTURE", "package_spec must be a JSON object or null")
                try:
                    json.dumps(package_spec, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)
                except (TypeError, ValueError) as exc:
                    raise ApiError(400, "INVALID_KNOWLEDGE_STRUCTURE", "package_spec must contain finite JSON values", {"reason": str(exc)})
                replace("package_spec", package_spec)
            if kind == "products" and "packaging_contracts" in payload:
                replace("packaging_contracts", self._validate_packaging_contracts(payload.get("packaging_contracts")))

            references = candidate.get("references") or []
            if not isinstance(references, list):
                raise ApiError(500, "INVALID_KNOWLEDGE_RECORD", "Stored knowledge references are invalid")
            immutable_references = {
                str(reference.get("id")): {
                    key: reference.get(key)
                    for key in ("id", "filename", "original_filename", "size", "sha256", "media_metadata", "created_at")
                }
                for reference in references
                if isinstance(reference, dict) and reference.get("id") not in (None, "")
            }
            if len(immutable_references) != len(references):
                raise ApiError(500, "INVALID_KNOWLEDGE_RECORD", "Stored reference IDs must be present and unique before editing")

            if "reference_metadata" in payload:
                metadata_updates = payload.get("reference_metadata")
                if not isinstance(metadata_updates, list):
                    raise ApiError(400, "INVALID_REFERENCE_METADATA", "reference_metadata must be a JSON array")
                references_by_id = {str(reference["id"]): reference for reference in references}
                seen_ids: set = set()
                allowed_metadata = {"id", "role", "label", "angle", "product_state", "packaging_layer"}
                for index, update in enumerate(metadata_updates):
                    if not isinstance(update, dict):
                        raise ApiError(400, "INVALID_REFERENCE_METADATA", "Each reference metadata update must be an object", {"index": index})
                    unknown_metadata = sorted(set(update) - allowed_metadata)
                    if unknown_metadata:
                        raise ApiError(
                            400,
                            "UNKNOWN_REFERENCE_METADATA_FIELDS",
                            "Reference metadata contains unsupported fields",
                            {"index": index, "fields": unknown_metadata},
                        )
                    reference_id = str(update.get("id") or "")
                    if not reference_id or reference_id not in references_by_id:
                        raise ApiError(
                            400,
                            "KNOWLEDGE_REFERENCE_NOT_FOUND",
                            "reference_metadata.id must name an existing reference",
                            {"index": index, "reference_id": reference_id or None},
                        )
                    if reference_id in seen_ids:
                        raise ApiError(400, "DUPLICATE_REFERENCE_ID", "Each reference may be updated only once per request")
                    seen_ids.add(reference_id)
                    reference = references_by_id[reference_id]
                    metadata_changed = False
                    for field in ("role", "label", "angle", "product_state"):
                        if field not in update:
                            continue
                        value = self._knowledge_text(update.get(field), field, 120)
                        if reference.get(field) != value:
                            reference[field] = value
                            metadata_changed = True
                    if "packaging_layer" in update:
                        if kind != "products":
                            raise ApiError(
                                400,
                                "PACKAGING_FIELDS_PRODUCT_ONLY",
                                "packaging_layer is only valid for product knowledge",
                                {"index": index},
                            )
                        raw_layer = update.get("packaging_layer")
                        layer = None
                        if raw_layer not in (None, ""):
                            if not isinstance(raw_layer, str):
                                raise ApiError(400, "UNKNOWN_PACKAGING_LAYER", "packaging_layer must be text or null", {"index": index})
                            layer = raw_layer.strip().lower().replace("-", "_").replace(" ", "_")
                            if layer not in PACKAGING_LAYER_SET:
                                raise ApiError(
                                    400,
                                    "UNKNOWN_PACKAGING_LAYER",
                                    "packaging_layer must name a supported packaging layer",
                                    {"index": index, "observed": layer, "allowed": list(PACKAGING_LAYERS)},
                                )
                        if reference.get("packaging_layer") != layer:
                            reference["packaging_layer"] = layer
                            metadata_changed = True
                        if reference.get("_packaging_layer_explicit") is not True:
                            reference["_packaging_layer_explicit"] = True
                            metadata_changed = True
                    if metadata_changed:
                        changed_fields.append("reference_metadata.%s" % reference_id)

            protected_after = {
                str(reference.get("id")): {
                    key: reference.get(key)
                    for key in ("id", "filename", "original_filename", "size", "sha256", "media_metadata", "created_at")
                }
                for reference in references
                if isinstance(reference, dict)
            }
            if protected_after != immutable_references:
                raise ApiError(500, "KNOWLEDGE_REFERENCE_IMMUTABLE_FIELDS_CHANGED", "An edit attempted to alter immutable reference identity or hash fields")

            if kind == "products":
                candidate["packaging_assets"] = self._packaging_asset_ids(references)
                layered = isinstance(candidate.get("packaging_contracts"), dict) or any(
                    self._reference_declares_layered_packaging(reference) for reference in references
                )
                candidate["schema_version"] = "workbench-knowledge-v2" if layered else "workbench-knowledge-v1"
            candidate["revision"] = current_revision + 1 if changed_fields else current_revision
            candidate["updated_at"] = now_iso() if changed_fields else record.get("updated_at") or now_iso()
            previous_record_sha256 = sha256_file(record_path)
            if changed_fields:
                atomic_write_json(record_path, candidate)
            current_record_sha256 = sha256_file(record_path)
            usage = self._knowledge_binding_usage(kind, asset_id)
            # Both custom immutable-contract formats record the exact source
            # record SHA. Consequently even a descriptive edit creates a new
            # knowledge revision for future applies, while already applied
            # projects continue to use their frozen project-local snapshot.
            material_changed = bool(changed_fields)
            return {
                "ok": True,
                "asset": self._public_knowledge_record(candidate),
                "changed_fields": changed_fields,
                "revision": candidate["revision"],
                "previous_record_sha256": previous_record_sha256,
                "record_sha256": current_record_sha256,
                "binding_impact": {
                    "existing_project_snapshots_preserved": True,
                    "material_contract_changed": material_changed,
                    "projects_using_entry": usage,
                    "projects_requiring_explicit_reapply": [value["project_id"] for value in usage] if material_changed else [],
                },
            }

    def upload_knowledge(
        self,
        kind: str,
        stream: BinaryIO,
        filename: str,
        fields: Dict[str, Any],
    ) -> Dict[str, Any]:
        if kind not in {"products", "avatars"}:
            raise ApiError(404, "UNKNOWN_KNOWLEDGE_KIND", "Knowledge kind must be products or avatars")
        clean_name = safe_filename(filename, "reference.png")
        suffix = Path(clean_name).suffix.lower()
        allowed = VALID_KNOWLEDGE_EXTENSIONS if kind == "products" else VALID_KNOWLEDGE_EXTENSIONS & {".jpg", ".jpeg", ".png", ".webp", ".gif", ".mp4", ".mov"}
        if suffix not in allowed:
            raise ApiError(415, "UNSUPPORTED_KNOWLEDGE_TYPE", "This file type is not supported for the selected library")
        asset_id = str(fields.get("id") or new_id("P" if kind == "products" else "AV"))
        validate_identifier(asset_id, "knowledge id")
        directory = self.knowledge_root / kind / asset_id
        is_new = not directory.exists()
        existing = read_json(directory / "record.json", {})

        def structured_field(name: str, current: Any = None) -> Any:
            if name not in fields:
                return current
            raw_value = fields.get(name)
            if raw_value in (None, ""):
                return None
            if isinstance(raw_value, (dict, list)):
                return raw_value
            try:
                def reject_constant(value: str) -> None:
                    raise ValueError("non-finite JSON constant: %s" % value)

                parsed = json.loads(str(raw_value), parse_constant=reject_constant)
            except (json.JSONDecodeError, ValueError):
                raise ApiError(400, "INVALID_KNOWLEDGE_STRUCTURE", "%s must be valid JSON" % name)
            if not isinstance(parsed, (dict, list)):
                raise ApiError(400, "INVALID_KNOWLEDGE_STRUCTURE", "%s must be a JSON object or array" % name)
            return parsed

        # Validate structured metadata before creating a directory or copying the
        # upload. A malformed JSON field must be a side-effect-free request.
        dimensions_value = structured_field("dimensions_cm", existing.get("dimensions_cm"))
        package_value = structured_field("package_spec", existing.get("package_spec"))
        packaging_contracts_value = structured_field("packaging_contracts", existing.get("packaging_contracts"))
        if kind == "products":
            if dimensions_value is not None and not isinstance(dimensions_value, dict):
                raise ApiError(400, "INVALID_KNOWLEDGE_STRUCTURE", "dimensions_cm must be a JSON object")
            if package_value is not None and not isinstance(package_value, dict):
                raise ApiError(400, "INVALID_KNOWLEDGE_STRUCTURE", "package_spec must be a JSON object")
            packaging_contracts_value = self._validate_packaging_contracts(packaging_contracts_value)
        elif "packaging_contracts" in fields or "packaging_layer" in fields:
            raise ApiError(400, "PACKAGING_FIELDS_PRODUCT_ONLY", "Packaging contracts and layers are only valid for product knowledge")
        usage_scope_value = fields.get("usage_scope", existing.get("usage_scope"))
        if kind == "avatars" and usage_scope_value not in (None, ""):
            usage_scope_value = str(usage_scope_value).strip()
            if usage_scope_value not in VALID_AVATAR_USAGE_SCOPES:
                raise ApiError(
                    400,
                    "INVALID_AVATAR_USAGE_SCOPE",
                    "Avatar usage_scope must be head_only, full_only or head_and_full",
                )
        reference_id = str(fields.get("reference_id") or new_id("ref"))
        validate_identifier(reference_id, "reference id")
        packaging_layer_value = fields.get("packaging_layer")
        if packaging_layer_value not in (None, ""):
            packaging_layer_value = str(packaging_layer_value).strip().lower().replace("-", "_").replace(" ", "_")
            if packaging_layer_value not in PACKAGING_LAYER_SET:
                raise ApiError(
                    400,
                    "UNKNOWN_PACKAGING_LAYER",
                    "packaging_layer must name a supported packaging layer",
                    {"observed": packaging_layer_value, "allowed": list(PACKAGING_LAYERS)},
                )
        else:
            packaging_layer_value = None
        stored_name = safe_filename(reference_id + "-" + clean_name, "reference" + suffix)
        destination = directory / stored_name
        if destination.exists():
            raise ApiError(409, "KNOWLEDGE_REFERENCE_EXISTS", "A reference with this id already exists")
        directory.mkdir(parents=True, exist_ok=True)
        try:
            size, digest = copy_stream_atomic(stream, destination, self.maximum_knowledge_bytes)
            media_metadata = None
            if suffix in VALID_IMAGE_EXTENSIONS:
                media_metadata = self._verified_media_metadata(destination, "image", "INVALID_KNOWLEDGE_MEDIA")
            elif suffix in VALID_VIDEO_EXTENSIONS:
                media_metadata = self._verified_media_metadata(destination, "video", "INVALID_KNOWLEDGE_MEDIA")
            for prior in existing.get("references") or []:
                if prior.get("sha256") == digest:
                    destination.unlink(missing_ok=True)
                    return {"ok": True, "asset": self._public_knowledge_record(existing), "duplicate_reference": True}
            authorized_raw = fields.get("authorized", False)
            authorized = authorized_raw is True or str(authorized_raw).strip().lower() in {"1", "true", "yes", "on"}
            role_value = str(fields.get("role") or packaging_layer_value or "").strip()[:100] or None
            reference = {
                "id": reference_id,
                "filename": stored_name,
                "original_filename": clean_name,
                "size": size,
                "sha256": digest,
                "role": role_value,
                "label": str(fields.get("label") or "").strip()[:120] or None,
                "angle": str(fields.get("angle") or "").strip()[:100] or None,
                "product_state": str(fields.get("product_state") or "").strip()[:100] or None,
                "packaging_layer": packaging_layer_value,
                "media_metadata": media_metadata,
                "created_at": now_iso(),
            }
            if existing:
                record = existing
                record.setdefault("references", []).append(reference)
                if fields.get("name"):
                    record["name"] = str(fields["name"]).strip()[:120]
                if "authorized" in fields:
                    record["authorized"] = authorized
                if fields.get("notes"):
                    record["notes"] = str(fields["notes"]).strip()[:4000]
                if fields.get("authorization_scope"):
                    record["authorization_scope"] = str(fields["authorization_scope"]).strip()[:1000]
                if fields.get("usage_scope"):
                    record["usage_scope"] = usage_scope_value
                if fields.get("version"):
                    record["version"] = str(fields["version"]).strip()[:100]
                record["dimensions_cm"] = dimensions_value
                record["package_spec"] = package_value
                if kind == "products":
                    record["packaging_contracts"] = packaging_contracts_value
                    record["packaging_assets"] = self._packaging_asset_ids(record.get("references") or [])
                    if packaging_contracts_value is not None or self._reference_declares_layered_packaging(reference):
                        record["schema_version"] = "workbench-knowledge-v2"
                record["revision"] = int(record.get("revision") or 1) + 1
                record["updated_at"] = now_iso()
            else:
                record = {
                    "schema_version": "workbench-knowledge-v1",
                    "id": asset_id,
                    "storage_kind": kind,
                    "name": str(fields.get("name") or Path(clean_name).stem).strip()[:120],
                    "authorized": authorized,
                    "authorization_scope": str(fields.get("authorization_scope") or "").strip() or None,
                    "usage_scope": usage_scope_value or None,
                    "version": str(fields.get("version") or "1").strip()[:100],
                    "dimensions_cm": dimensions_value,
                    "package_spec": package_value,
                    "notes": str(fields.get("notes") or "").strip()[:4000],
                    "source": "custom",
                    "references": [reference],
                    "revision": 1,
                    "created_at": now_iso(),
                    "updated_at": now_iso(),
                }
                if kind == "products":
                    record["packaging_contracts"] = packaging_contracts_value
                    record["packaging_assets"] = self._packaging_asset_ids([reference])
                    if packaging_contracts_value is not None or self._reference_declares_layered_packaging(reference):
                        record["schema_version"] = "workbench-knowledge-v2"
            atomic_write_json(directory / "record.json", record)
        except Exception:
            # `destination` was known not to exist before this request. Roll back
            # this request's file even if record persistence failed after copying.
            try:
                if destination.is_file():
                    destination.unlink()
            except OSError:
                pass
            try:
                if is_new and not (directory / "record.json").exists():
                    directory.rmdir()
            except OSError:
                pass
            raise
        return {"ok": True, "asset": self._public_knowledge_record(record)}

    def upload_knowledge_batch(
        self,
        kind: str,
        uploads: List[Tuple[BinaryIO, str]],
        fields: Dict[str, Any],
    ) -> Dict[str, Any]:
        """Commit a multi-reference form as one externally atomic transaction."""
        if not uploads:
            raise ApiError(400, "FILE_FIELD_REQUIRED", "At least one knowledge file is required")
        batch_fields = dict(fields)
        raw_reference_metadata = batch_fields.pop("reference_metadata", None)
        reference_metadata: Optional[List[Dict[str, Any]]] = None
        if raw_reference_metadata not in (None, ""):
            if isinstance(raw_reference_metadata, str):
                try:
                    parsed_metadata = json.loads(raw_reference_metadata)
                except json.JSONDecodeError:
                    raise ApiError(400, "INVALID_REFERENCE_METADATA", "reference_metadata must be valid JSON")
            else:
                parsed_metadata = raw_reference_metadata
            if not isinstance(parsed_metadata, list) or len(parsed_metadata) != len(uploads):
                raise ApiError(
                    400,
                    "REFERENCE_METADATA_COUNT_MISMATCH",
                    "reference_metadata must contain exactly one object for each multipart file, in order",
                    {"file_count": len(uploads), "metadata_count": len(parsed_metadata) if isinstance(parsed_metadata, list) else None},
                )
            reference_metadata = []
            seen_reference_ids: set = set()
            allowed_metadata = {"role", "angle", "product_state", "label", "reference_id", "packaging_layer"}
            for index, raw_metadata in enumerate(parsed_metadata):
                if not isinstance(raw_metadata, dict):
                    raise ApiError(400, "INVALID_REFERENCE_METADATA", "Each reference metadata item must be an object", {"index": index})
                unknown = sorted(set(raw_metadata) - allowed_metadata)
                if unknown:
                    raise ApiError(400, "UNKNOWN_REFERENCE_METADATA_FIELDS", "Reference metadata contains unsupported fields", {"index": index, "fields": unknown})
                clean_metadata: Dict[str, Any] = {}
                for key in ("role", "angle", "product_state", "label"):
                    if key in raw_metadata and raw_metadata[key] not in (None, ""):
                        value = str(raw_metadata[key]).strip()
                        if not value or len(value) > 120 or any(ord(character) < 32 for character in value):
                            raise ApiError(400, "INVALID_REFERENCE_METADATA", "%s must contain 1-120 visible characters" % key, {"index": index})
                        clean_metadata[key] = value
                if raw_metadata.get("packaging_layer") not in (None, ""):
                    if kind != "products":
                        raise ApiError(
                            400,
                            "PACKAGING_FIELDS_PRODUCT_ONLY",
                            "packaging_layer is only valid for product knowledge",
                            {"index": index},
                        )
                    packaging_layer = str(raw_metadata["packaging_layer"]).strip().lower().replace("-", "_").replace(" ", "_")
                    if packaging_layer not in PACKAGING_LAYER_SET:
                        raise ApiError(
                            400,
                            "UNKNOWN_PACKAGING_LAYER",
                            "reference_metadata packaging_layer is unsupported",
                            {"index": index, "observed": packaging_layer, "allowed": list(PACKAGING_LAYERS)},
                        )
                    clean_metadata["packaging_layer"] = packaging_layer
                if raw_metadata.get("reference_id") not in (None, ""):
                    reference_id = validate_identifier(str(raw_metadata["reference_id"]), "reference id")
                    if reference_id in seen_reference_ids:
                        raise ApiError(400, "DUPLICATE_REFERENCE_ID", "reference_metadata reference_id values must be unique")
                    seen_reference_ids.add(reference_id)
                    clean_metadata["reference_id"] = reference_id
                reference_metadata.append(clean_metadata)
        asset_id = str(batch_fields.get("id") or new_id("P" if kind == "products" else "AV"))
        validate_identifier(asset_id, "knowledge id")
        batch_fields["id"] = asset_id
        directory = self.knowledge_root / kind / asset_id
        temporary_root = Path(tempfile.mkdtemp(prefix="knowledge-batch-", dir=str(self.data_root)))
        backup = temporary_root / "previous"
        with self._lock:
            try:
                if directory.is_dir():
                    shutil.copytree(directory, backup)
                asset = None
                duplicate_count = 0
                for index, (stream, filename) in enumerate(uploads):
                    item_fields = dict(batch_fields)
                    if len(uploads) > 1:
                        item_fields.pop("reference_id", None)
                    if reference_metadata is not None:
                        for key in ("role", "angle", "product_state", "label", "reference_id", "packaging_layer"):
                            item_fields.pop(key, None)
                        item_fields.update(reference_metadata[index])
                    result = self.upload_knowledge(kind, stream, filename, item_fields)
                    asset = result["asset"]
                    if result.get("duplicate_reference"):
                        duplicate_count += 1
                return {
                    "ok": True,
                    "asset": asset,
                    "reference_count": len((asset or {}).get("references") or []),
                    "duplicate_count": duplicate_count,
                    "batch_committed": True,
                }
            except Exception:
                # Readers use the same service lock, so no partial record escapes.
                # Restore the exact prior directory before releasing the lock.
                if directory.exists():
                    shutil.rmtree(directory)
                if backup.is_dir():
                    shutil.copytree(backup, directory)
                raise
            finally:
                shutil.rmtree(temporary_root, ignore_errors=True)

    # ---- media ------------------------------------------------------------------

    def project_media_path(self, project_id: str, relative: str) -> Path:
        project_dir = self.get_project_dir(project_id)
        path = safe_join(project_dir, relative)
        if not path.is_file():
            raise ApiError(404, "MEDIA_NOT_FOUND", "Project media file was not found")
        return path

    def knowledge_media_path(self, kind: str, asset_id: str, filename: str) -> Path:
        if kind not in {"products", "avatars"}:
            raise ApiError(404, "UNKNOWN_KNOWLEDGE_KIND", "Unknown knowledge library")
        validate_identifier(asset_id, "knowledge id")
        directory = safe_join(self.knowledge_root / kind, asset_id)
        record = read_json(directory / "record.json")
        filenames = [str(item.get("filename")) for item in record.get("references") or [] if isinstance(item, dict)]
        if not filenames and record.get("filename"):
            filenames = [str(record["filename"])]
        if filename not in filenames:
            raise ApiError(404, "MEDIA_NOT_FOUND", "Knowledge media file was not found")
        path = safe_join(directory, filename)
        if not path.is_file():
            raise ApiError(404, "MEDIA_NOT_FOUND", "Knowledge media file was not found")
        return path

    def skill_media_path(self, relative: str) -> Path:
        path = safe_join(self.toolchain.skill_dir, relative)
        if not path.is_file():
            raise ApiError(404, "MEDIA_NOT_FOUND", "Built-in skill media file was not found")
        allowed_roots = [
            (self.toolchain.skill_dir / "assets").resolve(strict=False),
        ]
        if not any(path == root or root in path.parents for root in allowed_roots):
            raise ApiError(403, "MEDIA_ACCESS_DENIED", "Only built-in Skill assets may be served")
        return path

    # ---- approvals --------------------------------------------------------------

    def _invalidate_delivery_after_revocation(self, project_dir: Path, revocation_path: Path) -> None:
        """Make a user revocation an immediate delivery gate, independent of helper scripts."""
        workflow_path = project_dir / "planning" / "workflow_state.json"
        workflow = read_json(workflow_path, {})
        if not isinstance(workflow, dict):
            workflow = {}
        pending_inputs = [str(value) for value in workflow.get("pending_inputs") or []]
        for value in ("regenerate_after_asset_revocation", "document_visual_qa"):
            if value not in pending_inputs:
                pending_inputs.append(value)
        workflow.update(
            {
                "status": "delivery_asset_revoked",
                "docx_export_authorized": False,
                "docx_visual_qa_status": "invalidated",
                "docx_visual_qa_blocked_reason": "ACTIVE_DELIVERY_REVOCATION",
                "active_workbench_revocation_path": revocation_path.relative_to(project_dir).as_posix(),
                "pending_inputs": pending_inputs,
                "updated_at": now_iso(),
            }
        )
        atomic_write_json(workflow_path, workflow)
        alignment_path = project_dir / "review" / "alignment_manifest.json"
        alignment = read_json(alignment_path, {})
        if not isinstance(alignment, dict):
            alignment = {}
        alignment.update(
            {
                "status": "stale",
                "stale_reason": "approved_delivery_asset_revoked",
                "updated_at": now_iso(),
            }
        )
        atomic_write_json(alignment_path, alignment)

    def record_approval(self, project_id: str, payload: Dict[str, Any]) -> Dict[str, Any]:
        project_dir = self.get_project_dir(project_id)
        decision = str(payload.get("decision") or "")
        if decision not in {"approve", "revoke"}:
            raise ApiError(400, "INVALID_APPROVAL_DECISION", "decision must be approve or revoke")
        relative = str(payload.get("asset_path") or "")
        asset_path = safe_join(project_dir, relative)
        if not asset_path.is_file():
            raise ApiError(404, "APPROVAL_ASSET_NOT_FOUND", "The asset to approve or revoke does not exist")
        shot_id = payload.get("shot_id")
        if shot_id is not None:
            shot_id = validate_identifier(str(shot_id), "shot id")
        asset_id = str(payload.get("asset_id") or Path(relative).stem)
        asset_id = validate_identifier(re.sub(r"[^A-Za-z0-9._-]", "-", asset_id)[:128], "asset id")
        asset_sha256 = sha256_file(asset_path)
        current_generation_input = self._generation_input_snapshot(project_dir)
        result_manifest = read_json(project_dir / "shots" / "results" / "result_manifest.json", {"records": []})
        result_record = next(
            (
                value
                for value in (result_manifest.get("records") or [] if isinstance(result_manifest, dict) else [])
                if isinstance(value, dict)
                and (value.get("asset_id") == asset_id or value.get("path") == asset_path.relative_to(project_dir).as_posix())
            ),
            None,
        )
        dependency_state = read_json(project_dir / "workbench" / "dependency_state.json", {})
        if decision == "approve" and isinstance(result_record, dict):
            generated_for = result_record.get("generation_input_sha256")
            missing_contract_is_stale = not generated_for and isinstance(dependency_state, dict) and int(dependency_state.get("revision") or 0) > 0
            if missing_contract_is_stale or (generated_for and generated_for != current_generation_input["contract_sha256"]):
                raise ApiError(
                    409,
                    "STALE_GENERATED_ASSET_INPUTS",
                    "This result was generated from older source/config/script/shot inputs and cannot be approved for the current project",
                    {
                        "asset_id": asset_id,
                        "asset_generation_input_sha256": generated_for,
                        "current_generation_input_sha256": current_generation_input["contract_sha256"],
                    },
                )
        replacement_revocation_paths: List[Path] = []
        if decision == "approve":
            for revocation_path in (project_dir / "review" / "workbench-revocations").glob("*.json"):
                revocation = read_json(revocation_path, {})
                if not isinstance(revocation, dict) or str(revocation.get("status") or "active").lower() not in {"active", "invalidated", "blocked"}:
                    continue
                revoked_ids = {str(value) for value in revocation.get("revoked_asset_ids") or []}
                revoked_assets = [value for value in revocation.get("revoked_assets") or [] if isinstance(value, dict)]
                same_asset = asset_id in revoked_ids or any(
                    value.get("path") == asset_path.relative_to(project_dir).as_posix()
                    or (value.get("sha256") and value.get("sha256") == asset_sha256)
                    for value in revoked_assets
                )
                if same_asset:
                    raise ApiError(
                        409,
                        "REVOKED_ASSET_REQUIRES_NEW_VERSION",
                        "An active revocation cannot be cleared by an ordinary approval; import a corrected new asset version",
                        {"revocation_path": revocation_path.relative_to(project_dir).as_posix()},
                    )
                revoked_shot_ids = {str(revocation.get("shot_id") or "")}
                revoked_shot_ids.update(
                    str(value.get("shot_id") or "")
                    for value in revoked_assets
                    if isinstance(value, dict)
                )
                if shot_id and shot_id in revoked_shot_ids:
                    replacement_revocation_paths.append(revocation_path)
        record = {
            "id": new_id("approval"),
            "project_id": project_id,
            "asset_id": asset_id,
            "asset_path": asset_path.relative_to(project_dir).as_posix(),
            "asset_sha256": asset_sha256,
            "input_contract_sha256": current_generation_input["contract_sha256"],
            "shot_id": shot_id,
            "decision": decision,
            "reason": str(payload.get("reason") or "").strip()[:4000] or None,
            "created_at": now_iso(),
            "canonical_sync": "approval_recorded" if decision == "approve" else "pending_revocation_cascade",
        }
        ledger_path = project_dir / "workbench" / "approvals.json"
        ledger = read_json(ledger_path, {"schema_version": "workbench-approvals-v1", "records": []})
        ledger.setdefault("records", []).append(record)
        atomic_write_json(ledger_path, ledger)

        cascade: Optional[Dict[str, Any]] = None
        if decision == "revoke":
            revocation_path = project_dir / "review" / "workbench-revocations" / (record["id"] + ".json")
            reason_code = str(payload.get("reason_code") or "USER_REVOKED_ASSET")
            if not re.fullmatch(r"[A-Z0-9_:-]{2,100}", reason_code):
                reason_code = "USER_REVOKED_ASSET"
            revocation = {
                "schema_version": "workbench-revocation-v1",
                "status": "active",
                "project_id": project_id,
                "approval_id": record["id"],
                "shot_id": shot_id,
                "revoked_asset_ids": [asset_id],
                "revoked_assets": [
                    {
                        "asset_id": asset_id,
                        "path": record["asset_path"],
                        "sha256": record["asset_sha256"],
                        "shot_id": shot_id,
                    }
                ],
                "reason_codes": [reason_code],
                "created_at": now_iso(),
            }
            atomic_write_json(revocation_path, revocation)
            self._invalidate_delivery_after_revocation(project_dir, revocation_path)
            script = self.toolchain.script("invalidate_revoked_delivery.py")
            if script:
                result = self.toolchain.run_sync(
                    [self.toolchain.python_bin, str(script), "--project-dir", str(project_dir), "--revocation", str(revocation_path)],
                    cwd=project_dir,
                    timeout=90,
                )
                cascade = {
                    "status": "invalidated" if result["ok"] else "blocked",
                    "returncode": result.get("returncode"),
                    "message": (result.get("stdout") or result.get("stderr") or "")[-3000:],
                }
                if result["ok"]:
                    record["canonical_sync"] = "revocation_cascade_applied"
                else:
                    record["canonical_sync"] = "revocation_cascade_blocked"
            else:
                cascade = {"status": "blocked", "code": "REVOCATION_SCRIPT_NOT_AVAILABLE"}
                record["canonical_sync"] = "revocation_cascade_unavailable"
        elif replacement_revocation_paths:
            for revocation_path in replacement_revocation_paths:
                revocation = read_json(revocation_path, {})
                if not isinstance(revocation, dict):
                    continue
                revocation.update(
                    {
                        "status": "superseded",
                        "superseded_at": now_iso(),
                        "superseded_by": {
                            "asset_id": asset_id,
                            "path": record["asset_path"],
                            "sha256": record["asset_sha256"],
                            "shot_id": shot_id,
                            "approval_id": record["id"],
                        },
                    }
                )
                atomic_write_json(revocation_path, revocation)
        # Persist the final cascade outcome, not the earlier in-memory mutation.
        # The ledger remains the audit source returned after a process restart.
        atomic_write_json(ledger_path, ledger)
        return {"ok": True, "approval": record, "cascade": cascade}

    def list_approvals(self, project_id: str) -> Dict[str, Any]:
        project_dir = self.get_project_dir(project_id)
        ledger = read_json(project_dir / "workbench" / "approvals.json", {"schema_version": "workbench-approvals-v1", "records": []})
        return {"ok": True, "approvals": ledger.get("records") or []}

    # ---- task-facing helpers ----------------------------------------------------

    def _detection_input_hashes(self, project_dir: Path, detector_task_id: Optional[str] = None) -> Dict[str, Any]:
        state = self._load_state(project_dir)
        script = self._project_script(project_dir)
        active_text = script.get("revised_text") if script.get("active_source") == "revised" else script.get("source_text")
        shot_manifest_path = project_dir / "shots" / "shot_manifest.json"
        product_contract_path = project_dir / "library" / "product_immutable_contract.json"
        avatar_library_path = project_dir / "library" / "avatar_library.json"
        markers_path = project_dir / "planning" / "manual_markers.json"
        config = state.get("config") or {}
        product_binding = state.get("product_binding") or {}
        avatar_binding = state.get("avatar_binding") or {}
        stable_binding = {
            "product": {
                "mode": config.get("product_mode"),
                "selected_id": config.get("product_id"),
                "applied_id": product_binding.get("applied_id"),
                "contract_sha256": product_binding.get("immutable_contract_sha256"),
                "contract_file_sha256": sha256_file(product_contract_path) if product_contract_path.is_file() else None,
            },
            "avatar": {
                "mode": config.get("character_mode"),
                "selected_id": config.get("avatar_id"),
                "source_person_id": config.get("source_person_id"),
                "applied_id": avatar_binding.get("applied_id"),
                "bound_source_person_id": avatar_binding.get("source_person_id"),
                "avatar_library_sha256": sha256_file(avatar_library_path) if avatar_library_path.is_file() else None,
            },
        }
        binding_json = json.dumps(stable_binding, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)
        detector_contract_sha256 = None
        if detector_task_id:
            try:
                detector_task = self.tasks.get_task(detector_task_id)
            except ApiError:
                detector_task = None
            if detector_task:
                contract_value = {
                    "schema_version": "workbench-detector-contract-v1",
                    "operation": detector_task.get("operation"),
                    "instruction": detector_task.get("instruction"),
                    "owner_lane": detector_task.get("owner_lane"),
                    "detector_contract": detector_task.get("detector_contract"),
                }
                contract_json = json.dumps(contract_value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
                detector_contract_sha256 = hashlib.sha256(contract_json.encode("utf-8")).hexdigest()
        return {
            "source_video_sha256": (state.get("video") or {}).get("sha256"),
            "shot_manifest_sha256": sha256_file(shot_manifest_path) if shot_manifest_path.is_file() else None,
            "active_script_sha256": hashlib.sha256(str(active_text or "").encode("utf-8")).hexdigest(),
            "binding_sha256": hashlib.sha256(binding_json.encode("utf-8")).hexdigest(),
            "manual_markers_sha256": sha256_file(markers_path) if markers_path.is_file() else None,
            "detector_contract_sha256": detector_contract_sha256,
        }

    def _detection_results_with_freshness(self, project_dir: Path) -> Dict[str, Any]:
        artifact = read_json(project_dir / "review" / "workbench_detection.json", {})
        if not isinstance(artifact, dict) or not artifact:
            return {}
        result = dict(artifact)
        saved = artifact.get("input_hashes") if isinstance(artifact.get("input_hashes"), dict) else {}
        current = self._detection_input_hashes(project_dir, str(artifact.get("task_id") or "") or None)
        reason_by_key = {
            "source_video_sha256": "SOURCE_VIDEO_CHANGED",
            "shot_manifest_sha256": "SHOT_MANIFEST_CHANGED",
            "active_script_sha256": "ACTIVE_SCRIPT_CHANGED",
            "binding_sha256": "PRODUCT_OR_AVATAR_BINDING_CHANGED",
            "manual_markers_sha256": "MANUAL_MARKERS_CHANGED",
            "detector_contract_sha256": "DETECTOR_CONTRACT_CHANGED",
        }
        stale_reasons = [reason for key, reason in reason_by_key.items() if saved.get(key) != current.get(key)]
        stale_reasons.extend(str(value) for value in artifact.get("forced_stale_reasons") or [] if str(value))
        stale_reasons = list(dict.fromkeys(stale_reasons))
        result["current_input_hashes"] = current
        result["stale"] = bool(stale_reasons)
        result["stale_reasons"] = stale_reasons
        result["findings_are_effective"] = not stale_reasons
        result["effective_status"] = "historical_stale" if stale_reasons else "current"
        return result

    def save_detection_results(self, project_id: str, task_id: str, findings: Any) -> Dict[str, Any]:
        project_dir = self.get_project_dir(project_id)
        if not isinstance(findings, list):
            raise ApiError(409, "INVALID_DETECTION_FINDINGS", "Detector findings must be an array")
        try:
            detector_task = self.tasks.get_task(task_id)
        except ApiError:
            raise ApiError(409, "DETECTOR_TASK_REQUIRED", "Findings must belong to a persisted detector task")
        task_contract = detector_task.get("detector_contract") if isinstance(detector_task, dict) else None
        requested_detectors = [
            str(value)
            for value in ((task_contract or {}).get("requested_detectors") or [])
            if str(value)
        ]
        if detector_task.get("project_id") != project_id or detector_task.get("operation") != "codex" or not requested_detectors:
            raise ApiError(409, "INVALID_DETECTOR_TASK_CONTRACT", "Detector task must name requested detectors for this project")
        flat_shots, _ = self._project_shot_data(project_dir)
        units_by_id = {str(item.get("id")): item for item in flat_shots if item.get("id")}
        valid_units = {str(item.get("id")) for item in flat_shots if item.get("id")}
        allowed_results = {"pass", "issue", "not_observable"}
        allowed_severities = {"info", "warning", "error", "blocker"}
        allowed_lanes = {"image", "text", "controller"}
        clean_findings: List[Dict[str, Any]] = []
        for index, finding in enumerate(findings):
            if not isinstance(finding, dict):
                raise ApiError(409, "INVALID_DETECTION_FINDING", "Each detector finding must be an object", {"index": index})
            unit_id = str(finding.get("unit_id") or "")
            if unit_id not in valid_units:
                raise ApiError(
                    409,
                    "UNKNOWN_DETECTION_UNIT",
                    "Detector finding references an unknown delivery unit",
                    {"index": index, "unit_id": unit_id, "allowed": sorted(valid_units)},
                )
            result_value = str(finding.get("result") or "")
            severity = str(finding.get("severity") or "")
            owner_lane = str(finding.get("owner_lane") or "")
            detector = str(finding.get("detector") or "")
            code = str(finding.get("code") or "")
            message = str(finding.get("message") or "").strip()
            if result_value not in allowed_results or severity not in allowed_severities or owner_lane not in allowed_lanes:
                raise ApiError(409, "INVALID_DETECTION_ENUM", "Detector result, severity or owner_lane is invalid", {"index": index})
            if not re.fullmatch(r"[A-Za-z0-9_.:-]{2,100}", detector):
                raise ApiError(409, "INVALID_DETECTOR_NAME", "Detector name is invalid", {"index": index})
            if detector not in requested_detectors:
                raise ApiError(409, "UNREQUESTED_DETECTOR_FINDING", "Finding names a detector outside the persisted task contract", {"index": index})
            if not re.fullmatch(r"[A-Za-z0-9_.:-]{2,120}", code):
                raise ApiError(409, "INVALID_DETECTION_CODE", "Detector code is invalid", {"index": index})
            if not message or len(message) > 2000:
                raise ApiError(409, "INVALID_DETECTION_MESSAGE", "Detector message must contain 1-2000 characters", {"index": index})
            clean: Dict[str, Any] = {
                "detector": detector,
                "unit_id": unit_id,
                "result": result_value,
                "code": code,
                "severity": severity,
                "owner_lane": owner_lane,
                "message": message,
            }
            evidence_time = None
            if finding.get("evidence_time") is not None:
                try:
                    evidence_time = float(finding["evidence_time"])
                except (TypeError, ValueError):
                    raise ApiError(409, "INVALID_DETECTION_EVIDENCE_TIME", "evidence_time must be numeric", {"index": index})
                if evidence_time < 0:
                    raise ApiError(409, "INVALID_DETECTION_EVIDENCE_TIME", "evidence_time cannot be negative", {"index": index})
                unit = units_by_id[unit_id]
                timecode = unit.get("timeline_timecode") or unit.get("timecode") or {}
                try:
                    unit_start = float(timecode.get("start"))
                    unit_end = float(timecode.get("end"))
                except (TypeError, ValueError):
                    raise ApiError(409, "DETECTION_UNIT_TIMECODE_MISSING", "Evidence cannot be verified without a unit time range", {"index": index})
                if evidence_time < unit_start - 0.001 or evidence_time > unit_end + 0.001:
                    raise ApiError(
                        409,
                        "DETECTION_EVIDENCE_OUTSIDE_UNIT",
                        "evidence_time must fall inside the finding's delivery unit",
                        {"index": index, "unit_id": unit_id, "start": unit_start, "end": unit_end},
                    )
                clean["evidence_time"] = round(evidence_time, 6)
            evidence_asset = str(finding.get("evidence_asset") or "").strip()
            evidence_sha256 = str(finding.get("evidence_asset_sha256") or "").strip().lower()
            if result_value in {"pass", "issue"} and (evidence_time is None or not evidence_asset or not evidence_sha256):
                raise ApiError(
                    409,
                    "DETECTION_EVIDENCE_REQUIRED",
                    "pass and issue findings require evidence_time plus a verifiable project asset path and SHA-256",
                    {"index": index},
                )
            if evidence_asset or evidence_sha256:
                if not evidence_asset or len(evidence_asset) > 1000 or Path(evidence_asset).is_absolute():
                    raise ApiError(409, "INVALID_DETECTION_EVIDENCE_ASSET", "evidence_asset must be a relative project path", {"index": index})
                if not re.fullmatch(r"[a-f0-9]{64}", evidence_sha256):
                    raise ApiError(409, "INVALID_DETECTION_EVIDENCE_SHA256", "evidence_asset_sha256 must be a SHA-256 hex digest", {"index": index})
                try:
                    evidence_path = safe_join(project_dir, evidence_asset)
                except ApiError:
                    raise ApiError(409, "INVALID_DETECTION_EVIDENCE_ASSET", "evidence_asset escapes the project", {"index": index})
                if not evidence_path.is_file():
                    raise ApiError(409, "DETECTION_EVIDENCE_ASSET_MISSING", "evidence_asset does not exist", {"index": index})
                actual_evidence_sha256 = sha256_file(evidence_path)
                if actual_evidence_sha256 != evidence_sha256:
                    raise ApiError(
                        409,
                        "DETECTION_EVIDENCE_HASH_MISMATCH",
                        "evidence_asset_sha256 does not match the current file",
                        {"index": index, "actual_sha256": actual_evidence_sha256},
                    )
                clean["evidence_asset"] = evidence_path.relative_to(project_dir).as_posix()
                clean["evidence_asset_sha256"] = actual_evidence_sha256
            clean_findings.append(clean)
        scope = (task_contract or {}).get("shot_scope") or {"mode": "all"}
        scoped_unit_ids: List[str] = []
        for unit in flat_shots:
            unit_id = str(unit.get("id") or "")
            if not unit_id:
                continue
            if scope.get("mode") == "selected" and unit_id not in {str(value) for value in scope.get("shot_ids") or []}:
                continue
            if scope.get("mode") == "range":
                unit_start = float(unit.get("start") or 0.0)
                unit_end = float(unit.get("end") or unit_start)
                if unit_end <= float(scope.get("start") or 0.0) or unit_start >= float(scope.get("end") or 0.0):
                    continue
            scoped_unit_ids.append(unit_id)
        expected_pairs = [(detector, unit_id) for detector in requested_detectors for unit_id in scoped_unit_ids]
        covered_pairs = {(str(value["detector"]), str(value["unit_id"])) for value in clean_findings}
        if len(covered_pairs) != len(clean_findings):
            raise ApiError(409, "DUPLICATE_DETECTION_FINDING", "Each detector/unit pair must appear exactly once")
        missing_pairs = [
            {"detector": detector, "unit_id": unit_id}
            for detector, unit_id in expected_pairs
            if (detector, unit_id) not in covered_pairs
        ]
        if not requested_detectors or not scoped_unit_ids or missing_pairs:
            raise ApiError(
                409,
                "INCOMPLETE_DETECTION_COVERAGE",
                "Every requested detector must return pass, issue or not_observable for every scoped delivery unit",
                {
                    "requested_detectors": requested_detectors,
                    "scoped_unit_ids": scoped_unit_ids,
                    "missing": missing_pairs,
                },
            )
        detector_contract = {
            "schema_version": "workbench-detector-coverage-v1",
            "task_id": task_id,
            "requested_detectors": requested_detectors,
            "shot_scope": scope,
            "scoped_unit_ids": scoped_unit_ids,
            "expected_pair_count": len(expected_pairs),
            "covered_pair_count": len(expected_pairs),
            "coverage_complete": True,
        }
        contract_json = json.dumps(detector_contract, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)
        detector_contract["contract_sha256"] = hashlib.sha256(contract_json.encode("utf-8")).hexdigest()
        input_hashes = self._detection_input_hashes(project_dir, task_id)
        artifact = {
            "schema_version": "workbench-detection-v1",
            "project_id": project_id,
            "task_id": task_id,
            "created_at": now_iso(),
            "input_hashes": input_hashes,
            "detector_contract": detector_contract,
            "findings": clean_findings,
        }
        atomic_write_json(project_dir / "review" / "workbench_detection.json", artifact)
        return artifact

    def validate_analysis_contract(self, project_id: str) -> Dict[str, Any]:
        project_dir = self.get_project_dir(project_id)
        state = self._load_state(project_dir)
        script = self._project_script(project_dir)
        flat_shots, _ = self._project_shot_data(project_dir)
        shot_manifest_path = project_dir / "shots" / "shot_manifest.json"
        shot_manifest = read_json(shot_manifest_path, {})
        role_lock_path = project_dir / "planning" / "role_lock.json"
        story_path = project_dir / "planning" / "story_plan.json"
        role_lock = read_json(role_lock_path, {})
        story = read_json(story_path, {})
        video = state.get("video") or {}
        video_duration = ((video.get("metadata") or {}).get("duration"))
        try:
            video_duration_value = float(video_duration)
        except (TypeError, ValueError):
            video_duration_value = 0.0
        tolerance = 0.08
        interval_violations: List[Dict[str, Any]] = []
        intervals: List[Tuple[float, float, str]] = []
        duration_violations: List[Dict[str, Any]] = []
        try:
            maximum_unit_duration = float((shot_manifest or {}).get("max_delivery_unit_duration_seconds") or 3.5)
        except (TypeError, ValueError):
            maximum_unit_duration = 3.5
        maximum_unit_duration = min(max(maximum_unit_duration, 0.25), 10.0)
        for unit in flat_shots:
            unit_id = str(unit.get("id") or "")
            timecode = unit.get("timeline_timecode") or unit.get("timecode") or {}
            try:
                start = float(timecode.get("start"))
                end = float(timecode.get("end"))
            except (TypeError, ValueError):
                interval_violations.append({"unit_id": unit_id, "code": "UNIT_TIMECODE_MISSING"})
                continue
            if start < -tolerance or end <= start:
                interval_violations.append({"unit_id": unit_id, "code": "UNIT_TIMECODE_INVALID", "start": start, "end": end})
                continue
            intervals.append((start, end, unit_id))
            duration = end - start
            duration_reason = str(
                unit.get("duration_exception_reason")
                or unit.get("long_take_reason")
                or unit.get("action_beat_reason")
                or ""
            ).strip()
            if duration > maximum_unit_duration + tolerance and not duration_reason:
                duration_violations.append(
                    {
                        "unit_id": unit_id,
                        "duration": round(duration, 6),
                        "maximum_without_reason": maximum_unit_duration,
                        "code": "LONG_UNIT_REASON_REQUIRED",
                    }
                )
        intervals.sort(key=lambda value: (value[0], value[1], value[2]))
        if intervals:
            if abs(intervals[0][0]) > tolerance:
                interval_violations.append({"code": "TIMELINE_DOES_NOT_START_AT_ZERO", "observed": intervals[0][0]})
            cursor = intervals[0][1]
            prior_id = intervals[0][2]
            for start, end, unit_id in intervals[1:]:
                if start > cursor + tolerance:
                    interval_violations.append(
                        {"code": "TIMELINE_GAP", "after_unit_id": prior_id, "before_unit_id": unit_id, "start": cursor, "end": start}
                    )
                elif start < cursor - tolerance:
                    interval_violations.append(
                        {"code": "TIMELINE_OVERLAP", "prior_unit_id": prior_id, "unit_id": unit_id, "overlap_start": start, "overlap_end": cursor}
                    )
                if end > cursor:
                    cursor = end
                    prior_id = unit_id
            if video_duration_value > 0 and abs(cursor - video_duration_value) > tolerance:
                interval_violations.append(
                    {"code": "TIMELINE_DOES_NOT_END_AT_VIDEO", "observed": cursor, "video_duration": video_duration_value}
                )
        marker_violations: List[Dict[str, Any]] = []
        for marker in self._project_markers(project_dir):
            try:
                marker_time = float(marker.get("time"))
            except (TypeError, ValueError):
                marker_violations.append({"marker_id": marker.get("id"), "code": "MARKER_TIME_INVALID"})
                continue
            containing = [
                unit
                for unit in flat_shots
                if unit.get("start") is not None
                and unit.get("end") is not None
                and float(unit["start"]) - tolerance <= marker_time <= float(unit["end"]) + tolerance
            ]
            # At a shared boundary, the explicit owner resolves ambiguity.
            explicit_owner = str(marker.get("shot_id") or "")
            if len(containing) > 1 and explicit_owner:
                containing = [unit for unit in containing if str(unit.get("id")) == explicit_owner]
            if len(containing) != 1:
                marker_violations.append(
                    {"marker_id": marker.get("id"), "code": "MARKER_NOT_IN_EXACTLY_ONE_UNIT", "candidate_unit_ids": [unit.get("id") for unit in containing]}
                )
                continue
            owner = containing[0]
            if explicit_owner != str(owner.get("id")):
                marker_violations.append(
                    {"marker_id": marker.get("id"), "code": "MARKER_OWNER_NOT_BOUND", "expected_unit_id": owner.get("id"), "observed_unit_id": marker.get("shot_id")}
                )
            if str(marker.get("kind") or "") not in set(owner.get("semantic_tags") or []):
                marker_violations.append(
                    {"marker_id": marker.get("id"), "code": "MARKER_SEMANTIC_TAG_MISSING", "unit_id": owner.get("id"), "kind": marker.get("kind")}
                )
        known_people = self._project_source_people(project_dir)
        allowed_owner_ids = {
            alias
            for person in known_people
            for alias in [str(person.get("id") or "")] + [str(value) for value in person.get("aliases") or []]
            if alias
        }
        owner_violations: List[Dict[str, Any]] = []
        script_mapping_violations: List[Dict[str, Any]] = []
        shot_mapping = script.get("shot_mapping")
        mapped_units: set = set()
        if isinstance(shot_mapping, dict):
            mapped_units.update(str(key) for key in shot_mapping)
        elif isinstance(shot_mapping, list):
            for value in shot_mapping:
                if not isinstance(value, dict):
                    continue
                owner_id = value.get("unit_id") or value.get("shot_id") or value.get("source_shot_id") or value.get("inserted_shot_id")
                if owner_id:
                    mapped_units.add(str(owner_id))
        silence_values = {"silence", "silent", "none", "no_speech", "无口播", "静默", "画外无声"}
        for unit in flat_shots:
            unit_id = str(unit.get("id") or "")
            tags = set(unit.get("semantic_tags") or [])
            character = unit.get("character")
            if "person" in tags:
                owner_id = None
                if isinstance(character, dict):
                    owner_id = next(
                        (character.get(key) for key in ("source_person_id", "person_id", "owner", "id", "speaker_id") if character.get(key)),
                        None,
                    )
                elif isinstance(character, str) and character.strip():
                    owner_id = character.strip()
                owner_id = owner_id or unit.get("source_person_id") or unit.get("speaker_id") or unit.get("owner")
                if not owner_id or (allowed_owner_ids and str(owner_id) not in allowed_owner_ids):
                    owner_violations.append(
                        {"unit_id": unit_id, "code": "PERSON_OWNER_MAPPING_REQUIRED", "observed_owner": owner_id, "allowed_owner_ids": sorted(allowed_owner_ids)}
                    )
            explicit_silence = str(
                unit.get("speech_mode") or unit.get("dialogue_status") or unit.get("speech_source") or ""
            ).lower() in silence_values
            mapped = bool(unit.get("script_segment_ids")) or unit_id in mapped_units or explicit_silence
            if not mapped:
                script_mapping_violations.append({"unit_id": unit_id, "code": "SCRIPT_OR_SILENCE_MAPPING_REQUIRED"})
            if unit.get("requires_semantic_reanalysis") is True:
                script_mapping_violations.append({"unit_id": unit_id, "code": "SPLIT_UNIT_SEMANTIC_REANALYSIS_REQUIRED"})
        manifest_source_sha = shot_manifest.get("source_video_sha256") if isinstance(shot_manifest, dict) else None
        checks = {
            "source_text_present": bool(str(script.get("source_text") or "").strip()),
            "delivery_units_present": bool(flat_shots),
            "role_lock_materialized": role_lock_path.is_file() and isinstance(role_lock, dict) and bool(role_lock),
            "story_plan_materialized": story_path.is_file() and isinstance(story, dict) and bool(story),
            "source_video_metadata_ready": video_duration_value > 0,
            "shot_manifest_bound_to_current_video": bool(video.get("sha256")) and manifest_source_sha == video.get("sha256"),
            "timeline_zero_to_end_without_gap_or_overlap": bool(intervals) and not interval_violations,
            "unit_duration_contract": not duration_violations,
            "manual_markers_bound_to_matching_semantic_units": not marker_violations,
            "person_owner_mapping_complete": not owner_violations,
            "script_or_silence_mapping_complete": not script_mapping_violations,
            "shot_manifest_not_stale": isinstance(shot_manifest, dict)
            and shot_manifest.get("analysis_status") != "stale"
            and shot_manifest.get("requires_source_reanalysis") is not True,
        }
        return {
            "status": "ready" if all(checks.values()) else "blocked",
            "code": None if all(checks.values()) else "ANALYSIS_CONTRACT_NOT_MATERIALIZED",
            "checks": checks,
            "maximum_unit_duration_seconds": maximum_unit_duration,
            "violations": {
                "timeline": interval_violations,
                "duration": duration_violations,
                "markers": marker_violations,
                "owners": owner_violations,
                "script_mapping": script_mapping_violations,
            },
        }

    def generation_artifact_status(self, project_id: str, shot_id: Optional[str] = None) -> Dict[str, Any]:
        project_dir = self.get_project_dir(project_id)
        state = self._load_state(project_dir)
        config = state.get("config") or _default_config()
        flat_shots, _ = self._project_shot_data(project_dir)
        scope = config.get("shot_scope") or {"mode": "all"}
        relevant: List[Dict[str, Any]] = []
        for unit in flat_shots:
            if shot_id and str(unit.get("id")) != shot_id:
                continue
            if not shot_id and scope.get("mode") == "selected" and str(unit.get("id")) not in set(scope.get("shot_ids") or []):
                continue
            if not shot_id and scope.get("mode") == "range":
                unit_start = float(unit.get("start") or 0.0)
                unit_end = float(unit.get("end") or unit_start)
                if unit_end <= float(scope.get("start") or 0.0) or unit_start >= float(scope.get("end") or 0.0):
                    continue
            relevant.append(unit)
        assets = self._project_assets(project_dir, relevant)
        unit_status: Dict[str, Dict[str, Any]] = {}
        missing: List[str] = []
        for unit in relevant:
            unit_id = str(unit.get("id"))
            delivery_ids = {str(value) for value in unit.get("delivery_asset_ids") or []}
            candidates = [
                asset
                for asset in assets
                if (asset.get("effective_approval") or {}).get("decision") == "approve"
                and asset.get("kind") == "image"
                and (
                    unit_id in {str(value) for value in asset.get("owner_unit_ids") or []}
                    or str(asset.get("asset_id")) in delivery_ids
                )
                and (asset.get("result_kind") in {None, "first_frame"})
            ]
            candidates.sort(
                key=lambda value: (
                    str(value.get("created_at") or ""),
                    str(value.get("version") or ""),
                    str(value.get("asset_id") or ""),
                ),
                reverse=True,
            )
            current = candidates[0] if candidates else None
            if current is None:
                missing.append(unit_id)
            unit_status[unit_id] = {
                "status": "ready" if current else "waiting",
                "current_approved_asset_id": current.get("asset_id") if current else None,
                "current_approved_asset_path": current.get("path") if current else None,
                "current_approved_asset_version": current.get("version") if current else None,
                "approved_candidate_count": len(candidates),
            }
        return {
            "status": "ready" if relevant and not missing else "waiting",
            "code": None if relevant and not missing else "IMAGE_GENERATION_ADAPTER_UNCONFIGURED",
            "checked_unit_ids": [str(unit.get("id")) for unit in relevant],
            "missing_unit_ids": missing,
            "current_approved_asset_ids": {
                unit_id: value["current_approved_asset_id"]
                for unit_id, value in unit_status.items()
                if value.get("current_approved_asset_id")
            },
            "units": unit_status,
            "adapter": "manual_result_ingest" if any(value.get("current_approved_asset_id") for value in unit_status.values()) else "unconfigured",
        }

    def project_video_path(self, project_id: str) -> Optional[Path]:
        project_dir = self.get_project_dir(project_id)
        state = self._load_state(project_dir)
        video = state.get("video") or {}
        relative = video.get("path")
        if not relative:
            return None
        path = safe_join(project_dir, relative)
        return path if path.is_file() else None

    def project_config(self, project_id: str) -> Dict[str, Any]:
        return dict(self._load_state(self.get_project_dir(project_id)).get("config") or _default_config())

    def mark_video_analyzed(self, project_id: str) -> None:
        project_dir = self.get_project_dir(project_id)
        with self._lock:
            state = self._load_state(project_dir)
            if state.get("video"):
                state["video"]["analysis_status"] = "assets_extracted"
                state["video"]["analyzed_at"] = now_iso()
                state["updated_at"] = now_iso()
                atomic_write_json(self._workbench_path(project_dir), state)

    def mark_docx_qa_pending(self, project_id: str) -> None:
        project_dir = self.get_project_dir(project_id)
        workflow_path = project_dir / "planning" / "workflow_state.json"
        workflow = read_json(workflow_path, {})
        if not isinstance(workflow, dict):
            workflow = {}
        workflow.update(
            {
                "docx_visual_qa_status": "pending",
                "docx_visual_qa_blocked_reason": "DOCX_VISUAL_QA_REQUIRED",
                "updated_at": now_iso(),
            }
        )
        atomic_write_json(workflow_path, workflow)

    def _docx_render_state(self, project_dir: Path) -> Dict[str, Any]:
        manifest_path = project_dir / "review" / "docx_render_manifest.json"
        if not manifest_path.is_file():
            return {
                "status": "waiting",
                "code": "DOCX_RENDER_MANIFEST_REQUIRED",
                "document": None,
                "pages": [],
            }
        manifest = read_json(manifest_path, {})
        if not isinstance(manifest, dict):
            return {"status": "blocked", "code": "INVALID_DOCX_RENDER_MANIFEST", "document": None, "pages": []}
        document_relative = str(manifest.get("document_path") or "")
        try:
            document_path = safe_join(project_dir, document_relative)
        except ApiError:
            return {"status": "blocked", "code": "INVALID_DOCX_RENDER_DOCUMENT_PATH", "document": None, "pages": []}
        if not document_path.is_file() or document_path.suffix.lower() != ".docx" or not document_relative.startswith("exports/"):
            return {"status": "waiting", "code": "DOCX_RENDER_DOCUMENT_MISSING", "document": None, "pages": []}
        document_sha256 = sha256_file(document_path)
        if manifest.get("document_sha256") != document_sha256:
            return {
                "status": "blocked",
                "code": "DOCX_RENDER_DOCUMENT_HASH_MISMATCH",
                "document": {"path": document_relative, "sha256": document_sha256},
                "pages": [],
            }
        raw_pages = manifest.get("pages")
        if not isinstance(raw_pages, list) or not raw_pages:
            return {
                "status": "waiting",
                "code": "DOCX_RENDER_PAGES_REQUIRED",
                "document": {"path": document_relative, "sha256": document_sha256},
                "pages": [],
            }
        pages: List[Dict[str, Any]] = []
        for index, raw_page in enumerate(raw_pages):
            if not isinstance(raw_page, dict):
                return {"status": "blocked", "code": "INVALID_DOCX_RENDER_PAGE", "document": None, "pages": []}
            relative = str(raw_page.get("path") or "")
            try:
                page_path = safe_join(project_dir, relative)
            except ApiError:
                return {"status": "blocked", "code": "INVALID_DOCX_RENDER_PAGE_PATH", "document": None, "pages": []}
            if (
                not page_path.is_file()
                or page_path.suffix.lower() not in VALID_IMAGE_EXTENSIONS
                or not relative.startswith(("review/", "exports/"))
            ):
                return {"status": "waiting", "code": "DOCX_RENDER_PAGE_MISSING", "document": None, "pages": []}
            actual_sha256 = sha256_file(page_path)
            if raw_page.get("sha256") != actual_sha256:
                return {"status": "blocked", "code": "DOCX_RENDER_PAGE_HASH_MISMATCH", "document": None, "pages": []}
            media_metadata = self.toolchain.inspect_image(page_path)
            if media_metadata.get("status") != "ready":
                return {
                    "status": "blocked",
                    "code": "DOCX_RENDER_PAGE_NOT_DECODABLE",
                    "document": None,
                    "pages": [],
                    "details": media_metadata,
                }
            pages.append(
                {
                    "page": int(raw_page.get("page") or index + 1),
                    "path": relative,
                    "sha256": actual_sha256,
                    "media_metadata": media_metadata,
                    "media_url": "/api/v1/projects/%s/media/%s" % (project_dir.name, quoted_path(relative)),
                }
            )
        pages.sort(key=lambda value: value["page"])
        expected_pages = list(range(1, len(pages) + 1))
        if [value["page"] for value in pages] != expected_pages:
            return {"status": "blocked", "code": "DOCX_RENDER_PAGE_SEQUENCE_INVALID", "document": None, "pages": []}
        return {
            "status": "ready",
            "code": None,
            "manifest_path": manifest_path.relative_to(project_dir).as_posix(),
            "document": {
                "path": document_relative,
                "sha256": document_sha256,
                "media_url": "/api/v1/projects/%s/media/%s" % (project_dir.name, quoted_path(document_relative)),
            },
            "pages": pages,
        }

    def _docx_qa_receipt_state(
        self,
        project_dir: Path,
        workflow: Dict[str, Any],
        render_state: Dict[str, Any],
    ) -> Dict[str, Any]:
        """Verify that a passed QA flag still points to the exact current render."""
        visual_status = str(workflow.get("docx_visual_qa_status") or "").lower()
        if visual_status not in {"passed", "approved", "complete", "completed"}:
            return {"status": "waiting", "code": "DOCX_VISUAL_QA_REQUIRED"}
        if render_state.get("status") != "ready":
            return {"status": "blocked", "code": "DOCX_QA_RENDER_STALE"}
        relative = str(workflow.get("docx_visual_qa_receipt_path") or "")
        if not relative.startswith("review/docx-qa-receipts/") or not relative.endswith(".json"):
            return {"status": "blocked", "code": "DOCX_QA_RECEIPT_REQUIRED"}
        try:
            receipt_path = safe_join(project_dir, relative)
        except ApiError:
            return {"status": "blocked", "code": "DOCX_QA_RECEIPT_PATH_INVALID"}
        if not receipt_path.is_file():
            return {"status": "blocked", "code": "DOCX_QA_RECEIPT_MISSING"}
        receipt = read_json(receipt_path, {})
        if not isinstance(receipt, dict) or receipt.get("decision") != "approve":
            return {"status": "blocked", "code": "DOCX_QA_RECEIPT_INVALID"}
        document = render_state.get("document") or {}
        pages = render_state.get("pages") or []
        expected_pages = [
            {"page": value.get("page"), "path": value.get("path"), "sha256": value.get("sha256")}
            for value in pages
        ]
        received_pages = [
            {"page": value.get("page"), "path": value.get("path"), "sha256": value.get("sha256")}
            for value in receipt.get("pages") or []
            if isinstance(value, dict)
        ]
        exact = (
            receipt.get("project_id") == project_dir.name
            and receipt.get("document_path") == document.get("path")
            and str(receipt.get("document_sha256") or "").lower() == str(document.get("sha256") or "").lower()
            and received_pages == expected_pages
            and str(workflow.get("final_document_sha256") or "").lower() == str(document.get("sha256") or "").lower()
            and workflow.get("final_render_page_count") == len(pages)
        )
        if not exact:
            return {"status": "blocked", "code": "DOCX_QA_RECEIPT_STALE", "receipt_path": relative}
        return {"status": "ready", "code": None, "receipt_path": relative, "receipt": receipt}

    def record_docx_qa(self, project_id: str, payload: Dict[str, Any]) -> Dict[str, Any]:
        project_dir = self.get_project_dir(project_id)
        decision = str(payload.get("decision") or "")
        if decision not in {"approve", "reject"}:
            raise ApiError(400, "INVALID_DOCX_QA_DECISION", "decision must be approve or reject")
        document_sha256 = str(payload.get("document_sha256") or "").lower()
        if not re.fullmatch(r"[a-f0-9]{64}", document_sha256):
            raise ApiError(400, "INVALID_DOCX_SHA256", "document_sha256 must be a SHA-256 hex digest")
        page_sha256s = payload.get("page_sha256s")
        if not isinstance(page_sha256s, list) or not page_sha256s or not all(
            isinstance(value, str) and re.fullmatch(r"[a-fA-F0-9]{64}", value) for value in page_sha256s
        ):
            raise ApiError(400, "INVALID_DOCX_PAGE_SHA256S", "page_sha256s must be a non-empty ordered SHA-256 list")
        page_sha256s = [value.lower() for value in page_sha256s]
        reason = str(payload.get("reason") or "").strip()
        if decision == "reject" and not reason:
            raise ApiError(400, "DOCX_REJECTION_REASON_REQUIRED", "A rejected document requires a reason")
        if len(reason) > 4000:
            raise ApiError(400, "DOCX_QA_REASON_TOO_LONG", "reason cannot exceed 4000 characters")
        if decision == "approve":
            delivery_preflight = self.validate_docx_export_preflight(project_id)
            if delivery_preflight.get("status") != "ready":
                raise ApiError(
                    409,
                    str(delivery_preflight.get("code") or "DELIVERY_PREFLIGHT_INPUTS_STALE"),
                    "Current delivery inputs are not covered by a verified export preflight receipt",
                    delivery_preflight,
                )
        render = self._docx_render_state(project_dir)
        if render.get("status") != "ready":
            raise ApiError(409, str(render.get("code") or "DOCX_RENDER_PAGES_REQUIRED"), "Current verified Word render pages are required", render)
        expected_document_sha256 = str((render.get("document") or {}).get("sha256") or "").lower()
        expected_page_sha256s = [str(value.get("sha256") or "").lower() for value in render.get("pages") or []]
        if document_sha256 != expected_document_sha256 or page_sha256s != expected_page_sha256s:
            raise ApiError(
                409,
                "DOCX_QA_HASH_MISMATCH",
                "QA receipt hashes must exactly match the current document and every rendered page in order",
                {
                    "expected_document_sha256": expected_document_sha256,
                    "expected_page_sha256s": expected_page_sha256s,
                },
            )
        receipt = {
            "schema_version": "workbench-docx-visual-qa-receipt-v1",
            "id": new_id("docxqa"),
            "project_id": project_id,
            "decision": decision,
            "document_path": render["document"]["path"],
            "document_sha256": expected_document_sha256,
            "pages": [
                {"page": value["page"], "path": value["path"], "sha256": value["sha256"]}
                for value in render["pages"]
            ],
            "reason": reason or None,
            "created_at": now_iso(),
        }
        receipt_path = project_dir / "review" / "docx-qa-receipts" / (receipt["id"] + ".json")
        atomic_write_json(receipt_path, receipt)
        workflow_path = project_dir / "planning" / "workflow_state.json"
        workflow = read_json(workflow_path, {})
        if not isinstance(workflow, dict):
            workflow = {}
        pending_inputs = [str(value) for value in workflow.get("pending_inputs") or []]
        if decision == "approve":
            pending_inputs = [value for value in pending_inputs if value != "document_visual_qa"]
            workflow.update(
                {
                    "docx_visual_qa_status": "passed",
                    "docx_visual_qa_blocked_reason": None,
                    "docx_visual_qa_receipt_path": receipt_path.relative_to(project_dir).as_posix(),
                    "final_render_page_count": len(render["pages"]),
                    "final_document_sha256": expected_document_sha256,
                    "pending_inputs": pending_inputs,
                    "updated_at": now_iso(),
                }
            )
        else:
            if "revise_docx_after_visual_rejection" not in pending_inputs:
                pending_inputs.append("revise_docx_after_visual_rejection")
            workflow.update(
                {
                    "status": "docx_visual_qa_rejected",
                    "docx_export_authorized": False,
                    "docx_visual_qa_status": "rejected",
                    "docx_visual_qa_blocked_reason": "DOCX_VISUAL_QA_REJECTED",
                    "docx_visual_qa_receipt_path": receipt_path.relative_to(project_dir).as_posix(),
                    "pending_inputs": pending_inputs,
                    "updated_at": now_iso(),
                }
            )
        atomic_write_json(workflow_path, workflow)
        return {"ok": True, "receipt": receipt, "docx_qa": self.get_project(project_id)["docx_qa"]}
