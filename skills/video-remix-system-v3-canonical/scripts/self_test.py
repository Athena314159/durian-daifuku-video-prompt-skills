#!/usr/bin/env python3
import hashlib, json, subprocess, tempfile, sys
from pathlib import Path

HERE=Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import canonical_pipeline

def expect_block(args, label):
    result = subprocess.run(args, capture_output=True, text=True)
    if result.returncode == 0:
        raise AssertionError(f"{label}未被阻断")

assert canonical_pipeline.required_eating_count(19.99) == 0
assert canonical_pipeline.required_eating_count(20.0) == 3
assert canonical_pipeline.required_eating_count(49.99) == 5
assert canonical_pipeline.required_eating_count(50.0) == 6
assert canonical_pipeline.required_eating_count(54.892889) == 6
with tempfile.TemporaryDirectory() as td:
    root=Path(td); src=root/"canonical.json"; build=root/"build"
    shots=[]
    for i in range(1,6):
        frame=root/f"f{i}.png"; frame.write_bytes(f"source-{i}".encode())
        frame_hash=hashlib.sha256(frame.read_bytes()).hexdigest()
        shots.append({"shot_id":f"S{i:03d}","source_shot_ids":[f"SRC{i:03d}"],"source_frame":str(frame),"source_frame_sha256":frame_hash,"duration_seconds":4.0,"script_capacity":1,"line_ids":[f"L{i:03d}"],"source_action":"source action","product_state":"route_from_source_action","shot_mode":"person_visible","person_visible":True})
    data={
        "project_id":"self-test","target_duration_seconds":21.0,
        "immutable_speaker_key":"A",
        "script_lines":[{"line_id":f"L{i:03d}","text":f"测试口播{i}。","speaker_id":"Person-01","screen_status":"on_screen"} for i in range(1,6)],
        "generation_shots":shots,"eating_occurrences":[],"two_hand_tear_occurrences":[],
        "eating_frame_candidates":[{"shot_id":f"S{i:03d}","source_frame":str(root/f"f{i}.png"),"source_frame_sha256":shots[i-1]["source_frame_sha256"],"score":10-i} for i in (1,3,5)],
        "tear_frame_candidates":[{"shot_id":f"S{i:03d}","source_frame":str(root/f"f{i}.png"),"source_frame_sha256":shots[i-1]["source_frame_sha256"],"score":10-i} for i in (2,4)]
    }
    gate={
        "status":"PASS_AFTER_OPTIMIZATION",
        "role_lock":{"immutable_speaker_key":"A"},
        "source_action_mirror":[{"id":f"SRC{i:03d}"} for i in range(1,6)],
        "line_truth_table":[{"line_id":f"L{i:03d}"} for i in range(1,6)],
        "eating_plan":{"occurrences":[{"event_group_id":"E001","template_id":"bite_and_pull_string"},{"event_group_id":"E002","template_id":"bite_and_pull_string"},{"event_group_id":"E003","template_id":"bite_and_pull_string"}]},
        "generation_shots":[{
            "shot_id":f"S{i:03d}","duration":4.0,"narrative_reconstruction":"情境到镜头落点",
            "persona_drive":"分享欲","primary_emotion":"惊喜","secondary_emotions":["满足","期待"],
            "undertone":"熟人式安利","residue":"余韵","commercial_turn":"体验到证据",
            "evidence_basis":["SRC证据"],"creative_enhancement":{"status":"none"},
            "action_beats":[{"id":f"S{i:03d}-B01","start":0.0,"end":2.0,"trigger":"起始","emotion":"期待","visible_change":"视线移动","sound_change":"起音","product_change":"whole","camera_response":"跟随","next":"继续"},{"id":f"S{i:03d}-B02","start":2.0,"end":4.0,"trigger":"转折","emotion":"满足","visible_change":"眉眼打开","sound_change":"重音","product_change":"展示","camera_response":"回镜","next":"结束"}]
        } for i in range(1,6)]
    }
    gate_path=root/"semantic_gate.json"; gate_path.write_text(json.dumps(gate,ensure_ascii=False))
    data["semantic_role_performance_gate_file"]=str(gate_path)
    template_path=root/"reusable_prompt_templates.json"; template_path.write_text(json.dumps({"templates":{"bite_and_pull_string":{},"second_bite_existing_opening":{},"two_hand_tear_and_pull_string":{}}},ensure_ascii=False))
    data["eating_template_file"]=str(template_path)
    data["eating_template_bindings"]={"E001":"bite_and_pull_string","E002":"bite_and_pull_string","E003":"bite_and_pull_string"}
    data["source_coverage"]={"required_source_shot_ids":[f"SRC{i:03d}" for i in range(1,6)]}
    for s in shots:
        p=root/f"{s['shot_id']}.md"; p.write_text("原片叙事复原：测试")
        s["prompt_file"]=str(p)
    src.write_text(json.dumps(data,ensure_ascii=False))
    subprocess.run(["python3",str(HERE/"canonical_pipeline.py"),"compile",str(src),str(build)],check=True)
    subprocess.run(["python3",str(HERE/"canonical_pipeline.py"),"validate",str(build)],check=True)

    # A delivery-mode project cannot claim completion while images are still pending.
    delivery = json.loads(src.read_text())
    delivery["operation_mode"] = "full_delivery"
    delivery_path = root / "delivery-mode.json"
    delivery_path.write_text(json.dumps(delivery, ensure_ascii=False))
    expect_block(["python3", str(HERE/"canonical_pipeline.py"), "compile", str(delivery_path), str(root/"delivery-mode-build")], "full_delivery缺图")
    dry_result = root/"seedream-dry-run.json"
    subprocess.run(["python3", str(HERE/"seedream_ark.py"), "submit", str(build), str(dry_result), "--dry-run"], check=True)
    dry_payload = json.loads(dry_result.read_text())
    assert len(dry_payload["tasks"]) == len(shots)
    assert all(item.get("request_summary", {}).get("prompt_sha256") for item in dry_payload["tasks"])

    # Regression: a missing prompt must stop compilation.
    missing_prompt = json.loads(src.read_text())
    missing_prompt["generation_shots"][0].pop("prompt_file")
    missing_prompt_path = root/"missing-prompt.json"
    missing_prompt_path.write_text(json.dumps(missing_prompt, ensure_ascii=False))
    expect_block(["python3", str(HERE/"canonical_pipeline.py"), "compile", str(missing_prompt_path), str(root/"missing-prompt-build")], "缺prompt_file")

    # Regression: a missing reference must stop compilation before a provider call.
    missing_reference = json.loads(src.read_text())
    missing_reference["generation_shots"][0]["reference_assets"] = [str(root/"does-not-exist.png")]
    missing_reference_path = root/"missing-reference.json"
    missing_reference_path.write_text(json.dumps(missing_reference, ensure_ascii=False))
    expect_block(["python3", str(HERE/"canonical_pipeline.py"), "compile", str(missing_reference_path), str(root/"missing-reference-build")], "参考丢失")

    # Regression: a source frame cannot be declared as the approved image.
    masquerade = json.loads(src.read_text())
    masquerade["generation_shots"][0]["approved_image_path"] = masquerade["generation_shots"][0]["source_frame"]
    masquerade["generation_shots"][0]["approved_image_sha256"] = masquerade["generation_shots"][0]["source_frame_sha256"]
    masquerade_path = root/"source-as-approved.json"
    masquerade_path.write_text(json.dumps(masquerade, ensure_ascii=False))
    expect_block(["python3", str(HERE/"canonical_pipeline.py"), "compile", str(masquerade_path), str(root/"source-as-approved-build")], "源帧冒充批准图")

    # Regression: both prompt text and script-map body are protected by hashes.
    prompt_path = Path(shots[0]["prompt_file"])
    original_prompt = prompt_path.read_text()
    try:
        prompt_path.write_text(original_prompt + "\n篡改")
        expect_block(["python3", str(HERE/"canonical_pipeline.py"), "validate", str(build)], "Prompt正文篡改")
    finally:
        prompt_path.write_text(original_prompt)
    script_map = build/"script_shot_map.json"
    original_map = script_map.read_text()
    try:
        script_map.write_text(original_map.replace("测试口播1。", "被篡改的口播。"))
        expect_block(["python3", str(HERE/"canonical_pipeline.py"), "validate", str(build)], "script map正文篡改")
    finally:
        script_map.write_text(original_map)
print("V2 SELF TEST PASSED")
