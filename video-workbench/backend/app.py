"""Standard-library HTTP API and static-file host for the desktop workbench."""

from __future__ import annotations

import argparse
import cgi
import json
import mimetypes
import os
import re
import sys
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import parse_qs, unquote, urlparse

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    from backend.errors import ApiError
    from backend.service import WorkbenchService
    from backend.storage import safe_join
else:
    from .errors import ApiError
    from .service import WorkbenchService
    from .storage import safe_join


def is_allowed_local_origin(origin: str, port: int) -> bool:
    """Allow only this server's loopback browser origins."""
    try:
        parsed = urlparse(origin)
        origin_port = parsed.port
    except ValueError:
        return False
    if parsed.scheme != "http" or parsed.hostname not in {"127.0.0.1", "localhost"}:
        return False
    if parsed.username or parsed.password or parsed.path not in {"", "/"} or parsed.params or parsed.query or parsed.fragment:
        return False
    return (origin_port if origin_port is not None else 80) == int(port)


def static_file_cache_enabled(filename: str) -> bool:
    """Workbench source assets are edited in place and must never be stale."""
    return False


class WorkbenchHTTPServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True

    def __init__(
        self,
        server_address: Tuple[str, int],
        handler: Any,
        service: WorkbenchService,
        static_root: Optional[Path],
    ) -> None:
        super().__init__(server_address, handler)
        self.service = service
        self.static_root = static_root


