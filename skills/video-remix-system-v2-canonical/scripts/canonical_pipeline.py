#!/usr/bin/env python3
import hashlib, json, re, sys
from pathlib import Path

def dump(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n")

def sha(path):
    h=hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda:f.read(1024*1024), b""): h.update(chunk)
    return h.hexdigest()

def resolve_file(value, base):
    """Resolve a project path without changing the path stored in manifests."""
    path = Path(value)
    return path if path.is_absolute() else (base / path).resolve()

def reference_entries(shot):
    """Return reference file records from either supported input spelling."""
    raw = shot.get("reference_assets")
    if raw is None:
        raw = shot.get("references", [])
    if isinstance(raw, dict) and not any(key in raw for key in ("path", "file", "image_path", "asset_path")):
        raw = [{"path": value} for value in raw.values()]
    elif isinstance(raw, (str, dict)):
        raw = [raw]
    if not isinstance(raw, list):
        err(f"{shot.get('shot_id')}参考资产必须是列表")
    entries = []
    for item in raw:
        if isinstance(item, str):
            entries.append({"path": item})
            continue
        if not isinstance(item, dict):
            err(f"{shot.get('shot_id')}参考资产格式无效")
        path = item.get("path") or item.get("file") or item.get("image_path") or item.get("asset_path")
        if not path:
            err(f"{shot.get('shot_id')}参考资产缺路径")
        entries.append({"path": path, "sha256": item.get("sha256") or item.get("hash")})
    return entries

def validate_reference_files(shot, base_dir):
    """Validate references before they can reach a provider request."""
    checked = []
    for item in reference_entries(shot):
        path = resolve_file(item["path"], base_dir)
        if not path.exists() or not path.is_file():
            err(f"{shot.get('shot_id')}参考资产不存在：{path}")
        digest = sha(path)
        if item.get("sha256") and digest != item["sha256"]:
            err(f"{shot.get('shot_id')}参考资产哈希不一致：{path}")
        checked.append({"path": item["path"], "resolved_path": str(path), "sha256": digest})
    return checked

def err(msg): raise ValueError(msg)

