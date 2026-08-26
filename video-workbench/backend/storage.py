"""Filesystem primitives with atomic writes and traversal protection."""

from __future__ import annotations

import hashlib
import json
import os
import re
import tempfile
import unicodedata
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any, BinaryIO, Dict, Optional, Tuple
from urllib.parse import quote

from .errors import ApiError


IDENTIFIER_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
SAFE_FILENAME_RE = re.compile(r"[^A-Za-z0-9._-]+")


def now_iso() -> str:
    return datetime.now().astimezone().replace(microsecond=0).isoformat()


def validate_identifier(value: str, label: str = "id") -> str:
    if not isinstance(value, str) or not IDENTIFIER_RE.fullmatch(value):
        raise ApiError(400, "INVALID_IDENTIFIER", "%s contains unsafe characters" % label)
    if value in {".", ".."}:
        raise ApiError(400, "INVALID_IDENTIFIER", "%s is not allowed" % label)
    return value


def slugify(value: str, fallback: str = "item") -> str:
    normalized = unicodedata.normalize("NFKC", value or "").strip().lower()
    ascii_part = normalized.encode("ascii", "ignore").decode("ascii")
    ascii_part = SAFE_FILENAME_RE.sub("-", ascii_part).strip("-._")
    return (ascii_part[:48] or fallback).strip("-._") or fallback


def safe_filename(value: str, fallback: str = "upload.bin") -> str:
    # Both POSIX and Windows separators are stripped even though the server is local.
    leaf = (value or "").replace("\\", "/").rsplit("/", 1)[-1]
    leaf = unicodedata.normalize("NFKC", leaf).strip().replace("\x00", "")
    suffix = Path(leaf).suffix.lower()[:16]
    stem = Path(leaf).stem
    safe_stem = SAFE_FILENAME_RE.sub("-", stem.encode("ascii", "ignore").decode("ascii")).strip("-._")
    if not safe_stem:
        safe_stem = Path(fallback).stem
    safe_suffix = suffix if re.fullmatch(r"\.[a-z0-9]{1,15}", suffix or "") else Path(fallback).suffix
    return (safe_stem[:80] + safe_suffix).strip(".") or fallback


def safe_join(root: Path, relative: str) -> Path:
    if not isinstance(relative, str) or not relative or "\x00" in relative:
        raise ApiError(400, "INVALID_PATH", "A non-empty relative path is required")
    if relative.startswith(("/", "\\")) or re.match(r"^[A-Za-z]:", relative):
        raise ApiError(403, "PATH_TRAVERSAL_BLOCKED", "Absolute paths are not allowed")
    normalized = relative.replace("\\", "/")
    candidate = (root / normalized).resolve(strict=False)
    resolved_root = root.resolve(strict=False)
    if candidate != resolved_root and resolved_root not in candidate.parents:
        raise ApiError(403, "PATH_TRAVERSAL_BLOCKED", "Path must remain inside the allowed root")
    return candidate


def read_json(path: Path, default: Optional[Any] = None) -> Any:
    if not path.is_file():
        if default is not None:
            return default
        raise ApiError(404, "FILE_NOT_FOUND", "JSON file does not exist", {"path": str(path)})
    try:
        with path.open("r", encoding="utf-8") as handle:
            return json.load(handle)
    except (OSError, json.JSONDecodeError) as exc:
        raise ApiError(500, "INVALID_JSON_FILE", "Stored JSON cannot be read", {"path": str(path), "reason": str(exc)})


def atomic_write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(prefix=path.name + ".", suffix=".tmp", dir=str(path.parent))
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            json.dump(value, handle, ensure_ascii=False, indent=2)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary_name, str(path))
    finally:
        if os.path.exists(temporary_name):
            os.unlink(temporary_name)


def append_json_line(path: Path, value: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    line = json.dumps(value, ensure_ascii=False, separators=(",", ":")) + "\n"
    with path.open("a", encoding="utf-8") as handle:
        handle.write(line)
        handle.flush()
        os.fsync(handle.fileno())


def sha256_file(path: Path, chunk_size: int = 1024 * 1024) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while True:
            chunk = handle.read(chunk_size)
            if not chunk:
                break
            digest.update(chunk)
    return digest.hexdigest()


def copy_stream_atomic(source: BinaryIO, destination: Path, maximum_bytes: int) -> Tuple[int, str]:
    destination.parent.mkdir(parents=True, exist_ok=True)
    digest = hashlib.sha256()
    total = 0
    descriptor, temporary_name = tempfile.mkstemp(prefix=destination.name + ".", suffix=".upload", dir=str(destination.parent))
    try:
        with os.fdopen(descriptor, "wb") as output:
            while True:
                chunk = source.read(1024 * 1024)
                if not chunk:
                    break
                total += len(chunk)
                if total > maximum_bytes:
                    raise ApiError(413, "UPLOAD_TOO_LARGE", "Uploaded file exceeds the configured limit")
                digest.update(chunk)
                output.write(chunk)
            output.flush()
            os.fsync(output.fileno())
        if total == 0:
            raise ApiError(422, "EMPTY_UPLOAD", "Uploaded file is empty")
        os.replace(temporary_name, str(destination))
    finally:
        if os.path.exists(temporary_name):
            os.unlink(temporary_name)
    return total, digest.hexdigest()


def new_id(prefix: str) -> str:
    return "%s-%s-%s" % (prefix, datetime.now().strftime("%Y%m%d%H%M%S"), uuid.uuid4().hex[:8])


def quoted_path(relative: str) -> str:
    return "/".join(quote(part, safe="") for part in Path(relative).as_posix().split("/"))
