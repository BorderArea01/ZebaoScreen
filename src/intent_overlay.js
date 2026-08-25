(() => {
  const intentLabels = {
    0: "忽略",
    1: "对话",
    2: "视觉",
    3: "身份",
    4: "任务",
    5: "本地动作",
    6: "控制",
    7: "转人工",
  };
  const controlLabels = { 601: "暂停", 602: "清空上下文" };
  const style = document.createElement("style");
  style.textContent = [
    "#intentOverlay{position:fixed;left:8px;bottom:10px;z-index:26;display:none;max-width:430px;padding:6px 8px;border:1px solid rgba(108,224,255,.46);border-radius:4px;background:rgba(0,10,18,.78);color:rgba(214,246,255,.92);font:11px/1.4 'WenQuanYi Micro Hei','Microsoft YaHei',sans-serif;letter-spacing:.2px;pointer-events:none;transition:bottom .18s ease}",
    "#intentOverlay.on{display:block}",
    "#intentOverlay.raised{bottom:204px}",
    "#intentOverlay .intentSub{margin-top:2px;color:rgba(171,216,232,.7);font-size:9px}",
    "#intentOverlay .intentFinal{margin-top:2px;color:#d7ff70;font-weight:700}",
    "#intentOverlay .intentStream{color:#d6f6ff}",
  ].join("");
  document.head.appendChild(style);

  const overlay = document.createElement("div");
  overlay.id = "intentOverlay";
  overlay.setAttribute("aria-label", "当前意图");
  document.body.appendChild(overlay);

  function render(data) {
    const asr = data && data.diag && data.diag.asr;
    const intent = data && data.diag && data.diag.intent;
    if ((!asr || typeof asr !== "object") && (!intent || typeof intent !== "object")) {
      overlay.className = "";
      overlay.textContent = "";
      return;
    }
    const rawCode = asr && asr.code != null ? asr.code : intent && intent.code;
    const code = Number(rawCode);
    const hasCode = rawCode !== null && typeof rawCode !== "undefined" && rawCode !== "" && Number.isFinite(code);
    const actionCode = Number(intent && intent.action_code || 0);
    const controlCode = Number(intent && intent.control_code || 0);
    let detail = "";
    if (actionCode > 0) detail = `${actionCode}：${intent && intent.action_label || "本地动作"}`;
    if (controlCode > 0) detail = `${controlCode}：${controlLabels[controlCode] || "控制"}`;
    overlay.replaceChildren();
    const streamingText = String(asr && asr.streaming_text || "");
    const rawFinalText = String(asr && asr.asr_final_text || "");
    const rawFinalReceived = Boolean(
      asr && (asr.asr_final_received || rawFinalText)
    );
    const streamRow = document.createElement("div");
    streamRow.className = "intentStream";
    streamRow.textContent = `ASR streaming：${streamingText || "—"}`;
    overlay.appendChild(streamRow);
    const finalRow = document.createElement("div");
    finalRow.className = "intentFinal";
    finalRow.textContent = `ASR final：${
      rawFinalReceived ? rawFinalText || "空" : "—"
    }`;
    overlay.appendChild(finalRow);
    const reasonLabels = {
      startup: "启动中",
      wake_name_not_detected: "未叫泽宝，已拦截",
      wake_name_detected_waiting_physical: "检测到唤醒词，等待人声证据",
      wake_without_physical_evidence: "疑似唤醒词但无物理人声，已拦截",
      wake_physical_confirmed: "唤醒词与物理人声已确认，正在收音",
      turn_in_progress: "正在收音",
      passed_to_intent: "ASR final 已传入意图层",
      not_addressed_to_robot: "未明确叫机器人，已拦截",
      no_speech_evidence: "无可靠物理人声，已拦截",
      ambient_hallucination: "弱环境音幻听，已拦截",
      ambient_noise_hallucination: "环境音幻听，已拦截",
      empty_asr: "没有有效 final",
      wake_name_only: "仅唤醒，继续听",
      addressed_to_robot: "已叫泽宝，放行",
      same_source_continuation: "续聊放行",
    };
    const gate = document.createElement("div");
    gate.className = "intentSub";
    const reason = String(asr && asr.reason || "");
    const gateState = asr && (asr.status === "confirmed" || asr.status === "listening")
      ? "已开门收音"
      : asr && asr.passed_to_execution
        ? "放行"
        : "拦截/等待";
    gate.textContent = `门控：${gateState}${
      hasCode ? ` · ${code}：${intentLabels[code] || "未知"}` : ""
    } · ${reasonLabels[reason] || reason || "等待识别"}`;
    overlay.appendChild(gate);
    if (detail) {
      const sub = document.createElement("div");
      sub.className = "intentSub";
      sub.textContent = detail;
      overlay.appendChild(sub);
    }
    const pixelText = document.getElementById("pixelText");
    const raised = pixelText && pixelText.className.indexOf("on") >= 0;
    overlay.className = raised ? "on raised" : "on";
  }

  async function refresh() {
    try {
      const response = await fetch("/api/mood", { cache: "no-store" });
      if (response.ok) render(await response.json());
    } catch (_) {
      // The main eyes page stays responsive if local diagnostics are unavailable.
    }
  }

  setInterval(refresh, 800);
  refresh();
})();
