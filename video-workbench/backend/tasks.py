"""Persistent, concurrent task queue with real subprocess state reporting."""

from __future__ import annotations

import json
import os
import re
import signal
import subprocess
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple

from .errors import ApiError
from .storage import append_json_line, atomic_write_json, new_id, now_iso, read_json, validate_identifier


VALID_OPERATIONS = {
    "run",
    "apply_binding",
    "analyze",
    "extract_frames",
    "lint",
    "compile",
    "verify",
    "export_docx",
    "align",
    "codex",
    "retry_shot",
}
ACTIVE_STATUSES = {"queued", "running", "paused", "cancelling"}
TERMINAL_STATUSES = {"completed", "waiting", "blocked", "failed", "cancelled"}
PROJECT_WRITER_OPERATIONS = {
    "run",
    "apply_binding",
    "analyze",
    "extract_frames",
    "lint",
    "compile",
    "verify",
    "export_docx",
    "align",
    "codex",
    "retry_shot",
}


class TaskManager:
    def __init__(self, service: Any) -> None:
        self.service = service
        self.root = service.tasks_root
        self.root.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._active: Dict[str, List[subprocess.Popen]] = {}
        self._pause_requested: Set[str] = set()
        self._cancel_requested: Set[str] = set()
        self._threads: Dict[str, threading.Thread] = {}
        self._project_writer_leases: Dict[str, str] = {}
        self._recover_interrupted_tasks()

    def _task_dir(self, task_id: str) -> Path:
        validate_identifier(task_id, "task id")
        return self.root / task_id

    def _task_path(self, task_id: str) -> Path:
        return self._task_dir(task_id) / "task.json"

    def _events_path(self, task_id: str) -> Path:
        return self._task_dir(task_id) / "events.jsonl"

    def _recover_interrupted_tasks(self) -> None:
        for path in self.root.glob("*/task.json"):
            try:
                task = read_json(path)
            except ApiError:
                continue
            if task.get("status") in {"running", "paused", "cancelling"}:
                task["status"] = "blocked"
                task["phase"] = "server_restarted"
                task["message"] = "The local server restarted while this task was active. Start it again to retry safely."
                task["error"] = {"code": "SERVER_RESTARTED", "message": task["message"]}
                task["updated_at"] = now_iso()
                atomic_write_json(path, task)
            elif task.get("status") == "waiting" and task.get("phase") == "waiting_for_project_writer":
                task["status"] = "queued"
                task["phase"] = "queued_after_server_restart"
                task["queue_position"] = None
                task["message"] = "服务重启后写队列已安全恢复为待启动；不会永久卡在旧租约。"
                task["error"] = {"code": "SERVER_RESTARTED_QUEUE_RECOVERED", "message": task["message"]}
                task["updated_at"] = now_iso()
                atomic_write_json(path, task)

    def create_task(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        project_id = str(payload.get("project_id") or "")
        self.service.get_project_dir(project_id)
        operation = str(payload.get("operation") or "run")
        if operation not in VALID_OPERATIONS:
            raise ApiError(400, "INVALID_TASK_OPERATION", "Unsupported task operation", {"allowed": sorted(VALID_OPERATIONS)})
        shot_id = payload.get("shot_id")
        owner_lane = payload.get("owner_lane")
        if owner_lane is not None:
            owner_lane = str(owner_lane)
            if owner_lane not in {"image", "text", "controller"}:
                raise ApiError(400, "INVALID_OWNER_LANE", "owner_lane must be image, text or controller")
        if operation == "retry_shot":
            if not shot_id:
                raise ApiError(400, "SHOT_ID_REQUIRED", "retry_shot requires shot_id")
            shot_id = validate_identifier(str(shot_id), "shot id")
        reason = str(payload.get("reason") or "").strip()
        base_instruction = str(payload.get("instruction") or reason).strip()
        issue_codes = payload.get("issue_codes") or []
        if not isinstance(issue_codes, list) or len(issue_codes) > 50:
            raise ApiError(400, "INVALID_ISSUE_CODES", "issue_codes must be a list containing at most 50 codes")
        clean_issue_codes: List[str] = []
        for value in issue_codes:
            code = str(value).strip()
            if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,99}", code):
                raise ApiError(400, "INVALID_ISSUE_CODE", "Each issue code must be a short machine-readable code")
            if code not in clean_issue_codes:
                clean_issue_codes.append(code)
        user_overrides = payload.get("user_overrides") or {}
        if not isinstance(user_overrides, dict):
            raise ApiError(400, "INVALID_USER_OVERRIDES", "user_overrides must be an object")
        unknown_overrides = sorted(set(user_overrides) - {"emotion", "action_beats", "speech_transition"})
        if unknown_overrides:
            raise ApiError(400, "UNKNOWN_USER_OVERRIDE", "Unsupported shot override fields", {"fields": unknown_overrides})
        clean_overrides: Dict[str, Any] = {}
        if "emotion" in user_overrides:
            emotion = str(user_overrides.get("emotion") or "").strip()
            if not emotion or len(emotion) > 2000:
                raise ApiError(400, "INVALID_EMOTION_OVERRIDE", "emotion must contain 1-2000 characters")
            clean_overrides["emotion"] = emotion
        if "action_beats" in user_overrides:
            beats = user_overrides.get("action_beats")
            if not isinstance(beats, list) or not beats or len(beats) > 40:
                raise ApiError(400, "INVALID_ACTION_BEATS_OVERRIDE", "action_beats must contain 1-40 text beats")
            clean_beats = [str(value).strip() for value in beats]
            if any(not value or len(value) > 800 for value in clean_beats):
                raise ApiError(400, "INVALID_ACTION_BEATS_OVERRIDE", "Each action beat must contain 1-800 characters")
            clean_overrides["action_beats"] = clean_beats
        if "speech_transition" in user_overrides:
            transition = str(user_overrides.get("speech_transition") or "").strip()
            if not transition or len(transition) > 2000:
                raise ApiError(400, "INVALID_SPEECH_TRANSITION_OVERRIDE", "speech_transition must contain 1-2000 characters")
            clean_overrides["speech_transition"] = transition
        instruction_parts = [base_instruction] if base_instruction else []
        if clean_issue_codes:
            instruction_parts.append("issue_codes=" + ",".join(clean_issue_codes))
        if clean_overrides:
            instruction_parts.append("user_overrides=" + json.dumps(clean_overrides, ensure_ascii=False, separators=(",", ":")))
        instruction = "\n".join(instruction_parts)
        if len(instruction) > 8000:
            raise ApiError(400, "INSTRUCTION_TOO_LONG", "Task instruction cannot exceed 8000 characters")
        task_id = new_id("task")
        task_dir = self._task_dir(task_id)
        task_dir.mkdir(parents=True, exist_ok=False)
        config = self.service.project_config(project_id)
        detector_contract = None
        if operation == "codex" and "detectors" in instruction.lower():
            match = re.search(r"detectors\s*[:=]\s*([A-Za-z0-9_.:,\-]+)", instruction, flags=re.IGNORECASE)
            requested = []
            if match:
                for value in re.split(r"[,:]", match.group(1)):
                    detector = value.strip()
                    if detector and detector not in requested:
                        requested.append(detector)
            detector_contract = {
                "schema_version": "workbench-detector-request-v1",
                "requested_detectors": requested,
                "shot_scope": config.get("shot_scope") or {"mode": "all"},
            }
        binding_contract_sha256 = None
        binding_transaction_id = None
        if operation == "apply_binding":
            state = self.service._load_state(self.service.get_project_dir(project_id))
            binding = state.get("product_binding") or {}
            binding_contract_sha256 = binding.get("immutable_contract_sha256")
            binding_transaction_id = binding.get("transaction_id")
        dual = operation in {"run", "analyze"} and config.get("task_mode") == "dual"
        initial_lane = str(owner_lane or "controller") if operation == "retry_shot" else "controller"
        lanes = {
            initial_lane: {"status": "queued", "progress": 0, "message": "等待启动"},
        }
        if dual:
            lanes = {
                "image": {"status": "queued", "progress": 0, "message": "等待图线启动"},
                "text": {"status": "queued", "progress": 0, "message": "等待文线启动"},
                "controller": {"status": "queued", "progress": 0, "message": "等待图文回收"},
            }
        task = {
            "schema_version": "workbench-task-v1",
            "id": task_id,
            "project_id": project_id,
            "operation": operation,
            "shot_id": shot_id,
            "owner_lane": owner_lane or ("controller" if operation == "retry_shot" else None),
            "retry_of_task_id": payload.get("retry_of_task_id"),
            "instruction": instruction or None,
            "base_instruction": base_instruction or None,
            "reason": reason or None,
            "issue_codes": clean_issue_codes,
            "user_overrides": clean_overrides,
            "binding_contract_sha256": binding_contract_sha256,
            "binding_transaction_id": binding_transaction_id,
            "detector_contract": detector_contract,
            "status": "queued",
            "phase": "queued",
            "progress": 0,
            "message": "任务已创建，等待启动",
            "lanes": lanes,
            "result": None,
            "error": None,
            "created_at": now_iso(),
            "updated_at": now_iso(),
            "started_at": None,
            "finished_at": None,
            "next_event_seq": 1,
        }
        atomic_write_json(self._task_path(task_id), task)
        self._event(task_id, "task.created", "任务已创建", {"operation": operation})
        return self.get_task(task_id)

    def get_task(self, task_id: str) -> Dict[str, Any]:
        path = self._task_path(task_id)
        if not path.is_file():
            raise ApiError(404, "TASK_NOT_FOUND", "Task was not found")
        value = read_json(path)
        if not isinstance(value, dict):
            raise ApiError(500, "INVALID_TASK_STATE", "Stored task state is invalid")
        return value

    def list_tasks(self, project_id: Optional[str] = None, active_only: bool = False) -> List[Dict[str, Any]]:
        result: List[Dict[str, Any]] = []
        for path in self.root.glob("*/task.json"):
            try:
                task = read_json(path)
            except ApiError:
                continue
            if not isinstance(task, dict):
                continue
            if project_id and task.get("project_id") != project_id:
                continue
            if active_only and task.get("status") not in ACTIVE_STATUSES:
                continue
            result.append(task)
        result.sort(key=lambda item: item.get("created_at") or "", reverse=True)
        return result

    def _writer_waiters(self, project_id: str) -> List[Dict[str, Any]]:
        waiters = [
            task
            for task in self.list_tasks(project_id=project_id)
            if task.get("status") == "waiting"
            and task.get("phase") == "waiting_for_project_writer"
            and task.get("operation") in PROJECT_WRITER_OPERATIONS
            and task.get("id") not in self._cancel_requested
        ]
        waiters.sort(key=lambda value: (str(value.get("created_at") or ""), str(value.get("id") or "")))
        return waiters

    def _refresh_writer_queue_positions(self, project_id: str) -> None:
        for position, waiting in enumerate(self._writer_waiters(project_id), start=1):
            if waiting.get("queue_position") != position:
                waiting["queue_position"] = position
                self._save(waiting)

    def _wake_next_writer(self, project_id: str) -> None:
        with self._lock:
            if self._project_writer_leases.get(project_id):
                return
            waiters = self._writer_waiters(project_id)
            if not waiters:
                return
            next_task_id = str(waiters[0]["id"])
        # `start` performs a second atomic lease check. If another caller won the
        # race, this task remains queued with a truthful position.
        self.start(next_task_id)
        with self._lock:
            self._refresh_writer_queue_positions(project_id)

    def _save(self, task: Dict[str, Any]) -> None:
        task["updated_at"] = now_iso()
        atomic_write_json(self._task_path(str(task["id"])), task)

    def _mutate(self, task_id: str, **changes: Any) -> Dict[str, Any]:
        with self._lock:
            task = self.get_task(task_id)
            task.update(changes)
            self._save(task)
            return task

    def _lane(self, task_id: str, lane: str, status: str, progress: int, message: str) -> None:
        with self._lock:
            task = self.get_task(task_id)
            lanes = task.setdefault("lanes", {})
            value = dict(lanes.get(lane) or {})
            value.update({"status": status, "progress": max(0, min(100, int(progress))), "message": message, "updated_at": now_iso()})
            lanes[lane] = value
            self._save(task)

    def _event(self, task_id: str, event_type: str, message: str, data: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        with self._lock:
            task = self.get_task(task_id)
            sequence = int(task.get("next_event_seq") or 1)
            event = {
                "seq": sequence,
                "time": now_iso(),
                "type": event_type,
                "message": message[:2000],
                "data": data or {},
            }
            append_json_line(self._events_path(task_id), event)
            task["next_event_seq"] = sequence + 1
            self._save(task)
            return event

    def events(self, task_id: str, after: int = 0) -> Dict[str, Any]:
        task = self.get_task(task_id)
        events: List[Dict[str, Any]] = []
        path = self._events_path(task_id)
        if path.is_file():
            with path.open("r", encoding="utf-8") as handle:
                for line in handle:
                    try:
                        value = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    if int(value.get("seq") or 0) > after:
                        events.append(value)
        next_after = events[-1]["seq"] if events else after
        return {"ok": True, "events": events, "next_after": next_after, "task": task}

    def start(self, task_id: str) -> Dict[str, Any]:
        with self._lock:
            task = self.get_task(task_id)
            if task["status"] == "paused":
                return self.resume(task_id)
            if task["status"] == "running":
                return task
            if task["status"] not in {"queued", "waiting", "blocked", "failed"}:
                raise ApiError(409, "TASK_CANNOT_START", "Task cannot be started from its current state")
            existing = self._threads.get(task_id)
            if existing and existing.is_alive():
                return task
            if task.get("operation") in PROJECT_WRITER_OPERATIONS:
                project_id = str(task["project_id"])
                lease_owner = self._project_writer_leases.get(project_id)
                if lease_owner and lease_owner != task_id:
                    try:
                        owner_task = self.get_task(lease_owner)
                    except ApiError:
                        owner_task = {}
                    owner_thread = self._threads.get(lease_owner)
                    if owner_task.get("status") not in {"running", "paused", "cancelling"} and not (owner_thread and owner_thread.is_alive()):
                        self._project_writer_leases.pop(project_id, None)
                        lease_owner = None
                older_waiters = [value for value in self._writer_waiters(project_id) if value.get("id") != task_id]
                older_waiters = [
                    value
                    for value in older_waiters
                    if (str(value.get("created_at") or ""), str(value.get("id") or ""))
                    < (str(task.get("created_at") or ""), str(task.get("id") or ""))
                ]
                if (lease_owner and lease_owner != task_id) or older_waiters:
                    queue_position = len(older_waiters) + 1
                    task.update(
                        {
                            "status": "waiting",
                            "phase": "waiting_for_project_writer",
                            "message": "同一项目已有写任务，当前任务等待写入租约",
                            "queue_position": queue_position,
                            "error": {
                                "code": "PROJECT_WRITER_BUSY",
                                "project_id": project_id,
                                "lease_owner_task_id": lease_owner,
                                "queue_position": queue_position,
                            },
                            "finished_at": None,
                        }
                    )
                    self._save(task)
                    self._event(
                        task_id,
                        "task.waiting_for_project_writer",
                        "同一项目已有写任务，未并发写 canonical 文件",
                        task["error"],
                    )
                    self._refresh_writer_queue_positions(project_id)
                    return self.get_task(task_id)
                self._project_writer_leases[project_id] = task_id
            self._cancel_requested.discard(task_id)
            self._pause_requested.discard(task_id)
            task.update(
                {
                    "status": "running",
                    "phase": "starting",
                    "progress": 1,
                    "message": "任务正在启动",
                    "error": None,
                    "result": None,
                    "started_at": now_iso(),
                    "finished_at": None,
                    "queue_position": None,
                }
            )
            self._save(task)
            thread = threading.Thread(target=self._thread_main, args=(task_id,), name="workbench-" + task_id, daemon=True)
            self._threads[task_id] = thread
            thread.start()
        self._event(task_id, "task.started", "任务已启动")
        return self.get_task(task_id)

    def _thread_main(self, task_id: str) -> None:
        try:
            self._worker(task_id)
        finally:
            # A task may become terminal slightly before its final audit event is
            # flushed. Keep the thread registered until every write is finished.
            with self._lock:
                if self._threads.get(task_id) is threading.current_thread():
                    self._threads.pop(task_id, None)

    def wait(self, task_id: str, timeout: Optional[float] = None) -> Dict[str, Any]:
        """Join a task worker so callers never tear storage down mid-audit-write."""
        with self._lock:
            thread = self._threads.get(task_id)
        if thread is not None and thread is not threading.current_thread():
            thread.join(timeout)
        return self.get_task(task_id)

    def shutdown(self, timeout: float = 5.0) -> Dict[str, Any]:
        """Boundedly drain workers and terminate subprocesses that outlive shutdown."""
        deadline = time.monotonic() + max(0.0, timeout)
        with self._lock:
            threads = list(self._threads.items())
        for _, thread in threads:
            remaining = max(0.0, deadline - time.monotonic())
            if thread is not threading.current_thread() and remaining:
                thread.join(remaining)
        with self._lock:
            alive = [task_id for task_id, thread in self._threads.items() if thread.is_alive()]
            processes = [process for task_id in alive for process in (self._active.get(task_id) or [])]
            self._cancel_requested.update(alive)
        for process in processes:
            if process.poll() is None:
                try:
                    process.terminate()
                except OSError:
                    pass
        for task_id in alive:
            with self._lock:
                thread = self._threads.get(task_id)
            if thread is not None and thread is not threading.current_thread():
                thread.join(0.5)
        with self._lock:
            remaining_alive = [task_id for task_id, thread in self._threads.items() if thread.is_alive()]
        return {"ok": not remaining_alive, "active_task_ids": remaining_alive}

    def retry(self, task_id: str) -> Dict[str, Any]:
        original = self.get_task(task_id)
        if original.get("status") in ACTIVE_STATUSES:
            raise ApiError(409, "ACTIVE_TASK_CANNOT_RETRY", "An active task cannot be retried")
        if original.get("operation") == "apply_binding":
            raise ApiError(
                409,
                "PRODUCT_BINDING_REAPPLY_REQUIRED",
                "Product binding retries must start a new deterministic binding transaction",
            )
        clone = self.create_task(
            {
                "project_id": original.get("project_id"),
                "operation": original.get("operation"),
                "shot_id": original.get("shot_id"),
                "owner_lane": original.get("owner_lane"),
                "instruction": original.get("base_instruction") or original.get("reason") or original.get("instruction"),
                "reason": original.get("reason"),
                "issue_codes": original.get("issue_codes") or [],
                "user_overrides": original.get("user_overrides") or {},
                "retry_of_task_id": original.get("id"),
            }
        )
        return self.start(clone["id"])

    def pause(self, task_id: str) -> Dict[str, Any]:
        with self._lock:
            task = self.get_task(task_id)
            if task["status"] != "running":
                raise ApiError(409, "TASK_CANNOT_PAUSE", "Only a running task can be paused")
            self._pause_requested.add(task_id)
            processes = list(self._active.get(task_id) or [])
            if processes and hasattr(signal, "SIGSTOP"):
                for process in processes:
                    if process.poll() is None:
                        os.kill(process.pid, signal.SIGSTOP)
            task.update({"status": "paused", "phase": "paused", "message": "任务已暂停"})
            self._save(task)
        self._event(task_id, "task.paused", "任务已暂停")
        return self.get_task(task_id)

    def resume(self, task_id: str) -> Dict[str, Any]:
        with self._lock:
            task = self.get_task(task_id)
            if task["status"] != "paused":
                raise ApiError(409, "TASK_CANNOT_RESUME", "Only a paused task can be resumed")
            processes = list(self._active.get(task_id) or [])
            if processes and hasattr(signal, "SIGCONT"):
                for process in processes:
                    if process.poll() is None:
                        os.kill(process.pid, signal.SIGCONT)
            self._pause_requested.discard(task_id)
            task.update({"status": "running", "phase": "resumed", "message": "任务已继续"})
            self._save(task)
        self._event(task_id, "task.resumed", "任务已继续")
        return self.get_task(task_id)

    def cancel(self, task_id: str) -> Dict[str, Any]:
        with self._lock:
            task = self.get_task(task_id)
            writer_waiting = task.get("status") == "waiting" and task.get("phase") == "waiting_for_project_writer"
            if task["status"] in TERMINAL_STATUSES and not writer_waiting:
                return task
            self._cancel_requested.add(task_id)
            self._pause_requested.discard(task_id)
            processes = list(self._active.get(task_id) or [])
            thread = self._threads.get(task_id)
            thread_alive = bool(thread and thread.is_alive())
            for process in processes:
                if process.poll() is None:
                    try:
                        process.terminate()
                    except OSError:
                        pass
            task.update({"status": "cancelling", "phase": "cancelling", "message": "正在取消任务"})
            self._save(task)
        self._event(task_id, "task.cancelling", "正在取消任务")
        # A running worker may be in a synchronous canonical write before it
        # registers a subprocess. It owns the project lease until its `finally`
        # path reaches `_terminal`; only never-started queue entries end here.
        if not processes and not thread_alive:
            self._terminal(task_id, "cancelled", "任务已取消", {"code": "USER_CANCELLED"})
        return self.get_task(task_id)

    def _terminal(
        self,
        task_id: str,
        status: str,
        message: str,
        result_or_error: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        released_project_id: Optional[str] = None
        with self._lock:
            task = self.get_task(task_id)
            if task_id in self._cancel_requested and status != "cancelled":
                status = "cancelled"
                message = "任务已取消"
                result_or_error = {"code": "USER_CANCELLED"}
            task["status"] = status
            task["phase"] = status
            task["message"] = message
            task["progress"] = 100 if status == "completed" else task.get("progress", 0)
            task["finished_at"] = now_iso()
            if status == "completed":
                task["result"] = result_or_error or {"status": "completed"}
                task["error"] = None
            elif status in {"waiting", "blocked", "failed", "cancelled"}:
                task["error"] = result_or_error or {"code": status.upper(), "message": message}
            self._save(task)
            self._active.pop(task_id, None)
            self._pause_requested.discard(task_id)
            project_id = str(task.get("project_id") or "")
            if self._project_writer_leases.get(project_id) == task_id:
                self._project_writer_leases.pop(project_id, None)
                released_project_id = project_id
        self._event(task_id, "task." + status, message, result_or_error or {})
        if released_project_id:
            self._wake_next_writer(released_project_id)
        elif project_id:
            with self._lock:
                self._refresh_writer_queue_positions(project_id)
        return task

    def _register_process(self, task_id: str, process: subprocess.Popen) -> None:
        with self._lock:
            self._active.setdefault(task_id, []).append(process)

    def _unregister_process(self, task_id: str, process: subprocess.Popen) -> None:
        with self._lock:
            values = self._active.get(task_id) or []
            if process in values:
                values.remove(process)
            if not values:
                self._active.pop(task_id, None)

    def _run_process(
        self,
        task_id: str,
        command: List[str],
        cwd: Path,
        lane: str,
        label: str,
        stdin_text: Optional[str] = None,
    ) -> Tuple[bool, int, str]:
        while task_id in self._pause_requested and task_id not in self._cancel_requested:
            time.sleep(0.1)
        if task_id in self._cancel_requested:
            return False, -15, "cancelled"
        safe_command = [Path(command[0]).name] + [Path(value).name if value.startswith("/") else value for value in command[1:3]]
        self._event(task_id, "process.started", "%s 已启动" % label, {"lane": lane, "command": safe_command})
        try:
            process = subprocess.Popen(
                command,
                cwd=str(cwd),
                stdin=subprocess.PIPE if stdin_text is not None else subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                bufsize=1,
            )
        except OSError as exc:
            return False, -1, str(exc)
        self._register_process(task_id, process)
        if stdin_text is not None and process.stdin is not None:
            try:
                process.stdin.write(stdin_text)
                process.stdin.close()
            except OSError:
                pass
        tail: List[str] = []
        try:
            if process.stdout is not None:
                for raw_line in process.stdout:
                    line = raw_line.rstrip()
                    if not line:
                        continue
                    tail.append(line)
                    tail = tail[-40:]
                    message = line[:1200]
                    data: Dict[str, Any] = {"lane": lane}
                    if line.startswith("{"):
                        try:
                            parsed = json.loads(line)
                            event_name = str(parsed.get("type") or parsed.get("event") or "codex.event")
                            message = event_name
                            data["event_type"] = event_name
                        except json.JSONDecodeError:
                            pass
                    self._event(task_id, "process.output", message, data)
                    if task_id in self._cancel_requested and process.poll() is None:
                        process.terminate()
            returncode = process.wait()
        finally:
            if process.stdout is not None:
                process.stdout.close()
            self._unregister_process(task_id, process)
        output_tail = "\n".join(tail)[-12000:]
        self._event(
            task_id,
            "process.finished",
            "%s %s" % (label, "完成" if returncode == 0 else "未通过"),
            {"lane": lane, "returncode": returncode, "output_tail": output_tail[-3000:]},
        )
        return returncode == 0, returncode, output_tail

    def _run_director_step(self, task_id: str, operation: str, progress: int) -> bool:
        task = self.get_task(task_id)
        project_dir = self.service.get_project_dir(task["project_id"])
        video = self.service.project_video_path(task["project_id"])
        command = self.service.toolchain.director_command(operation, project_dir, video)
        if command is None:
            self._terminal(
                task_id,
                "blocked",
                "%s 所需导演脚本或输入不可用" % operation,
                {"code": "DIRECTOR_COMMAND_UNAVAILABLE", "operation": operation},
            )
            return False
        self._mutate(task_id, phase=operation, progress=progress, message="正在执行 %s" % operation)
        self._lane(task_id, "controller", "running", progress, "执行 %s" % operation)
        ok, returncode, output = self._run_process(task_id, command, project_dir, "controller", operation)
        if not ok:
            if task_id in self._cancel_requested:
                self._terminal(task_id, "cancelled", "任务已取消", {"code": "USER_CANCELLED"})
            else:
                self._terminal(
                    task_id,
                    "blocked",
                    "%s 未通过，任务没有被标记为完成" % operation,
                    {"code": "DIRECTOR_STEP_BLOCKED", "operation": operation, "returncode": returncode, "output_tail": output[-5000:]},
                )
            return False
        if operation == "analyze":
            self.service.mark_video_analyzed(task["project_id"])
        return True

    def _codex_schema(self, task_id: str, lane: str) -> Tuple[Path, Path]:
        task_dir = self._task_dir(task_id)
        schema_path = task_dir / ("codex-schema-%s.json" % lane)
        result_path = task_dir / ("codex-result-%s.json" % lane)
        schema = {
            "$schema": "http://json-schema.org/draft-07/schema#",
            "type": "object",
            "additionalProperties": False,
            "required": ["status", "summary", "pending_inputs", "blockers", "artifacts"],
            "properties": {
                "status": {"type": "string", "enum": ["completed", "waiting", "blocked", "failed"]},
                "summary": {"type": "string"},
                "pending_inputs": {"type": "array", "items": {"type": "string"}},
                "blockers": {"type": "array", "items": {"type": "string"}},
                "artifacts": {"type": "array", "items": {"type": "string"}},
            },
        }
        task = self.get_task(task_id)
        detector_task = task.get("operation") == "codex" and "detectors" in str(task.get("instruction") or "").lower()
        if detector_task:
            schema["required"].append("findings")
            schema["properties"]["findings"] = {
                "type": "array",
                "items": {
                    "type": "object",
                    "additionalProperties": False,
                    "required": ["detector", "unit_id", "result", "code", "severity", "owner_lane", "message"],
                    "properties": {
                        "detector": {"type": "string"},
                        "unit_id": {"type": "string"},
                        "evidence_time": {"type": "number", "minimum": 0},
                        "evidence_asset": {"type": "string"},
                        "evidence_asset_sha256": {"type": "string", "pattern": "^[a-fA-F0-9]{64}$"},
                        "result": {"type": "string", "enum": ["pass", "issue", "not_observable"]},
                        "code": {"type": "string"},
                        "severity": {"type": "string", "enum": ["info", "warning", "error", "blocker"]},
                        "owner_lane": {"type": "string", "enum": ["image", "text", "controller"]},
                        "message": {"type": "string"},
                    },
                    "allOf": [
                        {
                            "if": {"properties": {"result": {"enum": ["pass", "issue"]}}},
                            "then": {"required": ["evidence_time", "evidence_asset", "evidence_asset_sha256"]},
                        }
                    ],
                },
            }
        if task.get("operation") == "apply_binding":
            schema["required"].append("derived_guidance")
            schema["properties"]["derived_guidance"] = {
                "type": "object",
                "additionalProperties": False,
                "required": ["immutable_traits", "state_profiles", "non_authoritative_prompt_guidance"],
                "properties": {
                    "immutable_traits": {"type": "array", "minItems": 1, "items": {"type": "string", "minLength": 1}},
                    "state_profiles": {"type": "object", "minProperties": 1},
                    "non_authoritative_prompt_guidance": {"type": "array", "items": {"type": "string"}},
                },
            }
        atomic_write_json(schema_path, schema)
        if result_path.exists():
            result_path.unlink()
        return schema_path, result_path

    def _codex_prompt(self, task: Dict[str, Any], lane: str, branch_results: Optional[Dict[str, Any]] = None) -> str:
        config = self.service.project_config(task["project_id"])
        operation = task["operation"]
        tier = config.get("execution_tier")
        scope = json.dumps(config.get("shot_scope"), ensure_ascii=False)
        base = [
            "你正在执行本地即梦视频工作台中的真实项目任务。",
            "必须完整读取并遵守已安装的 jimeng-video-remix-director SKILL.md；涉及产品或人物时继续读取它要求的对应引用。",
            "项目事实只写入当前项目目录的 canonical JSON，不依赖聊天记忆。不得伪造图片、分析、审批、Prompt、Word 或完成状态。",
            "任何命令、输入、授权、用户图片确认或质量门禁缺失时，最终 status 必须是 waiting 或 blocked，并列出 pending_inputs/blockers。",
            "项目 ID：%s；执行档位：%s；镜头范围：%s；人物模式：%s；产品模式：%s；当前 lane：%s。"
            % (
                task["project_id"],
                tier,
                scope,
                config.get("character_mode"),
                config.get("product_mode"),
                lane,
            ),
        ]
        prompt_length_contract = config.get("prompt_length_contract") or {}
        if prompt_length_contract.get("enabled") is True:
            base.append(
                "本项目已显式启用 Prompt 长度合同：每个独立生成镜必须实算 %s–%s 个非空白字符，上下限同时是硬门。必须先按原片节奏正确拆镜并补足真实可见证据；禁止靠重复限制词、虚构细节、通用六层内容或无效水词凑字。"
                % (
                    prompt_length_contract.get("minimum_non_whitespace_characters"),
                    prompt_length_contract.get("maximum_non_whitespace_characters"),
                )
            )
        else:
            base.append(
                "本项目没有 Prompt 字符上下限。禁止为了 3000–4000 字扩写、重复限制词或补水词；在动作、情绪、说话转换和产品事实可执行的前提下，优先生成最短可执行 Prompt。"
            )
        contract_path = self.service.get_project_dir(task["project_id"]) / "library" / "product_immutable_contract.json"
        if contract_path.is_file():
            contract = read_json(contract_path, {})
            base.append(
                "产品不可变硬合同：library/product_immutable_contract.json；contract_sha256=%s。尺寸、包装数量、盒体拓扑、文字版面、参考 ID/路径/SHA 只能引用，禁止新增、删除或改写；冲突必须 blocked。"
                % contract.get("contract_sha256")
            )
        if operation == "apply_binding":
            product_id = config.get("product_id")
            knowledge_path = self.service.knowledge_root / "products" / str(product_id or "") / "record.json"
            base.append(
                "这是自定义产品只读派生任务。读取知识记录 %s、不可变合同与参考图；后台已经复制并锁定 references。你不得写任何项目文件，只返回 derived_guidance 中的 immutable_traits、state_profiles 和非权威提示建议。"
                % knowledge_path
            )
            base.append("derived_guidance 不得包含或重述 dimensions/package/quantity/topology/text_layout/references/contract_sha256；这些字段由后台唯一写入和逐项等值校验。")
        elif operation == "retry_shot":
            owner_lane = str(task.get("owner_lane") or "controller")
            base.append(
                "只返工镜头 %s；责任 lane=%s。原因：%s。必须从该镜原始真实首帧和批准参考重新做，禁止在失败候选上叠修。"
                % (task.get("shot_id"), owner_lane, task.get("instruction") or "用户要求单镜返工")
            )
            if task.get("issue_codes"):
                base.append("用户勾选的问题码：%s。必须逐项处理并在结果中可审计对应。" % ",".join(task["issue_codes"]))
            if task.get("user_overrides"):
                base.append("用户逐镜硬覆盖：%s。emotion/action_beats/speech_transition 不得被通用模板冲掉。" % json.dumps(task["user_overrides"], ensure_ascii=False))
            if owner_lane == "image":
                base.append("只修复图线的首帧、人物/产品几何、光影或画面证据，并真实写入该 unit 的图像结果；不要擅改口播。")
            elif owner_lane == "text":
                base.append("只修复文线的口播映射、节奏、情绪动作或 Prompt 证据；不要声称生成或批准了图片。")
            else:
                base.append("由 controller 同时核对图文影响并只修改该 unit 需要的 canonical 产物。")
        elif lane == "image":
            base.append("只做图线证据盘点、真实首帧、人物/产品绑定、吃食与掰开画面检查和候选 QA；不要修改 canonical 合并结果。")
        elif lane == "text":
            base.append("只做文线口播、角色锁、原片节奏、可见情绪动作与逐镜 Prompt 证据；不要修改 canonical 合并结果。")
        elif branch_results:
            base.append("图文分支结果如下，只能作为 handoff，必须亲自复核后再合并 canonical：%s" % json.dumps(branch_results, ensure_ascii=False)[:12000])
            base.append("作为唯一总控，消费两路 handoff、修复对齐问题并执行当前档位允许的 canonical 合并与门禁。")
        else:
            base.append("作为单任务总控，内部同时维护图线、文线和合并线清单；不能做完一条线就忘记另一条线。")
        if task.get("instruction") and operation != "retry_shot":
            base.append("用户补充指令：%s" % task["instruction"])
        if operation in {"run", "analyze"}:
            base.append("原片语义分析完成前，必须真实写入 planning/workbench_script.json 的 source_text、最小 SRC/ADD unit 的 shots/shot_manifest.json、planning/role_lock.json 与非空 planning/story_plan.json；只抽帧或只在结果 JSON 里声称分析完成不算完成。")
            base.append("shot_manifest 必须写入当前 source_video_sha256；所有最小 unit 的 timeline 必须从 0 秒无缝、无重叠覆盖到片尾。单 unit 默认不得超过 3.5 秒，确需长镜必须写 duration_exception_reason/long_take_reason/action_beat_reason。每个 unit 必须显式绑定 script_segment_ids 或明确 silence，并为有人物的 unit 写 source person owner。吃食/掰开 marker 必须落在且绑定到带同名 semantic_tags 的唯一最小 unit；不得把一个含多动作的长镜一笔写到底。")
        if operation == "codex" and "detectors" in str(task.get("instruction") or "").lower():
            base.append("这是强制只读的可审计检测任务；不得修改任何项目文件。必须逐条返回 findings，unit_id 只能取 shot_manifest 的 SRC/ADD 最小交付单元。pass/issue 必须给出落在该 unit 时间范围内的 evidence_time、项目内相对 evidence_asset 路径及其真实 SHA-256；无法验证就写 not_observable，禁止用推测写 pass。")
        base.append("最终只返回输出 schema 规定的 JSON；status=completed 仅代表当前明确档位的所有真实门禁均已通过。")
        return "\n".join(base)

    def _run_codex_lane(
        self,
        task_id: str,
        lane: str,
        read_only: bool,
        branch_results: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        task = self.get_task(task_id)
        project_dir = self.service.get_project_dir(task["project_id"])
        config = self.service.project_config(task["project_id"])
        schema_path, result_path = self._codex_schema(task_id, lane)
        command = self.service.toolchain.codex_command(
            project_dir,
            schema_path,
            result_path,
            model=(config.get("codex") or {}).get("model"),
            read_only=read_only,
        )
        if command is None:
            return {"status": "blocked", "summary": "Codex CLI 不可用", "pending_inputs": [], "blockers": ["CODEX_NOT_AVAILABLE"], "artifacts": []}
        self._lane(task_id, lane, "running", 20 if read_only else 60, "Codex 正在执行%s线" % lane)
        prompt = self._codex_prompt(task, lane, branch_results)
        ok, returncode, output = self._run_process(task_id, command, project_dir, lane, "Codex %s" % lane, stdin_text=prompt)
        if not ok:
            status = "cancelled" if task_id in self._cancel_requested else "blocked"
            self._lane(task_id, lane, status, 100, "Codex %s未完成" % lane)
            return {
                "status": status,
                "summary": "Codex process did not complete",
                "pending_inputs": [],
                "blockers": ["CODEX_PROCESS_FAILED:%s" % returncode],
                "artifacts": [],
                "output_tail": output[-3000:],
            }
        if not result_path.is_file():
            self._lane(task_id, lane, "blocked", 100, "Codex 未返回结构化结果")
            return {"status": "blocked", "summary": "Codex result file is missing", "pending_inputs": [], "blockers": ["CODEX_RESULT_MISSING"], "artifacts": []}
        try:
            result = read_json(result_path)
        except ApiError as exc:
            result = {"status": "blocked", "summary": exc.message, "pending_inputs": [], "blockers": ["CODEX_RESULT_INVALID"], "artifacts": []}
        status = result.get("status") if result.get("status") in {"completed", "waiting", "blocked", "failed"} else "blocked"
        result["status"] = status
        self._lane(task_id, lane, status, 100, str(result.get("summary") or status))
        self._event(task_id, "codex.result", "%s线返回 %s" % (lane, status), {"lane": lane, "result": result})
        return result

    def _semantic_stage(self, task_id: str) -> Optional[Dict[str, Any]]:
        task = self.get_task(task_id)
        config = self.service.project_config(task["project_id"])
        codex_config = config.get("codex") or {}
        if codex_config.get("enabled") is not True:
            result = {
                "code": "CODEX_DISABLED",
                "message": "项目未显式启用 Codex；确定性素材步骤已保留，语义/生图/Prompt 阶段等待启用。",
                "pending_inputs": ["enable_project_codex"],
            }
            self._terminal(task_id, "waiting", result["message"], result)
            return None
        if not self.service.toolchain.codex_bin:
            self._terminal(task_id, "blocked", "Codex CLI 不可用", {"code": "CODEX_NOT_AVAILABLE"})
            return None
        if config.get("task_mode") == "dual" and task["operation"] in {"run", "analyze"}:
            self._mutate(task_id, phase="parallel_image_text", progress=35, message="图线与文线并行执行")
            results: Dict[str, Any] = {}
            with ThreadPoolExecutor(max_workers=2) as executor:
                futures = {
                    executor.submit(self._run_codex_lane, task_id, lane, True): lane
                    for lane in ("image", "text")
                }
                for future in as_completed(futures):
                    lane = futures[future]
                    try:
                        results[lane] = future.result()
                    except Exception as exc:
                        results[lane] = {"status": "failed", "summary": str(exc), "pending_inputs": [], "blockers": ["BRANCH_EXCEPTION"], "artifacts": []}
            noncompleted = [value for value in results.values() if value.get("status") != "completed"]
            if noncompleted:
                final_status = "waiting" if all(value.get("status") == "waiting" for value in noncompleted) else "blocked"
                self._terminal(
                    task_id,
                    final_status,
                    "图文分支存在未满足条件，未进入总控合并",
                    {"code": "BRANCHES_NOT_READY", "branches": results},
                )
                return None
            self._mutate(task_id, phase="controller_merge", progress=60, message="总控正在复核并合并图文结果")
            result = self._run_codex_lane(task_id, "controller", False, branch_results=results)
        else:
            self._mutate(task_id, phase="codex", progress=45, message="Codex 总控正在执行")
            lane = str(task.get("owner_lane") or "controller") if task.get("operation") == "retry_shot" else "controller"
            detector_task = task.get("operation") == "codex" and bool(task.get("detector_contract"))
            result = self._run_codex_lane(task_id, lane, task.get("operation") == "apply_binding" or detector_task)
        if result.get("status") != "completed":
            status = result.get("status") if result.get("status") in {"waiting", "blocked", "failed"} else "blocked"
            self._terminal(task_id, status, str(result.get("summary") or "Codex 阶段未完成"), result)
            return None
        return result

    def _preflight(self, task: Dict[str, Any]) -> Optional[Tuple[str, Dict[str, Any]]]:
        project = self.service.get_project(task["project_id"])
        operation = task["operation"]
        if (project.get("initialization") or {}).get("status") == "blocked":
            return "blocked", {"code": "PROJECT_INITIALIZATION_BLOCKED", "details": project["initialization"]}
        prompt_length_issue = (project.get("skill_project") or {}).get("prompt_length_contract_issue")
        if prompt_length_issue and operation in {
            "run",
            "analyze",
            "codex",
            "retry_shot",
            "compile",
            "verify",
            "export_docx",
            "align",
        }:
            return "blocked", {
                "code": "PROMPT_LENGTH_CONTRACT_INVALID",
                "pending_inputs": ["repair_prompt_length_contract_in_project_settings"],
                "issue": prompt_length_issue,
            }
        if operation in {"run", "analyze", "extract_frames", "codex", "retry_shot"} and not self.service.project_video_path(task["project_id"]):
            return "waiting", {"code": "SOURCE_VIDEO_REQUIRED", "pending_inputs": ["source_video"]}
        if operation in {"compile", "verify", "export_docx", "align"}:
            tier = project["config"].get("execution_tier")
            if tier not in {"prompt_only", "full_delivery"}:
                return "blocked", {"code": "EXECUTION_TIER_CANNOT_DELIVER", "execution_tier": tier}
        if operation == "export_docx":
            export_preflight = self.service.validate_docx_export_preflight(task["project_id"])
            if export_preflight.get("status") != "ready":
                status = "waiting" if export_preflight.get("status") == "waiting" else "blocked"
                return status, export_preflight
        if operation in {"codex", "retry_shot", "apply_binding"}:
            if (project["config"].get("codex") or {}).get("enabled") is not True:
                return "waiting", {"code": "CODEX_DISABLED", "pending_inputs": ["enable_project_codex"]}
            if not self.service.toolchain.codex_bin:
                return "blocked", {"code": "CODEX_NOT_AVAILABLE"}
        config = project.get("config") or {}
        if operation != "apply_binding" and config.get("product_mode") == "replace" and config.get("product_id"):
            product = self.service.find_knowledge("products", str(config["product_id"]), required=False)
            if product and product.get("source") == "custom":
                try:
                    product_validation = self.service.validate_custom_product_binding(task["project_id"])
                except ApiError as exc:
                    product_validation = {"status": "blocked", "code": exc.code, "message": exc.message, "details": exc.details}
                if product_validation.get("status") != "ready":
                    return "waiting", {
                        "code": "PRODUCT_BINDING_CONTRACT_STALE",
                        "pending_inputs": ["reapply_selected_product_binding"],
                        "validation": product_validation,
                    }
        if operation != "apply_binding" and config.get("character_mode") in {"head_replace", "full_replace"} and config.get("avatar_id"):
            avatar = self.service.find_knowledge("avatars", str(config["avatar_id"]), required=False)
            if avatar and avatar.get("source") == "custom":
                avatar_validation = self.service.validate_custom_avatar_binding(task["project_id"])
                if avatar_validation.get("status") != "ready":
                    return "waiting", {
                        "code": "AVATAR_BINDING_CONTRACT_STALE",
                        "pending_inputs": ["reapply_selected_avatar_binding"],
                        "validation": avatar_validation,
                    }
        if operation == "run" and project["config"].get("execution_tier") != "source_intake" and project.get("blocking_inputs"):
            return "waiting", {"code": "REQUIRED_INPUTS_MISSING", "pending_inputs": project["blocking_inputs"]}
        if (
            operation == "run"
            and project.get("product_binding_status") == "waiting_for_product_rebind"
            and (project["config"].get("codex") or {}).get("enabled") is not True
        ):
            return "waiting", {"code": "PRODUCT_REBIND_REQUIRED", "pending_inputs": ["apply_selected_product_binding"]}
        return None

    def _worker(self, task_id: str) -> None:
        try:
            task = self.get_task(task_id)
            issue = self._preflight(task)
            if issue:
                status, detail = issue
                self._terminal(task_id, status, "任务前置条件未满足", detail)
                return
            operation = task["operation"]
            if operation == "analyze":
                if not self._run_director_step(task_id, "analyze", 20):
                    return
                result = self._semantic_stage(task_id)
                if result is not None:
                    validation = self.service.validate_analysis_contract(task["project_id"])
                    if validation.get("status") != "ready":
                        self._terminal(task_id, "blocked", "Codex 返回后原片语义合同仍未真实落盘", validation)
                        return
                    result["analysis_contract"] = validation
                    self._lane(task_id, "controller", "completed", 100, "原片素材、口播与分镜分析已完成")
                    self._terminal(task_id, "completed", str(result.get("summary") or "原片分析已完成"), result)
                return
            if operation in {"extract_frames", "lint", "compile", "verify", "align"}:
                if self._run_director_step(task_id, operation, 25):
                    if operation == "verify":
                        receipt = self.service.mark_delivery_preflight_verified(task["project_id"])
                    else:
                        receipt = None
                    self._lane(task_id, "controller", "completed", 100, "%s 已完成" % operation)
                    self._terminal(
                        task_id,
                        "completed",
                        "%s 已完成" % operation,
                        {"operation": operation, "delivery_preflight_receipt": receipt},
                    )
                return
            if operation == "export_docx":
                if not self._run_director_step(task_id, "lint", 15):
                    return
                if self._run_director_step(task_id, "export_docx", 70):
                    self.service.mark_docx_qa_pending(task["project_id"])
                    self._lane(task_id, "controller", "completed", 100, "Word 已导出，尚需逐页视觉 QA")
                    self._terminal(
                        task_id,
                        "waiting",
                        "Word 文件已生成，但必须完成逐页视觉 QA 后才能标记完整交付",
                        {"code": "DOCX_VISUAL_QA_REQUIRED", "pending_inputs": ["document_visual_qa"]},
                    )
                return
            if operation == "apply_binding":
                result = self._semantic_stage(task_id)
                if result is None:
                    return
                try:
                    validation = self.service.commit_custom_product_guidance(
                        task["project_id"],
                        result.get("derived_guidance"),
                        task.get("binding_contract_sha256"),
                    )
                except ApiError as exc:
                    self._terminal(
                        task_id,
                        "blocked",
                        "自定义产品派生结果未通过不可变合同提交门",
                        {"code": exc.code, "message": exc.message, "details": exc.details},
                    )
                    return
                if validation.get("status") != "ready":
                    self._terminal(
                        task_id,
                        "blocked",
                        "Codex 返回后自定义产品绑定仍未通过确定性校验",
                        validation,
                    )
                    return
                self._terminal(task_id, "completed", "自定义产品已绑定并通过结构校验", validation)
                return
            if operation in {"codex", "retry_shot"}:
                result = self._semantic_stage(task_id)
                if result is not None:
                    if operation == "codex" and "detectors" in str(task.get("instruction") or "").lower():
                        try:
                            detection = self.service.save_detection_results(task["project_id"], task_id, result.get("findings"))
                        except ApiError as exc:
                            self._terminal(
                                task_id,
                                "blocked",
                                "检测结果未通过确定性校验",
                                {"code": exc.code, "message": exc.message, "details": exc.details},
                            )
                            return
                        result["detection_artifact"] = "review/workbench_detection.json"
                        result["validated_finding_count"] = len(detection["findings"])
                    if operation == "retry_shot" and task.get("owner_lane") != "text":
                        generation = self.service.generation_artifact_status(task["project_id"], shot_id=task.get("shot_id"))
                        if generation.get("status") != "ready":
                            self._terminal(
                                task_id,
                                "waiting",
                                "单镜返工未产生可验证的批准首帧；图像生成适配器未配置",
                                generation,
                            )
                            return
                    self._terminal(task_id, "completed", str(result.get("summary") or "Codex 任务已完成"), result)
                return
            if operation != "run":
                self._terminal(task_id, "blocked", "Unknown task operation", {"code": "UNKNOWN_OPERATION"})
                return

            project = self.service.get_project(task["project_id"])
            if (project.get("video") or {}).get("analysis_status") != "assets_extracted":
                if not self._run_director_step(task_id, "analyze", 10):
                    return
            result = self._semantic_stage(task_id)
            if result is None:
                return
            analysis_validation = self.service.validate_analysis_contract(task["project_id"])
            if analysis_validation.get("status") != "ready":
                self._terminal(task_id, "blocked", "Codex 返回后原片语义合同仍未真实落盘", analysis_validation)
                return
            tier = self.service.project_config(task["project_id"]).get("execution_tier")
            if tier in {"first_frame_only", "full_delivery"}:
                generation = self.service.generation_artifact_status(task["project_id"])
                if generation.get("status") != "ready":
                    self._terminal(
                        task_id,
                        "waiting",
                        "所选镜头没有全部产生可验证批准首帧；当前图像生成适配器未配置",
                        generation,
                    )
                    return
            if tier in {"prompt_only", "full_delivery"}:
                for step, progress in (("lint", 72), ("compile", 80), ("verify", 88)):
                    if not self._run_director_step(task_id, step, progress):
                        return
                    if step == "verify":
                        self.service.mark_delivery_preflight_verified(task["project_id"])
            if tier == "full_delivery":
                export_preflight = self.service.validate_docx_export_preflight(task["project_id"])
                if export_preflight.get("status") != "ready":
                    self._terminal(
                        task_id,
                        "waiting" if export_preflight.get("status") == "waiting" else "blocked",
                        "Word 导出前当前输入哈希门禁未通过",
                        export_preflight,
                    )
                    return
                for step, progress in (("export_docx", 93), ("align", 97)):
                    if not self._run_director_step(task_id, step, progress):
                        return
                self.service.mark_docx_qa_pending(task["project_id"])
                self._terminal(
                    task_id,
                    "waiting",
                    "图文、编译、导出和对齐已执行；仍需 Word 全页视觉 QA 才能完整交付",
                    {"code": "DOCX_VISUAL_QA_REQUIRED", "pending_inputs": ["document_visual_qa"]},
                )
                return
            self._lane(task_id, "controller", "completed", 100, "当前执行档位已完成")
            self._terminal(task_id, "completed", "当前执行档位已完成", result)
        except ApiError as exc:
            self._terminal(task_id, "failed", exc.message, {"code": exc.code, "details": exc.details})
        except Exception as exc:
            self._terminal(task_id, "failed", "本地任务发生未处理错误", {"code": "UNHANDLED_TASK_ERROR", "message": str(exc)})