class WorkbenchHandler(BaseHTTPRequestHandler):
    server_version = "JimengWorkbench/0.1"
    protocol_version = "HTTP/1.1"

    @property
    def service(self) -> WorkbenchService:
        return self.server.service  # type: ignore[attr-defined]

    def log_message(self, fmt: str, *args: Any) -> None:
        sys.stderr.write("[%s] %s\n" % (self.log_date_time_string(), fmt % args))

    def _common_headers(self) -> None:
        origin = self.headers.get("Origin")
        current_port = int(self.server.server_address[1])  # type: ignore[attr-defined]
        if origin and is_allowed_local_origin(origin, current_port):
            self.send_header("Access-Control-Allow-Origin", origin)
            self.send_header("Vary", "Origin")
        self.send_header("Access-Control-Allow-Headers", "Content-Type, Range")
        self.send_header("Access-Control-Allow-Methods", "GET, HEAD, POST, PUT, PATCH, OPTIONS")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")

    def _json(self, status: int, payload: Dict[str, Any]) -> None:
        body = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        self.send_response(status)
        self._common_headers()
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def _read_json(self, maximum: int = 10 * 1024 * 1024) -> Dict[str, Any]:
        content_type = self.headers.get("Content-Type", "").split(";", 1)[0].strip().lower()
        if content_type != "application/json":
            raise ApiError(415, "JSON_REQUIRED", "Content-Type must be application/json")
        try:
            length = int(self.headers.get("Content-Length") or "0")
        except ValueError:
            raise ApiError(400, "INVALID_CONTENT_LENGTH", "Content-Length is invalid")
        if length <= 0:
            raise ApiError(400, "EMPTY_REQUEST_BODY", "JSON request body is required")
        if length > maximum:
            raise ApiError(413, "REQUEST_TOO_LARGE", "JSON request body exceeds the limit")
        try:
            value = json.loads(self.rfile.read(length).decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ApiError(400, "INVALID_JSON", "Request body is not valid JSON", {"reason": str(exc)})
        if not isinstance(value, dict):
            raise ApiError(400, "JSON_OBJECT_REQUIRED", "JSON request body must be an object")
        return value

    def _multipart(self, maximum: int) -> Tuple[Dict[str, Any], Dict[str, List[Any]]]:
        content_type = self.headers.get("Content-Type", "")
        if not content_type.lower().startswith("multipart/form-data"):
            raise ApiError(415, "MULTIPART_REQUIRED", "Content-Type must be multipart/form-data")
        try:
            length = int(self.headers.get("Content-Length") or "0")
        except ValueError:
            raise ApiError(400, "INVALID_CONTENT_LENGTH", "Content-Length is invalid")
        if length <= 0:
            raise ApiError(400, "EMPTY_REQUEST_BODY", "Multipart request body is required")
        if length > maximum:
            raise ApiError(413, "UPLOAD_TOO_LARGE", "Multipart request exceeds the configured limit")
        environment = {
            "REQUEST_METHOD": self.command,
            "CONTENT_TYPE": content_type,
            "CONTENT_LENGTH": str(length),
        }
        form = cgi.FieldStorage(fp=self.rfile, headers=self.headers, environ=environment, keep_blank_values=True)
        fields: Dict[str, Any] = {}
        files: Dict[str, List[Any]] = {}
        for key in form.keys():
            values = form[key] if isinstance(form[key], list) else [form[key]]
            for item in values:
                if item.filename:
                    files.setdefault(key, []).append(item)
                else:
                    if key in fields:
                        prior = fields[key] if isinstance(fields[key], list) else [fields[key]]
                        prior.append(item.value)
                        fields[key] = prior
                    else:
                        fields[key] = item.value
        return fields, files

    def _query(self) -> Dict[str, List[str]]:
        return parse_qs(urlparse(self.path).query, keep_blank_values=True)

    def _parts(self) -> List[str]:
        path = urlparse(self.path).path
        return [unquote(value) for value in path.strip("/").split("/") if value]

    def _api_parts(self) -> Optional[List[str]]:
        parts = self._parts()
        if len(parts) >= 2 and parts[:2] == ["api", "v1"]:
            return parts[2:]
        return None

    def _serve_file(self, path: Path, cache: bool = False) -> None:
        size = path.stat().st_size
        content_type = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
        range_header = self.headers.get("Range")
        start = 0
        end = size - 1
        status = HTTPStatus.OK
        if range_header:
            match = re.fullmatch(r"bytes=(\d*)-(\d*)", range_header.strip())
            if not match:
                raise ApiError(416, "INVALID_RANGE", "Only one byte range is supported")
            first, last = match.groups()
            if first:
                start = int(first)
                end = int(last) if last else size - 1
            elif last:
                suffix_length = int(last)
                start = max(0, size - suffix_length)
                end = size - 1
            if start < 0 or end < start or start >= size:
                self.send_response(HTTPStatus.REQUESTED_RANGE_NOT_SATISFIABLE)
                self._common_headers()
                self.send_header("Content-Range", "bytes */%d" % size)
                self.send_header("Content-Length", "0")
                self.end_headers()
                return
            end = min(end, size - 1)
            status = HTTPStatus.PARTIAL_CONTENT
        length = max(0, end - start + 1)
        self.send_response(status)
        self._common_headers()
        self.send_header("Content-Type", content_type)
        self.send_header("Accept-Ranges", "bytes")
        self.send_header("Content-Length", str(length))
        self.send_header("Cache-Control", "public, max-age=31536000, immutable" if cache else "no-store")
        self.send_header("ETag", '"%x-%x"' % (size, int(path.stat().st_mtime)))
        if status == HTTPStatus.PARTIAL_CONTENT:
            self.send_header("Content-Range", "bytes %d-%d/%d" % (start, end, size))
        self.end_headers()
        if self.command == "HEAD" or length == 0:
            return
        with path.open("rb") as handle:
            handle.seek(start)
            remaining = length
            while remaining:
                chunk = handle.read(min(1024 * 1024, remaining))
                if not chunk:
                    break
                self.wfile.write(chunk)
                remaining -= len(chunk)

    def _serve_static(self) -> None:
        static_root = self.server.static_root  # type: ignore[attr-defined]
        if static_root is None or not static_root.is_dir():
            raise ApiError(404, "FRONTEND_NOT_BUILT", "Frontend files were not found")
        path = urlparse(self.path).path
        relative = unquote(path.lstrip("/")) or "index.html"
        candidate = safe_join(static_root, relative)
        if not candidate.is_file() and "." not in Path(relative).name:
            candidate = static_root / "index.html"
        if not candidate.is_file():
            raise ApiError(404, "STATIC_FILE_NOT_FOUND", "Frontend file was not found")
        self._serve_file(candidate, cache=static_file_cache_enabled(candidate.name))

    def _project_action(self, project_id: str, operation: str, payload: Optional[Dict[str, Any]] = None) -> None:
        task = self.service.tasks.create_task({"project_id": project_id, "operation": operation, **(payload or {})})
        task = self.service.tasks.start(task["id"])
        self._json(202, {"ok": True, "task": task})

    def _dispatch_get(self, parts: List[str]) -> None:
        if not parts or parts == ["health"]:
            self._json(200, {"ok": True, "status": "ready", "version": "0.1.0"})
            return
        if parts == ["bootstrap"]:
            self._json(200, self.service.bootstrap())
            return
        if parts == ["projects"]:
            self._json(200, {"ok": True, "projects": self.service.list_projects()})
            return
        if len(parts) == 2 and parts[0] == "projects":
            self._json(200, {"ok": True, "project": self.service.get_project(parts[1])})
            return
        if len(parts) == 3 and parts[0] == "projects" and parts[2] == "status":
            self._json(200, self.service.project_status(parts[1]))
            return
        if len(parts) == 3 and parts[0] == "projects" and parts[2] == "script":
            self._json(200, self.service.get_script(parts[1]))
            return
        if len(parts) == 3 and parts[0] == "projects" and parts[2] == "markers":
            self._json(200, self.service.get_markers(parts[1]))
            return
        if len(parts) == 3 and parts[0] == "projects" and parts[2] == "approvals":
            self._json(200, self.service.list_approvals(parts[1]))
            return
        if len(parts) >= 4 and parts[0] == "projects" and parts[2] == "media":
            relative = "/".join(parts[3:])
            self._serve_file(self.service.project_media_path(parts[1], relative))
            return
        if parts == ["knowledge"]:
            self._json(200, self.service.list_knowledge())
            return
        if len(parts) == 3 and parts[0] == "knowledge" and parts[1] in {"products", "avatars"}:
            asset = self.service.find_knowledge(parts[1], parts[2])
            self._json(200, {"ok": True, "asset": asset})
            return
        if len(parts) >= 5 and parts[0] == "knowledge" and parts[3] == "media":
            filename = "/".join(parts[4:])
            self._serve_file(self.service.knowledge_media_path(parts[1], parts[2], filename), cache=True)
            return
        if len(parts) >= 2 and parts[0] == "skill-media":
            self._serve_file(self.service.skill_media_path("/".join(parts[1:])), cache=True)
            return
        if parts == ["tasks"]:
            query = self._query()
            project_id = (query.get("project_id") or [None])[0]
            self._json(200, {"ok": True, "tasks": self.service.tasks.list_tasks(project_id=project_id)})
            return
        if len(parts) == 2 and parts[0] == "tasks":
            self._json(200, {"ok": True, "task": self.service.tasks.get_task(parts[1])})
            return
        if len(parts) == 3 and parts[0] == "tasks" and parts[2] == "events":
            query = self._query()
            try:
                after = int((query.get("after") or ["0"])[0])
            except ValueError:
                raise ApiError(400, "INVALID_EVENT_CURSOR", "after must be an integer")
            self._json(200, self.service.tasks.events(parts[1], after=max(0, after)))
            return
        raise ApiError(404, "API_ROUTE_NOT_FOUND", "API route was not found")

    def _dispatch_post(self, parts: List[str]) -> None:
        if parts == ["projects"]:
            project = self.service.create_project(self._read_json())
            self._json(201, {"ok": True, "project": project})
            return
        if len(parts) == 3 and parts[0] == "projects" and parts[2] in {"video", "source"}:
            _, files = self._multipart(self.service.maximum_video_bytes + 1024 * 1024)
            uploads = files.get("video") or []
            if len(uploads) != 1:
                raise ApiError(400, "VIDEO_FIELD_REQUIRED", "Multipart field 'video' must contain exactly one file")
            item = uploads[0]
            result = self.service.upload_video(parts[1], item.file, item.filename)
            self._json(201, result)
            return
        if len(parts) == 3 and parts[0] == "projects" and parts[2] == "markers":
            self._json(201, self.service.add_marker(parts[1], self._read_json()))
            return
        if len(parts) == 3 and parts[0] == "projects" and parts[2] == "approvals":
            self._json(201, self.service.record_approval(parts[1], self._read_json()))
            return
        if len(parts) == 4 and parts[0] == "projects" and parts[2:4] == ["bindings", "apply"]:
            result = self.service.apply_bindings(parts[1])
            self._json(202 if result.get("task") else 200, result)
            return
        if len(parts) == 3 and parts[0] == "projects" and parts[2] == "docx-qa":
            self._json(201, self.service.record_docx_qa(parts[1], self._read_json()))
            return
        action_map = {
            "analyze": "analyze",
            "lint": "lint",
            "compile": "compile",
            "verify": "verify",
            "export-docx": "export_docx",
            "align": "align",
        }
        if len(parts) == 3 and parts[0] == "projects" and parts[2] in action_map:
            self._project_action(parts[1], action_map[parts[2]])
            return
        if len(parts) == 4 and parts[0] == "projects" and parts[2:4] == ["shots", "extract-frames"]:
            self._project_action(parts[1], "extract_frames")
            return
        if len(parts) == 5 and parts[0] == "projects" and parts[2] == "shots" and parts[4] == "results":
            fields, files = self._multipart(self.service.maximum_video_bytes + 1024 * 1024)
            uploads = files.get("file") or []
            if len(uploads) != 1:
                raise ApiError(400, "RESULT_FILE_REQUIRED", "Multipart field 'file' must contain exactly one result file")
            item = uploads[0]
            self._json(201, self.service.upload_shot_result(parts[1], parts[3], item.file, item.filename, fields))
            return
        if len(parts) == 5 and parts[0] == "projects" and parts[2] == "shots" and parts[4] == "retry":
            payload = self._read_json() if int(self.headers.get("Content-Length") or "0") else {}
            payload.update({"project_id": parts[1], "operation": "retry_shot", "shot_id": parts[3]})
            task = self.service.tasks.create_task(payload)
            task = self.service.tasks.start(task["id"])
            self._json(202, {"ok": True, "task": task})
            return
        if len(parts) == 5 and parts[0] == "projects" and parts[2] == "shots" and parts[4] == "split-plan":
            self._json(201, self.service.create_shot_split_plan(parts[1], parts[3], self._read_json()))
            return
        if len(parts) == 6 and parts[0] == "projects" and parts[2] == "shots" and parts[4:6] == ["split-plan", "confirm"]:
            payload = self._read_json()
            self._json(200, self.service.confirm_shot_split_plan(parts[1], parts[3], str(payload.get("plan_id") or "")))
            return
        if len(parts) == 2 and parts[0] == "knowledge" and parts[1] in {"products", "avatars"}:
            fields, files = self._multipart(self.service.maximum_knowledge_bytes * 8 + 1024 * 1024)
            uploads = files.get("file") or []
            if not uploads:
                raise ApiError(400, "FILE_FIELD_REQUIRED", "Multipart field 'file' must contain at least one file")
            result = self.service.upload_knowledge_batch(
                parts[1],
                [(item.file, item.filename) for item in uploads],
                fields,
            )
            self._json(201, result)
            return
        if parts == ["tasks"]:
            task = self.service.tasks.create_task(self._read_json())
            self._json(201, {"ok": True, "task": task})
            return
        if len(parts) == 3 and parts[0] == "tasks" and parts[2] in {"start", "pause", "resume", "cancel"}:
            action = getattr(self.service.tasks, parts[2])
            task = action(parts[1])
            self._json(202 if parts[2] in {"start", "resume"} else 200, {"ok": True, "task": task})
            return
        if len(parts) == 3 and parts[0] == "tasks" and parts[2] == "retry":
            task = self.service.tasks.retry(parts[1])
            self._json(202, {"ok": True, "task": task})
            return
        raise ApiError(404, "API_ROUTE_NOT_FOUND", "API route was not found")

    def _dispatch_put(self, parts: List[str]) -> None:
        if len(parts) == 3 and parts[0] == "knowledge" and parts[1] in {"products", "avatars"}:
            self._json(200, self.service.update_knowledge(parts[1], parts[2], self._read_json()))
            return
        if len(parts) == 3 and parts[0] == "projects" and parts[2] == "config":
            self._json(200, self.service.save_config(parts[1], self._read_json()))
            return
        if len(parts) == 3 and parts[0] == "projects" and parts[2] == "script":
            self._json(200, self.service.save_script_payload(parts[1], self._read_json()))
            return
        if len(parts) == 3 and parts[0] == "projects" and parts[2] == "story-plan":
            self._json(200, self.service.save_story_plan(parts[1], self._read_json()))
            return
        raise ApiError(404, "API_ROUTE_NOT_FOUND", "API route was not found")

    def _handle(self) -> None:
        try:
            origin = self.headers.get("Origin")
            current_port = int(self.server.server_address[1])  # type: ignore[attr-defined]
            if origin and not is_allowed_local_origin(origin, current_port):
                raise ApiError(403, "ORIGIN_NOT_ALLOWED", "Only this workbench's loopback browser origin may call the local API")
            parts = self._api_parts()
            if parts is None:
                if self.command in {"GET", "HEAD"}:
                    self._serve_static()
                    return
                raise ApiError(404, "ROUTE_NOT_FOUND", "Route was not found")
            if self.command in {"GET", "HEAD"}:
                self._dispatch_get(parts)
            elif self.command == "POST":
                self._dispatch_post(parts)
            elif self.command in {"PUT", "PATCH"}:
                self._dispatch_put(parts)
            else:
                raise ApiError(405, "METHOD_NOT_ALLOWED", "HTTP method is not supported")
        except ApiError as exc:
            self._json(exc.status, exc.payload())
        except (BrokenPipeError, ConnectionResetError):
            return
        except Exception as exc:
            details = {"reason": str(exc)} if os.environ.get("VIDEO_WORKBENCH_DEBUG") == "1" else None
            error = ApiError(500, "INTERNAL_SERVER_ERROR", "The local workbench server encountered an error", details)
            self._json(500, error.payload())

    def do_GET(self) -> None:
        self._handle()

    def do_HEAD(self) -> None:
        self._handle()

    def do_POST(self) -> None:
        self._handle()

    def do_PUT(self) -> None:
        self._handle()

    def do_PATCH(self) -> None:
        self._handle()

    def do_OPTIONS(self) -> None:
        try:
            origin = self.headers.get("Origin")
            current_port = int(self.server.server_address[1])  # type: ignore[attr-defined]
            if origin and not is_allowed_local_origin(origin, current_port):
                raise ApiError(403, "ORIGIN_NOT_ALLOWED", "Only this workbench's loopback browser origin may call the local API")
            self.send_response(204)
            self._common_headers()
            self.send_header("Content-Length", "0")
            self.end_headers()
        except ApiError as exc:
            self._json(exc.status, exc.payload())


def default_static_root() -> Optional[Path]:
    frontend = Path(__file__).resolve().parent.parent / "frontend"
    dist = frontend / "dist"
    if (dist / "index.html").is_file():
        return dist
    if (frontend / "index.html").is_file():
        return frontend
    return None


def create_server(
    service: Optional[WorkbenchService] = None,
    host: str = "127.0.0.1",
    port: int = 8765,
    static_root: Optional[Path] = None,
) -> WorkbenchHTTPServer:
    return WorkbenchHTTPServer((host, port), WorkbenchHandler, service or WorkbenchService(), static_root or default_static_root())


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Run the local Jimeng video workbench")
    parser.add_argument("--host", default="127.0.0.1", help="Bind address; loopback is the safe default")
    parser.add_argument("--port", default=8765, type=int)
    parser.add_argument("--data-root", type=Path)
    parser.add_argument("--projects-root", type=Path, help="Canonical project root; defaults to this workspace's work/ directory when present")
    parser.add_argument("--static-root", type=Path)
    parser.add_argument("--skill-dir", type=Path)
    args = parser.parse_args(argv)
    workspace_projects = Path(__file__).resolve().parents[2] / "work"
    projects_root = args.projects_root or (workspace_projects if workspace_projects.is_dir() else None)
    try:
        service = WorkbenchService(data_root=args.data_root, projects_root=projects_root, skill_dir=args.skill_dir)
    except ApiError as exc:
        sys.stderr.write(json.dumps(exc.payload(), ensure_ascii=False) + "\n")
        return 2
    server = create_server(service, args.host, args.port, args.static_root)
    host, port = server.server_address[:2]
    print("Jimeng workbench: http://%s:%s" % (host, port), flush=True)
    try:
        server.serve_forever(poll_interval=0.25)
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
        service.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
