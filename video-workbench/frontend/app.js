(() => {
  "use strict";

  const API_ROOT = "/api/v1";
  const POLL_INTERVAL = 2200;
  const state = {
    connected: false,
    server: {},
    projects: [],
    project: null,
    detail: {},
    products: [],
    avatars: [],
    tasks: [],
    globalTasks: [],
    events: [],
    eventCursors: new Map(),
    shots: [],
    assets: [],
    approvals: [],
    markers: [],
    selectedShotId: null,
    selectedTaskId: null,
    selectedShotIds: new Set(),
    scopeIntent: "all",
    selectedVideoFile: null,
    selectedAssetFiles: [],
    editingAsset: null,
    selectedResultFile: null,
    assetDisplayLimit: 160,
    activeView: "editor",
    activeLibraryTab: "products",
    activeScriptSource: "source",
    activePromptView: "final",
    activeDetectors: [],
    selectedAssetPath: null,
    pendingSplitPlan: null,
    docxReviewedPageKeys: new Set(),
    docxReviewedDocumentSha: null,
    scriptDrafts: new Map(),
    scriptDirtySources: new Set(),
    editorMode: "source",
    operation: "product_only",
    personMode: "head",
    polling: null,
    savingConfig: null,
    loadingProject: false,
    refreshInFlight: false,
    refreshQueuedForce: false,
    refreshWaiters: [],
    projectEpoch: 0,
    projectRevisions: null,
    pollSequence: 0,
  };

  const $ = (selector, root = document) => root.querySelector(selector);
  const $$ = (selector, root = document) => Array.from(root.querySelectorAll(selector));
  const byId = (id) => document.getElementById(id);

  const escapeHtml = (value) => String(value ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#039;");

  const attr = escapeHtml;
  const clamp = (value, min, max) => Math.min(max, Math.max(min, value));
  const asArray = (value) => Array.isArray(value) ? value : [];
  const sleep = (ms) => new Promise((resolve) => window.setTimeout(resolve, ms));

  function formatTime(value) {
    const seconds = Number(value);
    if (!Number.isFinite(seconds)) return "00:00.000";
    const whole = Math.max(0, seconds);
    const minutes = Math.floor(whole / 60);
    const rest = whole - minutes * 60;
    return `${String(minutes).padStart(2, "0")}:${rest.toFixed(3).padStart(6, "0")}`;
  }

  function formatBytes(value) {
    const bytes = Number(value);
    if (!Number.isFinite(bytes)) return "—";
    const units = ["B", "KB", "MB", "GB"];
    let amount = bytes;
    let index = 0;
    while (amount >= 1024 && index < units.length - 1) {
      amount /= 1024;
      index += 1;
    }
    return `${amount >= 100 || index === 0 ? amount.toFixed(0) : amount.toFixed(1)} ${units[index]}`;
  }

  function formatDate(value) {
    if (!value) return "—";
    const date = new Date(value);
    if (Number.isNaN(date.getTime())) return String(value);
    return new Intl.DateTimeFormat("zh-CN", {
      month: "2-digit",
      day: "2-digit",
      hour: "2-digit",
      minute: "2-digit",
      hour12: false,
    }).format(date);
  }

  function stageLabel(status) {
    const labels = {
      created: "待导入视频",
      ready: "已就绪",
      queued: "排队中",
      running: "执行中",
      waiting: "等待你的输入",
      paused: "已暂停",
      cancelling: "正在取消",
      pause_requested: "等待步骤后暂停",
      cancel_requested: "正在请求取消",
      waiting_input: "等待你的输入",
      blocked: "被硬门拦截",
      failed: "执行失败",
      completed: "已完成",
      cancelled: "已取消",
      first_frame_repair: "首帧返修",
      images_revoked: "图片已撤销",
    };
    return labels[status] || status || "待开始";
  }

  function taskName(operation) {
    const names = {
      run: "总控任务",
      analyze: "原片分析",
      lint: "Prompt 硬检",
      compile: "Prompt 编译",
      codex: "Codex 执行",
      retry_shot: "单镜返工",
      extract_frames: "候选帧提取",
      verify: "交付验证",
      align: "图文对齐",
      export_docx: "DOCX 导出",
    };
    return names[operation] || operation || "任务";
  }

  function toast(title, message = "", type = "") {
    const stack = byId("toast-stack");
    const item = document.createElement("div");
    item.className = `toast ${type}`.trim();
    const strong = document.createElement("strong");
    strong.textContent = title;
    item.appendChild(strong);
    if (message) {
      const small = document.createElement("small");
      small.textContent = message;
      item.appendChild(small);
    }
    stack.appendChild(item);
    window.setTimeout(() => item.remove(), 4800);
  }

  async function copyText(textValue) {
    const value = String(textValue ?? "");
    if (navigator.clipboard?.writeText) {
      try {
        await navigator.clipboard.writeText(value);
        return;
      } catch (_) {
        // WKWebView on some macOS releases rejects the async clipboard API;
        // keep a synchronous selection fallback so the visible copy button works.
      }
    }
    const textarea = document.createElement("textarea");
    textarea.value = value;
    textarea.setAttribute("readonly", "");
    textarea.style.position = "fixed";
    textarea.style.opacity = "0";
    textarea.style.pointerEvents = "none";
    document.body.appendChild(textarea);
    textarea.select();
    const copied = document.execCommand("copy");
    textarea.remove();
    if (!copied) throw new Error("当前系统拒绝写入剪贴板");
  }

  async function imageBlobToPng(blob) {
    const canvas = document.createElement("canvas");
    const context = canvas.getContext("2d");
    if (!context) throw new Error("无法创建图片复制画布");
    if (window.createImageBitmap) {
      const bitmap = await createImageBitmap(blob);
      canvas.width = bitmap.width;
      canvas.height = bitmap.height;
      context.drawImage(bitmap, 0, 0);
      bitmap.close?.();
    } else {
      const objectUrl = URL.createObjectURL(blob);
      try {
        const image = new Image();
        image.src = objectUrl;
        await new Promise((resolve, reject) => {
          image.onload = resolve;
          image.onerror = () => reject(new Error("无法解码图片"));
        });
        canvas.width = image.naturalWidth;
        canvas.height = image.naturalHeight;
        context.drawImage(image, 0, 0);
      } finally {
        URL.revokeObjectURL(objectUrl);
      }
    }
    return new Promise((resolve, reject) => canvas.toBlob(
      (png) => png ? resolve(png) : reject(new Error("无法转换图片格式")),
      "image/png"
    ));
  }

  async function copyImageToClipboard(url) {
    if (!url) throw new Error("当前镜头没有可复制图片");
    const absoluteUrl = new URL(url, location.origin).href;
    if (navigator.clipboard?.write && window.ClipboardItem) {
      try {
        const response = await fetch(absoluteUrl);
        if (!response.ok) throw new Error(`读取图片失败 (${response.status})`);
        const png = await imageBlobToPng(await response.blob());
        await navigator.clipboard.write([new ClipboardItem({ "image/png": png })]);
        toast("图片已复制", "已把实际图片像素写入剪贴板，可直接粘贴。", "success");
        return;
      } catch (error) {
        // Preserve a useful one-click result on older WKWebView builds.
        await copyText(absoluteUrl);
        toast("已复制图片链接", `系统未开放图片剪贴板，已复制本地图片地址：${error.message}`, "success");
        return;
      }
    }
    await copyText(absoluteUrl);
    toast("已复制图片链接", "当前系统不支持直接复制图片像素，已复制本地图片地址。", "success");
  }

  async function api(path, options = {}) {
    const headers = new Headers(options.headers || {});
    const init = { ...options, headers };
    if (init.body && !(init.body instanceof FormData) && typeof init.body !== "string") {
      headers.set("Content-Type", "application/json");
      init.body = JSON.stringify(init.body);
    }
    let response;
    try {
      response = await fetch(`${API_ROOT}${path}`, init);
    } catch (cause) {
      const error = new Error("无法连接本地执行层");
      error.code = "NETWORK_ERROR";
      error.status = 0;
      error.cause = cause;
      throw error;
    }
    const contentType = response.headers.get("content-type") || "";
    let payload;
    if (contentType.includes("application/json")) {
      payload = await response.json();
    } else {
      payload = { ok: response.ok, text: await response.text() };
    }
    if (!response.ok || payload?.ok === false) {
      const error = new Error(payload?.error?.message || payload?.message || `请求失败 (${response.status})`);
      error.code = payload?.error?.code || `HTTP_${response.status}`;
      error.details = payload?.error?.details;
      error.status = response.status;
      throw error;
    }
    return payload;
  }

  async function optionalApi(path, options = {}, fallback = null) {
    try {
      return await api(path, options);
    } catch (error) {
      if ([404, 405].includes(error.status)) return fallback;
      throw error;
    }
  }

  function captureProjectContext() {
    return {
      projectId: state.project?.id || null,
      epoch: state.projectEpoch,
    };
  }

  function isCurrentProjectContext(context) {
    return Boolean(
      context?.projectId
      && state.project?.id === context.projectId
      && state.projectEpoch === context.epoch
    );
  }

  function setConnection(connected, message) {
    state.connected = connected;
    byId("app").dataset.connected = String(connected);
    byId("connection-text").textContent = message || (connected ? "本地执行层已连接" : "本地执行层未连接");
  }

  function normalizedPromptLengthContract(config = {}) {
    const contract = config?.prompt_length_contract;
    if (contract?.enabled === true) {
      const minimum = Number(contract.minimum_non_whitespace_characters);
      const maximum = Number(contract.maximum_non_whitespace_characters);
      return {
        enabled: true,
        minimum_non_whitespace_characters: Number.isInteger(minimum) && minimum > 0 ? minimum : 3000,
        maximum_non_whitespace_characters: Number.isInteger(maximum) && maximum >= minimum ? maximum : 4000,
      };
    }
    return { enabled: false, minimum_non_whitespace_characters: 0, maximum_non_whitespace_characters: 0 };
  }

  function syncPromptLengthSettings(config = {}) {
    const contract = normalizedPromptLengthContract(config);
    const issue = state.project?.skill_project?.prompt_length_contract_issue;
    byId("settings-prompt-length-enabled").checked = contract.enabled;
    byId("settings-prompt-length-bounds").hidden = !contract.enabled;
    byId("settings-prompt-length-minimum").value = String(contract.enabled ? contract.minimum_non_whitespace_characters : 3000);
    byId("settings-prompt-length-maximum").value = String(contract.enabled ? contract.maximum_non_whitespace_characters : 4000);
    byId("settings-prompt-length-status").textContent = issue
      ? "这个旧项目的 Prompt 字数合同无效，工作台没有在读取时偷偷改文件；当前先按关闭状态显示。请核对后保存项目设置，系统会带失效回执修复 canonical 合同。"
      : "默认关闭字数上下限，按最短可执行 Prompt 编译。只有目标平台明确要求时才打开；一旦打开，上下限同时成为硬门，但吃/掰拆镜、人物情绪和证据覆盖仍单独校验，不能靠重复水词凑字。";
  }

  function currentConfig() {
    const operation = state.operation;
    const replacesProduct = ["product_only", "head_product"].includes(operation);
    const replacesHead = ["head_only", "head_product"].includes(operation);
    const selectedScope = $("input[name='scope']:checked")?.value || "all";
    const existing = state.project?.config || {};
    const existingScope = existing.shot_scope && typeof existing.shot_scope === "object"
      ? existing.shot_scope
      : { mode: "all" };
    // An unresolved UI intent must never broaden a previously narrow scope to the
    // whole video. Preflight blocks the run while the persisted scope stays intact.
    let shotScope = existingScope;
    if (selectedScope === "all") {
      shotScope = { mode: "all" };
    } else if (selectedScope === "selected") {
      if (state.selectedShotIds.size) shotScope = { mode: "selected", shot_ids: Array.from(state.selectedShotIds) };
    } else if (selectedScope === "people") {
      const peopleShots = state.shots.filter((shot) => hasPeople(shot)).map(shotId);
      if (peopleShots.length) shotScope = { mode: "selected", shot_ids: peopleShots };
    } else if (selectedScope === "range") {
      const start = Number(byId("scope-range-start").value);
      const end = Number(byId("scope-range-end").value);
      if (Number.isFinite(start) && Number.isFinite(end) && end > start) shotScope = { mode: "range", start, end };
    }
    const settingsOpen = Boolean(byId("project-settings-dialog")?.open);
    const executionMode = settingsOpen
      ? (byId("settings-task-mode").value === "single" ? "single" : "paired")
      : byId("execution-mode").value;
    const existingPromptLength = normalizedPromptLengthContract(existing);
    const promptLengthEnabled = settingsOpen
      ? byId("settings-prompt-length-enabled").checked
      : existingPromptLength.enabled;
    const promptLengthContract = promptLengthEnabled
      ? {
        enabled: true,
        minimum_non_whitespace_characters: settingsOpen
          ? Number(byId("settings-prompt-length-minimum").value)
          : existingPromptLength.minimum_non_whitespace_characters,
        maximum_non_whitespace_characters: settingsOpen
          ? Number(byId("settings-prompt-length-maximum").value)
          : existingPromptLength.maximum_non_whitespace_characters,
      }
      : { enabled: false, minimum_non_whitespace_characters: 0, maximum_non_whitespace_characters: 0 };
    return {
      product_mode: replacesProduct ? "replace" : "preserve",
      product_id: replacesProduct ? (byId("product-select").value || null) : null,
      character_mode: replacesHead ? "head_replace" : "preserve",
      avatar_id: replacesHead ? (byId("avatar-select").value || null) : null,
      source_person_id: replacesHead ? (byId("source-person-select").value || null) : null,
      shot_scope: shotScope,
      execution_tier: settingsOpen ? byId("settings-execution-tier").value : existing.execution_tier || byId("new-project-tier").value || "source_intake",
      task_mode: executionMode === "single" ? "single" : "dual",
      prompt_length_contract: promptLengthContract,
      codex: {
        enabled: settingsOpen ? byId("settings-codex-enabled").checked : Boolean(existing.codex?.enabled),
        model: existing.codex?.model || null,
      },
    };
  }

  function bindingState(config = currentConfig()) {
    const project = state.project || {};
    const product = project.product_binding || {};
    const avatar = project.avatar_binding || {};
    const productReady = config.product_mode === "preserve"
      ? product.status === "ready" && !product.applied_id
      : Boolean(config.product_id) && ["ready", "bound_missing_approved_reference"].includes(product.status) && product.applied_id === config.product_id;
    const avatarOwnerReady = !config.source_person_id || avatar.source_person_id === config.source_person_id;
    const avatarReady = config.character_mode === "preserve"
      ? avatar.status === "ready" && !avatar.applied_id
      : Boolean(config.avatar_id) && avatar.status === "ready" && avatar.applied_id === config.avatar_id && avatarOwnerReady;
    return {
      productReady,
      avatarReady,
      ready: productReady && avatarReady,
      product,
      avatar,
    };
  }

  function operationFromConfig(config = {}) {
    const product = config.product_mode === "replace" || config.product_mode === "replace_product";
    // 历史项目里的 full_replace 只做兼容读取；新版工作台不再提供整人替换，
    // 统一降级为换头，避免参考图的身体、服装和背景污染原片。
    const head = ["head_replace", "full_replace"].includes(config.character_mode);
    if (head && product) return "head_product";
    if (head) return "head_only";
    if (product) return "product_only";
    return "product_only";
  }

  function shotId(shot, index = 0) {
    return String(shot?.shot_id || shot?.source_shot_id || shot?.inserted_shot_id || shot?.id || shot?.unit_id || `SRC${String(index + 1).padStart(2, "0")}`);
  }

  function shotStart(shot) {
    return Number(shot?.start ?? shot?.start_time ?? shot?.source_start ?? shot?.time_range?.start ?? shot?.source_timecode?.start ?? shot?.timeline_timecode?.start ?? shot?.generation_timecode?.start ?? 0) || 0;
  }

  function shotEnd(shot) {
    const start = shotStart(shot);
    return Number(shot?.end ?? shot?.end_time ?? shot?.source_end ?? shot?.time_range?.end ?? shot?.source_timecode?.end ?? shot?.timeline_timecode?.end ?? shot?.generation_timecode?.end ?? start) || start;
  }

  function normalizeShots(rawShots) {
    const groups = asArray(rawShots);
    const hasNestedUnits = groups.some((shot) => asArray(shot?.source_units).length || asArray(shot?.inserted_units).length);
    if (!hasNestedUnits) return groups;
    const flattened = [];
    groups.forEach((group, groupIndex) => {
      const parentId = group.id || group.shot_id || `S${String(groupIndex + 1).padStart(2, "0")}`;
      const parentStart = Number(group?.timecode?.start ?? group?.start ?? 0) || 0;
      const inherit = {
        parent_shot_id: parentId,
        parent_title: group.title,
        visual_type: group.visual_type,
        narrative_role: group.narrative_role,
        character: group.character,
        emotion: group.emotion,
        action_beats: group.action_beats,
        product_state: group.product_state,
        asset_links: group.asset_links,
        risk: group.risk,
        status: group.status,
      };
      asArray(group.source_units).forEach((unit) => {
        const local = unit.generation_timecode || {};
        flattened.push({
          ...inherit,
          ...unit,
          id: unit.source_shot_id || unit.id,
          unit_kind: "SRC",
          timeline_timecode: unit.source_timecode || {
            start: parentStart + Number(local.start || 0),
            end: parentStart + Number(local.end || local.duration || 0),
          },
        });
      });
      asArray(group.inserted_units).forEach((unit) => {
        const local = unit.generation_timecode || {};
        flattened.push({
          ...inherit,
          ...unit,
          id: unit.inserted_shot_id || unit.id,
          unit_kind: "ADD",
          timeline_timecode: {
            start: parentStart + Number(local.start || 0),
            end: parentStart + Number(local.end || local.duration || 0),
          },
        });
      });
    });
    return flattened.filter((shot) => shot.id);
  }

  function shotTags(shot) {
    const tags = new Set([...asArray(shot?.tags), ...asArray(shot?.semantic_tags)].map((value) => String(value).toLowerCase()));
    const splitSemanticReset = shot?.semantic_reset_after_split === true;
    const visualType = String(shot?.visual_type || shot?.action_type || "").toLowerCase();
    const observableText = [
      shot?.title,
      shot?.storyboard_description,
      shot?.narrative_role,
      editableValue(shot?.action_beats),
      editableValue(shot?.product_state),
    ].filter(Boolean).join(" ").toLowerCase();
    // A split child has deliberately lost its parent's active semantics. Its
    // label and archived source prose are not visual evidence: only marker-
    // backed semantic_tags or a fresh analysis may add eating/breaking again.
    if (!splitSemanticReset && (shot?.eating || shot?.contains_eating || visualType.includes("eating") || visualType.includes("bite") || visualType.includes("chew") || /(吃|咬|入口|离嘴|咀嚼|鼓腮)/.test(observableText))) tags.add("eating");
    if (!splitSemanticReset && (shot?.breaking || shot?.contains_breaking || visualType.includes("breaking") || visualType.includes("break") || visualType.includes("snap") || /(掰开|掰断|脆断|断裂|裂开|一分为二|两半分离)/.test(observableText))) tags.add("breaking");
    if (shot?.has_person || shot?.person_visible || shot?.speaker_id) tags.add("person");
    if (typeof shot?.character === "string" && !["", "none", "no_person"].includes(shot.character.toLowerCase())) tags.add("person");
    if (shot?.character && typeof shot.character === "object") {
      const character = shot.character;
      const visibility = String(character.visibility || "").toLowerCase();
      const explicitNoPerson = character.present === false || character.hands_only === true || ["none", "hands_only", "product_only"].includes(visibility);
      const explicitPerson = character.present === true
        || Boolean(character.id || character.person_id || character.speaker_id || character.owner_id)
        || ["face", "head", "body", "full", "partial", "person"].includes(visibility);
      if (explicitNoPerson) tags.delete("person");
      else if (explicitPerson) tags.add("person");
    }
    if (shot?.issue || shot?.issues?.length || shot?.status === "failed") tags.add("issue");
    if (Array.isArray(shot?.delivery_asset_ids) && shot.delivery_asset_ids.length === 0) tags.add("issue");
    return tags;
  }

  function hasPeople(shot) {
    const tags = shotTags(shot);
    return tags.has("person") || Boolean(shot?.character_ids?.length || shot?.people?.length);
  }

  function characterOwnerIds() {
    const ids = new Set();
    asArray(state.detail?.source_people || state.detail?.role_lock?.people || state.detail?.role_lock?.speakers).forEach((person) => {
      const id = person?.id || person?.person_id || person?.speaker_id;
      if (id) ids.add(String(id));
    });
    state.shots.forEach((shot) => {
      const character = shot.character;
      if (Array.isArray(character)) character.forEach((person) => ids.add(String(person?.id || person?.person_id || person?.speaker_id || "")));
      else if (character && typeof character === "object") {
        const id = character.id || character.person_id || character.speaker_id;
        if (id) ids.add(String(id));
      }
    });
    ids.delete("");
    return Array.from(ids);
  }

  function assetPath(asset) {
    return asset?.asset_path || asset?.path || asset?.relative_path || asset?.file || "";
  }

  function assetUrl(asset) {
    return asset?.media_url || asset?.url || asset?.image_url || asset?.thumbnail_url || "";
  }

  function imageAssets() {
    return state.assets.filter((asset) => {
      if (asset.kind) return asset.kind === "image";
      return /\.(png|jpe?g|webp|gif)$/i.test(assetPath(asset) || assetUrl(asset));
    });
  }

  function videoAssets() {
    return state.assets.filter((asset) => {
      if (asset.kind) return asset.kind === "video";
      return /\.(mp4|mov|m4v|webm)$/i.test(assetPath(asset) || assetUrl(asset));
    });
  }

  function visualAssets() {
    return [...imageAssets(), ...videoAssets()];
  }

  function isShotResultAsset(asset) {
    if (asset?.asset_class) return asset.asset_class === "shot_result";
    const path = assetPath(asset).replaceAll("\\", "/");
    return path.includes("/shots/results/")
      || path.startsWith("shots/results/")
      || /\bresult for\b/i.test(String(asset?.purpose || ""));
  }

  function isCandidateAsset(asset) {
    if (asset?.asset_class) return asset.asset_class === "candidate";
    const path = assetPath(asset).replaceAll("\\", "/");
    return !isShotResultAsset(asset)
      && (path.includes("/candidates/") || path.startsWith("candidates/") || /candidate|first.frame/i.test(String(asset?.purpose || "")));
  }

  function assetIdentifiers(asset) {
    return new Set([
      asset?.asset_id,
      asset?.id,
      asset?.filename,
      asset?.asset_path,
      asset?.path,
      asset?.relative_path,
      asset?.file,
    ].filter(Boolean).map((value) => String(value).replaceAll("\\", "/").toLowerCase()));
  }

  function assetOwnsUnit(asset, unitId) {
    const target = String(unitId || "").toLowerCase();
    return asArray(asset?.owner_unit_ids || asset?.unit_ids || (asset?.shot_id ? [asset.shot_id] : []))
      .some((value) => String(value).toLowerCase() === target);
  }

  function deliveryImageAssets() {
    const linkedIds = new Set(state.shots.flatMap((shot) => asArray(shot.delivery_asset_ids)).map((value) => String(value).toLowerCase()));
    const explicit = asArray(state.detail?.delivery_assets || state.project?.delivery_assets);
    if (explicit.length) {
      const combined = [...explicit.filter((asset) => !asset.kind || asset.kind === "image"), ...imageAssets().filter(isShotResultAsset)];
      const seen = new Set();
      return combined.filter((asset) => {
        const key = asset.asset_id || assetPath(asset);
        if (!key || seen.has(key)) return false;
        seen.add(key);
        return true;
      });
    }
    return imageAssets().filter((asset) => (
      Array.from(assetIdentifiers(asset)).some((id) => linkedIds.has(id))
      || state.shots.some((shot, index) => assetOwnsUnit(asset, shotId(shot, index)))
    ));
  }

  function shotHasDeliveryFile(shot) {
    const ids = new Set(asArray(shot.delivery_asset_ids).map((value) => String(value).replaceAll("\\", "/").toLowerCase()));
    const unitId = shotId(shot);
    return deliveryImageAssets().some((asset) => {
      if (assetOwnsUnit(asset, unitId)) return true;
      return Array.from(assetIdentifiers(asset)).some((id) => ids.has(id));
    });
  }

  function shotHasCurrentPrompt(shot) {
    const hasPrompt = Boolean(String(shot?.compiled_prompt || shot?.prompt || shot?.final_prompt || "").trim());
    const invalidated = shot?.requires_regeneration === true
      || shot?.requires_reanalysis === true
      || shot?.semantic_reset === true
      || shot?.prompt_stale === true
      || ["stale", "invalidated", "pending_reanalysis", "pending_regeneration"].includes(String(shot?.prompt_status || "").toLowerCase());
    return hasPrompt && !invalidated;
  }

  function currentApprovedAssetForShot(shot) {
    const ids = new Set(asArray(shot.delivery_asset_ids).map((value) => String(value).replaceAll("\\", "/").toLowerCase()));
    const unitId = shotId(shot);
    return deliveryImageAssets()
      .filter((asset) => latestApproval(asset)?.decision === "approve")
      .filter((asset) => assetOwnsUnit(asset, unitId) || Array.from(assetIdentifiers(asset)).some((id) => ids.has(id)))
      .sort((left, right) => {
        const classRank = (asset) => isShotResultAsset(asset) ? 0 : asset.asset_class === "delivery" ? 1 : 2;
        return classRank(left) - classRank(right) || String(right.created_at || right.filename || "").localeCompare(String(left.created_at || left.filename || ""));
      })[0] || null;
  }

  function latestApproval(asset) {
    if (asset && Object.prototype.hasOwnProperty.call(asset, "effective_approval")) {
      const effective = asset.effective_approval;
      return effective?.decision && effective.effective !== false && effective.valid !== false ? effective : null;
    }
    const path = assetPath(asset);
    const records = state.approvals.filter((record) => record.asset_path === path || (record.asset_id && record.asset_id === asset.asset_id));
    const validRecords = records.filter((record) => record.effective !== false && record.valid !== false);
    return validRecords.length ? validRecords[validRecords.length - 1] : null;
  }

  function getVideo(project = state.project) {
    return project?.video || state.detail?.video || null;
  }

  function getVideoMeta(video = getVideo()) {
    return video?.metadata || video?.probe || {};
  }

  function setView(view) {
    state.activeView = view;
    $$('[data-view]').forEach((button) => button.classList.toggle("is-active", button.dataset.view === view));
    $$('[data-view-panel]').forEach((panel) => panel.classList.toggle("is-active", panel.dataset.viewPanel === view));
    if (view === "library") renderLibrary();
    if (view === "tasks") renderTasks();
    if (view === "deliveries") renderDeliveries();
  }

  function setInspectorTab(tab) {
    $$('[data-inspector-tab]').forEach((button) => {
      const active = button.dataset.inspectorTab === tab;
      button.setAttribute("aria-selected", String(active));
    });
    $$('[data-inspector-panel]').forEach((panel) => panel.classList.toggle("is-active", panel.dataset.inspectorPanel === tab));
  }

  function setDockTab(tab) {
    $$('[data-dock-tab]').forEach((button) => button.setAttribute("aria-selected", String(button.dataset.dockTab === tab)));
    $$('[data-dock-panel]').forEach((panel) => panel.classList.toggle("is-active", panel.dataset.dockPanel === tab));
  }

  async function bootstrap() {
    setConnection(false, "正在连接本地执行层");
    try {
      const payload = await api("/bootstrap");
      state.server = payload.server || {};
      state.projects = asArray(payload.projects);
      state.products = asArray(payload.knowledge?.products);
      state.avatars = asArray(payload.knowledge?.avatars);
      const globalTaskPayload = await optionalApi("/tasks", {}, { tasks: [] });
      state.globalTasks = asArray(globalTaskPayload?.tasks);
      setConnection(true, "本地执行层已连接");
      byId("api-version").textContent = state.server.version || "v1";
      byId("codex-version").textContent = state.server.codex_available ? "可用 · 默认关闭" : "未找到";
      const mediaReady = Boolean(state.server.ffmpeg && state.server.ffprobe);
      byId("media-tools").textContent = mediaReady ? "FFmpeg 就绪" : "缺少 FFmpeg";
      const imageAdapter = state.server.adapters?.image_generation || state.server.image_generation_adapter;
      byId("image-adapter").textContent = [true, "ready", "configured"].includes(imageAdapter) ? "已连接" : "未配置";
      renderProjectList();
      renderKnowledgeSelectors();
      renderLibrary();
      const remembered = localStorage.getItem("jingliu.currentProjectId");
      const project = state.projects.find((item) => item.id === remembered) || state.projects[0];
      if (project) await selectProject(project.id, { quiet: true });
      else renderAll();
    } catch (error) {
      setConnection(false, "无法连接本地执行层");
      byId("api-version").textContent = "未启动";
      byId("codex-version").textContent = "—";
      byId("media-tools").textContent = "—";
      byId("image-adapter").textContent = "—";
      toast("工作台后端未启动", "请双击 run.command，或在终端启动本地服务。", "error");
      renderAll();
    }
  }

  async function refreshBootstrap({ quiet = false } = {}) {
    try {
      const payload = await api("/bootstrap");
      state.server = payload.server || state.server;
      state.projects = asArray(payload.projects);
      state.products = asArray(payload.knowledge?.products);
      state.avatars = asArray(payload.knowledge?.avatars);
      const globalTaskPayload = await optionalApi("/tasks", {}, { tasks: [] });
      state.globalTasks = asArray(globalTaskPayload?.tasks);
      setConnection(true, "本地执行层已连接");
      renderProjectList();
      renderKnowledgeSelectors();
      renderLibrary();
      if (!quiet) toast("已刷新", "项目与知识库状态已从本地磁盘重新读取。", "success");
    } catch (error) {
      setConnection(false, "连接已中断");
      if (!quiet) toast("刷新失败", error.message, "error");
    }
  }

  async function selectProject(projectId, { quiet = false } = {}) {
    if (!projectId || state.loadingProject) return;
    if (state.project?.id && state.project.id !== projectId && state.scriptDirtySources.size) {
      setView("editor");
      setInspectorTab("script");
      toast("还有未保存的口播草稿", "先保存当前口播，再切换项目；草稿不会被旧项目内容静默覆盖。", "error");
      return;
    }
    state.loadingProject = true;
    if (state.savingConfig) {
      window.clearTimeout(state.savingConfig);
      state.savingConfig = null;
    }
    hideUploadProgress();
    state.refreshQueuedForce = false;
    state.refreshWaiters.splice(0).forEach((resolve) => resolve());
    state.projectEpoch += 1;
    state.projectRevisions = null;
    state.pollSequence = 0;
    stopPolling();
    state.events = [];
    state.eventCursors.clear();
    state.tasks = [];
    state.shots = [];
    state.assets = [];
    state.approvals = [];
    state.markers = [];
    state.selectedShotId = null;
    state.selectedTaskId = null;
    state.selectedShotIds = new Set();
    state.selectedAssetPath = null;
    state.pendingSplitPlan = null;
    state.docxReviewedPageKeys = new Set();
    state.docxReviewedDocumentSha = null;
    state.scriptDrafts = new Map();
    state.scriptDirtySources = new Set();
    state.assetDisplayLimit = 160;
    try {
      const listProject = state.projects.find((item) => item.id === projectId);
      const detailPayload = await optionalApi(`/projects/${encodeURIComponent(projectId)}`, {}, null);
      state.project = detailPayload?.project || listProject || null;
      state.detail = detailPayload?.detail || detailPayload?.project || detailPayload || {};
      if (!state.project) throw new Error("找不到这个项目");
      localStorage.setItem("jingliu.currentProjectId", projectId);
      hydrateProjectState();
      await refreshProjectRuntime({ quiet: true, forceDetail: true });
      renderAll();
      startPolling();
      if (!quiet) toast("项目已打开", state.project.name, "success");
    } catch (error) {
      toast("无法打开项目", error.message, "error");
    } finally {
      state.loadingProject = false;
    }
  }

  function hydrateProjectState() {
    const config = state.project?.config || state.detail?.config || {};
    state.operation = operationFromConfig(config);
    state.personMode = "head";
    state.shots = normalizeShots(state.detail?.shots || state.project?.shots || state.detail?.shot_manifest?.shots || state.detail?.shot_manifest?.source_units);
    state.assets = asArray(state.detail?.assets || state.project?.assets || state.detail?.gallery?.assets);
    state.approvals = asArray(state.detail?.approvals || state.project?.approvals);
    state.markers = asArray(state.detail?.markers || state.project?.markers);
    const selected = asArray(config.shot_scope?.shot_ids);
    state.selectedShotIds = new Set(selected.map(String));
    if (!state.selectedShotId && state.shots.length) state.selectedShotId = shotId(state.shots[0], 0);

    byId("product-select").value = config.product_id || "";
    byId("avatar-select").value = config.avatar_id || "";
    renderSourcePersonSelector(config.source_person_id || state.project?.avatar_binding?.source_person_id || "");
    byId("portrait-rights").checked = Boolean(state.avatars.find((item) => item.id === config.avatar_id)?.authorized);
    byId("execution-mode").value = config.task_mode === "single" ? "single" : config.task_mode === "dual" ? "paired" : "smart";
    if (byId("settings-execution-tier")) byId("settings-execution-tier").value = config.execution_tier || "source_intake";
    if (byId("settings-task-mode")) byId("settings-task-mode").value = config.task_mode || "dual";
    if (byId("settings-codex-enabled")) byId("settings-codex-enabled").checked = Boolean(config.codex?.enabled);
    syncPromptLengthSettings(config);

    const rememberedScope = localStorage.getItem(`jingliu.scope.${state.project.id}`);
    const scope = ["all", "people", "selected", "range"].includes(rememberedScope)
      ? rememberedScope
      : config.shot_scope?.mode === "selected" ? "selected" : config.shot_scope?.mode === "range" ? "range" : "all";
    state.scopeIntent = scope;
    const scopeInput = $(`input[name='scope'][value='${scope}']`);
    if (scopeInput) scopeInput.checked = true;
    if (config.shot_scope?.mode === "range") {
      byId("scope-range-start").value = String(config.shot_scope.start ?? 0);
      byId("scope-range-end").value = String(config.shot_scope.end ?? 5);
    }
    syncOperationControls(false);
    hydrateScript();
  }

  function hydrateScript() {
    const script = state.detail?.script || state.project?.script || {};
    state.activeScriptSource = script.active_source || "source";
    state.scriptDrafts = new Map([
      ["source", script.source_text || script.transcript || ""],
      ["revised", script.revised_text || ""],
    ]);
    state.scriptDirtySources = new Set();
    renderScript();
  }

  function syncCleanScriptDrafts(script = {}) {
    const values = {
      source: script.source_text || script.transcript || "",
      revised: script.revised_text || "",
    };
    Object.entries(values).forEach(([source, value]) => {
      if (!state.scriptDirtySources.has(source)) state.scriptDrafts.set(source, value);
    });
  }

  async function refreshProjectRuntime({ quiet = true, forceDetail = false } = {}) {
    if (!state.project) return;
    if (state.refreshInFlight) {
      if (!forceDetail) return;
      state.refreshQueuedForce = true;
      return new Promise((resolve) => state.refreshWaiters.push(resolve));
    }
    state.refreshInFlight = true;
    const projectId = state.project.id;
    const epoch = state.projectEpoch;
    try {
      state.pollSequence += 1;
      const shouldRefreshGlobal = state.pollSequence === 1 || state.pollSequence % 3 === 0;
      const [statusPayload, taskPayload, globalTaskPayload] = await Promise.all([
        api(`/projects/${encodeURIComponent(projectId)}/status`),
        api(`/tasks?project_id=${encodeURIComponent(projectId)}`),
        shouldRefreshGlobal ? api("/tasks") : Promise.resolve(null),
      ]);
      if (state.project?.id !== projectId || state.projectEpoch !== epoch) return;
      const statusProject = statusPayload?.project || {};
      const nextRevisions = statusProject.revisions || {};
      const previousRevisions = state.projectRevisions;
      const revisionChanged = forceDetail
        || !previousRevisions
        || Object.keys(nextRevisions).some((key) => nextRevisions[key] !== previousRevisions[key]);
      const approvalsChanged = forceDetail
        || !previousRevisions
        || nextRevisions.approvals !== previousRevisions.approvals;
      const [projectPayload, approvalPayload] = await Promise.all([
        revisionChanged ? optionalApi(`/projects/${encodeURIComponent(projectId)}`, {}, null) : Promise.resolve(null),
        approvalsChanged ? optionalApi(`/projects/${encodeURIComponent(projectId)}/approvals`, {}, null) : Promise.resolve(null),
      ]);
      if (state.project?.id !== projectId || state.projectEpoch !== epoch) return;
      if (projectPayload?.project) {
        state.project = projectPayload.project;
        state.detail = projectPayload.detail || projectPayload.project;
        syncCleanScriptDrafts(state.detail?.script || state.project?.script || {});
        state.shots = normalizeShots(state.detail?.shots || state.project?.shots || state.detail?.shot_manifest?.shots || state.detail?.shot_manifest?.source_units);
        state.assets = asArray(state.detail?.assets || state.project?.assets || state.detail?.gallery?.assets);
        state.markers = asArray(state.detail?.markers || state.project?.markers);
      } else {
        Object.assign(state.project, {
          status: statusProject.status || state.project.status,
          updated_at: statusProject.updated_at || state.project.updated_at,
          pending_inputs: statusProject.pending_inputs || [],
          blocking_inputs: statusProject.blocking_inputs || [],
        });
      }
      if (Array.isArray(approvalPayload?.approvals)) state.approvals = approvalPayload.approvals;
      state.tasks = asArray(taskPayload.tasks);
      if (globalTaskPayload) state.globalTasks = asArray(globalTaskPayload.tasks);
      state.projectRevisions = nextRevisions;
      await refreshTaskEvents(projectId, epoch);
      if (state.project?.id !== projectId || state.projectEpoch !== epoch) return;
      if (revisionChanged) renderAll();
      else {
        renderProjectList();
        renderProjectHeader();
        renderTaskTabs();
        renderTasks();
        renderEvents();
        renderPreflight();
        renderDetection();
      }
    } catch (error) {
      if (!quiet) toast("刷新项目失败", error.message, "error");
      if (error.status === 0 || error.code === "NETWORK_ERROR") setConnection(false, "连接已中断");
    } finally {
      state.refreshInFlight = false;
      if (state.refreshQueuedForce) {
        state.refreshQueuedForce = false;
        const waiters = state.refreshWaiters.splice(0);
        try {
          if (state.project?.id === projectId && state.projectEpoch === epoch) {
            await refreshProjectRuntime({ quiet: true, forceDetail: true });
          }
        } finally {
          waiters.forEach((resolve) => resolve());
        }
      } else if (state.refreshWaiters.length) {
        state.refreshWaiters.splice(0).forEach((resolve) => resolve());
      }
    }
  }

  async function refreshTaskEvents(projectId = state.project?.id, epoch = state.projectEpoch) {
    const activeIds = new Set(state.tasks.map((task) => String(task.id)));
    for (const key of state.eventCursors.keys()) {
      if (!activeIds.has(key)) state.eventCursors.delete(key);
    }
    const results = await Promise.all(state.tasks.slice(0, 12).map(async (task) => {
      const key = String(task.id);
      const after = state.eventCursors.get(key) || 0;
      try {
        const payload = await api(`/tasks/${encodeURIComponent(key)}/events?after=${after}`);
        state.eventCursors.set(key, Number(payload.next_after ?? after));
        return asArray(payload.events).map((event) => ({ ...event, task_id: key, operation: task.operation }));
      } catch (_) {
        return [];
      }
    }));
    const incoming = results.flat();
    if (incoming.length && state.project?.id === projectId && state.projectEpoch === epoch) {
      state.events = [...state.events, ...incoming].slice(-300);
    }
  }

  function startPolling() {
    stopPolling();
    if (!state.project) return;
    state.polling = window.setInterval(() => refreshProjectRuntime({ quiet: true }), POLL_INTERVAL);
  }

  function stopPolling() {
    if (state.polling) window.clearInterval(state.polling);
    state.polling = null;
  }

  function renderAll() {
    renderProjectList();
    renderProjectHeader();
    renderTaskTabs();
    renderVideo();
    renderKnowledgeSelectors();
    renderOperation();
    renderTimeline();
    renderShots();
    renderShotFocus();
    renderTasks();
    renderEvents();
    renderAssets();
    renderScript();
    renderPrompt();
    renderPreflight();
    renderDeliveries();
    renderDetection();
  }

  function renderProjectList() {
    const root = byId("project-list");
    if (!state.projects.length) {
      root.innerHTML = '<div class="project-list-empty">暂无项目</div>';
      return;
    }
    root.innerHTML = state.projects.map((project) => `
      <button class="project-item ${state.project?.id === project.id ? "is-active" : ""}" type="button" data-project-id="${attr(project.id)}">
        <strong>${escapeHtml(project.name || project.id)}</strong>
        <small>${escapeHtml(stageLabel(project.status))} · ${escapeHtml(formatDate(project.updated_at || project.created_at))}</small>
      </button>`).join("");
  }

  function renderProjectHeader() {
    const project = state.project;
    byId("project-title").textContent = project?.name || "尚未导入项目";
    const running = state.tasks.find((task) => ["running", "queued"].includes(task.status));
    const waiting = state.tasks.find((task) => ["waiting_input", "blocked", "failed"].includes(task.status));
    byId("project-stage").textContent = running
      ? `${taskName(running.operation)} · ${stageLabel(running.status)}`
      : waiting ? `${taskName(waiting.operation)} · ${stageLabel(waiting.status)}`
        : project ? stageLabel(project.status) : "请先置入原视频";
  }

  function renderTaskTabs() {
    const projectName = state.project?.name || "尚未选择项目";
    byId("taskbar-project-name").textContent = projectName;
    byId("dock-project-context").textContent = state.project
      ? `${projectName} / ${state.selectedShotId || "全部分镜"}`
      : "项目 / 分镜";
    byId("new-task-button").hidden = !state.project;
    byId("new-task-button").disabled = !state.project;
    const root = byId("task-tab-strip");
    if (!state.project) {
      root.innerHTML = '<span class="task-tab-empty">先从左侧选择项目，或置入 9:16 原视频</span>';
      state.selectedTaskId = null;
      return;
    }
    if (!state.tasks.length) {
      root.innerHTML = '<span class="task-tab-empty">当前项目尚无任务 · 点击右侧“新建任务”</span>';
      state.selectedTaskId = null;
      return;
    }
    if (!state.tasks.some((task) => task.id === state.selectedTaskId)) {
      state.selectedTaskId = state.tasks.find((task) => ["running", "queued", "waiting_input", "paused"].includes(task.status))?.id || state.tasks[0].id;
    }
    root.innerHTML = state.tasks.map((task, index) => `
      <button class="task-tab ${attr(task.status)} ${task.id === state.selectedTaskId ? "is-active" : ""}" type="button" role="tab" aria-selected="${task.id === state.selectedTaskId}" data-task-tab-id="${attr(task.id)}" title="${attr(taskMessage(task))}">
        <i aria-hidden="true"></i><span>任务 ${index + 1} · ${escapeHtml(taskName(task.operation))}</span><small>${escapeHtml(taskStageLabel(task))}</small>
      </button>`).join("");
  }

  function renderVideo() {
    if (!new Set(["source", "candidate"]).has(state.editorMode)) state.editorMode = "candidate";
    const video = getVideo();
    const player = byId("source-video");
    const imagePreview = byId("media-image-preview");
    const modeEmpty = byId("media-mode-empty");
    const hasVideo = Boolean(video?.video_url);
    byId("choose-video-button-label").textContent = state.project && !hasVideo ? "上传原视频到当前项目" : "选择原视频";
    byId("replace-video-button").hidden = !state.project || !hasVideo;
    const sourceMode = state.editorMode === "source";
    const availableAssets = visualAssets().filter((asset) =>
      (isCandidateAsset(asset) || isShotResultAsset(asset)) && latestApproval(asset)?.decision !== "revoke");
    const selectedAsset = availableAssets.find((asset) => assetPath(asset) === state.selectedAssetPath) || availableAssets[0];
    const selectedIsVideo = selectedAsset?.kind === "video" || /\.(mp4|mov|m4v|webm)$/i.test(assetPath(selectedAsset));
    const showImage = !sourceMode && Boolean(selectedAsset) && !selectedIsVideo;
    const showResultVideo = !sourceMode && Boolean(selectedAsset) && selectedIsVideo;
    byId("video-empty-state").hidden = hasVideo || !sourceMode;
    player.hidden = !(sourceMode ? hasVideo : showResultVideo);
    player.controls = showResultVideo;
    imagePreview.hidden = !showImage;
    modeEmpty.hidden = sourceMode || showImage || showResultVideo;
    byId("transport").hidden = !hasVideo || !sourceMode;
    byId("timeline-panel").hidden = !hasVideo;
    byId("video-resolution").hidden = (!hasVideo && sourceMode) || (!selectedAsset && !sourceMode);
    byId("video-mode-label").hidden = !hasVideo && sourceMode;
    const playbackUrl = sourceMode ? video?.video_url : showResultVideo ? assetUrl(selectedAsset) : "";
    if (playbackUrl) {
      const absoluteUrl = new URL(playbackUrl, location.origin).href;
      if (player.src !== absoluteUrl) player.src = absoluteUrl;
    } else {
      player.removeAttribute("src");
    }
    if (showImage) {
      imagePreview.src = assetUrl(selectedAsset);
      state.selectedAssetPath = assetPath(selectedAsset);
      byId("video-resolution").textContent = selectedAsset.filename || selectedAsset.id || "项目图片";
    } else {
      imagePreview.removeAttribute("src");
    }
    if (showResultVideo) {
      state.selectedAssetPath = assetPath(selectedAsset);
      byId("video-resolution").textContent = `${selectedAsset.filename || selectedAsset.id || "生成视频"} · ${selectedAsset.version || "未标版本"}`;
    }
    const meta = getVideoMeta(video);
    if (sourceMode) {
      byId("video-resolution").textContent = meta.width && meta.height ? `${meta.width}×${meta.height}` : "媒体信息待读取";
      byId("video-resolution").title = meta.width && meta.height
        ? `${meta.width}×${meta.height} · ${Number(meta.fps || 0).toFixed(meta.fps ? 2 : 0)}fps`
        : "媒体信息待读取";
    }
    if (sourceMode) byId("duration-time").textContent = formatTime(meta.duration || player.duration);
    byId("video-mode-label").textContent = state.editorMode === "source" ? "原片预览" : "图片 / 视频总览";
  }

  function renderKnowledgeSelectors() {
    const productValue = byId("product-select")?.value || state.project?.config?.product_id || "";
    const avatarValue = byId("avatar-select")?.value || state.project?.config?.avatar_id || "";
    byId("product-select").innerHTML = `<option value="">${state.products.length ? "请选择目标产品" : "暂无产品 · 请先新增"}</option>` + state.products.map((item) => {
      const historical = item.selectable === false;
      return `<option value="${attr(item.id)}" ${historical ? "disabled" : ""}>${escapeHtml(item.name || item.id)}${item.version ? ` · v${escapeHtml(item.version)}` : ""}${historical ? " · 旧版仅供历史项目" : ""}</option>`;
    }).join("");
    byId("avatar-select").innerHTML = `<option value="">${state.avatars.length ? "请选择目标人物" : "暂无人物 · 请先新增"}</option>` + state.avatars.map((item) => {
      const historical = item.selectable === false;
      return `<option value="${attr(item.id)}" ${historical ? "disabled" : ""}>${escapeHtml(item.name || item.id)}${item.authorized ? " · 已授权" : " · 授权待确认"}${historical ? " · 旧版仅供历史项目" : ""}</option>`;
    }).join("");
    if (state.products.some((item) => item.id === productValue)) byId("product-select").value = productValue;
    if (state.avatars.some((item) => item.id === avatarValue)) byId("avatar-select").value = avatarValue;
    renderAssetPreview("product", state.products.find((item) => item.id === byId("product-select").value));
    renderAssetPreview("avatar", state.avatars.find((item) => item.id === byId("avatar-select").value));
    renderSourcePersonSelector();
  }

  function sourcePeople() {
    const explicit = asArray(state.detail?.source_people || state.project?.source_people);
    if (explicit.length) return explicit;
    return characterOwnerIds().map((id) => ({ id, name: id }));
  }

  function renderSourcePersonSelector(preferred = null) {
    const select = byId("source-person-select");
    const people = sourcePeople();
    const current = preferred ?? select.value ?? state.project?.config?.source_person_id ?? "";
    select.innerHTML = '<option value="">单人物项目 · 自动</option>' + people.map((person) => {
      const id = person.id || person.person_id || person.speaker_id;
      const label = person.name || person.label || person.role || id;
      return `<option value="${attr(id)}">${escapeHtml(label)} · ${escapeHtml(id)}</option>`;
    }).join("");
    if (people.some((person) => String(person.id || person.person_id || person.speaker_id) === String(current))) select.value = current;
    byId("source-person-field").hidden = people.length <= 1;
  }

  function renderAssetPreview(kind, item) {
    const root = byId(`${kind === "product" ? "product" : "avatar"}-preview-row`);
    if (!item) {
      root.innerHTML = `<div class="asset-preview-placeholder">尚未选择${kind === "product" ? "产品" : "人物"}</div>`;
      return;
    }
    const media = item.media_url || asArray(item.references)[0]?.media_url || asArray(item.media_urls)[0] || "";
    const packaging = kind === "product" ? packagingLayerSummary(item) : "";
    const details = kind === "product"
      ? [formatDimensions(item.dimensions_cm), packaging].filter(Boolean).join(" · ")
      : `${item.authorized ? "授权已确认" : "授权待确认"} · ${asArray(item.references).length || asArray(item.media_urls).length || 0} 张身份参考`;
    root.innerHTML = `
      ${media ? `<img class="asset-thumb" src="${attr(media)}" alt="">` : '<div class="asset-thumb"></div>'}
      <div><strong>${escapeHtml(item.name || item.id)}</strong><small>${escapeHtml(details || (kind === "product" ? "产品与包装资料待补齐" : "人物身份资料待补齐"))}</small></div>`;
  }

  function renderOperation() {
    $$('input[name="operation"]').forEach((input) => {
      input.checked = input.value === state.operation;
      input.closest(".operation-card")?.classList.toggle("is-selected", input.checked);
    });
    const productRequired = ["product_only", "head_product"].includes(state.operation);
    const avatarRequired = ["head_only", "head_product"].includes(state.operation);
    byId("product-control-section").classList.toggle("is-inactive", !productRequired);
    byId("avatar-control-section").classList.toggle("is-inactive", !avatarRequired);
    byId("product-mode-status").textContent = productRequired ? "当前模式启用 · 必选" : "当前模式不替换产品 · 选择后自动启用";
    byId("avatar-mode-status").textContent = avatarRequired ? "当前模式启用 · 必选" : "当前模式不替换人物 · 选择后自动启用";
    const scope = $("input[name='scope']:checked")?.value || "all";
    const labels = { all: "全片", people: "人物镜头", selected: `${state.selectedShotIds.size} 个镜头`, range: "时间范围" };
    byId("selection-scope-label").textContent = labels[scope];
    byId("scope-range-fields").hidden = scope !== "range";
    byId("selection-summary").textContent = !state.shots.length
      ? "尚未生成分镜清单"
      : scope === "selected" ? `已选择 ${state.selectedShotIds.size} / ${state.shots.length} 个镜头`
        : scope === "people" ? `检测到 ${state.shots.filter(hasPeople).length} 个有人物的镜头`
          : scope === "range" ? `作用于 ${byId("scope-range-start").value}s–${byId("scope-range-end").value}s 内的镜头`
          : `将作用于全部 ${state.shots.length} 个镜头`;
    const owners = characterOwnerIds();
    byId("character-owner-notice").textContent = owners.length > 1
      ? `检测到 ${owners.length} 位人物（${owners.join("、")}）。当前只替换上方选中的 source owner，其他人物保持原片。`
      : owners.length === 1 ? `当前绑定目标：${owners[0]}。其他像素区域继续取原片。` : "当前按单人物绑定；原片分析若检测到多位人物，会先要求完成人物 owner / 角色锁，不会把所有人的头一起替换。";
    const config = currentConfig();
    const bindings = state.project ? bindingState(config) : { ready: false, product: {}, avatar: {} };
    const selectedProductRecord = state.products.find((item) => item.id === config.product_id);
    const selectedAvatarRecord = state.avatars.find((item) => item.id === config.avatar_id);
    const productLabel = config.product_mode === "replace" ? (selectedProductRecord?.name || config.product_id || "目标产品未选择") : "本任务不换产品";
    const avatarLabel = config.character_mode === "head_replace" ? (selectedAvatarRecord?.name || config.avatar_id || "目标人物未选择") : "保留原片人物身份";
    const targetsSelected = (config.product_mode !== "replace" || Boolean(config.product_id))
      && (config.character_mode !== "head_replace" || Boolean(config.avatar_id));
    byId("binding-state").textContent = bindings.ready ? "已应用" : bindings.product.status === "applying_with_codex" ? "Codex 应用中" : "需要应用";
    byId("binding-state").classList.toggle("ready", bindings.ready);
    byId("binding-summary").textContent = !state.project
      ? "选择人物和产品后，绑定事务会把下拉选择写入当前项目事实源。"
      : bindings.ready ? `产品：${productLabel}；人物：${avatarLabel}。当前项目事实源已同步。`
        : `产品：${productLabel}；人物：${avatarLabel}。目标选齐后才能应用到项目事实源。`;
    byId("apply-bindings").disabled = !state.project || bindings.ready || !targetsSelected;
  }

  function renderTimeline() {
    const root = byId("shot-timeline");
    const ruler = byId("timeline-ruler");
    const splitShot = selectedShot();
    byId("open-split-plan").disabled = !state.project || !splitShot || shotEnd(splitShot) - shotStart(splitShot) <= 0.01;
    const duration = Number(getVideoMeta().duration || (state.shots.length ? Math.max(...state.shots.map(shotEnd), 1) : 1));
    const labels = [0, .25, .5, .75, 1].map((ratio) => `<span class="ruler-label" style="left:${ratio * 100}%">${escapeHtml(formatTime(duration * ratio).slice(0, 5))}</span>`);
    const markerPins = state.markers.map((marker) => {
      const left = clamp((Number(marker.time || 0) / Math.max(duration, .001)) * 100, 0, 100);
      const owner = state.shots.find((shot) => Number(marker.time) >= shotStart(shot) && Number(marker.time) <= shotEnd(shot));
      const label = marker.kind === "eating" ? "吃" : "掰";
      return `<button class="timeline-marker ${attr(marker.kind)}" type="button" style="left:${left}%" title="${attr(`${label} · ${formatTime(marker.time)} · ${owner ? shotId(owner) : "待拆镜"}`)}" data-marker-time="${attr(marker.time)}">${label}</button>`;
    });
    ruler.innerHTML = [...labels, ...markerPins].join("");
    if (!state.shots.length) {
      root.innerHTML = '<div class="timeline-empty">完成“分析原片”后，这里会显示从 0 秒到片尾的全部 SRC 镜头。</div>';
      byId("timeline-summary").textContent = state.markers.length ? `已有 ${state.markers.length} 个人工时点，等待生成逐镜清单` : "等待分析原片";
      return;
    }
    root.innerHTML = state.shots.map((shot, index) => {
      const id = shotId(shot, index);
      const tags = shotTags(shot);
      const width = clamp(((shotEnd(shot) - shotStart(shot)) / Math.max(duration, .001)) * 100, 3.5, 100);
      const classes = ["timeline-shot", id === state.selectedShotId ? "is-selected" : "", tags.has("issue") ? "issue" : ""].filter(Boolean).join(" ");
      return `<button class="${classes}" type="button" data-shot-id="${attr(id)}" style="flex-basis:${width}%">
        <strong>${escapeHtml(id)} ${tags.has("eating") ? "· 吃" : ""}${tags.has("breaking") ? "· 掰" : ""}</strong>
        <small>${escapeHtml(formatTime(shotStart(shot)))}–${escapeHtml(formatTime(shotEnd(shot)))}</small>
      </button>`;
    }).join("");
    const eating = state.shots.filter((shot) => shotTags(shot).has("eating")).length;
    const breaking = state.shots.filter((shot) => shotTags(shot).has("breaking")).length;
    const sourceCount = state.shots.filter((shot) => shot.unit_type === "source" || shot.unit_kind === "SRC" || shotId(shot).startsWith("SRC")).length;
    const addCount = state.shots.filter((shot) => shot.unit_type === "inserted" || shot.unit_kind === "ADD" || shotId(shot).startsWith("ADD")).length;
    const covered = state.shots.filter(shotHasDeliveryFile).length;
    byId("timeline-summary").textContent = `总 unit ${state.shots.length} · SRC ${sourceCount} · ADD ${addCount} · 吃 ${eating} · 掰 ${breaking} · 图片覆盖 ${covered}/${state.shots.length}`;
  }

  function shotPreview(shot) {
    if (!shot) return { url: "", isVideo: false, label: "" };
    const approved = currentApprovedAssetForShot(shot);
    if (approved && assetUrl(approved)) {
      return { url: assetUrl(approved), isVideo: false, label: "已批准交付图" };
    }
    const result = shot.latest_result || {};
    const resultUrl = assetUrl(result) || result.media_url || "";
    if (resultUrl) {
      const isVideo = result.kind === "video" || /\.(mp4|mov|m4v|webm)(?:$|\?)/i.test(resultUrl);
      return { url: resultUrl, isVideo, label: isVideo ? "最新即梦视频" : "最新即梦首帧" };
    }
    const url = shot.thumbnail_url || shot.first_frame_url || shot.source_frame_url || shot.source_first_frame_url || "";
    return { url, isVideo: false, label: url ? "原片时间首帧" : "" };
  }

  function renderShotFocus() {
    const shot = selectedShot();
    const root = byId("shot-focus-media");
    const copyButton = byId("copy-selected-image");
    if (!shot) {
      byId("shot-focus-id").textContent = "未选择分镜";
      byId("shot-focus-time").textContent = state.project ? "请选择下方 SRC / ADD 分镜" : "项目 → 任务 → 分镜";
      root.innerHTML = "<span>选择分镜后在这里查看大图</span>";
      copyButton.disabled = true;
      delete copyButton.dataset.copyImageUrl;
      return;
    }
    const id = shotId(shot);
    const preview = shotPreview(shot);
    const tags = shotTags(shot);
    const tagText = [tags.has("eating") ? "吃镜头" : "", tags.has("breaking") ? "掰开镜头" : "", preview.label].filter(Boolean).join(" · ");
    byId("shot-focus-id").textContent = id;
    byId("shot-focus-time").textContent = `${formatTime(shotStart(shot))}–${formatTime(shotEnd(shot))}${tagText ? ` · ${tagText}` : ""}`;
    if (!preview.url) {
      root.innerHTML = "<span>本镜缺少首帧 / 结果图，图片覆盖检测会拦截交付</span>";
    } else if (preview.isVideo) {
      root.innerHTML = `<video preload="metadata" controls muted src="${attr(preview.url)}" aria-label="${attr(id)} 最新结果"></video>`;
    } else {
      root.innerHTML = `<img src="${attr(preview.url)}" alt="${attr(`${id} 分镜大图`)}">`;
    }
    copyButton.disabled = !preview.url || preview.isVideo;
    if (!copyButton.disabled) copyButton.dataset.copyImageUrl = preview.url;
    else delete copyButton.dataset.copyImageUrl;
    byId("dock-project-context").textContent = `${state.project?.name || "项目"} / ${id}`;
  }

  function renderShots() {
    byId("shot-count").textContent = String(state.shots.length);
    const root = byId("shot-board");
    const existingEmpty = root.previousElementSibling;
    if (existingEmpty?.classList.contains("dock-empty")) existingEmpty.hidden = state.shots.length > 0;
    if (!state.shots.length) {
      root.innerHTML = "";
      return;
    }
    root.innerHTML = state.shots.map((shot, index) => {
      const id = shotId(shot, index);
      const tags = shotTags(shot);
      const latestResult = shot.latest_result;
      const latestResultAsset = latestResult?.asset_id ? state.assets.find((asset) => asset.asset_id === latestResult.asset_id) : null;
      const preview = shotPreview(shot);
      const image = preview.isVideo ? (shot.thumbnail_url || shot.first_frame_url || shot.source_frame_url || shot.source_first_frame_url || "") : preview.url;
      const resultDecision = latestApproval(latestResultAsset || latestResult || {})?.decision;
      const flagText = [
        tags.has("eating") ? "吃" : "",
        tags.has("breaking") ? "掰" : "",
        tags.has("issue") ? "需返工" : "",
        latestResult ? `即梦 ${latestResult.version || "未标版"}${resultDecision === "approve" ? "已批准" : resultDecision === "revoke" ? "已撤销" : "待审核"}` : "",
      ].filter(Boolean).join(" · ") || "普通镜头";
      return `<article class="shot-card ${id === state.selectedShotId ? "is-selected" : ""}" data-shot-id="${attr(id)}">
        <div class="shot-card-image">${image ? `<img loading="lazy" src="${attr(image)}" alt="${attr(id)}"><button class="shot-card-copy" type="button" data-copy-image-url="${attr(image)}" aria-label="复制 ${attr(id)} 图片">复制</button>` : '<span class="missing-frame">缺首帧</span>'}</div>
        <div class="shot-card-body"><strong>${escapeHtml(id)}</strong><div class="shot-card-meta">${escapeHtml(formatTime(shotStart(shot)))}–${escapeHtml(formatTime(shotEnd(shot)))} · ${escapeHtml(flagText)}</div></div>
      </article>`;
    }).join("");
  }

  function taskProgress(task) {
    const raw = task.progress;
    if (typeof raw === "number") return raw;
    if (raw && typeof raw.percent === "number") return raw.percent;
    if (task.status === "completed") return 100;
    return 0;
  }

  function taskMessage(task) {
    const pendingLabels = {
      source_video: "导入原视频",
      enable_project_codex: "在项目设置开启 Codex",
      locked_revised_script: "确认并锁定新版口播",
      target_product_reference: "选择产品并补参考",
      avatar_reference: "选择人物并补参考",
      portrait_authorization: "补人物授权记录",
      apply_selected_product_binding: "应用产品绑定",
      apply_selected_avatar_binding: "应用人物绑定",
      source_person_id: "选择原片人物 owner",
      document_visual_qa: "完成 Word 全页视觉 QA",
      image_generation_adapter: "连接图像生成适配器",
    };
    const error = typeof task.error === "string"
      ? task.error
      : task.error?.message || task.error?.code || "";
    const pending = asArray(task.error?.pending_inputs || task.result?.pending_inputs || task.pending_inputs);
    const base = task.message || task.waiting_reason || error || task.result_summary || "等待状态更新";
    const code = typeof task.error === "object" ? task.error?.code : "";
    const queue = task.queue_position ? ` · 同项目写队列第 ${task.queue_position} 位` : "";
    return `${base}${queue}${pending.length ? ` · 需要：${pending.map((item) => pendingLabels[item] || item).join("、")}` : ""}${code && !base.includes(code) ? ` · ${code}` : ""}`;
  }

  function taskStageLabel(task) {
    if (task?.phase === "waiting_for_project_writer" || task?.error?.code === "PROJECT_WRITER_BUSY") {
      return task.queue_position ? `排队第 ${task.queue_position} 位` : "同项目排队";
    }
    return stageLabel(task?.status);
  }

  function taskButtons(task) {
    const id = attr(task.id);
    const status = task.status;
    const waitingForWriter = task.phase === "waiting_for_project_writer" || task.error?.code === "PROJECT_WRITER_BUSY";
    const buttons = [];
    if (["created", "queued", "ready"].includes(status) || (status === "waiting" && !waitingForWriter)) buttons.push(`<button data-task-action="start" data-task-id="${id}">重新检查并启动</button>`);
    if (status === "running") buttons.push(`<button data-task-action="pause" data-task-id="${id}">暂停</button>`);
    if (status === "paused") buttons.push(`<button data-task-action="resume" data-task-id="${id}">继续</button>`);
    if (["running", "paused", "queued", "waiting_input"].includes(status) || waitingForWriter) buttons.push(`<button data-task-action="cancel" data-task-id="${id}">取消</button>`);
    if (["failed", "blocked", "cancelled"].includes(status)) buttons.push(`<button data-task-action="retry" data-task-id="${id}" data-operation="${attr(task.operation)}">新建重试</button>`);
    return buttons.join("");
  }

  function laneMarkup(task) {
    const lanes = task.lanes && typeof task.lanes === "object" ? Object.entries(task.lanes) : [];
    if (!lanes.length) return "";
    const labels = { image: "图像审计", text: "文本分析", controller: "总控" };
    return `<div class="task-branch-grid">${lanes.map(([key, lane]) => `
      <div class="task-branch"><div><strong>${escapeHtml(labels[key] || key)}</strong><span>${escapeHtml(stageLabel(lane?.status))}</span></div><div class="task-progress"><span style="width:${clamp(Number(lane?.progress || 0), 0, 100)}%"></span></div><small>${escapeHtml(lane?.message || "等待依赖")}</small></div>`).join("")}</div>`;
  }

  function renderTasks() {
    const allTasks = state.globalTasks.length ? state.globalTasks : state.tasks;
    const count = allTasks.filter((task) => ["running", "queued", "paused", "waiting", "waiting_input", "cancelling"].includes(task.status)).length;
    byId("nav-task-count").textContent = String(count);
    byId("dock-task-count").textContent = String(state.tasks.length);
    byId("task-counter").hidden = count === 0;
    byId("task-counter").textContent = String(count);

    const laneRoot = byId("task-lanes");
    const centerRoot = byId("task-center-grid");
    const drawerRoot = byId("drawer-task-list");
    if (!state.tasks.length) {
      laneRoot.innerHTML = '<div class="dock-empty">还没有任务。导入视频后可启动原片分析或总控任务。</div>';
    } else {
      const cards = state.tasks.map((task) => {
        const progress = clamp(taskProgress(task), 0, 100);
        return `<article class="task-lane ${task.id === state.selectedTaskId ? "is-selected" : ""}" data-task-card-id="${attr(task.id)}">
          <div class="task-lane-head"><strong>${escapeHtml(taskName(task.operation))}</strong><span class="state-label ${attr(task.status)}">${escapeHtml(taskStageLabel(task))}</span></div>
          <div class="task-progress"><span style="width:${progress}%"></span></div>
          <small>${escapeHtml(taskMessage(task))}</small>
          ${laneMarkup(task)}
          <div class="task-lane-actions">${taskButtons(task)}</div>
        </article>`;
      }).join("");
      laneRoot.innerHTML = cards;
    }
    centerRoot.innerHTML = allTasks.length ? allTasks.map((task) => `
      <article class="task-center-card ${task.id === state.selectedTaskId ? "is-selected" : ""}" data-task-card-id="${attr(task.id)}">
        <div class="task-lane-head"><h2>${escapeHtml(taskName(task.operation))}</h2><span class="state-label ${attr(task.status)}">${escapeHtml(taskStageLabel(task))}</span></div>
        <p>${escapeHtml(taskMessage(task))}</p>
        <div class="task-progress"><span style="width:${clamp(taskProgress(task), 0, 100)}%"></span></div>
        ${laneMarkup(task)}
        <div class="library-card-meta"><span>项目 ${escapeHtml(state.projects.find((project) => project.id === task.project_id)?.name || task.project_id)}</span><span>任务 ${escapeHtml(task.id)}</span><span>${escapeHtml(formatDate(task.updated_at || task.created_at))}</span>${task.shot_id ? `<span>镜头 ${escapeHtml(task.shot_id)}</span>` : ""}</div>
        <div class="task-lane-actions">${taskButtons(task)}</div>
      </article>`).join("") : '<div class="page-empty">还没有任何项目任务。</div>';
    const active = allTasks.filter((task) => ["running", "queued", "paused", "waiting", "waiting_input", "cancelling", "blocked", "failed"].includes(task.status));
    drawerRoot.innerHTML = active.length ? active.map((task) => `
      <article class="task-lane"><div class="task-lane-head"><strong>${escapeHtml(taskName(task.operation))}</strong><span>${escapeHtml(taskStageLabel(task))}</span></div><small>${escapeHtml(state.projects.find((project) => project.id === task.project_id)?.name || task.project_id)} · ${escapeHtml(taskMessage(task))}</small>${laneMarkup(task)}<div class="task-lane-actions">${taskButtons(task)}</div></article>`).join("") : '<div class="drawer-empty">暂无运行任务</div>';
  }

  function renderEvents() {
    const root = byId("event-stream");
    if (!state.events.length) {
      root.innerHTML = '<div class="dock-empty">运行记录会显示每个任务的真实状态、命令、等待输入和失败原因。</div>';
      return;
    }
    root.innerHTML = [...state.events].reverse().map((event) => `
      <div class="event-row"><time>${escapeHtml(formatDate(event.time))}</time><span>${escapeHtml(taskName(event.operation || event.data?.operation))}</span><strong>${escapeHtml(event.message || event.type || "状态更新")}</strong></div>`).join("");
  }

  function renderAssets() {
    const root = byId("asset-gallery");
    const assets = visualAssets().sort((left, right) => {
      const rank = (asset) => isShotResultAsset(asset) ? 0 : isCandidateAsset(asset) ? 1 : asset.asset_class === "delivery" ? 2 : 3;
      return rank(left) - rank(right) || String(right.created_at || right.filename || "").localeCompare(String(left.created_at || left.filename || ""));
    });
    byId("asset-count").textContent = String(assets.length);
    byId("result-upload-selection").textContent = state.selectedShotId
      ? `当前生成结果目标：${state.selectedShotId}`
      : "先在镜头板选择一个 SRC / ADD";
    byId("open-result-upload").disabled = !state.project || !state.selectedShotId;
    byId("show-more-assets").hidden = assets.length <= state.assetDisplayLimit;
    byId("show-more-assets").textContent = `再显示 ${Math.min(160, Math.max(0, assets.length - state.assetDisplayLimit))} 个`;
    if (!assets.length) {
      root.innerHTML = '<div class="dock-empty">候选图、批准图和完整图库总览会在这里显示，不会只给你文件路径。</div>';
      return;
    }
    root.innerHTML = assets.slice(0, state.assetDisplayLimit).map((asset, index) => renderGalleryCard(asset, index, isShotResultAsset(asset) || isCandidateAsset(asset))).join("");
  }

  function renderGalleryCard(asset, index, approvalMode) {
    const url = assetUrl(asset);
    const path = assetPath(asset);
    const id = asset.asset_id || asset.id || `asset-${index}`;
    const approval = latestApproval(asset);
    const isVideo = asset.kind === "video" || /\.(mp4|mov|m4v|webm)$/i.test(path);
    const ownerIds = asArray(asset.owner_unit_ids || asset.unit_ids || (asset.shot_id ? [asset.shot_id] : []));
    const ownerId = ownerIds[0] || asset.unit_id || asset.shot_id || "";
    const branchStatus = asset.approval_status || asset.status;
    const status = approval?.decision === "approve"
      ? "用户有效批准"
      : approval?.decision === "revoke"
        ? "已撤销"
        : branchStatus
          ? `分支自报：${branchStatus}`
          : "候选素材";
    const actionButtons = [];
    if (!isVideo && url) actionButtons.push(`<button class="gallery-copy" type="button" data-copy-image-url="${attr(url)}">复制图片</button>`);
    if (approvalMode && path && approval?.decision === "revoke") {
      actionButtons.push("<button disabled>撤销已生效 · 请导入新版本</button>");
    } else if (approvalMode && path && approval?.decision === "approve") {
      actionButtons.push(`<button data-approval="revoke" data-asset-path="${attr(path)}" data-asset-id="${attr(id)}" data-shot-id="${attr(ownerId)}">撤销批准</button>`);
    } else if (approvalMode && path) {
      actionButtons.push(`<button data-approval="approve" data-asset-path="${attr(path)}" data-asset-id="${attr(id)}" data-shot-id="${attr(ownerId)}">批准</button>`);
      actionButtons.push(`<button data-approval="revoke" data-asset-path="${attr(path)}" data-asset-id="${attr(id)}" data-shot-id="${attr(ownerId)}">拒绝并撤销</button>`);
    }
    const buttons = actionButtons.length ? `<div class="task-lane-actions">${actionButtons.join("")}</div>` : "";
    return `<article class="gallery-card" data-project-asset="${attr(path)}">
      <div class="gallery-image">${url ? (isVideo ? `<video preload="metadata" controls muted src="${attr(url)}" aria-label="${attr(id)}"></video>` : `<img loading="lazy" src="${attr(url)}" alt="${attr(id)}">`) : ""}</div>
      <div class="gallery-body"><strong>${escapeHtml(ownerId || id)}</strong><div class="shot-card-meta">${escapeHtml(isShotResultAsset(asset) ? "生成结果" : asset.asset_class || "项目素材")} · ${escapeHtml(asset.result_kind || (isVideo ? "视频" : "图片"))}${asset.version ? ` · ${escapeHtml(asset.version)}` : ""}</div><div class="shot-card-meta">${escapeHtml(status)}${asset.role ? ` · ${escapeHtml(asset.role)}` : ""}</div>${buttons}</div>
    </article>`;
  }

  function packagingLayerSummary(item = {}) {
    const labels = { individual_package: "独立包装", retail_box: "零售盒", inner_tray: "内托", shipping_carton: "运输箱" };
    const contracts = item.packaging_contracts && typeof item.packaging_contracts === "object" ? item.packaging_contracts : {};
    const declared = asArray(item.packaging_layers).length ? asArray(item.packaging_layers) : Object.keys(contracts);
    const active = declared.filter((layer) => contracts[layer]?.present !== false && labels[layer]);
    if (active.length) return active.map((layer) => labels[layer]).join(" / ");
    if (declared.length && declared.every((layer) => contracts[layer]?.present === false)) return "明确无包装";
    if (item.package_spec?.present === false) return "明确无包装";
    if (item.package_spec?.present === true) return "旧版包装合同";
    if (item.package_spec && typeof item.package_spec === "object" && Object.keys(item.package_spec).length) return "内置包装资料";
    if (item.dimensions_cm?.individual_pouch || item.dimensions_cm?.retail_box) return "内置分层包装尺寸";
    return "";
  }

  function formatDimensions(dimensions) {
    if (Array.isArray(dimensions) && dimensions.length >= 3) {
      const values = dimensions.slice(0, 3).map(Number);
      return values.every((value) => Number.isFinite(value) && value > 0) ? `${values[0]} × ${values[1]} × ${values[2]} cm` : "";
    }
    if (!dimensions || typeof dimensions !== "object") return "";
    const finite = (...keys) => {
      for (const key of keys) {
        const value = Number(dimensions[key]);
        if (Number.isFinite(value) && value > 0) return value;
      }
      return null;
    };
    const length = finite("length", "length_cm");
    const width = finite("width", "width_cm");
    const height = finite("height", "height_cm");
    const thickness = finite("thickness", "thickness_cm");
    if (length === null && [width, height, thickness].every((value) => value !== null)) return `${width} × ${height} × ${thickness} cm`;
    const thirdAxis = height ?? thickness;
    if ([length, width, thirdAxis].every((value) => value !== null)) return `${length} × ${width} × ${thirdAxis} cm`;
    const diameter = finite("diameter", "diameter_cm");
    if (diameter !== null) return `直径 ${diameter} cm${thirdAxis !== null ? ` × 高 ${thirdAxis} cm` : ""}`;
    return "";
  }

  function topologyText(value) {
    if (!value) return "";
    if (typeof value === "string") return value;
    if (Array.isArray(value)) return value.filter(Boolean).join(" / ");
    if (typeof value === "object") {
      return [value.shape, value.type, value.seal, value.description]
        .filter((item) => typeof item === "string" && item.trim())
        .join(" / ");
    }
    return String(value);
  }

  function packagingReferences(item, layer) {
    const indexed = asArray(item.packaging_assets?.[layer]);
    if (indexed.length) return indexed;
    return asArray(item.references).filter((reference) => reference.packaging_layer === layer || String(reference.role || "").startsWith(`${layer}_`));
  }

  function referenceStrip(references) {
    if (!references.length) return "";
    return `<div class="library-reference-strip">${references.slice(0, 8).map((reference) => {
      const url = reference.media_url || reference.url || "";
      return url ? `<button class="library-reference-thumb" type="button" data-copy-image-url="${attr(url)}" title="复制 ${attr(reference.role || reference.label || "包装参考图")}"><img loading="lazy" src="${attr(url)}" alt="${attr(reference.role || reference.label || "参考图")}"></button>` : "";
    }).join("")}</div>`;
  }

  function productKnowledgeDetails(item) {
    const layerLabels = {
      individual_package: "独立包装",
      retail_box: "零售盒",
      inner_tray: "内托",
      shipping_carton: "运输箱",
    };
    const contracts = item.packaging_contracts && typeof item.packaging_contracts === "object" ? item.packaging_contracts : {};
    const bodyDimensions = formatDimensions(item.dimensions_cm?.single_stick || item.dimensions_cm?.product_body || item.dimensions_cm);
    const rows = [`<div class="library-package-row ${bodyDimensions ? "" : "is-missing"}"><b>产品主体</b><div class="library-package-value">${escapeHtml(bodyDimensions || "尺寸未登记")}</div></div>`];
    Object.entries(layerLabels).forEach(([layer, label]) => {
      const contract = contracts[layer];
      const references = packagingReferences(item, layer);
      const legacyLevels = asArray(item.package_spec?.package_levels).map((value) => ({ individual_pouch: "individual_package", retail_outer_box: "retail_box", shipping_carton: "shipping_carton" }[value] || value));
      const legacyDimensions = layer === "individual_package"
        ? item.dimensions_cm?.individual_pouch
        : layer === "retail_box"
          ? item.dimensions_cm?.retail_box || item.package_spec?.retail_outer_box_identity?.dimensions_cm
          : null;
      const legacyDeclared = Boolean(legacyDimensions) || legacyLevels.includes(layer);
      const declared = Boolean(contract) || references.length > 0 || legacyDeclared;
      const present = declared && contract?.present !== false;
      const attributes = contract?.attributes && typeof contract.attributes === "object" ? contract.attributes : {};
      const details = [];
      if (present) {
        details.push(formatDimensions(contract?.dimensions_cm || legacyDimensions) || "尺寸未登记");
        const quantity = contract?.quantity || (layer === "retail_box" ? item.package_spec?.retail_outer_box_identity?.units_per_box : null);
        if (Number(quantity) > 0) details.push(`直接容纳 ${quantity}`);
        if (contract?.contains) details.push(`内含 ${contract.contains}`);
        const topology = topologyText(contract?.topology);
        if (topology) details.push(topology);
        if (contract?.material) details.push(contract.material);
        if (Number(attributes.arrangement_layers) > 0) details.push(`${attributes.arrangement_layers} 层 × 每层 ${attributes.direct_units_per_layer || "?"}`);
        if (attributes.hierarchy_note) details.push(attributes.hierarchy_note);
        if (contract?.text_layout?.description) details.push(`版面锁：${contract.text_layout.description}`);
        if (!contract && legacyDeclared) details.push("内置旧版合同 · 只读展示");
        details.push(`${references.length} 张分层参考`);
      }
      const status = contract?.present === false ? "明确不存在" : declared ? details.join(" · ") : "未登记";
      rows.push(`<div class="library-package-row ${present ? "" : "is-missing"}"><b>${escapeHtml(label)}</b><div class="library-package-value">${escapeHtml(status)}${referenceStrip(references)}</div></div>`);
    });
    return `<div class="library-package-panel"><strong>产品主体与包装合同</strong><div class="library-package-list">${rows.join("")}</div></div>`;
  }

  function avatarKnowledgeDetails(item) {
    const references = asArray(item.references);
    const roles = references.map((reference) => reference.role).filter(Boolean);
    const facts = [
      item.authorization_scope ? `授权范围：${item.authorization_scope}` : "授权范围未说明",
      roles.length ? `参考角度：${roles.join(" / ")}` : "参考角度未标注",
      item.notes ? `硬约束：${item.notes}` : "硬约束未登记",
    ];
    return `<div class="library-package-panel"><strong>人物身份与授权</strong><div class="library-package-list">${facts.map((fact, index) => `<div class="library-package-row ${fact.includes("未") ? "is-missing" : ""}"><b>${["授权", "身份参考", "限制"][index]}</b><div class="library-package-value">${escapeHtml(fact)}</div></div>`).join("")}</div>${referenceStrip(references)}</div>`;
  }

  function renderLibrary() {
    const root = byId("library-grid");
    const list = state.activeLibraryTab === "products" ? state.products : state.avatars;
    $$('[data-library-tab]').forEach((button) => button.classList.toggle("is-active", button.dataset.libraryTab === state.activeLibraryTab));
    if (!list.length) {
      root.innerHTML = `<div class="page-empty">还没有${state.activeLibraryTab === "products" ? "产品" : "人物"}条目。点击右上角新增。</div>`;
      return;
    }
    root.innerHTML = list.map((item) => {
      const media = item.media_url || asArray(item.references)[0]?.media_url || asArray(item.media_urls)[0] || "";
      const kind = state.activeLibraryTab === "products" ? "product" : "avatar";
      const selectable = item.selectable !== false;
      const editable = item.source === "custom";
      const packaging = kind === "product" ? packagingLayerSummary(item) : "";
      const detailId = `library-detail-${kind}-${item.id}`;
      const useDisabled = !state.project || !selectable;
      return `<article class="library-card ${selectable ? "" : "is-historical"}">
        <div class="library-card-header">
          <div class="library-card-image">${media ? `<img loading="lazy" src="${attr(media)}" alt=""><button class="library-copy" type="button" data-copy-image-url="${attr(media)}" aria-label="复制 ${attr(item.name || item.id)} 参考图">复制</button>` : ""}</div>
          <div class="library-card-title"><strong>${escapeHtml(item.name || item.id)}</strong><small>${escapeHtml(item.id)}${item.version ? ` · v${escapeHtml(item.version)}` : ""}${editable ? ` · 可编辑${item.revision ? ` · 修订 ${escapeHtml(item.revision)}` : ""}` : " · 系统内置"}</small></div>
        </div>
        <div class="library-card-meta"><span>${kind === "avatar" ? (item.authorized ? "授权已确认" : "授权待确认") : "产品主体事实源"}</span><span>${asArray(item.references).length || asArray(item.media_urls).length || 0} 张参考</span>${packaging ? `<span class="package-layer-badge">${escapeHtml(packaging)}</span>` : ""}${item.notes ? "<span>含硬约束</span>" : ""}${selectable ? "" : "<span>已被新版替代 · 仅供历史项目读取</span>"}</div>
        <div id="${attr(detailId)}">${kind === "product" ? productKnowledgeDetails(item) : avatarKnowledgeDetails(item)}</div>
        <div class="task-lane-actions"><button data-use-asset="${attr(kind)}" data-asset-id="${attr(item.id)}" ${useDisabled ? "disabled" : ""}>${!state.project ? "先选择项目" : selectable ? "用于当前项目" : "旧版不可新绑定"}</button><button type="button" data-copy-text-target="${attr(detailId)}">复制资料</button>${editable ? `<button type="button" data-edit-asset="${attr(kind)}" data-asset-id="${attr(item.id)}">编辑${kind === "product" ? "产品与包装" : "人物"}</button>` : `<button type="button" data-clone-asset="${attr(kind)}" data-asset-id="${attr(item.id)}">复制为可编辑${kind === "product" ? "产品" : "人物"}</button>`}</div>
      </article>`;
    }).join("");
  }

  function renderScript() {
    const script = state.detail?.script || state.project?.script || {};
    const source = state.activeScriptSource;
    $$('[data-script-source]').forEach((button) => button.classList.toggle("is-active", button.dataset.scriptSource === source));
    let text = "";
    if (source === "source") text = state.scriptDrafts.has("source") ? state.scriptDrafts.get("source") : script.source_text || script.transcript || "";
    if (source === "revised") text = state.scriptDrafts.has("revised") ? state.scriptDrafts.get("revised") : script.revised_text || "";
    if (source === "mapping") text = typeof script.shot_mapping === "string" ? script.shot_mapping : JSON.stringify(script.shot_mapping || {}, null, 2);
    const editor = byId("script-editor");
    if (document.activeElement !== editor) editor.value = text;
    editor.readOnly = source === "mapping";
    byId("script-char-count").textContent = `${text.replace(/\s/g, "").length} 个有效字符`;
    byId("script-language").textContent = `语种：${script.language || "待检测"}`;
    const dirty = state.scriptDirtySources.has(source);
    byId("script-lock-state").textContent = dirty && script.locked ? "内容已改 · 需重新锁定" : script.locked ? "已由你确认" : dirty ? "草稿未保存" : "未锁定";
    byId("script-lock-state").classList.toggle("ready", Boolean(script.locked && !dirty));
    byId("lock-script").disabled = source !== "revised" || !state.project || !text.trim();
    byId("save-script").disabled = source === "mapping" || !state.project;
    byId("save-script").textContent = dirty && script.locked ? "保存并解除旧锁" : "保存修改";
  }

  function selectedShot() {
    const index = state.shots.findIndex((shot, shotIndex) => shotId(shot, shotIndex) === state.selectedShotId);
    return index >= 0 ? state.shots[index] : null;
  }

  function editableValue(value) {
    if (value == null) return "";
    if (typeof value === "string") return value;
    if (Array.isArray(value)) return value.map((item) => {
      if (typeof item === "string") return item;
      return item?.description || item?.action || item?.beat || JSON.stringify(item, null, 0);
    }).join("\n");
    return JSON.stringify(value, null, 2);
  }

  function renderShotReworkControls(shot) {
    const root = byId("rework-issue-codes");
    const id = shot ? shotId(shot) : "";
    byId("rework-shot-label").textContent = id || "未选择镜头";
    byId("rework-prompt").disabled = !shot;
    if (root.dataset.loadedShot === id) return;
    root.dataset.loadedShot = id;
    $$("input[type='checkbox']", root).forEach((input) => { input.checked = false; });
    byId("rework-emotion").value = editableValue(shot?.emotion || shot?.performance?.emotion || shot?.source_performance_layers?.emotion);
    byId("rework-action-beats").value = editableValue(shot?.action_beats || shot?.beats || shot?.source_performance_layers?.action_beats);
    byId("rework-speech-transition").value = editableValue(shot?.speech_transition || shot?.source_performance_layers?.speech_transition);
    byId("rework-reason").value = "";
  }

  function renderPrompt() {
    const select = byId("prompt-shot-select");
    const value = state.selectedShotId || select.value || "";
    if (!state.shots.length) {
      select.innerHTML = "<option>暂无生成段</option>";
      byId("prompt-state").textContent = "未编译";
      byId("prompt-editor").textContent = "尚未编译。完成原片分析、锁定新版口播并确认人物/产品绑定后，Prompt 会按 SRC/ADD 最小 unit 显示。";
      renderShotReworkControls(null);
      return;
    }
    select.innerHTML = state.shots.map((shot, index) => {
      const id = shotId(shot, index);
      return `<option value="${attr(id)}">${escapeHtml(id)} · ${escapeHtml(formatTime(shotStart(shot)))}–${escapeHtml(formatTime(shotEnd(shot)))}</option>`;
    }).join("");
    if (state.shots.some((shot, index) => shotId(shot, index) === value)) select.value = value;
    const shot = selectedShot();
    const prompt = shotHasCurrentPrompt(shot) ? shot?.compiled_prompt || shot?.prompt || shot?.final_prompt || "" : "";
    $$('[data-prompt-view]').forEach((button) => button.classList.toggle("is-active", button.dataset.promptView === state.activePromptView));
    if (state.activePromptView === "evidence") {
      byId("prompt-editor").textContent = shot ? JSON.stringify({
        unit_id: shotId(shot),
        parent_shot_id: shot.parent_shot_id,
        source_timecode: shot.source_timecode || shot.timeline_timecode,
        storyboard_description: shot.storyboard_description,
        source_performance_layers: shot.source_performance_layers,
        character: shot.character,
        emotion: shot.emotion,
        action_beats: shot.action_beats,
        product_state: shot.product_state,
        delivery_asset_ids: shot.delivery_asset_ids,
      }, null, 2) : "尚未选择镜头";
    } else if (state.activePromptView === "lint") {
      byId("prompt-editor").textContent = shot ? JSON.stringify({
        unit_id: shotId(shot),
        status: shot.status,
        risk: shot.risk,
        issues: shot.issues || [],
        prohibited: shot.prohibited || [],
        asset_coverage: asArray(shot.delivery_asset_ids).length ? "linked" : "missing",
      }, null, 2) : "尚未选择镜头";
    } else {
      byId("prompt-editor").textContent = prompt || (shot?.requires_regeneration || shot?.requires_reanalysis || shot?.semantic_reset
        ? "本镜刚被拆分或上游事实已变化，旧 Prompt 已失效。需先完成本 unit 的语义回填与局部重编译，不会继承父镜套话冒充完成。"
        : "本镜尚未编译。Prompt 会从原片证据、已锁定口播、人物/产品事实源和动作节拍生成；没有证据的情绪与动作不会被六层套话补齐。");
    }
    byId("prompt-state").textContent = prompt ? "已编译" : "未编译";
    renderShotReworkControls(shot);
  }

  function preflight() {
    const blockers = [];
    const warnings = [];
    const video = getVideo();
    const config = currentConfig();
    const script = state.detail?.script || state.project?.script || {};
    const mediaExtracted = video?.analysis_status === "assets_extracted";
    const semanticIntakeReady = Boolean(String(script.source_text || script.transcript || "").trim() && state.shots.length);
    const needsIntake = !mediaExtracted || !semanticIntakeReady;
    if (!state.project) blockers.push("先创建项目并置入原视频");
    else if (!video?.video_url) blockers.push("项目还没有原视频");
    if (state.project && video?.video_url && needsIntake) {
      if (mediaExtracted && !semanticIntakeReady && !config.codex.enabled) blockers.push("素材已提取；请开启 Codex 完成口播、人物与逐镜语义分析");
      if (!mediaExtracted) warnings.push("下一步先抽帧、抽音频并建立原片证据，不要求提前锁定新版口播");
      if (mediaExtracted && config.codex.enabled) warnings.push("下一步补全原片口播、人物 owner 与 SRC/ADD 分镜");
      return { blockers, warnings, ready: blockers.length === 0, operation: "analyze", phase: "source_intake" };
    }
    const intakeOnly = config.execution_tier === "source_intake";
    if (!intakeOnly && config.product_mode === "replace" && !config.product_id) blockers.push("选择目标产品或上传参考图");
    if (!intakeOnly && config.character_mode !== "preserve" && !config.avatar_id) blockers.push("选择目标人物或上传参考图");
    const selectedAvatar = state.avatars.find((item) => item.id === config.avatar_id);
    if (!intakeOnly && config.character_mode !== "preserve" && selectedAvatar?.authorized !== true) blockers.push("人物库未记录当前素材授权；请重新新增或补齐授权记录");
    const avatarRoles = asArray(selectedAvatar?.references || selectedAvatar?.reference_assets).map((item) => item?.role).filter(Boolean);
    if (!intakeOnly && config.character_mode === "head_replace" && selectedAvatar?.usage_scope === "full_only") blockers.push("这个旧人物条目没有换头授权，请重新新增正脸 / 45° 身份参考");
    if (!intakeOnly && config.character_mode === "head_replace" && selectedAvatar?.source === "custom" && avatarRoles.length && !avatarRoles.some((role) => ["frontal", "left_45", "right_45", "profile"].includes(role))) blockers.push("换头至少需要正脸或 45° 身份参考");
    if (!intakeOnly && config.character_mode !== "preserve" && characterOwnerIds().length > 1 && !config.source_person_id) blockers.push("原片包含多人，请选择要替换的 source person owner");
    const selectedProduct = state.products.find((item) => item.id === config.product_id);
    const hasScaleLock = Boolean(selectedProduct?.dimensions_cm || selectedProduct?.physical_dimensions_cm || selectedProduct?.scale_contract || String(selectedProduct?.notes || "").includes("尺寸硬锁"));
    if (!intakeOnly && config.product_mode === "replace" && config.execution_tier === "full_delivery" && selectedProduct?.source === "custom" && !hasScaleLock) blockers.push("完整交付前必须填写产品或包装尺寸硬锁");
    if (!intakeOnly && state.scopeIntent === "selected" && !state.selectedShotIds.size) blockers.push("已选择“指定镜头”，但还没有勾选任何 SRC / ADD；不会自动改成全片");
    if (!intakeOnly && state.scopeIntent === "selected" && state.selectedShotIds.size) {
      const currentIds = new Set(state.shots.map((shot, index) => shotId(shot, index)));
      const missingIds = [...state.selectedShotIds].filter((id) => !currentIds.has(id));
      if (missingIds.length) blockers.push(`指定镜头已变化或被拆分：${missingIds.join("、")}；请重新逐镜选择，不会空跑`);
    }
    if (!intakeOnly && state.scopeIntent === "people" && state.shots.length && !state.shots.some((shot) => hasPeople(shot))) blockers.push("已选择“含人物镜头”，但当前分镜没有可确认的人物 owner；不会自动改成全片");
    if (!intakeOnly && state.scopeIntent === "range") {
      const rangeStart = Number(byId("scope-range-start").value);
      const rangeEnd = Number(byId("scope-range-end").value);
      if (!Number.isFinite(rangeStart) || !Number.isFinite(rangeEnd) || rangeEnd <= rangeStart) blockers.push("时间范围结束秒必须大于开始秒；范围无效时不会自动改成全片");
      else {
        const duration = Number(getVideoMeta().duration || 0);
        if (rangeStart < 0 || (duration > 0 && rangeEnd > duration + 0.001)) blockers.push(`时间范围必须落在原片 0–${duration.toFixed(3)} 秒内`);
        else if (state.shots.length && !state.shots.some((shot) => shotEnd(shot) > rangeStart && shotStart(shot) < rangeEnd)) blockers.push("所选时间范围没有覆盖任何当前 SRC / ADD；不会创建空任务");
      }
    }
    if (["prompt_only", "full_delivery"].includes(config.execution_tier) && !script.locked) blockers.push("先确认并锁定新版口播");
    if (config.codex.enabled && !state.server.codex_available) blockers.push("本机未找到 Codex CLI");
    if (!intakeOnly && !config.codex.enabled) blockers.push("在项目设置中明确开启 Codex CLI");
    if (intakeOnly && config.product_mode === "replace" && !config.product_id) warnings.push("产品参考可在原片提取后补充，不阻塞 intake");
    if (intakeOnly && config.character_mode !== "preserve" && !config.avatar_id) warnings.push("人物参考可在原片提取后补充，不阻塞 intake");
    if (!intakeOnly && state.project?.product_binding_status === "waiting_for_product_rebind" && !config.codex.enabled) blockers.push("选中产品尚未写入项目绑定事务");
    if (!intakeOnly && state.project?.product_binding?.status === "applying_with_codex") blockers.push("等待自定义产品绑定任务完成");
    if (!intakeOnly && state.project && !bindingState(config).ready) warnings.push("启动前会先执行人物 / 产品绑定事务");
    return { blockers, warnings, ready: blockers.length === 0, operation: intakeOnly ? "analyze" : "run", phase: intakeOnly ? "source_intake" : config.execution_tier };
  }

  function renderPreflight() {
    const result = preflight();
    const root = byId("preflight-state");
    const run = byId("run-project-button");
    if (result.ready) {
      root.innerHTML = `<span class="preflight-dot ready"></span><span><strong>可以启动</strong><small>${escapeHtml(result.warnings[0] || "执行合同已完整")}</small></span>`;
      run.disabled = false;
      byId("run-button-hint").textContent = result.operation === "analyze" ? "创建并启动原片 intake 任务" : "按当前选择创建真实任务";
    } else {
      root.innerHTML = `<span class="preflight-dot blocked"></span><span><strong>还差 ${result.blockers.length} 项</strong><small>${escapeHtml(result.blockers[0])}</small></span>`;
      run.disabled = true;
      byId("run-button-hint").textContent = result.blockers[0] || "检查项目输入";
    }
  }

  function renderDetection() {
    const detectorTask = state.tasks
      .filter((task) => task.operation === "codex" && String(task.instruction || "").includes("detectors="))
      .sort((a, b) => String(b.created_at || "").localeCompare(String(a.created_at || "")))[0];
    const detectionArtifact = state.detail?.detection_results;
    const artifactTaskId = detectionArtifact?.task_id;
    const artifactMatchesTask = Boolean(detectorTask?.id && artifactTaskId && detectorTask.id === artifactTaskId);
    const detectionHistorical = Boolean(detectionArtifact && !artifactMatchesTask);
    const detectionStale = detectionArtifact?.stale === true || detectionHistorical;
    const requested = detectorTask
      ? String(detectorTask.instruction).match(/detectors=([^;\n]+)/)?.[1]?.split(",") || []
      : state.activeDetectors;
    ["eating", "breaking", "avatar", "product", "coverage"].forEach((key) => {
      if (!requested.includes(key)) byId(`detector-${key}-state`).textContent = "待运行";
      else if (!detectorTask) byId(`detector-${key}-state`).textContent = "准备中";
      else if (detectorTask.status === "completed" && artifactMatchesTask && detectionArtifact?.stale) byId(`detector-${key}-state`).textContent = "本次结果已失效";
      else if (detectorTask.status === "completed" && artifactMatchesTask) byId(`detector-${key}-state`).textContent = "已运行 · 查证据";
      else if (detectorTask.status === "completed") byId(`detector-${key}-state`).textContent = "本次结果未落盘";
      else byId(`detector-${key}-state`).textContent = stageLabel(detectorTask.status);
    });
    const issues = asArray(detectionArtifact?.findings);
    const root = byId("detection-results");
    if (!issues.length) {
      root.innerHTML = `<div class="inspector-empty">${detectionHistorical ? "当前看到的是新的检测任务，但还没有与该任务 ID 对应的结果；不会借用上一次 findings 冒充本次通过。" : detectionStale ? "旧检测的原片、口播、分镜、人工标记或绑定哈希已经变化，结果已失效，请重新检测。" : detectorTask?.status === "completed" ? "检测任务已返回；当前没有结构化问题清单。‘已运行’不等于画面自动通过，仍以逐镜证据、视觉 QA 和你的批准为准。" : "运行检测后，问题会按镜头显示，并可一键派回对应工作线。"}</div>`;
      return;
    }
    const laneLabels = { image: "图像线", text: "文本线", controller: "总控" };
    const resultLabels = { pass: "有证据通过", issue: "发现问题", not_observable: "证据不足" };
    const staleNotice = detectionStale
      ? `<div class="inline-notice danger"><strong>${detectionHistorical ? "这份 findings 不属于当前检测任务" : "这是一份已失效的历史检测"}</strong><small>${escapeHtml(detectionHistorical ? `结果任务 ${artifactTaskId || "未知"}；当前任务 ${detectorTask?.id || "尚未创建"}` : asArray(detectionArtifact.stale_reasons).join("、") || "输入哈希已变化")}；所有旧 pass 均不参与当前放行。</small></div>`
      : "";
    root.innerHTML = staleNotice + issues.map((issue) => {
      const unitId = issue.unit_id || issue.shot_id || "全局";
      const evidence = [
        issue.evidence_time != null ? `证据时点 ${formatTime(issue.evidence_time)}` : "",
        issue.evidence_asset ? `证据 ${issue.evidence_asset}` : "",
      ].filter(Boolean).join(" · ");
      const needsRepair = !detectionStale
        && artifactMatchesTask
        && issue.result !== "pass"
        && unitId !== "全局";
      return `<div class="event-row detection-finding ${attr(issue.severity || "info")} ${detectionStale ? "stale" : ""}">
        <span>${escapeHtml(unitId)} · ${escapeHtml(issue.detector || "检测")}</span>
        <strong>${escapeHtml(issue.message || issue.reason || issue.code)}</strong>
        <small>${escapeHtml(detectionStale ? `历史${resultLabels[issue.result] || issue.result || "结果"}（已失效）` : resultLabels[issue.result] || issue.result || "待判定")} · ${escapeHtml(issue.code || "NO_CODE")} · ${escapeHtml(laneLabels[issue.owner_lane] || issue.owner_lane || "待派线")}${evidence ? ` · ${escapeHtml(evidence)}` : ""}</small>
        ${needsRepair ? `<div class="task-lane-actions"><button data-repair-unit="${attr(unitId)}" data-repair-lane="${attr(issue.owner_lane || "controller")}" data-repair-reason="${attr(`${issue.code || "DETECTION_ISSUE"}: ${issue.message || "检测未通过"}`)}">派回${escapeHtml(laneLabels[issue.owner_lane] || "对应工作线")}</button></div>` : ""}
      </div>`;
    }).join("");
  }

  function docxPageReviewKey(documentSha, page) {
    return `${documentSha || "no-document"}|${page?.page ?? "no-page"}|${page?.sha256 || "no-page-sha"}`;
  }

  function renderDocxQa(docxQa) {
    const root = byId("docx-qa-workspace");
    const pages = asArray(docxQa?.render_pages);
    const ready = docxQa?.render_status === "ready" && pages.length > 0 && docxQa?.document?.sha256;
    const passed = docxQa?.status === "passed";
    const documentSha = String(docxQa?.document?.sha256 || "");
    if (state.docxReviewedDocumentSha !== documentSha) {
      state.docxReviewedPageKeys = new Set();
      state.docxReviewedDocumentSha = documentSha;
    }
    const validKeys = new Set(pages.map((page) => docxPageReviewKey(documentSha, page)));
    state.docxReviewedPageKeys = new Set([...state.docxReviewedPageKeys].filter((key) => validKeys.has(key)));
    if (passed) pages.forEach((page) => state.docxReviewedPageKeys.add(docxPageReviewKey(documentSha, page)));
    const reviewedCount = pages.filter((page) => state.docxReviewedPageKeys.has(docxPageReviewKey(documentSha, page))).length;
    const labels = {
      passed: "当前版本已通过",
      waiting: "等待逐页检查",
      blocked: "已失效 / 被拦截",
      rejected: "当前版本已驳回",
    };
    const status = passed ? "passed" : docxQa?.status || (ready ? "waiting" : "blocked");
    const documentLink = docxQa?.document?.media_url
      ? `<a class="secondary-button small" href="${attr(docxQa.document.media_url)}" target="_blank" rel="noopener">打开当前 Word</a>`
      : "";
    root.innerHTML = `<div class="docx-qa-heading"><div><span class="eyebrow">Word 最后一门</span><h2>逐页渲染检查</h2></div><span class="status-pill ${passed ? "ready" : ""}">${escapeHtml(labels[status] || status)}</span></div>
      ${ready ? `<div class="docx-page-grid">${pages.map((page) => {
        const reviewKey = docxPageReviewKey(documentSha, page);
        const checked = state.docxReviewedPageKeys.has(reviewKey);
        return `<article class="docx-page-card"><img loading="lazy" src="${attr(page.media_url)}" alt="Word 第 ${attr(page.page)} 页渲染"><label class="docx-page-check"><input type="checkbox" data-docx-page-key="${attr(reviewKey)}" ${checked ? "checked" : ""} ${passed ? "disabled" : ""}><span>第 ${escapeHtml(page.page)} 页：无裁切、错图、漏图、错序</span></label></article>`;
      }).join("")}</div>
      <div class="docx-qa-controls"><label class="form-field"><span>驳回原因 / QA 备注</span><textarea id="docx-qa-reason" rows="2" maxlength="4000" placeholder="驳回时必填；例如：第 4 页 SRC07 图片被裁切，且 Prompt 与图错位。"></textarea></label>${documentLink}<button class="secondary-button" type="button" data-docx-qa="reject">驳回当前 Word</button><button class="primary-button" type="button" data-docx-qa="approve" ${passed || reviewedCount !== pages.length ? "disabled" : ""}>${passed ? "当前版本已通过" : `批准全部 ${pages.length} 页`}</button></div>
      <div class="dialog-notice">已检查 ${reviewedCount} / ${pages.length} 页。批准回执绑定当前 Word 与每一页渲染图 SHA-256；文件一变，回执自动失效。</div>`
      : `<div class="page-empty compact">${escapeHtml(docxQa?.message || "Word 导出并完成逐页渲染后，缩略图会出现在这里；不会把“文件存在”当成 QA 通过。")}</div>`}`;
  }

  async function recordDocxQa(decision) {
    const docxQa = state.detail?.docx_qa || state.project?.docx_qa || {};
    const pages = asArray(docxQa.render_pages);
    if (!state.project || docxQa.render_status !== "ready" || !pages.length || !docxQa.document?.sha256) {
      toast("当前 Word 还不能审核", docxQa.message || "先导出并生成全部页面渲染图。", "error");
      return;
    }
    const reason = byId("docx-qa-reason")?.value.trim() || "";
    const documentSha = String(docxQa.document.sha256 || "");
    if (decision === "approve" && pages.some((page) => !state.docxReviewedPageKeys.has(docxPageReviewKey(documentSha, page)))) {
      toast("还有页面未确认", "请逐页查看并勾选全部页面后再批准。", "error");
      return;
    }
    if (decision === "reject" && !reason) {
      toast("请写明驳回原因", "需要指出具体页码、漏图、裁切、错序或图文错位，返工任务才不会继续猜。", "error");
      return;
    }
    const context = captureProjectContext();
    $$('[data-docx-qa]').forEach((button) => { button.disabled = true; });
    try {
      await api(`/projects/${encodeURIComponent(context.projectId)}/docx-qa`, {
        method: "POST",
        body: {
          decision,
          document_sha256: docxQa.document.sha256,
          page_sha256s: pages.map((page) => page.sha256),
          reason,
        },
      });
      if (!isCurrentProjectContext(context)) return;
      await refreshProjectRuntime({ quiet: true, forceDetail: true });
      toast(decision === "approve" ? "Word 全页 QA 已通过" : "Word 已驳回", decision === "approve" ? "回执已绑定当前文档和全部页面哈希。" : "导出权限已关闭，必须修订后重新渲染检查。", decision === "approve" ? "success" : "error");
    } catch (error) {
      if (isCurrentProjectContext(context)) toast("Word QA 没有写入", error.message, "error");
      if (isCurrentProjectContext(context)) renderDeliveries();
    }
  }

  function renderDeliveries() {
    const gallery = byId("approval-gallery");
    const images = deliveryImageAssets();
    if (!images.length) gallery.innerHTML = '<div class="page-empty">还没有待批准图片。</div>';
    else gallery.innerHTML = images.map((asset, index) => renderGalleryCard(asset, index, true)).join("");

    const script = state.detail?.script || state.project?.script || {};
    const gates = asArray(state.detail?.gates || state.detail?.delivery_gates);
    const generationStatus = state.detail?.generation_status || state.project?.generation_status || {};
    const checkedIds = new Set(asArray(generationStatus.checked_unit_ids).map(String));
    const relevantShots = checkedIds.size
      ? state.shots.filter((shot, index) => checkedIds.has(shotId(shot, index)))
      : state.shots;
    const approvedUnits = relevantShots.filter((shot) => currentApprovedAssetForShot(shot));
    const generationReady = generationStatus.status
      ? generationStatus.status === "ready"
      : Boolean(relevantShots.length && approvedUnits.length === relevantShots.length);
    const docxQa = state.detail?.docx_qa || state.project?.docx_qa || {};
    renderDocxQa(docxQa);
    const computed = [
      { title: "原片与新版口播", ok: Boolean(getVideo()?.video_url && script.locked), note: script.locked ? "新版口播已确认" : "等待你确认新版口播" },
      { title: "SRC / ADD 全覆盖", ok: Boolean(relevantShots.length && relevantShots.every(shotHasDeliveryFile)), note: relevantShots.length ? `${relevantShots.filter(shotHasDeliveryFile).length} / ${relevantShots.length} 个当前作用 unit 有可读取结果；历史版本不计为当前通过` : "等待逐镜清单" },
      { title: "图片 QA 与用户回执", ok: generationReady, note: generationReady ? `${approvedUnits.length || relevantShots.length} / ${relevantShots.length} 个 unit 有当前有效批准结果` : `${approvedUnits.length} / ${relevantShots.length} 个 unit 有当前有效批准；缺 ${asArray(generationStatus.missing_unit_ids).join("、") || "待审核 unit"}` },
      { title: "Prompt 编译与图文对齐", ok: Boolean(relevantShots.length && relevantShots.every(shotHasCurrentPrompt)), note: relevantShots.some((shot) => !shotHasCurrentPrompt(shot)) ? `待重编译：${relevantShots.filter((shot) => !shotHasCurrentPrompt(shot)).map(shotId).join("、")}` : "全部当前作用 unit 已有未失效 Prompt；仍以对齐验证器为准" },
      { title: "DOCX 全页渲染 QA", ok: docxQa.status === "passed", note: docxQa.message || (docxQa.status === "passed" ? "全部页面与当前文档哈希已核验" : "先导出并逐页检查；允许导出不等于 QA 已通过") },
    ];
    const gateList = gates.length
      ? gates.map((gate) => /docx|word|全页/i.test(String(gate.title || gate.name || "")) ? computed[4] : gate)
      : computed;
    const documents = state.assets.filter((asset) => asset.kind === "document" && /\.docx$/i.test(assetPath(asset)));
    byId("delivery-gates").innerHTML = `<h2>最终放行门</h2>${gateList.map((gate, index) => {
      const ok = Boolean(gate.ok ?? gate.passed ?? gate.status === "passed");
      return `<div class="gate-item ${ok ? "ok" : "waiting"}"><span>${index + 1}</span><div><strong>${escapeHtml(gate.title || gate.name)}</strong><small>${escapeHtml(gate.note || gate.message || (ok ? "已通过" : "等待通过"))}</small></div></div>`;
    }).join("")}${documents.length ? `<div class="document-links"><strong>已生成文件</strong>${documents.map((asset) => `<a class="secondary-button full" href="${attr(assetUrl(asset))}" target="_blank" rel="noopener">${escapeHtml(asset.filename || "打开 DOCX")}${state.detail?.docx_export_authorized ? " · 已放行" : " · 未放行草稿"}</a>`).join("")}</div>` : ""}`;
    const explicitAuthorization = state.detail?.docx_export_authorized === true
      || state.project?.docx_export_authorized === true
      || state.detail?.workflow?.docx_export_authorized === true;
    const preExportReady = explicitAuthorization && computed.slice(0, 4).every((gate) => gate.ok);
    byId("export-docx").disabled = !state.project || !preExportReady;
    byId("export-docx").title = !explicitAuthorization
      ? "canonical workflow 尚未授权 Word 导出"
      : !preExportReady ? "前四项交付门尚未通过" : docxQa.status === "passed" ? "当前 Word 已通过 QA；仍可重新导出新版本" : "导出后必须完成全页视觉 QA";
  }

  function syncOperationControls(save = true) {
    state.personMode = "head";
    renderOperation();
    renderPreflight();
    if (save) scheduleConfigSave();
  }

  function scheduleConfigSave() {
    if (!state.project) return;
    if (state.savingConfig) window.clearTimeout(state.savingConfig);
    state.savingConfig = window.setTimeout(() => saveConfig({ quiet: true }), 450);
  }

  async function saveConfig({ quiet = false, includeProjectContract = false } = {}) {
    if (!state.project) return null;
    if (state.savingConfig) window.clearTimeout(state.savingConfig);
    state.savingConfig = null;
    const context = captureProjectContext();
    const config = currentConfig();
    if (!includeProjectContract) {
      delete config.execution_tier;
      delete config.prompt_length_contract;
    }
    try {
      const payload = await api(`/projects/${encodeURIComponent(context.projectId)}/config`, { method: "PUT", body: config });
      if (!isCurrentProjectContext(context)) return payload;
      state.project = payload.project || state.project;
      if (payload.project) state.detail = payload.project;
      state.project.config = payload.config || config;
      if (!quiet) toast("项目设置已保存", "替换对象、镜头范围、任务拓扑和显式字数合同已按当前项目保存。", "success");
      renderPreflight();
      renderOperation();
      return payload;
    } catch (error) {
      if (isCurrentProjectContext(context)) toast("项目设置保存失败", error.message, "error");
      throw error;
    }
  }

  async function applyBindings({ quiet = false } = {}) {
    if (!state.project) return null;
    const context = captureProjectContext();
    await saveConfig({ quiet: true });
    if (!isCurrentProjectContext(context)) return null;
    try {
      const payload = await api(`/projects/${encodeURIComponent(context.projectId)}/bindings/apply`, { method: "POST" });
      if (!isCurrentProjectContext(context)) return payload;
      if (payload.project) {
        state.project = payload.project;
        state.detail = payload.project;
        hydrateProjectState();
      }
      renderAll();
      const status = payload.binding?.status || "waiting";
      if (!quiet) {
        if (status === "ready") toast("项目绑定已应用", "旧事实源已备份，产品与人物选择已写入当前项目。", "success");
        else if (status === "applying") toast("绑定任务已启动", "自定义产品需要 Codex 生成并复核项目级产品圣经；完成前总控不会越过硬门。", "success");
        else toast("绑定仍在等待", payload.binding?.product?.code || payload.binding?.avatar?.code || "请补齐参考素材或授权。", "error");
      }
      return payload;
    } catch (error) {
      if (isCurrentProjectContext(context)) toast("应用绑定失败", error.message, "error");
      throw error;
    }
  }

  function prepareVideoFile(file) {
    if (!file) return;
    const allowed = ["video/mp4", "video/quicktime", "video/x-m4v", "video/webm"];
    if (file.type && !allowed.includes(file.type)) {
      state.selectedVideoFile = null;
      byId("create-project-submit").disabled = true;
      byId("drop-zone-title").textContent = "拖入原视频，或点击选择";
      byId("drop-zone-meta").textContent = "支持 MP4、MOV、M4V、WebM；文件保存在本地项目目录";
      toast("不支持这个视频格式", "请选择 MP4、MOV、M4V 或 WebM。", "error");
      return;
    }
    state.selectedVideoFile = file;
    byId("drop-zone-title").textContent = file.name;
    byId("drop-zone-meta").textContent = `${formatBytes(file.size)} · 将保存在本地项目目录`;
    if (!byId("new-project-name").value.trim()) byId("new-project-name").value = file.name.replace(/\.[^.]+$/, "");
    byId("create-project-submit").disabled = false;
  }

  async function createProjectWithVideo() {
    const file = state.selectedVideoFile;
    if (!file) return;
    const button = byId("create-project-submit");
    button.disabled = true;
    button.textContent = "正在创建…";
    let createdProject = null;
    try {
      const name = byId("new-project-name").value.trim() || file.name.replace(/\.[^.]+$/, "");
      const tier = byId("new-project-tier").value;
      const productMode = byId("new-project-product-mode").value === "replace_product" ? "replace" : "preserve";
      const created = await api("/projects", {
        method: "POST",
        body: { name, execution_tier: tier, product_mode: productMode },
      });
      const project = created.project;
      createdProject = project;
      showUploadProgress(0, "正在把视频写入本地项目…");
      const upload = await uploadVideo(project.id, file, (progress) => showUploadProgress(progress, `正在导入视频… ${Math.round(progress)}%`));
      showUploadProgress(100, "视频已导入，正在读取媒体信息…");
      await sleep(250);
      byId("new-project-dialog").close();
      resetNewProjectDialog();
      await refreshBootstrap({ quiet: true });
      await selectProject(project.id, { quiet: true });
      toast("视频项目已创建", `${upload.video?.filename || file.name} 已保存，媒体信息来自 FFprobe。`, "success");
    } catch (error) {
      if (createdProject?.id) {
        byId("new-project-dialog").close();
        resetNewProjectDialog();
        await refreshBootstrap({ quiet: true });
        await selectProject(createdProject.id, { quiet: true });
        toast("项目已保留，视频导入失败", `${error.message}。请在当前项目点击“重新上传原视频”，不会重复创建项目。`, "error");
      } else {
        toast("创建项目失败", error.message, "error");
      }
    } finally {
      hideUploadProgress();
      button.disabled = !state.selectedVideoFile;
      button.textContent = "创建并导入视频";
    }
  }

  async function uploadVideoToCurrent(file) {
    if (!state.project || !file) return;
    const context = captureProjectContext();
    const allowed = ["video/mp4", "video/quicktime", "video/x-m4v", "video/webm"];
    if (file.type && !allowed.includes(file.type)) {
      toast("不支持这个视频格式", "请选择 MP4、MOV、M4V 或 WebM。", "error");
      return;
    }
    try {
      showUploadProgress(0, "正在上传到当前项目…");
      await uploadVideo(context.projectId, file, (progress) => {
        if (isCurrentProjectContext(context)) showUploadProgress(progress, `正在导入视频… ${Math.round(progress)}%`);
      });
      if (!isCurrentProjectContext(context)) return;
      await refreshProjectRuntime({ quiet: true, forceDetail: true });
      if (!isCurrentProjectContext(context)) return;
      toast("原视频已写入当前项目", file.name, "success");
    } catch (error) {
      if (isCurrentProjectContext(context)) toast("视频导入失败", `${error.message}；项目和既有文件均已保留。`, "error");
    } finally {
      if (isCurrentProjectContext(context)) {
        hideUploadProgress();
        byId("current-project-video-input").value = "";
      }
    }
  }

  function uploadVideo(projectId, file, onProgress) {
    return new Promise((resolve, reject) => {
      const xhr = new XMLHttpRequest();
      xhr.open("POST", `${API_ROOT}/projects/${encodeURIComponent(projectId)}/video`);
      xhr.responseType = "json";
      xhr.upload.addEventListener("progress", (event) => {
        if (event.lengthComputable) onProgress((event.loaded / event.total) * 100);
      });
      xhr.addEventListener("load", () => {
        const payload = xhr.response;
        if (xhr.status >= 200 && xhr.status < 300 && payload?.ok !== false) resolve(payload);
        else reject(new Error(payload?.error?.message || `上传失败 (${xhr.status})`));
      });
      xhr.addEventListener("error", () => reject(new Error("视频上传连接中断")));
      const form = new FormData();
      form.append("video", file, file.name);
      xhr.send(form);
    });
  }

  function showUploadProgress(percent, text) {
    byId("upload-progress").hidden = false;
    byId("upload-progress-bar").style.width = `${clamp(percent, 0, 100)}%`;
    byId("upload-progress-text").textContent = text;
  }

  function hideUploadProgress() {
    byId("upload-progress").hidden = true;
    byId("upload-progress-bar").style.width = "0%";
  }

  function resetNewProjectDialog() {
    state.selectedVideoFile = null;
    byId("new-project-form").reset();
    byId("drop-zone-title").textContent = "拖入原视频，或点击选择";
    byId("drop-zone-meta").textContent = "支持 MP4、MOV、M4V、WebM；文件保存在本地项目目录";
    byId("create-project-submit").disabled = true;
  }

  function openFilePicker(inputId) {
    const input = byId(inputId);
    if (!input) return;
    input.value = "";
    try {
      if (typeof input.showPicker === "function") input.showPicker();
      else input.click();
    } catch (_) {
      input.click();
    }
  }

  function assetRoleOptions(kind) {
    return kind === "product"
      ? [
          ["product_whole", "产品主体 · 完整"], ["product_cross_section", "产品主体 · 横截面"], ["product_bitten_state", "产品主体 · 咬后"], ["product_broken_state", "产品主体 · 掰开"], ["product_texture", "产品主体 · 材质细节"], ["scale_reference", "产品主体 · 尺寸比例"],
          ["individual_package_front", "独立包装 · 正面"], ["individual_package_back", "独立包装 · 背面"], ["individual_package_side", "独立包装 · 侧面"],
          ["retail_box_front", "零售盒 · 正面"], ["retail_box_back", "零售盒 · 背面"], ["retail_box_side", "零售盒 · 侧面"], ["retail_box_top", "零售盒 · 顶面"],
          ["inner_tray_top", "内托 · 顶视"], ["inner_tray_side", "内托 · 侧面"],
          ["shipping_carton_front", "运输箱 · 正面"], ["shipping_carton_side", "运输箱 · 侧面"], ["shipping_carton_top", "运输箱 · 顶面"],
        ]
      : [["frontal", "正脸"], ["left_45", "左 45°"], ["right_45", "右 45°"], ["profile", "侧脸"], ["hair_master", "发型母版"]];
  }

  function setAssetField(id, value) {
    const input = byId(id);
    if (!input) return;
    input.value = value === null || value === undefined ? "" : String(value);
  }

  function dimensionValue(dimensions, axis) {
    if (!dimensions || typeof dimensions !== "object") return "";
    if (axis === "height") return dimensions.height ?? dimensions.height_cm ?? dimensions.thickness ?? dimensions.thickness_cm ?? "";
    return dimensions[axis] ?? dimensions[`${axis}_cm`] ?? "";
  }

  function normalizedThreeAxisDimensions(value, diameter = null) {
    if (Array.isArray(value)) {
      return { length: value[0] ?? "", width: value[1] ?? "", height: value[2] ?? "" };
    }
    const dimensions = value && typeof value === "object" ? value : {};
    const circle = Number(diameter ?? dimensions.diameter_cm ?? 0);
    if (circle > 0) return { length: circle, width: circle, height: circle };
    const legacyBox = dimensions.length == null && dimensions.width != null && dimensions.height != null && dimensions.thickness != null;
    if (legacyBox) {
      return { length: dimensions.width, width: dimensions.height, height: dimensions.thickness };
    }
    return {
      length: dimensionValue(dimensions, "length"),
      width: dimensionValue(dimensions, "width"),
      height: dimensionValue(dimensions, "height"),
    };
  }

  function cloneBuiltInAssetDraft(kind, item) {
    const draft = {
      ...item,
      id: "",
      source: "clone_draft",
      name: `${item.name || item.id}（自定义）`,
      references: [],
      media_urls: [],
      revision: null,
    };
    if (kind !== "product") {
      draft.usage_scope = "head_only";
      return draft;
    }

    const legacyDimensions = item.dimensions_cm && typeof item.dimensions_cm === "object" ? item.dimensions_cm : {};
    const bodySource = legacyDimensions.single_stick || legacyDimensions.product_body || legacyDimensions;
    draft.dimensions_cm = normalizedThreeAxisDimensions(bodySource, legacyDimensions.diameter_cm);
    if (item.packaging_contracts && typeof item.packaging_contracts === "object") {
      draft.packaging_contracts = JSON.parse(JSON.stringify(item.packaging_contracts));
      return draft;
    }

    const levels = new Set(asArray(item.package_spec?.package_levels));
    const individualSource = legacyDimensions.individual_pouch;
    const retailIdentity = item.package_spec?.retail_outer_box_identity || {};
    const retailSource = legacyDimensions.retail_box || retailIdentity.dimensions_cm;
    const individualPresent = Boolean(individualSource || levels.has("individual_pouch"));
    const retailPresent = Boolean(retailSource || levels.has("retail_outer_box") || levels.has("retail_box"));
    const shippingPresent = levels.has("shipping_carton");
    const retailQuantity = Number(retailIdentity.units_per_box || 0) || null;
    const retailDescription = [retailIdentity.brand, retailIdentity.visible_net_content].filter(Boolean).join("；");
    draft.packaging_contracts = {
      individual_package: individualPresent ? {
        present: true,
        dimensions_cm: normalizedThreeAxisDimensions(individualSource),
        quantity: 1,
        topology: "",
        contains: "product_body",
      } : { present: false },
      retail_box: retailPresent ? {
        present: true,
        dimensions_cm: normalizedThreeAxisDimensions(retailSource),
        quantity: retailQuantity,
        topology: { shape: "rectangular_carton" },
        contains: individualPresent ? "individual_package" : "product_body",
        text_layout: { policy: "preserve_master_projection", description: retailDescription },
        attributes: { arrangement_layers: null, direct_units_per_layer: null },
      } : { present: false },
      inner_tray: { present: false },
      shipping_carton: shippingPresent ? {
        present: true,
        dimensions_cm: { length: "", width: "", height: "" },
        quantity: null,
        topology: "",
        contains: retailPresent ? "retail_box" : individualPresent ? "individual_package" : "product_body",
      } : { present: false },
    };
    return draft;
  }

  function renderExistingAssetReferences(kind, item) {
    const root = byId("asset-existing-references");
    const references = asArray(item?.references);
    root.hidden = !references.length;
    if (!references.length) {
      root.innerHTML = "";
      return;
    }
    const roles = assetRoleOptions(kind);
    root.innerHTML = `<strong>已有 ${references.length} 张参考图 · 保存时全部保留</strong><small>可在这里修正每张图属于产品主体、哪一层包装或哪个人物角度；点击缩略图可复制。</small><div class="existing-reference-grid">${references.map((reference) => {
      const currentRole = reference.role || (reference.packaging_layer ? `${reference.packaging_layer}_front` : kind === "product" ? "product_whole" : "frontal");
      const effectiveLayer = reference.packaging_layer || ["individual_package", "retail_box", "inner_tray", "shipping_carton"].find((layer) => currentRole === layer || currentRole.startsWith(`${layer}_`)) || "";
      const url = reference.media_url || "";
      const knownRole = roles.some(([value]) => value === currentRole);
      return `<div class="existing-reference-item">${url ? `<button class="existing-reference-copy" type="button" data-copy-image-url="${attr(url)}" title="复制已有参考图"><img src="${attr(url)}" alt=""></button>` : '<span class="asset-thumb"></span>'}<span><span>${escapeHtml(reference.label || reference.original_filename || reference.id)}</span><small>${escapeHtml(effectiveLayer || reference.angle || "未分层")}</small><select data-existing-reference-id="${attr(reference.id)}" data-original-role="${attr(currentRole)}" data-existing-packaging-layer="${attr(effectiveLayer)}">${knownRole ? "" : `<option value="${attr(currentRole)}" selected>现有角色 · ${escapeHtml(currentRole)}</option>`}${roles.map(([value, label]) => `<option value="${value}" ${value === currentRole ? "selected" : ""}>${label}</option>`).join("")}</select></span></div>`;
    }).join("")}</div>`;
  }

  function openAssetDialog(kind, item = null, cloneBuiltIn = false) {
    if (item && item.source !== "custom" && !cloneBuiltIn) {
      toast("系统内置条目是只读模板", "请新建一个自有条目；工作台不会静默改写 Skill 内置事实源。", "error");
      return;
    }
    const formItem = cloneBuiltIn ? cloneBuiltInAssetDraft(kind, item) : item;
    state.selectedAssetFiles = [];
    state.editingAsset = cloneBuiltIn ? null : item || null;
    byId("asset-form").reset();
    byId("asset-kind").value = kind;
    byId("asset-edit-id").value = cloneBuiltIn ? "" : item?.id || "";
    byId("asset-dialog-eyebrow").textContent = kind === "product" ? "产品事实源" : "人物身份源";
    byId("asset-dialog-title").textContent = `${cloneBuiltIn ? "复制为可编辑" : item ? "编辑" : "新增"}${kind === "product" ? "产品与包装" : "人物"}`;
    byId("save-asset").textContent = item && !cloneBuiltIn ? "保存修改" : "保存到知识库";
    byId("asset-name").placeholder = kind === "product" ? "例如：达尔顿黄油脆丝棒" : "例如：主播 A";
    byId("asset-notes").placeholder = kind === "product" ? "写明尺寸、数量、材质、逐层包装拓扑、文字母版与禁止变化。" : "写明授权范围、脸部身份、发型，以及禁止继承参考背景、服装和身体。";
    byId("product-spec-fields").hidden = kind !== "product";
    byId("avatar-spec-fields").hidden = kind !== "avatar";
    setAssetField("asset-name", formItem?.name || "");
    setAssetField("asset-version", formItem?.version || "1");
    setAssetField("asset-notes", formItem?.notes || "");
    byId("asset-rights").checked = Boolean(formItem?.authorized);
    setAssetField("asset-usage-scope", "head_only");
    setAssetField("asset-authorization-scope", formItem?.authorization_scope || "");

    const contracts = formItem?.packaging_contracts && typeof formItem.packaging_contracts === "object" ? formItem.packaging_contracts : {};
    const layerNames = ["individual_package", "retail_box", "inner_tray", "shipping_carton"];
    const hasLayerContracts = layerNames.some((layer) => contracts[layer]);
    const noPackage = kind === "product" && (formItem?.package_spec?.present === false || (hasLayerContracts && layerNames.every((layer) => contracts[layer]?.present === false)));
    byId("asset-no-package").checked = noPackage;
    byId("package-contract-fields").hidden = noPackage;

    const defaults = formItem
      ? { individual_package: contracts.individual_package?.present === true, retail_box: contracts.retail_box?.present === true, inner_tray: contracts.inner_tray?.present === true, shipping_carton: contracts.shipping_carton?.present === true }
      : { individual_package: true, retail_box: true, inner_tray: false, shipping_carton: false };
    const presentBindings = [
      ["individual_package", "asset-individual-package-present", "individual-package-fields"],
      ["retail_box", "asset-retail-box-present", "retail-box-fields"],
      ["inner_tray", "asset-inner-tray-present", "inner-tray-fields"],
      ["shipping_carton", "asset-shipping-carton-present", "shipping-carton-fields"],
    ];
    presentBindings.forEach(([layer, checkboxId, fieldsId]) => {
      byId(checkboxId).checked = !noPackage && defaults[layer];
      byId(fieldsId).hidden = noPackage || !defaults[layer];
    });

    const dimensions = formItem?.dimensions_cm || {};
    setAssetField("asset-product-length", dimensionValue(dimensions, "length"));
    setAssetField("asset-product-width", dimensionValue(dimensions, "width"));
    setAssetField("asset-product-height", dimensionValue(dimensions, "height"));
    const layerFieldBindings = {
      individual_package: ["asset-individual-length", "asset-individual-width", "asset-individual-height"],
      retail_box: ["asset-box-length", "asset-box-width", "asset-box-height"],
      inner_tray: ["asset-inner-tray-length", "asset-inner-tray-width", "asset-inner-tray-height"],
      shipping_carton: ["asset-shipping-length", "asset-shipping-width", "asset-shipping-height"],
    };
    Object.entries(layerFieldBindings).forEach(([layer, ids]) => {
      const layerDimensions = contracts[layer]?.dimensions_cm || {};
      setAssetField(ids[0], dimensionValue(layerDimensions, "length"));
      setAssetField(ids[1], dimensionValue(layerDimensions, "width"));
      setAssetField(ids[2], dimensionValue(layerDimensions, "height"));
    });
    setAssetField("asset-units-per-individual", contracts.individual_package?.quantity || 1);
    setAssetField("asset-individual-topology", topologyText(contracts.individual_package?.topology));
    setAssetField("asset-individual-material", contracts.individual_package?.material || "");
    setAssetField("asset-units-per-box", contracts.retail_box?.quantity || "");
    const retailTopology = contracts.retail_box?.topology;
    const retailShape = typeof retailTopology === "object" ? retailTopology.shape : "";
    const allowedShapes = ["rectangular_carton", "flat_rectangular_carton", "square_carton", "cylindrical_container", "custom"];
    setAssetField("asset-box-shape", allowedShapes.includes(retailShape) ? retailShape : retailTopology ? "custom" : "rectangular_carton");
    setAssetField("asset-package-containment", contracts.retail_box?.attributes?.hierarchy_note || (typeof retailTopology === "string" ? retailTopology : ""));
    setAssetField("asset-package-layers", contracts.retail_box?.attributes?.arrangement_layers || "");
    setAssetField("asset-units-per-layer", contracts.retail_box?.attributes?.direct_units_per_layer || "");
    setAssetField("asset-package-text-layout", contracts.retail_box?.text_layout?.description || "");
    setAssetField("asset-inner-tray-count", contracts.inner_tray?.quantity || 1);
    setAssetField("asset-inner-tray-topology", topologyText(contracts.inner_tray?.topology));
    setAssetField("asset-boxes-per-shipping", contracts.shipping_carton?.quantity || "");
    setAssetField("asset-shipping-topology", topologyText(contracts.shipping_carton?.topology));

    byId("selected-asset-files").textContent = cloneBuiltIn
      ? "已带入内置尺寸与包装资料；请添加你确认的产品/包装或人物参考图后保存"
      : item ? `没有追加新图；已有 ${asArray(item.references).length} 张参考会原样保留` : "尚未选择图片";
    byId("reference-role-list").innerHTML = "";
    renderExistingAssetReferences(kind, cloneBuiltIn ? null : item);
    byId("asset-dialog").showModal();
  }

  function prepareAssetFiles(files) {
    state.selectedAssetFiles = Array.from(files || []);
    byId("selected-asset-files").textContent = state.selectedAssetFiles.length
      ? state.selectedAssetFiles.map((file) => `${file.name} · ${formatBytes(file.size)}`).join("\n")
      : "尚未选择图片";
    const kind = byId("asset-kind").value;
    const roles = assetRoleOptions(kind);
    const expectedProductRoles = [
      "product_whole",
      ...(byId("asset-individual-package-present").checked ? ["individual_package_front"] : []),
      ...(byId("asset-retail-box-present").checked ? ["retail_box_front"] : []),
      ...(byId("asset-inner-tray-present").checked ? ["inner_tray_top"] : []),
      ...(byId("asset-shipping-carton-present").checked ? ["shipping_carton_front"] : []),
      "product_cross_section", "product_bitten_state", "product_broken_state", "product_texture", "scale_reference",
    ];
    const inferredRole = (file, index) => {
      const name = String(file?.name || "").toLowerCase();
      if (kind !== "product") return roles[Math.min(index, roles.length - 1)][0];
      if (/运输|外箱|shipping|carton/.test(name)) return "shipping_carton_front";
      if (/内托|托盘|tray/.test(name)) return "inner_tray_top";
      if (/独立|单包|individual|wrapper|wrap/.test(name)) return "individual_package_front";
      if (/包装盒|零售盒|外盒|retail|box/.test(name)) return "retail_box_front";
      if (/横截|断面|cross/.test(name)) return "product_cross_section";
      if (/掰|broken|break/.test(name)) return "product_broken_state";
      if (/咬|bite|bitten/.test(name)) return "product_bitten_state";
      return expectedProductRoles[Math.min(index, expectedProductRoles.length - 1)];
    };
    byId("reference-role-list").innerHTML = state.selectedAssetFiles.map((file, index) => `
      <label class="reference-role-row"><span>${escapeHtml(file.name)}</span><select data-file-role-index="${index}">${roles.map(([value, label]) => `<option value="${value}" ${value === inferredRole(file, index) ? "selected" : ""}>${label}</option>`).join("")}</select></label>`).join("");
  }

  async function saveAsset() {
    const kind = byId("asset-kind").value;
    const endpoint = kind === "product" ? "products" : "avatars";
    const editing = state.editingAsset?.source === "custom" ? state.editingAsset : null;
    const name = byId("asset-name").value.trim();
    const notes = byId("asset-notes").value.trim();
    const authorized = byId("asset-rights").checked;
    const version = byId("asset-version").value.trim();
    const noPackage = byId("asset-no-package").checked;
    const usageScope = byId("asset-usage-scope").value;
    const authorizationScope = byId("asset-authorization-scope").value.trim();
    const roleFor = (index) => $(`[data-file-role-index='${index}']`)?.value || "reference";
    const roles = state.selectedAssetFiles.map((_, index) => roleFor(index));
    const packagingLayerForRole = (role) => ["individual_package", "retail_box", "inner_tray", "shipping_carton"]
      .find((layer) => role === layer || role.startsWith(`${layer}_`)) || null;
    const existingReferenceMetadata = $$('[data-existing-reference-id]').map((select) => ({
      id: select.dataset.existingReferenceId,
      role: select.value,
      ...(kind === "product" ? { packaging_layer: select.value === select.dataset.originalRole
        ? (select.dataset.existingPackagingLayer || null)
        : packagingLayerForRole(select.value) } : {}),
    }));
    const existingRoles = existingReferenceMetadata.map((reference) => reference.role);
    const allRoles = [...existingRoles, ...roles];
    const positiveNumber = (id) => {
      const raw = byId(id).value.trim();
      const value = Number(raw);
      return raw && Number.isFinite(value) && value > 0 ? value : null;
    };
    const positiveInteger = (id) => {
      const value = positiveNumber(id);
      return value !== null && Number.isInteger(value) ? value : null;
    };
    const productDimensions = {
      length: positiveNumber("asset-product-length"),
      width: positiveNumber("asset-product-width"),
      height: positiveNumber("asset-product-height"),
    };
    const present = {
      individual_package: !noPackage && byId("asset-individual-package-present").checked,
      retail_box: !noPackage && byId("asset-retail-box-present").checked,
      inner_tray: !noPackage && byId("asset-inner-tray-present").checked,
      shipping_carton: !noPackage && byId("asset-shipping-carton-present").checked,
    };
    const layerDimensions = {
      individual_package: { length: positiveNumber("asset-individual-length"), width: positiveNumber("asset-individual-width"), height: positiveNumber("asset-individual-height") },
      retail_box: { length: positiveNumber("asset-box-length"), width: positiveNumber("asset-box-width"), height: positiveNumber("asset-box-height") },
      inner_tray: { length: positiveNumber("asset-inner-tray-length"), width: positiveNumber("asset-inner-tray-width"), height: positiveNumber("asset-inner-tray-height") },
      shipping_carton: { length: positiveNumber("asset-shipping-length"), width: positiveNumber("asset-shipping-width"), height: positiveNumber("asset-shipping-height") },
    };
    const layerQuantity = {
      individual_package: positiveInteger("asset-units-per-individual"),
      retail_box: positiveInteger("asset-units-per-box"),
      inner_tray: positiveInteger("asset-inner-tray-count"),
      shipping_carton: positiveInteger("asset-boxes-per-shipping"),
    };
    const individualTopology = byId("asset-individual-topology").value.trim();
    const individualMaterial = byId("asset-individual-material").value.trim();
    const retailShape = byId("asset-box-shape").value;
    const packageTextLayout = byId("asset-package-text-layout").value.trim();
    const packageContainment = byId("asset-package-containment").value.trim();
    const innerTrayTopology = byId("asset-inner-tray-topology").value.trim();
    const shippingTopology = byId("asset-shipping-topology").value.trim();
    const retailLayers = positiveInteger("asset-package-layers");
    const retailPerLayer = positiveInteger("asset-units-per-layer");
    const directContainedLayer = (layer) => {
      if (layer === "individual_package") return "product_body";
      if (layer === "inner_tray") return present.individual_package ? "individual_package" : "product_body";
      if (layer === "retail_box") return present.inner_tray ? "inner_tray" : present.individual_package ? "individual_package" : "product_body";
      return present.retail_box ? "retail_box" : present.inner_tray ? "inner_tray" : present.individual_package ? "individual_package" : "product_body";
    };
    const packagingContracts = {
      individual_package: present.individual_package ? {
        present: true,
        dimensions_cm: layerDimensions.individual_package,
        quantity: layerQuantity.individual_package,
        topology: individualTopology,
        contains: directContainedLayer("individual_package"),
        ...(individualMaterial ? { material: individualMaterial } : {}),
      } : { present: false },
      retail_box: present.retail_box ? {
        present: true,
        dimensions_cm: layerDimensions.retail_box,
        quantity: layerQuantity.retail_box,
        topology: { shape: retailShape },
        contains: directContainedLayer("retail_box"),
        text_layout: { policy: "preserve_master_projection", description: packageTextLayout },
        attributes: {
          arrangement_layers: retailLayers,
          direct_units_per_layer: retailPerLayer,
          ...(packageContainment ? { hierarchy_note: packageContainment } : {}),
        },
      } : { present: false },
      inner_tray: present.inner_tray ? {
        present: true,
        dimensions_cm: layerDimensions.inner_tray,
        quantity: layerQuantity.inner_tray,
        topology: innerTrayTopology,
        contains: directContainedLayer("inner_tray"),
      } : { present: false },
      shipping_carton: present.shipping_carton ? {
        present: true,
        dimensions_cm: layerDimensions.shipping_carton,
        quantity: layerQuantity.shipping_carton,
        topology: shippingTopology,
        contains: directContainedLayer("shipping_carton"),
      } : { present: false },
    };
    if (!name || (!editing && !state.selectedAssetFiles.length)) {
      toast("参考素材不完整", editing ? "请填写知识库名称。" : "请填写名称并至少选择一张图片。", "error");
      return;
    }
    if (!version || version.length > 100 || /[\r\n]/.test(version)) {
      toast("版本格式不正确", "版本必须是 1–100 个单行字符。", "error");
      return;
    }
    if (kind === "product" && Object.values(productDimensions).some((value) => value === null)) {
      toast("产品尺寸不完整", "产品长、宽、厚/高必须分别填写正数厘米；不知道时应先测量，不能交给模型猜。", "error");
      return;
    }
    if (kind === "product" && !allRoles.includes("product_whole")) {
      toast("缺少产品主体母版", "至少把一张完整产品图标记为“产品主体 · 完整”；包装图不能冒充产品主体。", "error");
      return;
    }
    if (kind === "product" && !noPackage && !Object.values(present).some(Boolean)) {
      toast("包装层未选择", "请选择实际存在的包装层；如果确实完全无包装，请勾选“明确无包装”。", "error");
      return;
    }
    if (kind === "product") {
      for (const layer of Object.keys(present)) {
        const layerRoles = [
          ...existingReferenceMetadata.filter((reference) => reference.packaging_layer === layer),
          ...roles.filter((role) => packagingLayerForRole(role) === layer),
        ];
        if (present[layer] && Object.values(layerDimensions[layer]).some((value) => value === null)) {
          toast("包装尺寸不完整", `${layer} 已标记为存在，长、宽、高必须全部填写正数厘米。`, "error");
          return;
        }
        if (present[layer] && !layerQuantity[layer]) {
          toast("包装装量不完整", `${layer} 的“直接容纳数”必须是正整数。`, "error");
          return;
        }
        if (present[layer] && !layerRoles.length) {
          toast("包装层缺少参考图", `${layer} 已标记为存在，请把至少一张图归到这一层；不能让模型自行想象。`, "error");
          return;
        }
        if (!present[layer] && layerRoles.length) {
          toast("包装层事实冲突", `${layer} 被标记为不存在，但仍有图片归到这一层。请修改勾选或图片角色。`, "error");
          return;
        }
      }
      if (present.individual_package && !individualTopology) {
        toast("独立包装结构不完整", "请填写封口 / 结构，例如三边热封流动包装。", "error");
        return;
      }
      if (present.retail_box && (!packageTextLayout || !allRoles.includes("retail_box_front"))) {
        toast("零售盒正面母版不完整", "请填写文字 / Logo / 色块版面，并把一张图标记为“零售盒 · 正面”。", "error");
        return;
      }
      if (present.retail_box && (!retailLayers || !retailPerLayer || retailLayers * retailPerLayer !== layerQuantity.retail_box)) {
        toast("零售盒装量矛盾", "摆放层数 × 每层直接容纳数，必须等于每盒直接容纳数。", "error");
        return;
      }
      if (present.inner_tray && !innerTrayTopology) {
        toast("内托结构不完整", "请填写可观察的内托形态与槽位结构。", "error");
        return;
      }
      if (present.shipping_carton && !shippingTopology) {
        toast("运输箱结构不完整", "请填写运输箱材质、开槽和封口形态。", "error");
        return;
      }
    }
    const button = byId("save-asset");
    button.disabled = true;
    button.textContent = "正在保存…";
    const sharedId = editing?.id || `${kind}-${Date.now()}-${Math.random().toString(16).slice(2, 8)}`;
    let saved = null;
    let bindingImpact = null;
    try {
      const referenceMetadata = state.selectedAssetFiles.map((file, index) => {
        const role = roleFor(index);
        const packagingLayer = kind === "product" ? packagingLayerForRole(role) : null;
        const angle = ["front", "back", "side", "top"].find((value) => role.endsWith(`_${value}`));
        const stateByRole = { product_cross_section: "cross_section", product_bitten_state: "bitten_state", product_broken_state: "broken_state" };
        return {
          reference_id: `ref-${String(index + 1).padStart(2, "0")}-${Math.random().toString(16).slice(2, 8)}`,
          role,
          label: file.name.slice(0, 120),
          ...(["left_45", "right_45", "profile"].includes(role) ? { angle: role } : angle ? { angle } : {}),
          ...(packagingLayer ? { packaging_layer: packagingLayer } : {}),
          ...(kind === "product" && stateByRole[role] ? { product_state: stateByRole[role] } : {}),
        };
      });
      const metadataBody = {
        name,
        version,
        authorized,
        notes,
        ...(kind === "product" ? { dimensions_cm: productDimensions, packaging_contracts: packagingContracts } : {}),
        ...(kind === "avatar" ? { usage_scope: usageScope, authorization_scope: authorizationScope || null } : {}),
        ...(editing?.revision ? { expected_revision: editing.revision } : {}),
        ...(existingReferenceMetadata.length ? { reference_metadata: existingReferenceMetadata } : {}),
      };
      if (editing) {
        const updated = await api(`/knowledge/${endpoint}/${encodeURIComponent(editing.id)}`, { method: "PATCH", body: metadataBody });
        saved = updated.asset || saved;
        bindingImpact = updated.binding_impact || null;
      }
      if (state.selectedAssetFiles.length) {
        const form = new FormData();
        state.selectedAssetFiles.forEach((file) => form.append("file", file, file.name));
        form.append("id", sharedId);
        form.append("name", name);
        form.append("authorized", String(authorized));
        form.append("version", version);
        form.append("reference_metadata", JSON.stringify(referenceMetadata));
        if (kind === "product") form.append("dimensions_cm", JSON.stringify(productDimensions));
        if (kind === "product") form.append("packaging_contracts", JSON.stringify(packagingContracts));
        if (kind === "avatar") form.append("usage_scope", usageScope);
        if (kind === "avatar") form.append("authorization_scope", authorizationScope);
        form.append("notes", notes);
        try {
          const uploaded = await api(`/knowledge/${endpoint}`, { method: "POST", body: form });
          saved = uploaded.asset || saved;
        } catch (uploadError) {
          if (!editing) throw uploadError;
          await refreshBootstrap({ quiet: true });
          renderLibrary();
          byId("asset-dialog").close();
          state.editingAsset = null;
          toast("资料修改已保存，但新图没有追加", `${uploadError.message}；已有图片和刚才修改的尺寸、包装资料仍安全保留。`, "error");
          return;
        }
      }
      byId("asset-dialog").close();
      await refreshBootstrap({ quiet: true });
      if (!editing && state.project) {
        if (kind === "product") {
          byId("product-select").value = saved?.id || sharedId;
          if (!state.operation.includes("product")) state.operation = state.operation === "head_only" ? "head_product" : "product_only";
        } else {
          byId("avatar-select").value = saved?.id || sharedId;
          byId("portrait-rights").checked = authorized;
          state.operation = state.operation === "product_only" ? "head_product" : "head_only";
        }
        syncOperationControls(true);
      }
      renderOperation();
      renderKnowledgeSelectors();
      renderLibrary();
      state.editingAsset = null;
      const reapplyCount = asArray(bindingImpact?.projects_requiring_explicit_reapply).length;
      const actionLabel = editing ? "知识库修改已保存" : "知识库条目已保存";
      const referenceLabel = state.selectedAssetFiles.length ? `追加 ${state.selectedAssetFiles.length} 张参考图` : "已有参考图全部保留";
      toast(actionLabel, reapplyCount
        ? `${name} · ${referenceLabel}；${reapplyCount} 个使用该条目的项目需要明确重新应用绑定，旧快照没有被静默覆盖。`
        : `${name} · ${referenceLabel}`, "success");
    } catch (error) {
      toast("保存知识库失败", error.message, "error");
    } finally {
      button.disabled = false;
      button.textContent = editing ? "保存修改" : "保存到知识库";
    }
  }

  function openNewTaskDialog() {
    const select = byId("new-task-project");
    select.innerHTML = state.projects.length
      ? state.projects.map((project) => `<option value="${attr(project.id)}">${escapeHtml(project.name || project.id)}</option>`).join("")
      : '<option value="">请先创建项目</option>';
    if (state.project?.id) select.value = state.project.id;
    byId("new-task-operation").value = getVideo()?.video_url && state.shots.length ? "run" : "analyze";
    byId("new-task-start-mode").value = "start";
    byId("new-task-instruction").value = "";
    byId("new-task-scope-summary").textContent = state.selectedShotId
      ? `项目任务与分镜是并列可追踪对象；当前选中 ${state.selectedShotId}。单镜返工请从该分镜的“返工本镜”发起，避免伪造历史任务范围。`
      : "任务会固化所属项目；单镜返工仍从具体分镜的“返工本镜”发起，避免把项目全部分镜伪装成某个任务的子节点。";
    byId("new-task-dialog").showModal();
  }

  async function submitNewTask() {
    const projectId = byId("new-task-project").value;
    if (!projectId) {
      toast("还不能新建任务", "请先创建或选择一个视频项目。", "error");
      return;
    }
    const operation = byId("new-task-operation").value;
    const instruction = byId("new-task-instruction").value.trim();
    const autoStart = byId("new-task-start-mode").value === "start";
    const button = byId("create-task-submit");
    button.disabled = true;
    button.textContent = autoStart ? "正在创建并启动…" : "正在创建…";
    try {
      if (state.project?.id === projectId) await saveConfig({ quiet: true });
      const created = await api("/tasks", {
        method: "POST",
        body: { project_id: projectId, operation, ...(instruction ? { instruction } : {}) },
      });
      let task = created.task;
      if (autoStart && task?.id) {
        const started = await api(`/tasks/${encodeURIComponent(task.id)}/start`, { method: "POST" });
        task = started.task || task;
      }
      state.selectedTaskId = task?.id || null;
      byId("new-task-dialog").close("created");
      await refreshBootstrap({ quiet: true });
      if (state.project?.id === projectId) {
        await refreshProjectRuntime({ quiet: true, forceDetail: true });
        renderTaskTabs();
        renderTasks();
      } else {
        await selectProject(projectId, { quiet: true });
      }
      setView("editor");
      setDockTab("tasks");
      toast(autoStart ? "任务已创建并启动" : "任务已创建", `${taskName(operation)} · ${task?.id || projectId}`, "success");
    } catch (error) {
      toast("新建任务失败", error.message, "error");
    } finally {
      button.disabled = false;
      button.textContent = "创建任务";
    }
  }

  async function createTask(operation, extra = {}, autoStart = true) {
    if (!state.project) throw new Error("请先选择项目");
    const context = captureProjectContext();
    await saveConfig({ quiet: true });
    if (!isCurrentProjectContext(context)) throw new Error("项目已经切换，未创建旧项目任务");
    const payload = await api("/tasks", {
      method: "POST",
      body: { project_id: context.projectId, operation, ...extra },
    });
    let task = payload.task;
    if (autoStart && task?.id) {
      const started = await api(`/tasks/${encodeURIComponent(task.id)}/start`, { method: "POST" });
      task = started.task || task;
    }
    if (isCurrentProjectContext(context)) {
      state.selectedTaskId = task?.id || state.selectedTaskId;
      await refreshProjectRuntime({ quiet: true, forceDetail: true });
      if (isCurrentProjectContext(context)) setDockTab("tasks");
    }
    return task;
  }

  async function runProject() {
    const check = preflight();
    if (!check.ready) {
      toast("还不能启动", check.blockers[0], "error");
      return;
    }
    const tier = currentConfig().execution_tier;
    const operation = check.operation || (tier === "source_intake" ? "analyze" : "run");
    const context = captureProjectContext();
    const button = byId("run-project-button");
    button.disabled = true;
    try {
      if (operation === "run" && !bindingState().ready) {
        const binding = await applyBindings({ quiet: false });
        if (binding?.binding?.status !== "ready") {
          setDockTab("tasks");
          return;
        }
      }
      const task = await createTask(operation);
      if (isCurrentProjectContext(context)) toast("任务已启动", `${taskName(task.operation)}正在真实执行；可以在任务中心暂停或取消。`, "success");
    } catch (error) {
      if (isCurrentProjectContext(context)) toast("任务启动失败", error.message, "error");
    } finally {
      if (isCurrentProjectContext(context)) renderPreflight();
    }
  }

  async function handleTaskAction(button) {
    const taskId = button.dataset.taskId;
    const action = button.dataset.taskAction;
    button.disabled = true;
    try {
      if (action === "retry") {
        await api(`/tasks/${encodeURIComponent(taskId)}/retry`, { method: "POST" });
        const globalPayload = await api("/tasks");
        state.globalTasks = asArray(globalPayload.tasks);
        const original = state.globalTasks.find((task) => String(task.id) === String(taskId));
        if (original?.project_id === state.project?.id) await refreshProjectRuntime({ quiet: true, forceDetail: true });
        else renderTasks();
      } else {
        await api(`/tasks/${encodeURIComponent(taskId)}/${encodeURIComponent(action)}`, { method: "POST" });
        const globalPayload = await api("/tasks");
        state.globalTasks = asArray(globalPayload.tasks);
        if (state.tasks.some((task) => String(task.id) === String(taskId))) await refreshProjectRuntime({ quiet: true, forceDetail: true });
        else renderTasks();
      }
      toast("任务状态已更新", action === "pause" ? "任务已请求暂停。" : action === "cancel" ? "任务已请求取消。" : "执行层已接受操作。", "success");
    } catch (error) {
      toast("任务操作失败", error.message, "error");
    } finally {
      button.disabled = false;
    }
  }

  async function retryUnit(unitId, reason, ownerLane = "controller", overrides = {}) {
    if (!state.project || !unitId) {
      toast("没有选中镜头", "先在时间轴或镜头板选择一个 SRC/ADD。", "error");
      return;
    }
    const context = captureProjectContext();
    const selectedShotId = unitId;
    try {
      const payload = await api(`/projects/${encodeURIComponent(context.projectId)}/shots/${encodeURIComponent(selectedShotId)}/retry`, {
        method: "POST",
        body: {
          reason: reason || "用户在工作台要求重写当前镜头 Prompt",
          owner_lane: ownerLane,
          issue_codes: asArray(overrides.issue_codes),
          user_overrides: overrides.user_overrides || {},
        },
      });
      if (!isCurrentProjectContext(context)) return;
      await refreshProjectRuntime({ quiet: true, forceDetail: true });
      if (!isCurrentProjectContext(context)) return;
      toast("已创建定点返工", `${selectedShotId} 已派回${ownerLane === "image" ? "图像线" : ownerLane === "text" ? "文本线" : "总控"}，不会把整批任务清零。`, "success");
    } catch (error) {
      if (isCurrentProjectContext(context)) toast("定点返工失败", error.message, "error");
    }
  }

  async function retrySelectedShot() {
    if (!state.selectedShotId) return retryUnit(null);
    const issueCodes = $$("#rework-issue-codes input:checked").map((input) => input.value);
    const emotionOverride = byId("rework-emotion").value.trim();
    const actionBeatOverride = byId("rework-action-beats").value.split(/\n+/).map((item) => item.trim()).filter(Boolean);
    const speechOverride = byId("rework-speech-transition").value.trim();
    const userOverrides = {};
    if (emotionOverride) userOverrides.emotion = emotionOverride;
    if (actionBeatOverride.length) userOverrides.action_beats = actionBeatOverride;
    if (speechOverride) userOverrides.speech_transition = speechOverride;
    const specificReason = byId("rework-reason").value.trim();
    const reasonParts = [
      "用户在工作台对当前最小 unit 发起定点返工；不得用通用六层套话补齐未观察内容。",
      specificReason ? `具体要求：${specificReason}` : "",
    ].filter(Boolean);
    const imageCodes = new Set(["PRODUCT_SCALE_WRONG", "LIGHTING_LAZY"]);
    const textCodes = new Set(["EMOTION_FLAT", "GENERIC_SIX_LAYER"]);
    const hasImage = issueCodes.some((code) => imageCodes.has(code));
    const hasText = issueCodes.some((code) => textCodes.has(code));
    const hasController = issueCodes.some((code) => ["PACING_NOT_SPLIT", "EATING_SHOT_MISSING", "BREAKING_SHOT_MISSING"].includes(code));
    const ownerLane = hasController || (hasImage && hasText) ? "controller" : hasImage ? "image" : "text";
    return retryUnit(state.selectedShotId, reasonParts.join("\n"), ownerLane, { issue_codes: issueCodes, user_overrides: userOverrides });
  }

  async function saveScript({ lock = false, preserveLock = false } = {}) {
    if (!state.project || state.activeScriptSource === "mapping") return;
    if (lock && !preserveLock && state.activeScriptSource !== "revised") {
      toast("请先切到“新版口播”", "只允许锁定你明确编辑并确认的新版口播，原片提取稿不会被冒充新版。", "error");
      return;
    }
    const context = captureProjectContext();
    const existing = state.detail?.script || state.project?.script || {};
    const payload = {
      source_text: existing.source_text || existing.transcript || "",
      revised_text: existing.revised_text || "",
      active_source: state.activeScriptSource,
      locked: preserveLock ? Boolean(existing.locked) : lock === true,
      language: existing.language || "zh-CN",
      shot_mapping: existing.shot_mapping || {},
    };
    payload[state.activeScriptSource === "source" ? "source_text" : "revised_text"] = byId("script-editor").value;
    try {
      const response = await api(`/projects/${encodeURIComponent(context.projectId)}/script`, { method: "PUT", body: payload });
      if (!isCurrentProjectContext(context)) return;
      state.detail.script = response.script || payload;
      state.project.script = response.script || payload;
      state.scriptDrafts.set(state.activeScriptSource, byId("script-editor").value);
      state.scriptDirtySources.delete(state.activeScriptSource);
      renderScript();
      renderPreflight();
      const explicitlyLocked = lock && !preserveLock;
      toast(explicitlyLocked ? "新版口播已锁定" : "口播已保存", explicitlyLocked ? "后续 Prompt 必须逐镜引用这个版本。" : existing.locked && !preserveLock ? "新内容已写入，旧锁已解除；请重新确认新版口播。" : "内容已真实写入项目文件。", "success");
    } catch (error) {
      if (isCurrentProjectContext(context)) toast("口播保存失败", error.message, "error");
    }
  }

  function syncResultUploadKind() {
    const kind = byId("result-upload-kind").value;
    const input = byId("result-file-input");
    input.accept = kind === "video"
      ? "video/mp4,video/quicktime,video/x-m4v,video/webm"
      : "image/png,image/jpeg,image/webp";
    byId("result-file-title").textContent = state.selectedResultFile?.name
      || (kind === "video" ? "＋ 选择即梦视频结果" : "＋ 选择即梦首帧图片");
    byId("result-file-meta").textContent = state.selectedResultFile
      ? `${formatBytes(state.selectedResultFile.size)} · 将绑定到 ${state.selectedShotId || "未选择 unit"}`
      : kind === "video" ? "MP4、MOV、M4V、WebM；文件会复制到当前项目并计算哈希" : "PNG、JPG、WebP；文件会复制到当前项目并计算哈希";
    byId("save-shot-result").disabled = !state.selectedResultFile || !state.project || !state.selectedShotId;
  }

  function openResultUploadDialog() {
    if (!state.project || !state.selectedShotId) {
      toast("先选择回传镜头", "在镜头板或时间轴点击一个 SRC / ADD，再导入生成结果。", "error");
      return;
    }
    state.selectedResultFile = null;
    byId("result-upload-form").reset();
    byId("result-upload-version").value = "v1";
    byId("result-upload-unit").textContent = state.selectedShotId;
    syncResultUploadKind();
    byId("result-upload-dialog").showModal();
  }

  function prepareResultFile(file) {
    state.selectedResultFile = null;
    if (!file) {
      syncResultUploadKind();
      return;
    }
    const kind = byId("result-upload-kind").value;
    const extension = (file.name.match(/\.[^.]+$/)?.[0] || "").toLowerCase();
    const allowed = kind === "video"
      ? [".mp4", ".mov", ".m4v", ".webm"]
      : [".png", ".jpg", ".jpeg", ".webp"];
    if (!allowed.includes(extension)) {
      byId("result-file-input").value = "";
      toast("文件类型和结果类型不一致", kind === "video" ? "请选择 MP4、MOV、M4V 或 WebM。" : "请选择 PNG、JPG、JPEG 或 WebP。", "error");
      syncResultUploadKind();
      return;
    }
    state.selectedResultFile = file;
    syncResultUploadKind();
  }

  function uploadShotResult(projectId, unitId, file, fields, onProgress) {
    return new Promise((resolve, reject) => {
      const xhr = new XMLHttpRequest();
      xhr.open("POST", `${API_ROOT}/projects/${encodeURIComponent(projectId)}/shots/${encodeURIComponent(unitId)}/results`);
      xhr.responseType = "json";
      xhr.upload.addEventListener("progress", (event) => {
        if (event.lengthComputable) onProgress((event.loaded / event.total) * 100);
      });
      xhr.addEventListener("load", () => {
        const payload = xhr.response;
        if (xhr.status >= 200 && xhr.status < 300 && payload?.ok !== false) resolve(payload);
        else reject(new Error(payload?.error?.message || `结果导入失败 (${xhr.status})`));
      });
      xhr.addEventListener("error", () => reject(new Error("结果上传连接中断")));
      const form = new FormData();
      form.append("file", file, file.name);
      Object.entries(fields).forEach(([key, value]) => form.append(key, String(value ?? "")));
      xhr.send(form);
    });
  }

  async function saveShotResult() {
    const file = state.selectedResultFile;
    if (!state.project || !state.selectedShotId || !file) return;
    const context = captureProjectContext();
    const unitId = state.selectedShotId;
    const kind = byId("result-upload-kind").value;
    const version = byId("result-upload-version").value.trim() || "v1";
    const notes = byId("result-upload-notes").value.trim();
    const button = byId("save-shot-result");
    button.disabled = true;
    button.textContent = "正在导入…";
    try {
      showUploadProgress(0, `正在回收 ${unitId} 的生成结果…`);
      const payload = await uploadShotResult(context.projectId, unitId, file, { kind, version, notes }, (progress) => {
        if (!isCurrentProjectContext(context)) return;
        showUploadProgress(progress, `正在导入 ${unitId}… ${Math.round(progress)}%`);
        button.textContent = `正在导入 ${Math.round(progress)}%`;
      });
      if (!isCurrentProjectContext(context)) return;
      byId("result-upload-dialog").close();
      state.selectedResultFile = null;
      await refreshProjectRuntime({ quiet: true, forceDetail: true });
      if (!isCurrentProjectContext(context)) return;
      state.selectedAssetPath = payload.result?.path || null;
      state.editorMode = "candidate";
      $$('[data-editor-mode]').forEach((item) => item.classList.toggle("is-active", item.dataset.editorMode === "candidate"));
      setDockTab("assets");
      renderVideo();
      renderAssets();
      renderDeliveries();
      toast(payload.duplicate ? "结果已登记过" : "生成结果已回收到当前镜头", `${unitId} · ${kind === "video" ? "视频" : "首帧"} · ${version}；当前仍是待审核，不会自动批准。`, "success");
    } catch (error) {
      if (isCurrentProjectContext(context)) toast("生成结果导入失败", `${error.message}；既有项目素材没有被覆盖。`, "error");
    } finally {
      if (isCurrentProjectContext(context)) {
        hideUploadProgress();
        button.textContent = "导入并进入审核";
        button.disabled = !state.selectedResultFile;
      }
    }
  }

  async function addMarker(kind) {
    const video = byId("source-video");
    if (!state.project || !getVideo()?.video_url) return;
    const context = captureProjectContext();
    const timestamp = Number(video.currentTime.toFixed(3));
    const owner = state.shots.find((shot) => timestamp >= shotStart(shot) && timestamp <= shotEnd(shot) && (shot.unit_type !== "inserted" && shot.unit_kind !== "ADD"));
    try {
      const payload = await api(`/projects/${encodeURIComponent(context.projectId)}/markers`, {
        method: "POST",
        body: { kind, time: timestamp, shot_id: owner ? shotId(owner) : undefined },
      });
      if (!isCurrentProjectContext(context)) return;
      state.markers = asArray(payload.markers);
      if (owner) state.selectedShotId = shotId(owner);
      renderTimeline();
      renderShots();
      renderPrompt();
      const longShot = owner && shotEnd(owner) - shotStart(owner) > 5;
      toast(kind === "eating" ? "已标记吃镜头时点" : "已标记掰开镜头时点", `${formatTime(video.currentTime)}${owner ? ` · ${shotId(owner)}` : " · 待拆镜"}${longShot ? "；该镜超过 5 秒，请点击“在游标处拆镜”拆成独立动作拍" : ""}`, "success");
    } catch (error) {
      if (isCurrentProjectContext(context)) toast("时点标记失败", error.message, "error");
    }
  }

  function splitPlanForSelectedUnit() {
    return asArray(state.detail?.split_plans)
      .filter((plan) => plan?.unit_id === state.selectedShotId && plan?.status === "pending_confirmation")
      .sort((a, b) => String(b.created_at || "").localeCompare(String(a.created_at || "")))[0] || null;
  }

  function renderSplitPlanPreview(plan) {
    state.pendingSplitPlan = plan || null;
    const root = byId("split-plan-preview");
    const proposed = asArray(plan?.proposed_units);
    if (!plan || proposed.length !== 2) {
      root.innerHTML = "<strong>尚未创建计划</strong><small>先预览两段准确时码；只有点击“确认写入分镜”才会更改 canonical 分镜。</small>";
      byId("confirm-split-plan").disabled = true;
      return;
    }
    root.innerHTML = `<strong>待你确认 · canonical 尚未改变</strong><div class="split-plan-segments">${proposed.map((unit) => `<div class="split-plan-segment"><strong>${escapeHtml(unit.id)} · ${escapeHtml(unit.label)}</strong><small>${escapeHtml(formatTime(unit.timecode?.start))}–${escapeHtml(formatTime(unit.timecode?.end))}</small></div>`).join("")}</div>`;
    byId("confirm-split-plan").disabled = false;
  }

  function openSplitPlanDialog() {
    const shot = selectedShot();
    if (!state.project || !shot) {
      toast("先选择一个镜头", "请在时间轴或镜头板选择要拆分的 SRC / ADD。", "error");
      return;
    }
    const start = shotStart(shot);
    const end = shotEnd(shot);
    if (end - start <= 0.01) {
      toast("这个镜头没有可拆时长", `${shotId(shot)} 的时码不足，先修复分镜时码。`, "error");
      return;
    }
    const video = byId("source-video");
    const current = Number(video.currentTime);
    const cursor = current > start + 0.001 && current < end - 0.001 ? current : start + (end - start) / 2;
    const cursorInput = byId("split-plan-cursor");
    cursorInput.min = String(Number((start + 0.002).toFixed(3)));
    cursorInput.max = String(Number((end - 0.002).toFixed(3)));
    cursorInput.value = cursor.toFixed(3);
    byId("split-plan-unit").textContent = `${shotId(shot)} · ${formatTime(start)}–${formatTime(end)}`;
    const nearbyMarker = state.markers.find((marker) => Math.abs(Number(marker.time) - cursor) <= 0.15);
    if (nearbyMarker?.kind === "eating") {
      byId("split-plan-label-a").value = "产品送到嘴边、咬下与短暂阻力";
      byId("split-plan-label-b").value = "脆断后闭口咀嚼与可见真实反应";
    } else if (nearbyMarker?.kind === "breaking") {
      byId("split-plan-label-a").value = "双手施力、产品弯曲与断裂前阻力";
      byId("split-plan-label-b").value = "一次脆断、断面展示与人物反应";
    } else {
      byId("split-plan-label-a").value = "动作起势与停顿";
      byId("split-plan-label-b").value = "关键动作与反应";
    }
    byId("split-plan-reason").value = nearbyMarker
      ? `${nearbyMarker.kind === "eating" ? "吃" : "掰开"}动作不能和前后动作压在一个长生成单元里。`
      : "按原片节奏把长镜头拆成两个可独立审核的动作拍。";
    const existing = splitPlanForSelectedUnit();
    if (existing) {
      const proposed = asArray(existing.proposed_units);
      cursorInput.value = Number(existing.cursor_time).toFixed(3);
      byId("split-plan-label-a").value = existing.labels?.[0] || proposed[0]?.label || "动作起势与停顿";
      byId("split-plan-label-b").value = existing.labels?.[1] || proposed[1]?.label || "关键动作与反应";
      byId("split-plan-reason").value = existing.reason || "按原片节奏把长镜头拆成两个可独立审核的动作拍。";
    }
    renderSplitPlanPreview(existing);
    byId("split-plan-dialog").showModal();
  }

  async function previewSplitPlan() {
    const shot = selectedShot();
    if (!state.project || !shot) return;
    const context = captureProjectContext();
    const cursor = Number(byId("split-plan-cursor").value);
    const labels = [byId("split-plan-label-a").value.trim(), byId("split-plan-label-b").value.trim()];
    if (!Number.isFinite(cursor) || labels.some((value) => !value)) {
      toast("拆分计划不完整", "请填写拆分秒数和前后两个动作拍名称。", "error");
      return;
    }
    const button = byId("preview-split-plan");
    button.disabled = true;
    try {
      const payload = await api(`/projects/${encodeURIComponent(context.projectId)}/shots/${encodeURIComponent(shotId(shot))}/split-plan`, {
        method: "POST",
        body: { cursor_time: cursor, labels, reason: byId("split-plan-reason").value.trim() },
      });
      if (!isCurrentProjectContext(context)) return;
      renderSplitPlanPreview(payload.split_plan);
      toast("拆分预览已生成", "请核对两段时码；此时还没有改动正式分镜。", "success");
    } catch (error) {
      if (isCurrentProjectContext(context)) toast("无法创建拆分计划", error.message, "error");
    } finally {
      if (isCurrentProjectContext(context)) button.disabled = false;
    }
  }

  async function confirmSplitPlan() {
    const plan = state.pendingSplitPlan;
    if (!state.project || !plan || plan.unit_id !== state.selectedShotId) return;
    const context = captureProjectContext();
    const button = byId("confirm-split-plan");
    button.disabled = true;
    try {
      const payload = await api(`/projects/${encodeURIComponent(context.projectId)}/shots/${encodeURIComponent(plan.unit_id)}/split-plan/confirm`, {
        method: "POST",
        body: { plan_id: plan.id },
      });
      if (!isCurrentProjectContext(context)) return;
      state.pendingSplitPlan = null;
      state.selectedShotId = payload.split_plan?.proposed_units?.[0]?.id || null;
      byId("split-plan-dialog").close("confirmed");
      await refreshProjectRuntime({ quiet: true, forceDetail: true });
      toast("正式分镜已拆开", `${plan.unit_id} 已变成 ${asArray(payload.split_plan?.proposed_units).map((unit) => unit.id).join("、")}；旧图不继承，待局部重编译、出图和检测。`, "success");
    } catch (error) {
      if (isCurrentProjectContext(context)) toast("拆分没有写入", error.message, "error");
    } finally {
      if (isCurrentProjectContext(context) && byId("split-plan-dialog").open) button.disabled = !state.pendingSplitPlan;
    }
  }

  async function runDetection() {
    if (!state.project || !getVideo()?.video_url) {
      toast("还没有原视频", "先导入视频再运行检测。", "error");
      return;
    }
    const selected = $$(".detector-item input:checked").map((input) => input.value);
    if (!selected.length) {
      toast("没有选择检测项", "至少勾选一个检测项目。", "error");
      return;
    }
    if (!currentConfig().codex.enabled) {
      toast("视觉检测需要开启 Codex", "原片抽帧只能生成证据，不能证明吃、掰、人物或包装真的正确。请在项目设置中明确开启。", "error");
      byId("project-settings-dialog").showModal();
      return;
    }
    state.activeDetectors = selected;
    try {
      const instruction = `detectors=${selected.join(",")}; manual_markers=${JSON.stringify(state.markers.map((marker) => ({ kind: marker.kind, time: marker.time, shot_id: marker.shot_id })))}; 逐镜读取真实抽帧与 canonical SRC/ADD，长镜按动作拍拆分，输出可观察证据、缺失 unit、reject code；不得把 Prompt 文本当成视觉通过，不得把模型自报 approved 当用户批准。`;
      const task = await createTask("codex", { instruction });
      toast("检测任务已启动", `${selected.join("、")} · 任务 ${task.id}`, "success");
    } catch (error) {
      toast("检测启动失败", error.message, "error");
    }
  }

  async function decideApproval(button) {
    if (!state.project) return;
    const context = captureProjectContext();
    const decision = button.dataset.approval;
    if (decision === "revoke") {
      const confirmed = window.confirm("撤销会使当前文件和依赖它的旧 Prompt / DOCX 失效，普通“批准”不能恢复。确认后需要导入一个新版本再审核。是否继续？");
      if (!confirmed) return;
    }
    try {
      await api(`/projects/${encodeURIComponent(context.projectId)}/approvals`, {
        method: "POST",
        body: {
          asset_path: button.dataset.assetPath,
          asset_id: button.dataset.assetId || undefined,
          shot_id: button.dataset.shotId || undefined,
          decision,
          reason: decision === "revoke" ? "用户在工作台撤销批准" : "用户在工作台批准",
        },
      });
      if (!isCurrentProjectContext(context)) return;
      await refreshProjectRuntime({ quiet: true, forceDetail: true });
      if (!isCurrentProjectContext(context)) return;
      toast(decision === "approve" ? "图片已批准" : "图片批准已撤销", "交付门已按最新回执重新计算。", "success");
    } catch (error) {
      if (isCurrentProjectContext(context)) toast("批准操作失败", error.message, "error");
    }
  }

  function renderShotSelectionDialog() {
    const root = byId("shot-selection-grid");
    if (!state.shots.length) {
      root.innerHTML = '<div class="dialog-notice">还没有分镜。请先运行原片分析。</div>';
      return;
    }
    root.innerHTML = state.shots.map((shot, index) => {
      const id = shotId(shot, index);
      const tags = shotTags(shot);
      return `<label class="shot-selection-card"><input type="checkbox" value="${attr(id)}" ${state.selectedShotIds.has(id) ? "checked" : ""}><strong>${escapeHtml(id)}</strong><small>${escapeHtml(formatTime(shotStart(shot)))}–${escapeHtml(formatTime(shotEnd(shot)))}</small><small>${tags.has("eating") ? "吃食 " : ""}${tags.has("breaking") ? "掰开 " : ""}${hasPeople(shot) ? "人物" : ""}</small></label>`;
    }).join("");
  }

  async function exportDocx() {
    try {
      const task = await createTask("export_docx");
      toast("DOCX 导出已启动", `任务 ${task.id} 会先经过验证与全页渲染检查。`, "success");
    } catch (error) {
      toast("无法导出 DOCX", error.message, "error");
    }
  }

  function openTaskDrawer(open) {
    byId("task-drawer").classList.toggle("is-open", open);
    byId("task-drawer").setAttribute("aria-hidden", String(!open));
    byId("drawer-scrim").hidden = !open;
  }

  function bindEvents() {
    document.addEventListener("click", async (event) => {
      const closeDialog = event.target.closest("[data-close-dialog]");
      if (closeDialog) {
        const dialog = closeDialog.closest("dialog");
        if (dialog?.id === "new-project-dialog") resetNewProjectDialog();
        dialog?.close("cancel");
        return;
      }
      const docxQaAction = event.target.closest("[data-docx-qa]");
      if (docxQaAction) return recordDocxQa(docxQaAction.dataset.docxQa);
      const imageCopy = event.target.closest("[data-copy-image-url]");
      if (imageCopy) {
        event.preventDefault();
        event.stopPropagation();
        try {
          await copyImageToClipboard(imageCopy.dataset.copyImageUrl);
        } catch (error) {
          toast("复制失败", error.message, "error");
        }
        return;
      }
      const textCopy = event.target.closest("[data-copy-text-target]");
      if (textCopy) {
        event.preventDefault();
        const source = byId(textCopy.dataset.copyTextTarget);
        if (!source) return toast("复制失败", "没有找到要复制的资料区域。", "error");
        try {
          await copyText(source.innerText || source.textContent || "");
          toast("资料已复制", "产品、包装或人物资料已写入剪贴板。", "success");
        } catch (error) {
          toast("复制失败", error.message, "error");
        }
        return;
      }
      const markerPin = event.target.closest("[data-marker-time]");
      if (markerPin) {
        byId("source-video").currentTime = Number(markerPin.dataset.markerTime || 0);
        return;
      }
      const projectButton = event.target.closest("[data-project-id]");
      if (projectButton) return selectProject(projectButton.dataset.projectId);
      const taskTab = event.target.closest("[data-task-tab-id]");
      if (taskTab) {
        state.selectedTaskId = taskTab.dataset.taskTabId;
        renderTaskTabs();
        renderTasks();
        setDockTab("tasks");
        return;
      }
      const viewButton = event.target.closest("[data-view]");
      if (viewButton) return setView(viewButton.dataset.view);
      const inspectorButton = event.target.closest("[data-inspector-tab]");
      if (inspectorButton) return setInspectorTab(inspectorButton.dataset.inspectorTab);
      const dockButton = event.target.closest("[data-dock-tab]");
      if (dockButton) return setDockTab(dockButton.dataset.dockTab);
      const taskAction = event.target.closest("[data-task-action]");
      if (taskAction) return handleTaskAction(taskAction);
      const createAsset = event.target.closest("[data-create-asset]");
      if (createAsset) return openAssetDialog(createAsset.dataset.createAsset);
      const editAsset = event.target.closest("[data-edit-asset]");
      if (editAsset) {
        const library = editAsset.dataset.editAsset === "product" ? state.products : state.avatars;
        const item = library.find((record) => record.id === editAsset.dataset.assetId);
        if (!item) return toast("条目不存在", "知识库可能刚刚刷新，请重新打开。", "error");
        return openAssetDialog(editAsset.dataset.editAsset, item);
      }
      const cloneAsset = event.target.closest("[data-clone-asset]");
      if (cloneAsset) {
        const library = cloneAsset.dataset.cloneAsset === "product" ? state.products : state.avatars;
        const item = library.find((record) => record.id === cloneAsset.dataset.assetId);
        if (!item) return toast("条目不存在", "知识库可能刚刚刷新，请重新打开。", "error");
        return openAssetDialog(cloneAsset.dataset.cloneAsset, item, true);
      }
      const openLibrary = event.target.closest("[data-open-library]");
      if (openLibrary) {
        state.activeLibraryTab = openLibrary.dataset.openLibrary === "product" ? "products" : "avatars";
        setView("library");
        return;
      }
      const useAsset = event.target.closest("[data-use-asset]");
      if (useAsset) {
        if (useAsset.disabled) return;
        if (!state.project) return toast("先选择项目", "知识库条目需要绑定到一个项目。", "error");
        const library = useAsset.dataset.useAsset === "product" ? state.products : state.avatars;
        const selectedRecord = library.find((item) => item.id === useAsset.dataset.assetId);
        if (selectedRecord?.selectable === false) return toast("这个版本不能新绑定", "它已被新版替代，只保留给已有历史项目读取。", "error");
        if (useAsset.dataset.useAsset === "product") {
          byId("product-select").value = useAsset.dataset.assetId;
          const headOnly = ["head_only", "head_product"].includes(state.operation);
          state.operation = headOnly ? "head_product" : "product_only";
        } else {
          byId("avatar-select").value = useAsset.dataset.assetId;
          const avatar = state.avatars.find((item) => item.id === useAsset.dataset.assetId);
          byId("portrait-rights").checked = Boolean(avatar?.authorized);
          const withProduct = ["product_only", "head_product"].includes(state.operation);
          state.operation = withProduct ? "head_product" : "head_only";
        }
        renderOperation();
        syncOperationControls(true);
        setView("editor");
        setInspectorTab("replace");
        return toast("已用于当前项目", "请检查替换方式、授权和作用镜头后启动。", "success");
      }
      const repairUnit = event.target.closest("[data-repair-unit]");
      if (repairUnit) return retryUnit(repairUnit.dataset.repairUnit, repairUnit.dataset.repairReason, repairUnit.dataset.repairLane);
      const shotElement = event.target.closest("[data-shot-id]");
      if (shotElement && !event.target.closest("[data-approval]")) {
        state.selectedShotId = shotElement.dataset.shotId;
        const shot = state.shots.find((item, index) => shotId(item, index) === state.selectedShotId);
        if (shot && getVideo()?.video_url) byId("source-video").currentTime = shotStart(shot);
        renderTimeline();
        renderShots();
        renderShotFocus();
        renderTaskTabs();
        renderAssets();
        renderPrompt();
        return;
      }
      const approval = event.target.closest("[data-approval]");
      if (approval) return decideApproval(approval);
      const projectAsset = event.target.closest("[data-project-asset]");
      if (projectAsset) {
        state.selectedAssetPath = projectAsset.dataset.projectAsset;
        const selectedAsset = state.assets.find((asset) => assetPath(asset) === state.selectedAssetPath) || {};
        state.editorMode = "candidate";
        $$('[data-editor-mode]').forEach((button) => button.classList.toggle("is-active", button.dataset.editorMode === state.editorMode));
        setView("editor");
        renderVideo();
        return;
      }
      const editorMode = event.target.closest("[data-editor-mode]");
      if (editorMode) {
        state.editorMode = editorMode.dataset.editorMode;
        $$('[data-editor-mode]').forEach((button) => button.classList.toggle("is-active", button === editorMode));
        renderVideo();
        if (state.editorMode !== "source") setDockTab("assets");
        return;
      }
      const libraryTab = event.target.closest("[data-library-tab]");
      if (libraryTab) {
        state.activeLibraryTab = libraryTab.dataset.libraryTab;
        renderLibrary();
        return;
      }
      const scriptSource = event.target.closest("[data-script-source]");
      if (scriptSource) {
        state.activeScriptSource = scriptSource.dataset.scriptSource;
        renderScript();
        return;
      }
      const promptView = event.target.closest("[data-prompt-view]");
      if (promptView) {
        state.activePromptView = promptView.dataset.promptView;
        renderPrompt();
        return;
      }
    });

    document.addEventListener("change", (event) => {
      const checkbox = event.target.closest?.("[data-docx-page-key]");
      if (!checkbox) return;
      const reviewKey = checkbox.dataset.docxPageKey;
      if (checkbox.checked) state.docxReviewedPageKeys.add(reviewKey);
      else state.docxReviewedPageKeys.delete(reviewKey);
      const docxQa = state.detail?.docx_qa || state.project?.docx_qa || {};
      const pages = asArray(docxQa.render_pages);
      const documentSha = String(docxQa.document?.sha256 || "");
      const reviewedCount = pages.filter((page) => state.docxReviewedPageKeys.has(docxPageReviewKey(documentSha, page))).length;
      const approve = $('[data-docx-qa="approve"]');
      if (approve) approve.disabled = reviewedCount !== pages.length;
      const notice = $("#docx-qa-workspace .dialog-notice");
      if (notice) notice.textContent = `已检查 ${reviewedCount} / ${pages.length} 页。批准回执绑定当前 Word 与每一页渲染图 SHA-256；文件一变，回执自动失效。`;
    });

    $$('input[name="operation"]').forEach((input) => input.addEventListener("change", (event) => {
      state.operation = event.target.value;
      syncOperationControls(true);
    }));
    $$('input[name="scope"]').forEach((input) => input.addEventListener("change", () => {
      state.scopeIntent = input.value;
      if (state.project) localStorage.setItem(`jingliu.scope.${state.project.id}`, input.value);
      if (input.value === "selected" && !state.selectedShotIds.size) {
        renderShotSelectionDialog();
        byId("shot-selection-dialog").showModal();
      }
      renderOperation();
      renderPreflight();
      scheduleConfigSave();
    }));
    ["scope-range-start", "scope-range-end"].forEach((id) => byId(id).addEventListener("change", () => {
      renderOperation();
      renderPreflight();
      scheduleConfigSave();
    }));
    ["product-select", "avatar-select", "source-person-select", "portrait-rights", "execution-mode"].forEach((id) => byId(id).addEventListener("change", () => {
      if (id === "product-select" && byId(id).value && state.operation === "head_only") state.operation = "head_product";
      if (id === "avatar-select") {
        const avatar = state.avatars.find((item) => item.id === byId(id).value);
        byId("portrait-rights").checked = Boolean(avatar?.authorized);
        if (byId(id).value && state.operation === "product_only") state.operation = "head_product";
      }
      renderKnowledgeSelectors();
      renderOperation();
      renderPreflight();
      scheduleConfigSave();
    }));

    byId("new-project-button").addEventListener("click", () => {
      resetNewProjectDialog();
      byId("new-project-dialog").showModal();
      openFilePicker("video-file-input");
    });
    byId("new-task-button").addEventListener("click", openNewTaskDialog);
    byId("new-task-form").addEventListener("submit", (event) => {
      event.preventDefault();
      submitNewTask();
    });
    byId("choose-video-button").addEventListener("keydown", (event) => {
      if (["Enter", " "].includes(event.key)) {
        event.preventDefault();
        openFilePicker("workbench-video-input");
      }
    });
    byId("workbench-video-input").addEventListener("change", (event) => {
      const file = event.target.files[0];
      if (!file) return;
      if (state.project && !getVideo()?.video_url) {
        uploadVideoToCurrent(file);
      } else {
        resetNewProjectDialog();
        prepareVideoFile(file);
        if (state.selectedVideoFile && !byId("new-project-dialog").open) byId("new-project-dialog").showModal();
      }
      event.target.value = "";
    });
    byId("replace-video-button").addEventListener("keydown", (event) => {
      if (["Enter", " "].includes(event.key)) {
        event.preventDefault();
        openFilePicker("current-project-video-input");
      }
    });
    byId("current-project-video-input").addEventListener("change", (event) => uploadVideoToCurrent(event.target.files[0]));
    byId("open-existing-project").addEventListener("click", () => {
      if (state.projects.length) {
        toast("请从左侧选择已有项目", `当前本地执行层已发现 ${state.projects.length} 个项目。`);
      } else {
        toast("没有已登记项目", "请先置入原视频创建项目；目录导入需要项目登记清单。", "error");
      }
    });
    byId("refresh-projects").addEventListener("click", () => refreshBootstrap());
    byId("video-file-input").addEventListener("change", (event) => {
      prepareVideoFile(event.target.files[0]);
      if (state.selectedVideoFile && !byId("new-project-dialog").open) byId("new-project-dialog").showModal();
    });
    byId("new-project-form").addEventListener("submit", (event) => {
      event.preventDefault();
      createProjectWithVideo();
    });
    byId("new-project-dialog").addEventListener("close", () => {
      if (byId("new-project-dialog").returnValue === "cancel") resetNewProjectDialog();
    });

    const dropZone = byId("dialog-drop-zone");
    const mediaStage = byId("media-stage");
    [dropZone, mediaStage].forEach((zone) => {
      zone.addEventListener("dragover", (event) => {
        event.preventDefault();
        zone.classList.add("is-dragging");
      });
      zone.addEventListener("dragleave", () => zone.classList.remove("is-dragging"));
      zone.addEventListener("drop", (event) => {
        event.preventDefault();
        zone.classList.remove("is-dragging");
        const file = event.dataTransfer.files[0];
        if (!file) return;
        if (zone === mediaStage && state.project && !getVideo()?.video_url) {
          uploadVideoToCurrent(file);
          return;
        }
        prepareVideoFile(file);
        if (!byId("new-project-dialog").open) byId("new-project-dialog").showModal();
      });
    });

    byId("upload-product-reference").addEventListener("click", () => openAssetDialog("product"));
    byId("upload-avatar-reference").addEventListener("click", () => openAssetDialog("avatar"));
    byId("product-reference-input").addEventListener("change", (event) => { openAssetDialog("product"); prepareAssetFiles(event.target.files); });
    byId("avatar-reference-input").addEventListener("change", (event) => { openAssetDialog("avatar"); prepareAssetFiles(event.target.files); });
    byId("asset-file-input").addEventListener("change", (event) => prepareAssetFiles(event.target.files));
    byId("asset-no-package").addEventListener("change", (event) => {
      byId("package-contract-fields").hidden = event.target.checked;
    });
    byId("asset-individual-package-present").addEventListener("change", (event) => {
      byId("individual-package-fields").hidden = !event.target.checked;
    });
    byId("asset-retail-box-present").addEventListener("change", (event) => {
      byId("retail-box-fields").hidden = !event.target.checked;
    });
    byId("asset-inner-tray-present").addEventListener("change", (event) => {
      byId("inner-tray-fields").hidden = !event.target.checked;
    });
    byId("asset-shipping-carton-present").addEventListener("change", (event) => {
      byId("shipping-carton-fields").hidden = !event.target.checked;
    });
    byId("asset-form").addEventListener("submit", (event) => {
      event.preventDefault();
      saveAsset();
    });
    byId("open-result-upload").addEventListener("click", openResultUploadDialog);
    byId("show-more-assets").addEventListener("click", () => {
      state.assetDisplayLimit += 160;
      renderAssets();
    });
    byId("result-upload-kind").addEventListener("change", () => {
      state.selectedResultFile = null;
      byId("result-file-input").value = "";
      syncResultUploadKind();
    });
    byId("result-file-input").addEventListener("change", (event) => prepareResultFile(event.target.files[0]));
    byId("result-upload-form").addEventListener("submit", (event) => {
      event.preventDefault();
      saveShotResult();
    });

    byId("project-settings-button").addEventListener("click", () => {
      if (!state.project) return toast("先选择项目", "项目设置属于具体项目。", "error");
      const config = state.project.config || {};
      byId("settings-execution-tier").value = config.execution_tier || "source_intake";
      byId("settings-task-mode").value = config.task_mode || "dual";
      byId("settings-codex-enabled").checked = Boolean(config.codex?.enabled);
      syncPromptLengthSettings(config);
      byId("project-settings-dialog").showModal();
    });
    byId("settings-prompt-length-enabled").addEventListener("change", (event) => {
      byId("settings-prompt-length-bounds").hidden = !event.target.checked;
      if (event.target.checked) {
        if (!Number(byId("settings-prompt-length-minimum").value)) byId("settings-prompt-length-minimum").value = "3000";
        if (!Number(byId("settings-prompt-length-maximum").value)) byId("settings-prompt-length-maximum").value = "4000";
      }
    });
    byId("project-settings-form").addEventListener("submit", async (event) => {
      event.preventDefault();
      const selectedTaskMode = byId("settings-task-mode").value;
      if (byId("settings-prompt-length-enabled").checked) {
        const minimum = Number(byId("settings-prompt-length-minimum").value);
        const maximum = Number(byId("settings-prompt-length-maximum").value);
        if (!Number.isInteger(minimum) || minimum < 1 || !Number.isInteger(maximum) || maximum < minimum) {
          toast("Prompt 字数范围无效", "启用后必须同时填写正整数上下限，且最大值不能小于最小值。", "error");
          return;
        }
      }
      try {
        await saveConfig({ quiet: false, includeProjectContract: true });
        byId("execution-mode").value = selectedTaskMode === "single" ? "single" : "paired";
        byId("project-settings-dialog").close("saved");
      } catch (_) {
        // saveConfig already reports the concrete failure and leaves the draft open.
      }
    });
    byId("project-settings-dialog").addEventListener("close", () => {
      const config = state.project?.config || {};
      byId("settings-execution-tier").value = config.execution_tier || "source_intake";
      byId("settings-task-mode").value = config.task_mode || "dual";
      byId("settings-codex-enabled").checked = Boolean(config.codex?.enabled);
      syncPromptLengthSettings(config);
    });

    byId("select-shots-button").addEventListener("click", () => {
      renderShotSelectionDialog();
      byId("shot-selection-dialog").showModal();
    });
    byId("confirm-shot-selection").addEventListener("click", (event) => {
      event.preventDefault();
      state.selectedShotIds = new Set($$("#shot-selection-grid input:checked").map((input) => input.value));
      const selectedRadio = $("input[name='scope'][value='selected']");
      selectedRadio.checked = true;
      byId("shot-selection-dialog").close();
      renderOperation();
      renderPreflight();
      scheduleConfigSave();
    });

    byId("run-project-button").addEventListener("click", runProject);
    byId("apply-bindings").addEventListener("click", () => applyBindings({ quiet: false }));
    byId("run-detection").addEventListener("click", runDetection);
    byId("rework-prompt").addEventListener("click", retrySelectedShot);
    byId("save-script").addEventListener("click", () => {
      const script = state.detail?.script || state.project?.script || {};
      const preserveExistingLock = Boolean(script.locked && !state.scriptDirtySources.has(state.activeScriptSource));
      saveScript({ preserveLock: preserveExistingLock });
    });
    byId("lock-script").addEventListener("click", () => saveScript({ lock: true }));
    byId("script-editor").addEventListener("input", (event) => {
      if (["source", "revised"].includes(state.activeScriptSource)) {
        state.scriptDrafts.set(state.activeScriptSource, event.target.value);
        state.scriptDirtySources.add(state.activeScriptSource);
      }
      byId("script-char-count").textContent = `${event.target.value.replace(/\s/g, "").length} 个有效字符`;
      const script = state.detail?.script || state.project?.script || {};
      byId("script-lock-state").textContent = script.locked ? "内容已改 · 需重新锁定" : "草稿未保存";
      byId("script-lock-state").classList.remove("ready");
      byId("save-script").textContent = script.locked ? "保存并解除旧锁" : "保存修改";
    });
    byId("copy-prompt").addEventListener("click", async () => {
      try {
        await copyText(byId("prompt-editor").textContent);
        toast("Prompt 已复制", "已复制当前镜头的可见内容。", "success");
      } catch (error) {
        toast("复制失败", error.message, "error");
      }
    });
    byId("prompt-shot-select").addEventListener("change", (event) => {
      state.selectedShotId = event.target.value;
      renderTimeline();
      renderShots();
      renderShotFocus();
      renderTaskTabs();
      renderAssets();
      renderPrompt();
    });

    const player = byId("source-video");
    byId("play-toggle").addEventListener("click", () => player.paused ? player.play() : player.pause());
    byId("jump-start").addEventListener("click", () => { player.currentTime = 0; });
    byId("jump-end").addEventListener("click", () => { player.currentTime = Math.max(0, (player.duration || 0) - .05); });
    byId("video-scrubber").addEventListener("input", (event) => {
      if (Number.isFinite(player.duration)) player.currentTime = (Number(event.target.value) / 1000) * player.duration;
    });
    player.addEventListener("timeupdate", () => {
      byId("current-time").textContent = formatTime(player.currentTime);
      if (Number.isFinite(player.duration) && player.duration > 0) byId("video-scrubber").value = String(Math.round((player.currentTime / player.duration) * 1000));
    });
    player.addEventListener("loadedmetadata", () => {
      byId("duration-time").textContent = formatTime(player.duration);
    });
    player.addEventListener("play", () => { byId("play-toggle").textContent = "❚❚"; });
    player.addEventListener("pause", () => { byId("play-toggle").textContent = "▶"; });
    byId("mark-eating").addEventListener("click", () => addMarker("eating"));
    byId("mark-breaking").addEventListener("click", () => addMarker("breaking"));
    byId("open-split-plan").addEventListener("click", openSplitPlanDialog);
    byId("preview-split-plan").addEventListener("click", previewSplitPlan);
    byId("confirm-split-plan").addEventListener("click", confirmSplitPlan);
    ["split-plan-cursor", "split-plan-label-a", "split-plan-label-b", "split-plan-reason"].forEach((id) => {
      byId(id).addEventListener("input", () => {
        if (state.pendingSplitPlan) renderSplitPlanPreview(null);
      });
    });

    byId("toggle-dock").addEventListener("click", () => {
      const dock = byId("bottom-dock");
      dock.classList.toggle("is-collapsed");
      byId("view-editor").classList.toggle("dock-collapsed", dock.classList.contains("is-collapsed"));
      byId("toggle-dock").textContent = dock.classList.contains("is-collapsed") ? "展开" : "收起";
    });
    byId("open-task-center").addEventListener("click", () => openTaskDrawer(true));
    byId("close-task-drawer").addEventListener("click", () => openTaskDrawer(false));
    byId("drawer-scrim").addEventListener("click", () => openTaskDrawer(false));
    byId("tasks-start-ready").addEventListener("click", async () => {
      const queued = state.globalTasks.filter((task) => ["created", "queued", "ready"].includes(task.status));
      if (!queued.length) return toast("没有待启动任务", "需要先从工作台创建分析、总控或返工任务。");
      const task = queued.sort((a, b) => String(a.created_at || "").localeCompare(String(b.created_at || "")))[0];
      try {
        await api(`/tasks/${encodeURIComponent(task.id)}/start`, { method: "POST" });
        const globalPayload = await api("/tasks");
        state.globalTasks = asArray(globalPayload.tasks);
        if (task.project_id === state.project?.id) await refreshProjectRuntime({ quiet: true, forceDetail: true });
        else renderTasks();
        toast("任务已启动", `${state.projects.find((project) => project.id === task.project_id)?.name || task.project_id} · ${taskName(task.operation)}`, "success");
      } catch (error) {
        toast("任务未启动", error.message, "error");
      }
    });
    byId("export-docx").addEventListener("click", exportDocx);

    document.addEventListener("keydown", (event) => {
      if ((event.metaKey || event.ctrlKey) && event.key.toLowerCase() === "n") {
        event.preventDefault();
        byId("new-project-dialog").showModal();
      }
      if (event.code === "Space" && document.activeElement?.tagName !== "TEXTAREA" && document.activeElement?.tagName !== "INPUT") {
        if (!byId("source-video").hidden) {
          event.preventDefault();
          player.paused ? player.play() : player.pause();
        }
      }
    });
  }

  bindEvents();
  bootstrap();
})();
