"use strict";

// SRT 断行排版：粘贴 SRT + 每行最大显示宽度（12~60），
// 预览原文 / 排版结果 / 状态，并下载仅调整换行的 SRT。
(() => {
  const $ = (id) => document.getElementById(id);

  const els = {
    srt: $("rwSrt"),
    width: $("rwWidth"),
    runBtn: $("rwRunBtn"),
    clearBtn: $("rwClearBtn"),
    status: $("rwStatus"),
    summary: $("rwSummary"),
    resultWrap: $("rwResultWrap"),
    body: $("rwBody"),
    downloadBtn: $("rwDownloadBtn"),
  };

  const SAMPLE_SRT = [
    "1",
    "00:00:01,000 --> 00:00:04,000",
    "今天我们来介绍这个全新的断行排版功能。",
    "",
    "2",
    "00:00:05,000 --> 00:00:08,000",
    "It enumerates legal break points and balances lines.",
    "",
    "3",
    "00:00:09,000 --> 00:00:12,000",
    "短字幕无需调整，原样保留。",
    "",
    "4",
    "00:00:13,000 --> 00:00:16,000",
    "supercalifragilisticexpialidocious 是无法在窄行内容纳的超长单词",
    "",
  ].join("\n");

  $("rwSample").addEventListener("click", () => {
    els.srt.value = SAMPLE_SRT;
  });

  els.clearBtn.addEventListener("click", () => {
    els.srt.value = "";
    els.summary.classList.add("hidden");
    els.resultWrap.classList.add("hidden");
    setStatus("", false);
  });

  function setStatus(msg, isError) {
    els.status.textContent = msg;
    els.status.classList.toggle("error", !!isError);
  }

  function escapeHtml(s) {
    return String(s ?? "").replace(/[&<>"']/g, (c) => ({
      "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
    })[c]);
  }

  const STATUS_META = {
    rewrapped: { label: "已排版", badge: "badge-rw-done" },
    unchanged: { label: "无需调整", badge: "badge-rw-same" },
    manual: { label: "人工处理", badge: "badge-missing_script" },
  };

  function renderTextLines(text) {
    // 换行在预览中显式呈现（保留断行位置）
    return escapeHtml(text).replace(/\n/g, '<span class="ln-break">⏎</span><br>');
  }

  function render(data) {
    const s = data.summary;
    $("rwCntDone").textContent = s.rewrapped;
    $("rwCntSame").textContent = s.unchanged;
    $("rwCntManual").textContent = s.manual;
    $("rwCnt").textContent = s.total;

    const frag = document.createDocumentFragment();
    for (const r of data.rows) {
      const meta = STATUS_META[r.status] || STATUS_META.manual;
      const tr = document.createElement("tr");
      tr.className = `row-rw-${r.status}`;
      const widthInfo = r.status === "manual"
        ? ""
        : r.lines.length > 1
          ? `<span class="rw-w">${r.width1} / ${r.width2}</span>`
          : `<span class="rw-w">${r.width1}</span>`;
      const reason = r.status === "manual"
        ? `<div class="rw-reason">${escapeHtml(r.reason || "无合法两行排法")}</div>`
        : "";
      tr.innerHTML = `
        <td class="cell-idx">${r.index}</td>
        <td class="cell-tc">${escapeHtml(r.start_tc)} → ${escapeHtml(r.end_tc)}</td>
        <td class="cell-text cell-rw-text">${renderTextLines(r.original_text)}</td>
        <td class="cell-text cell-rw-text">${renderTextLines(r.text)}${widthInfo}${reason}</td>
        <td><span class="badge ${meta.badge}">${meta.label}</span></td>`;
      frag.appendChild(tr);
    }
    els.body.replaceChildren(frag);
    els.summary.classList.remove("hidden");
    els.resultWrap.classList.remove("hidden");
  }

  let lastRequest = null;

  async function run() {
    const width = Number.parseInt(els.width.value, 10);
    if (!Number.isInteger(width) || width < 12 || width > 60) {
      setStatus("每行最大显示宽度需为 12~60 之间的整数", true);
      return;
    }
    if (!els.srt.value.trim()) {
      setStatus("请先粘贴 SRT 字幕", true);
      return;
    }
    lastRequest = { srt: els.srt.value, max_width: width };

    els.runBtn.disabled = true;
    setStatus("断行排版中……", false);
    try {
      const resp = await fetch("/api/rewrap", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(lastRequest),
      });
      if (!resp.ok) {
        const err = await resp.json().catch(() => ({ detail: `请求失败（${resp.status}）` }));
        throw new Error(err.detail || `请求失败（${resp.status}）`);
      }
      const data = await resp.json();
      render(data);
      setStatus(
        `完成：${data.summary.total} 块，已排版 ${data.summary.rewrapped} 块` +
        (data.summary.manual ? `，${data.summary.manual} 块需人工处理` : ""),
        false
      );
    } catch (e) {
      lastRequest = null;
      els.summary.classList.add("hidden");
      els.resultWrap.classList.add("hidden");
      setStatus(e.message, true);
    } finally {
      els.runBtn.disabled = false;
    }
  }

  async function downloadSrt() {
    if (!lastRequest) {
      setStatus("请先执行断行排版", true);
      return;
    }
    els.downloadBtn.disabled = true;
    try {
      const resp = await fetch("/api/rewrap.srt", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(lastRequest),
      });
      if (!resp.ok) {
        const err = await resp.json().catch(() => ({ detail: "下载失败" }));
        throw new Error(err.detail || "下载失败");
      }
      const blob = await resp.blob();
      const url = URL.createObjectURL(blob);
      const a = document.createElement("a");
      a.href = url;
      a.download = "rewrapped.srt";
      document.body.appendChild(a);
      a.click();
      a.remove();
      URL.revokeObjectURL(url);
    } catch (e) {
      setStatus(e.message, true);
    } finally {
      els.downloadBtn.disabled = false;
    }
  }

  els.runBtn.addEventListener("click", run);
  els.downloadBtn.addEventListener("click", downloadSrt);
})();
