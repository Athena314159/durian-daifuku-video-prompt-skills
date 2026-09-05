#!/usr/bin/env python3
"""Submit compiled V2 image tasks to Volcano Engine Ark."""
import base64, json, os, sys
from pathlib import Path
from urllib.request import Request, urlopen
from urllib.error import HTTPError, URLError

ARK_URL = "https://ark.cn-beijing.volces.com/api/v3/images/generations"

def fail(message): raise SystemExit(message)

def image_value(path):
    p = Path(path)
    if not p.exists(): fail(f"源帧不存在：{p}")
    fmt = p.suffix.lower().lstrip('.') or 'png'
    if fmt == 'jpg': fmt = 'jpeg'
    return f"data:image/{fmt};base64," + base64.b64encode(p.read_bytes()).decode('ascii')

def submit(task, api_key):
    provider = task.get("provider") or {}
    if provider.get("type") != "seedream": fail("任务provider不是seedream")
    payload = {
        "model": provider.get("ark_model_id") or "doubao-seedream-5-0-260128",
        "prompt": Path(task["prompt_file"]).read_text(encoding="utf-8"),
        "size": provider.get("size", "2K"), "response_format": "url",
        "watermark": bool(provider.get("watermark", False))
    }
    if task.get("source_frame"): payload["image"] = [image_value(task["source_frame"])]
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
    if not dry and not os.environ.get("ARK_API_KEY"): fail("请设置ARK_API_KEY；不会从项目文件读取密钥")
    outputs = []
    for task in manifest.get("tasks", []):
        item = {"shot_id": task["shot_id"], "provider": task.get("provider")}
        item["status"] = "DRY_RUN" if dry else "SUBMITTED"
        if not dry: item["response"] = submit(task, os.environ["ARK_API_KEY"])
        outputs.append(item)
    result.parent.mkdir(parents=True, exist_ok=True)
    result.write_text(json.dumps({"provider":"seedream","model":"doubao-seedream-5-0-260128","tasks":outputs}, ensure_ascii=False, indent=2) + "\n")
    print(json.dumps({"status":"PASS","tasks":len(outputs),"result":str(result),"dry_run":dry}, ensure_ascii=False))

if __name__ == "__main__": main()
