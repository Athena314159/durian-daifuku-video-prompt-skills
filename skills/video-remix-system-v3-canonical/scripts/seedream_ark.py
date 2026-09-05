#!/usr/bin/env python3
"""Submit compiled V3 image tasks to Volcano Engine Ark."""
import base64, hashlib, json, os, sys
from pathlib import Path
from urllib.request import Request, urlopen
from urllib.error import HTTPError, URLError

ARK_URL = "https://ark.cn-beijing.volces.com/api/v3/images/generations"

def fail(message): raise SystemExit(message)

def sha(path):
    h = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()

def resolve_file(value, build):
    path = Path(value)
    return path if path.is_absolute() else (build / path).resolve()

def image_value(path):
    p = Path(path)
    if not p.exists(): fail(f"源帧不存在：{p}")
    fmt = p.suffix.lower().lstrip('.') or 'png'
    if fmt == 'jpg': fmt = 'jpeg'
    return f"data:image/{fmt};base64," + base64.b64encode(p.read_bytes()).decode('ascii')

def validate_task(task, build):
    """Validate every file used by a request before reading request content."""
    shot = task.get("shot_id", "?")
    prompt_path = resolve_file(task.get("prompt_file", ""), build)
    if not prompt_path.exists() or not prompt_path.is_file():
        fail(f"{shot} Prompt文件不存在：{prompt_path}")
    prompt_hash = task.get("prompt_file_sha256")
    if not prompt_hash or sha(prompt_path) != prompt_hash:
        fail(f"{shot} Prompt哈希缺失或已变化")
    source_path = resolve_file(task.get("source_frame", ""), build)
    if not source_path.exists() or not source_path.is_file():
        fail(f"{shot}源帧不存在：{source_path}")
    source_hash = task.get("source_frame_sha256")
    if not source_hash or sha(source_path) != source_hash:
        fail(f"{shot}源帧哈希缺失或已变化")
    approved = task.get("approved_image_path")
    approved_hash = task.get("approved_image_sha256")
    if approved:
        approved_path = resolve_file(approved, build)
        if not approved_hash:
            fail(f"{shot}批准图缺少哈希")
        if not approved_path.exists() or not approved_path.is_file() or sha(approved_path) != approved_hash:
            fail(f"{shot}批准图不存在或哈希已变化")
        if approved_hash == source_hash or approved_path == source_path:
            fail(f"{shot}源帧与批准图相同")
    elif approved_hash:
        fail(f"{shot}存在批准图哈希但缺批准图")
    geometry = task.get("geometry_guide")
    geometry_hash = task.get("geometry_guide_sha256")
    if geometry_hash and not geometry:
        fail(f"{shot}geometry guide缺失")
    if geometry_hash and isinstance(geometry, str):
        geometry_path = resolve_file(geometry, build)
        if not geometry_path.exists() or not geometry_path.is_file() or sha(geometry_path) != geometry_hash:
            fail(f"{shot}geometry guide不存在或哈希已变化")
    references = []
    for ref in task.get("reference_file_hashes", []):
        ref_path = resolve_file(ref.get("path", ""), build)
        if not ref_path.exists() or not ref_path.is_file():
            fail(f"{shot}参考资产不存在：{ref_path}")
        ref_hash = ref.get("sha256")
        if not ref_hash or sha(ref_path) != ref_hash:
            fail(f"{shot}参考资产哈希缺失或已变化：{ref_path}")
        references.append((ref_path, ref_hash))
    return prompt_path, source_path, references

def submit(task, api_key, build):
    prompt_path, source_path, references = validate_task(task, build)
    provider = task.get("provider") or {}
    if provider.get("type") != "seedream": fail("任务provider不是seedream")
    payload = {
        "model": provider.get("ark_model_id") or "doubao-seedream-5-0-260128",
        "prompt": prompt_path.read_text(encoding="utf-8"),
        "size": provider.get("size", "2K"), "response_format": "url",
        "watermark": bool(provider.get("watermark", False))
    }
    payload["image"] = [image_value(source_path)] + [image_value(path) for path, _ in references]
    request = Request(ARK_URL, data=json.dumps(payload, ensure_ascii=False).encode(),
        headers={"Content-Type":"application/json", "Authorization":f"Bearer {api_key}"}, method="POST")
    try:
        with urlopen(request, timeout=180) as response:
            return json.loads(response.read().decode("utf-8"))
    except HTTPError as exc: fail(f"Ark HTTP {exc.code}: {exc.read().decode('utf-8', errors='replace')}")
    except URLError as exc: fail(f"Ark网络请求失败：{exc.reason}")

def main():
    if len(sys.argv) not in (4, 5) or sys.argv[1] != "submit":
        fail("usage: seedream_ark.py submit <build-dir> <result.json> [--dry-run]")
    build, result = Path(sys.argv[2]), Path(sys.argv[3]); dry = len(sys.argv) == 5 and sys.argv[4] == "--dry-run"
    manifest = json.loads((build / "image_task_manifest.json").read_text())
    receipt = json.loads((build / "rule_receipt.json").read_text())
    if receipt.get("status") != "PASS": fail("规则收据未通过，禁止正式提交")
    # This verifies the compiled artifact chain before any provider request.
    try:
        import canonical_pipeline
        canonical_pipeline.validate(build)
    except (ValueError, SystemExit) as exc:
        fail(str(exc))
    if not dry and not os.environ.get("ARK_API_KEY"): fail("请设置ARK_API_KEY；不会从项目文件读取密钥")
    outputs = []
    for task in manifest.get("tasks", []):
        prompt_path, source_path, references = validate_task(task, build)
        item = {"shot_id": task["shot_id"], "provider": task.get("provider")}
        item["status"] = "DRY_RUN" if dry else "SUBMITTED"
        item["request_summary"] = {
            "model": (task.get("provider") or {}).get("ark_model_id") or "doubao-seedream-5-0-260128",
            "prompt_file": task.get("prompt_file"),
            "prompt_sha256": task.get("prompt_file_sha256"),
            "prompt_chars": len(prompt_path.read_text(encoding="utf-8")),
            "source_frame": task.get("source_frame"),
            "source_frame_sha256": task.get("source_frame_sha256"),
            "reference_assets": [{"path": ref.get("path"), "sha256": ref.get("sha256")} for ref in task.get("reference_file_hashes", [])],
            "image_count": 1 + len(references),
        }
        if not dry: item["response"] = submit(task, os.environ["ARK_API_KEY"], build)
        outputs.append(item)
    result.parent.mkdir(parents=True, exist_ok=True)
    result.write_text(json.dumps({"provider":"seedream","model":"doubao-seedream-5-0-260128","tasks":outputs}, ensure_ascii=False, indent=2) + "\n")
    print(json.dumps({"status":"PASS","tasks":len(outputs),"result":str(result),"dry_run":dry}, ensure_ascii=False))

if __name__ == "__main__": main()