def required_eating_count(duration_seconds):
    """20秒起3个，之后每完整10秒增加1个；保留已有合法源事件。"""
    duration = float(duration_seconds)
    if duration < 20.0:
        return 0
    return 3 + int((duration - 20.0) // 10.0)

def validate_prompt_role(prompt_path, shot):
    """在提交任何生成任务前拦截内部字段和角色类型串线。"""
    text = prompt_path.read_text(encoding="utf-8")
    labels = ("动作触发：", "具体情绪：", "可见变化：", "声音变化：",
              "产品状态变化：", "镜头响应：", "下一动作：")
    if "触发" in text or any(label in text for label in labels):
        err(f"{shot['shot_id']} Prompt含内部字段或禁用词")
    if shot.get("shot_mode") in {"hands_only", "product_macro"}:
        leaked = re.findall(r"眼睛|眉峰|眉心|嘴角|鼻翼|肩线|双颊|下巴|口型|咀嚼|人物表情", text)
        if leaked:
            err(f"{shot['shot_id']}是{shot['shot_mode']}，Prompt泄漏人物表演字段：{sorted(set(leaked))}")

def validate_source_performance_contract(generation_gate, shot, prompt_path, enabled):
    """Require observable source performance evidence for new V2 projects."""
    if not enabled:
        return
    evidence=generation_gate.get("source_performance_evidence") or {}
    mode=shot.get("shot_mode")
    prompt=prompt_path.read_text(encoding="utf-8")
    if mode == "person_visible":
        required=("gaze_path","facial_micro_reactions","shoulder_weight_shift",
                   "hand_roles","voice_observation","emotion_landing","source_anchor_terms")
        missing=[key for key in required if not evidence.get(key)]
        if missing: err(f"{shot['shot_id']}缺源片人物表演证据：{missing}")
        lexical_groups=(r"目光|视线|眼神|看", r"眉|嘴角|眼睑|鼻翼",
                        r"肩|重心|前倾|回稳|下沉", r"左手|右手|双手|拇指|食指")
        for group in lexical_groups:
            if not re.search(group, prompt):
                err(f"{shot['shot_id']}人物Prompt缺源片表演证据词：{group}")
        anchors=[str(x) for x in evidence.get("source_anchor_terms", []) if str(x)]
        if anchors and sum(1 for term in anchors if term in prompt) < min(2, len(anchors)):
            err(f"{shot['shot_id']}人物Prompt没有回写足够的源片动作锚点")
    else:
        required=("finger_force","weight_transfer","packaging_friction","focus_path","product_change")
        missing=[key for key in required if not evidence.get(key)]
        if missing: err(f"{shot['shot_id']}无人物镜头缺物理证据：{missing}")
        if evidence.get("facial_micro_reactions") or evidence.get("gaze_path"):
            err(f"{shot['shot_id']}无人物镜头混入脸部/视线证据")

def compile_project(source_path, out_dir):
    data=json.loads(source_path.read_text())
    out=Path(out_dir); out.mkdir(parents=True, exist_ok=True)
    required=("project_id","target_duration_seconds","script_lines","generation_shots","semantic_role_performance_gate_file")
    for key in required:
        if key not in data: err(f"canonical缺{key}")
    lines=data["script_lines"]; shots=data["generation_shots"]
    # Image generation is provider-configurable.  Seedream 5.0 Pro is the
    # canonical default for static assets/first frames, while the actual
    # provider submission remains an explicit downstream execution step.
    image_provider=data.get("image_generation_provider") or {
        "type":"seedream", "model":"seedream-5.0-pro", "mode":"image_generation"
    }
    if image_provider.get("type") != "seedream":
        err("当前V2图像层仅支持seedream provider")
    if not image_provider.get("model"):
        err("image_generation_provider缺model")
    line_ids=[x.get("line_id") for x in lines]
    if not line_ids or None in line_ids or len(line_ids)!=len(set(line_ids)): err("line_id必须非空唯一")
    shot_ids=[x.get("shot_id") for x in shots]
    if not shot_ids or None in shot_ids or len(shot_ids)!=len(set(shot_ids)): err("shot_id必须非空唯一")
    if any(not re.fullmatch(r"S\d{3}", str(sid)) for sid in shot_ids):
        err("正式generation_shots只能使用源片S001格式编号；补入事件绑定既有shot_id，不新建ADD镜头")
    order={sid:i for i,sid in enumerate(shot_ids)}
    required_source=list(data.get("source_coverage",{}).get("required_source_shot_ids",[]))
    covered_source=[]
    for shot in shots:
        source_ids=shot.get("source_shot_ids") or []
        if not source_ids:
            err(f"{shot.get('shot_id')}缺source_shot_ids；不能从图片数量反推镜头")
        covered_source.extend(source_ids)
        if shot.get("shot_mode") not in {"person_visible","hands_only","product_macro"}:
            err(f"{shot.get('shot_id')}缺有效shot_mode")
        if shot.get("shot_mode") == "person_visible" and shot.get("person_visible") is False:
            err(f"{shot.get('shot_id')} shot_mode与person_visible冲突")
        if shot.get("shot_mode") in {"hands_only","product_macro"} and shot.get("person_visible") is True:
            err(f"{shot.get('shot_id')} shot_mode与person_visible冲突")
        frame_path=resolve_file(shot.get("source_frame", ""), source_path.parent)
        declared_frame_hash=shot.get("source_frame_sha256")
        if not frame_path.exists():
            err(f"{shot.get('shot_id')}源帧不存在：{frame_path}")
        if not declared_frame_hash or sha(frame_path) != declared_frame_hash:
            err(f"{shot.get('shot_id')}源帧哈希缺失或不一致")
    if required_source:
        missing=set(required_source)-set(covered_source)
        if missing: err("generation_shots漏源片镜头："+str(sorted(missing)))
        declared_order=data.get("source_coverage",{}).get("source_shot_order")
        if declared_order and covered_source != list(declared_order):
            err("generation_shots排列与source_shot_order不一致")

    # V2 semantic-role-performance gate is a hard input, not an optional report.
    gate_path=Path(data["semantic_role_performance_gate_file"])
    if not gate_path.is_absolute(): gate_path=(source_path.parent/gate_path).resolve()
    if not gate_path.exists(): err("语义门禁文件不存在："+str(gate_path))
    gate=json.loads(gate_path.read_text())
    if gate.get("status") != "PASS_AFTER_OPTIMIZATION": err("语义门禁未通过："+str(gate.get("status")))
    gate_shots={x.get("shot_id"):x for x in gate.get("generation_shots",[])}
    if set(gate_shots) != set(shot_ids): err("语义门禁镜头与canonical镜头不一致")
    performance_contract_enabled=bool(data.get("performance_contract_version") or data.get("project_rules",{}).get("performance_evidence_contract"))
    for s in shots:
        gs=gate_shots[s["shot_id"]]
        duration=float(s.get("duration_seconds",0))
        if abs(float(gs.get("duration",-1))-duration)>1e-6: err(f"{s['shot_id']}语义门禁时长不一致")
        beats=gs.get("action_beats") or []
        if not beats or abs(float(beats[0].get("start",-1)))>1e-6 or abs(float(beats[-1].get("end",-1))-duration)>1e-6:
            err(f"{s['shot_id']} action beat 未从0.00连续覆盖到结束")
        for a,b in zip(beats,beats[1:]):
            if abs(float(a.get("end",-1))-float(b.get("start",-2)))>1e-6: err(f"{s['shot_id']} action beat 有空档或重叠")
        for a in beats:
            if float(a.get("end",0))-float(a.get("start",0))>2.0+1e-6: err(f"{s['shot_id']}存在未说明的超过2秒节拍")
        for field in ("narrative_reconstruction","persona_drive","primary_emotion","secondary_emotions","undertone","residue","commercial_turn","evidence_basis","creative_enhancement"):
            if not gs.get(field): err(f"{s['shot_id']}缺语义字段：{field}")
        if len(gs.get("secondary_emotions",[]))<2: err(f"{s['shot_id']}并行情绪少于2项")
        pf=s.get("prompt_file")
        prompt_path=resolve_file(pf, source_path.parent) if pf else Path("")
        if not pf or not prompt_path.exists() or not prompt_path.is_file(): err(f"{s['shot_id']}缺有效canonical Prompt文件")
        validate_prompt_role(prompt_path, s)
        validate_source_performance_contract(gs, s, prompt_path, performance_contract_enabled)
        validate_reference_files(s, source_path.parent)
        geometry=s.get("geometry_guide")
        geometry_hash=s.get("geometry_guide_sha256")
        if geometry_hash and not geometry:
            err(f"{s['shot_id']}geometry_guide_sha256缺geometry_guide")
        if geometry_hash and isinstance(geometry, str):
            geometry_path=resolve_file(geometry, source_path.parent)
            if not geometry_path.exists() or not geometry_path.is_file():
                err(f"{s['shot_id']}geometry guide不存在：{geometry_path}")
            if sha(geometry_path) != geometry_hash:
                err(f"{s['shot_id']}geometry guide哈希不一致")
        approved=s.get("approved_image_path")
        if approved:
            approved_path=resolve_file(approved, source_path.parent)
            if not approved_path.exists() or not approved_path.is_file(): err(f"{s['shot_id']}批准图不存在：{approved_path}")
            declared=s.get("approved_image_sha256")
            if not declared:
                err(f"{s['shot_id']}批准图缺少approved_image_sha256")
            approved_digest=sha(approved_path)
            if approved_digest != declared:
                err(f"{s['shot_id']}批准图哈希不一致")
            shot_frame_path=resolve_file(s.get("source_frame", ""), source_path.parent)
            if approved_digest == s.get("source_frame_sha256") or approved_path == shot_frame_path:
                err(f"{s['shot_id']}源帧与批准图相同，禁止把源帧冒充批准图")
        elif s.get("approved_image_sha256"):
            err(f"{s['shot_id']}存在批准图哈希但缺approved_image_path")
    mirror={x.get("id") for x in gate.get("source_action_mirror",[])}
    required_source=set(data.get("source_coverage",{}).get("required_source_shot_ids",[]))
    if required_source and mirror != required_source: err("source_action_mirror未覆盖全部SRC源镜头")
    truth={x.get("line_id") for x in gate.get("line_truth_table",[])}
    if truth != set(line_ids): err("逐句真值表与script_lines不一致")
    if gate.get("role_lock",{}).get("immutable_speaker_key") != data.get("immutable_speaker_key"):
        err("不可变说话人键未与语义门禁一致")
    template_path=Path(data.get("eating_template_file", gate.get("eating_template",{}).get("file", "")))
    if not template_path.is_absolute(): template_path=(source_path.parent/template_path).resolve()
    if not template_path.exists(): err("榴莲大福吃食模板不存在："+str(template_path))
    template=json.loads(template_path.read_text())
    template_ids=set((template.get("templates") or {}).keys())
    bindings=data.get("eating_template_bindings") or gate.get("eating_template",{}).get("template_ids") or {}
    for event in (gate.get("eating_plan",{}).get("occurrences") or []):
        eid=event.get("event_group_id"); tid=event.get("template_id") or bindings.get(eid)
        if not tid or tid not in template_ids: err(f"吃食事件{eid}未绑定有效固定模板")

    # Line allocation is a source/script lock, not a heuristic.  Guessing a
    # destination from script_capacity is what previously moved copy into the
    # wrong shot.  Every line must be explicitly mapped in canonical_project.
    explicit={}
    for shot in shots:
        for lid in shot.get("line_ids",[]):
            if lid in explicit: err(f"{lid}跨镜重复：{explicit[lid]}和{shot['shot_id']}")
            explicit[lid]=shot["shot_id"]
    unknown=set(explicit)-set(line_ids)
    if unknown: err(f"镜头引用未知line_id：{sorted(unknown)}")
    pending=[lid for lid in line_ids if lid not in explicit]
    if pending:
        err(f"存在未显式锁定的口播映射：{pending}；请在canonical_project逐条填写line_ids")
    allocated=[lid for s in shots for lid in s.get("line_ids",[])]
    if allocated != line_ids: err("口播映射不是原稿顺序；请调整镜头line_ids或容量")

    line_by_id={x["line_id"]:x for x in lines}
    script_map=[]
    for s in shots:
        for lid in s.get("line_ids",[]):
            line=line_by_id[lid]
            script_map.append({
                "line_id":lid,"text":line["text"],"order":line_ids.index(lid)+1,
                "shot_id":s["shot_id"],"speaker_id":line.get("speaker_id") or s.get("default_speaker_id"),
                "screen_status":line.get("screen_status") or s.get("default_screen_status","inherit_source")
            })

    target=float(data["target_duration_seconds"])
    operation_mode=data.get("operation_mode","prompt_only")
    if operation_mode in {"full_delivery", "video_generation"}:
        missing_images=[s["shot_id"] for s in shots if not s.get("approved_image_path")]
        if missing_images:
            err("BLOCK_INCOMPLETE_IMAGE_DELIVERY：" + ",".join(missing_images))
    video_provider=data.get("video_generation_provider")
    video_status=("READY_FOR_SUBMIT" if video_provider else
                  "BLOCKED_VIDEO_BACKEND" if operation_mode=="video_generation" else
                  "NOT_REQUESTED")
    eating=list(data.get("eating_occurrences") or [])
    tearing=list(data.get("two_hand_tear_occurrences") or [])
    def supplement(events, minimum, kind, candidates_key):
        if target < 20: return events
        need=max(0,minimum-len(events)); pool=list(data.get(candidates_key) or [])
        used={e.get("source_frame") for e in events}
        used_shots={e.get("shot_id") for e in events if e.get("shot_id")}
        pool=[x for x in pool if x.get("source_frame") and x.get("source_frame") not in used
              and x.get("shot_id") not in used_shots]
        pool.sort(key=lambda x:float(x.get("score",0)), reverse=True)
        if len(pool)<need: err(f"{kind}缺{need}个合法原视频目标帧候选")
        for index,c in enumerate(pool[:need],1):
            events.append({
                "id":f"ADD-{kind.upper()}-{index:02d}","origin":"inserted_on_source_frame",
                "source_frame":c["source_frame"],"source_frame_sha256":c.get("source_frame_sha256"),
                "shot_id":c.get("shot_id"),"source_shot_id":c.get("source_shot_id"),"non_contiguous":True,
                "edit_scope":"source_frame_local_product_and_required_contact_only"
            })
        return events
    required_eating=required_eating_count(target)
    source_eating_count=len(eating)
    eating=supplement(eating,required_eating,"eating","eating_frame_candidates")
    tearing=supplement(tearing,2,"tear","tear_frame_candidates")
    if len(eating) < required_eating:
        err(f"吃食相关状态不足：需要{required_eating}个，当前{len(eating)}个")
    for kind, events in (("eating", eating), ("tear", tearing)):
        ids=[e.get("id") for e in events]
        if None in ids or len(ids)!=len(set(ids)): err(f"{kind} occurrence id重复或为空")
        for event in events:
            if event.get("shot_id") and event["shot_id"] not in shot_ids:
                err(f"{kind}事件{event.get('id')}引用了不存在的shot_id {event.get('shot_id')}")

    source_file=Path(data.get("source_video","")) if data.get("source_video") else None
    receipt={
        "system":"video-remix-system-v2-canonical","version":"2.1",
        "project_id":data["project_id"],"canonical_sha256":sha(source_path),
        "source_video_sha256":sha(source_file) if source_file and source_file.exists() else data.get("source_video_sha256"),
        "semantic_role_performance_gate_sha256":sha(gate_path),
        "semantic_role_performance_gate_status":gate.get("status"),
        "eating_template_sha256":sha(template_path),
        "eating_template_file":str(template_path),
        "rules":{
            "duration_eating_formula":"0 if <20s else 3 + floor((duration_seconds-20)/10)",
            "target_duration_gte_20_min_eating":required_eating,"target_duration_gte_20_min_two_hand_tear":2,
            "source_eating_count":source_eating_count,"inserted_eating_count":len(eating)-source_eating_count,
            "source_frame_only_insertions":True,"script_exactly_once":True,
            "direct_generation_bypass_forbidden":True,"auto_repair_rounds":2,
            "no_new_generation_shot_for_event_supplement":True,
            "video_generation_requires_explicit_provider_and_submission":True
        },"image_generation_provider":image_provider,
        "operation_mode":operation_mode,
        "video_generation_provider":video_provider,
        "video_generation_status":video_status,
        "status":"BLOCKED_VIDEO_BACKEND" if video_status=="BLOCKED_VIDEO_BACKEND" else "PASS"
    }
    final_manifest={
        "project_id":data["project_id"],"target_duration_seconds":target,
        "selected_shots":shot_ids,"eating_occurrences":eating,
        "two_hand_tear_occurrences":tearing,"canonical_sha256":receipt["canonical_sha256"],
        "required_eating_occurrences":required_eating,
        "source_eating_occurrences":source_eating_count,
        "inserted_eating_occurrences":len(eating)-source_eating_count,
        "video_generation":{"status":video_status,"reason":"V2 compile produces manifests only; video submission requires an explicit configured backend."}
    }
    image_tasks=[]; prompt_tasks=[]
    for s in shots:
        prompt_path=resolve_file(s["prompt_file"], source_path.parent)
        checked_references=validate_reference_files(s, source_path.parent)
        image_tasks.append({
            "shot_id":s["shot_id"],"source_frame":s.get("source_frame"),
            "provider":image_provider,
            "source_frame_sha256":s.get("source_frame_sha256"),"locked_regions":s.get("locked_regions",["person_identity","scene","camera","lighting","composition"]),
            "replace_regions":s.get("replace_regions",["target_product","approved_packaging_region","necessary_contact"]),
            "product_state":s.get("product_state","route_from_source_action"),"bbox_xywh":s.get("bbox_xywh"),
            "scale_anchors":s.get("scale_anchors",[]),
            "shot_mode":s.get("shot_mode"),
            "prompt_file":s.get("prompt_file"),
            "prompt_file_sha256":sha(prompt_path),
            "prompt_content_sha256":sha(prompt_path),
            "avatar_id":s.get("avatar_id"),
            "reference_assets":s.get("reference_assets"),
            "references":s.get("references"),
            "reference_file_hashes":checked_references,
            "packaging_lock":s.get("packaging_lock"),
            "geometry_guide":s.get("geometry_guide"),
            "geometry_guide_sha256":s.get("geometry_guide_sha256"),
            "arrangement_lock":s.get("arrangement_lock"),
            "approved_image_path":s.get("approved_image_path"),
            "approved_image_sha256":s.get("approved_image_sha256"),
            "image_status":"user_approved" if s.get("approved_image_path") else "awaiting_generation",
            "formal_generation_requires_manifest":True
        })
        prompt_tasks.append({
            "shot_id":s["shot_id"],"total_duration_seconds":s.get("duration_seconds"),
            "line_ids":s.get("line_ids",[]),"source_action":s.get("source_action",""),
            "prompt_file":s.get("prompt_file"),
            "prompt_file_sha256":sha(prompt_path),
            "prompt_content_sha256":sha(prompt_path),
            "shot_mode":s.get("shot_mode"),
            "story_chain":["cause_cue","intention","gaze","microexpression","hand_mouth_action","product_physics","sensory_feedback","emotion_landing","voice_breath","camera_exit"],
            "two_stage_compile":["fact_action_script","strong_human_narrative_enhancement"],
            "formal_dialogue_injected_from_script_map_only":True
        })
    dump(out/"canonical_project.json",data)
    dump(out/"semantic_role_performance_gate.json",gate)
    dump(out/"reusable_prompt_templates.json",template)
    dump(out/"script_shot_map.json",{"lines":script_map})
    dump(out/"final_generation_manifest.json",final_manifest)
    dump(out/"image_task_manifest.json",{"tasks":image_tasks})
    dump(out/"prompt_task_manifest.json",{"tasks":prompt_tasks})
    dump(out/"rule_receipt.json",receipt)
    receipt["compiled_canonical_sha256"]=sha(out/"canonical_project.json")
    receipt["prompt_file_sha256"]={s["shot_id"]:sha(resolve_file(s["prompt_file"], source_path.parent)) for s in shots}
    receipt["script_shot_map_sha256"]=sha(out/"script_shot_map.json")
    receipt["script_map_sha256"]=receipt["script_shot_map_sha256"]
    receipt["script_content_sha256"]=receipt["script_shot_map_sha256"]
    receipt["image_task_manifest_sha256"]=sha(out/"image_task_manifest.json")
    receipt["prompt_task_manifest_sha256"]=sha(out/"prompt_task_manifest.json")
    # Rewrite the receipt after recording the compiled artifact hashes.
    dump(out/"rule_receipt.json",receipt)
    print(json.dumps({"status":receipt["status"],"build_dir":str(out),"shots":len(shots),"lines":len(lines),"eating":len(eating),"tearing":len(tearing),"video_generation_status":video_status},ensure_ascii=False))

def validate(build_dir):
    root=Path(build_dir); required=["canonical_project.json","semantic_role_performance_gate.json","reusable_prompt_templates.json","script_shot_map.json","final_generation_manifest.json","image_task_manifest.json","prompt_task_manifest.json","rule_receipt.json"]
    missing=[x for x in required if not (root/x).exists()]
    if missing: err(f"缺编译产物：{missing}")
    canonical=json.loads((root/"canonical_project.json").read_text())
    gate=json.loads((root/"semantic_role_performance_gate.json").read_text())
    if gate.get("status") != "PASS_AFTER_OPTIMIZATION": err("编译产物语义门禁未通过")
    receipt=json.loads((root/"rule_receipt.json").read_text())
    compiled_hash=receipt.get("compiled_canonical_sha256")
    if not compiled_hash or sha(root/"canonical_project.json") != compiled_hash:
        err("BLOCK_STALE_BUILD：canonical编译产物哈希与规则收据不一致")
    prompt_manifest=json.loads((root/"prompt_task_manifest.json").read_text()).get("tasks",[])
    for task in prompt_manifest:
        expected_hash=receipt.get("prompt_file_sha256",{}).get(task.get("shot_id"))
        prompt_path=resolve_file(task.get("prompt_file", ""), root)
        if not prompt_path.exists() or not prompt_path.is_file():
            err(f"BLOCK_STALE_BUILD：{task.get('shot_id')} Prompt文件不存在")
        actual_prompt_hash=sha(prompt_path)
        if (not expected_hash or task.get("prompt_file_sha256") != expected_hash
                or task.get("prompt_content_sha256", task.get("prompt_file_sha256")) != actual_prompt_hash
                or actual_prompt_hash != task.get("prompt_file_sha256")):
            err(f"BLOCK_STALE_BUILD：{task.get('shot_id')} Prompt哈希缺失或已变化")
    image_manifest=json.loads((root/"image_task_manifest.json").read_text()).get("tasks",[])
    if len(image_manifest) != len(canonical.get("generation_shots", [])):
        err("image_task_manifest镜头数量与canonical不一致")
    for task in image_manifest:
        shot_id=task.get("shot_id")
        source_path=resolve_file(task.get("source_frame", ""), root)
        if not source_path.exists() or not source_path.is_file():
            err(f"BLOCK_STALE_BUILD：{shot_id}源帧不存在")
        if not task.get("source_frame_sha256") or sha(source_path) != task.get("source_frame_sha256"):
            err(f"BLOCK_STALE_BUILD：{shot_id}源帧哈希缺失或已变化")
        image_prompt_path=resolve_file(task.get("prompt_file", ""), root)
        if (not image_prompt_path.exists() or not image_prompt_path.is_file()
                or not task.get("prompt_file_sha256")
                or sha(image_prompt_path) != task.get("prompt_file_sha256")
                or task.get("prompt_content_sha256", task.get("prompt_file_sha256")) != sha(image_prompt_path)):
            err(f"BLOCK_STALE_BUILD：{shot_id} image task Prompt哈希缺失或已变化")
        geometry = task.get("geometry_guide")
        geometry_hash = task.get("geometry_guide_sha256")
        if geometry_hash and not geometry:
            err(f"BLOCK_STALE_BUILD：{shot_id} geometry guide缺失")
        if geometry_hash and isinstance(geometry, str):
            geometry_path = resolve_file(geometry, root)
            if not geometry_path.exists() or not geometry_path.is_file() or sha(geometry_path) != geometry_hash:
                err(f"BLOCK_STALE_BUILD：{shot_id} geometry guide不存在或哈希已变化")
        approved=task.get("approved_image_path")
        approved_hash=task.get("approved_image_sha256")
        if approved:
            approved_path=resolve_file(approved, root)
            if not approved_hash:
                err(f"BLOCK_STALE_BUILD：{shot_id}批准图缺少哈希")
            if not approved_path.exists() or not approved_path.is_file() or sha(approved_path) != approved_hash:
                err(f"BLOCK_STALE_BUILD：{shot_id}批准图不存在或哈希已变化")
            if approved_hash == task.get("source_frame_sha256") or approved_path == source_path:
                err(f"BLOCK_STALE_BUILD：{shot_id}源帧与批准图相同")
        elif approved_hash:
            err(f"BLOCK_STALE_BUILD：{shot_id}存在批准图哈希但缺批准图")
        for ref in task.get("reference_file_hashes", []):
            ref_path=resolve_file(ref.get("path", ""), root)
            if not ref_path.exists() or not ref_path.is_file() or sha(ref_path) != ref.get("sha256"):
                err(f"BLOCK_STALE_BUILD：{shot_id}参考资产不存在或哈希已变化")
    image_hash=receipt.get("image_task_manifest_sha256")
    if not image_hash or sha(root/"image_task_manifest.json") != image_hash:
        err("BLOCK_STALE_BUILD：image_task_manifest哈希不一致")
    prompt_manifest_hash=receipt.get("prompt_task_manifest_sha256")
    if not prompt_manifest_hash or sha(root/"prompt_task_manifest.json") != prompt_manifest_hash:
        err("BLOCK_STALE_BUILD：prompt_task_manifest哈希不一致")
    mapping=json.loads((root/"script_shot_map.json").read_text())["lines"]
    map_hash=receipt.get("script_shot_map_sha256")
    if not map_hash or sha(root/"script_shot_map.json") != map_hash:
        err("BLOCK_STALE_BUILD：script_shot_map正文已变化")
    expected=[x["line_id"] for x in canonical["script_lines"]]
    actual=[x["line_id"] for x in mapping]
    if actual!=expected: err("script_shot_map漏句、重复或逆序")
    final=json.loads((root/"final_generation_manifest.json").read_text())
    required_eating=required_eating_count(float(final["target_duration_seconds"]))
    if len(final["eating_occurrences"])<required_eating:
        err(f"吃播数量不足：需要{required_eating}，当前{len(final['eating_occurrences'])}")
    if float(final["target_duration_seconds"])>=20 and len(final["two_hand_tear_occurrences"])<2:
        err("双手掰开数量不足2")
    selected=list(final.get("selected_shots") or [])
    if any(not re.fullmatch(r"S\d{3}", str(sid)) for sid in selected):
        err("final_generation_manifest含新增S编号；新增事件必须绑定既有源镜头")
    print(json.dumps({"status":"PASS","build_dir":str(root),"required_eating":required_eating,
                      "eating":len(final["eating_occurrences"]),"tearing":len(final["two_hand_tear_occurrences"]),
                      "video_status":final.get("video_generation",{}).get("status","NOT_SUBMITTED")},ensure_ascii=False))

def main():
    if len(sys.argv)<3: raise SystemExit("usage: canonical_pipeline.py compile <canonical.json> <build-dir> | validate <build-dir>")
    if sys.argv[1]=="compile" and len(sys.argv)==4: compile_project(Path(sys.argv[2]).resolve(),Path(sys.argv[3]).resolve())
    elif sys.argv[1]=="validate" and len(sys.argv)==3: validate(Path(sys.argv[2]).resolve())
    else: raise SystemExit("invalid arguments")

if __name__=="__main__": main()
