(() => {
  "use strict";
  const BUILD = "6";
  try {
    if (document.body.dataset.build !== BUILD) {
      const u = new URL(location.href);
      u.searchParams.set("v", BUILD);
      location.replace(u.toString());
    }
  } catch (e) { /* non-browser */ }
  const dummy = {
    onclick: null, onchange: null, oninput: null, value: "", textContent: "", innerHTML: "",
    dataset: {}, style: {}, classList: { add() {}, remove() {}, toggle() {}, contains() { return false; } },
    appendChild() {}, addEventListener() {}, querySelector() { return null; },
  };
  const $ = (s) => document.querySelector(s) || dummy;
  window.addEventListener("error", (e) => {
    const s = document.getElementById("status");
    if (s) s.textContent = "脚本错误：" + (e.message || e.error);
    console.error("[wb]", e.message || e.error);
  });
  const state = {
    tab: "projects", projects: [], project: null, docs: [], doc: null,
    sessions: [], session: null, personas: [], multi: false,
    citeIndex: {}, lastAnswer: null, selection: null, busy: false, devOpen: false,
  };
  const esc = (s) => (s || "").replace(/[&<>"']/g, (c) => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  const setStatus = (t) => { $("#status").textContent = t || ""; };

  function setSelect(el, value) {
    if (!el || !el.options) return;
    for (const opt of el.options) {
      if (opt.value === value) { opt.selected = true; return; }
    }
  }

  let askResolver = null;
  function askText(title, value = "", desc = "") {
    $("#ask-title").textContent = title;
    $("#ask-desc").textContent = desc || "";
    const input = $("#ask-input");
    input.classList.remove("hidden");
    input.value = value;
    $("#ask-modal").classList.remove("hidden");
    setTimeout(() => { try { input.focus(); input.select(); } catch (e) { /* headless */ } }, 0);
    return new Promise((resolve) => { askResolver = { resolve, withInput: true }; });
  }
  function askConfirm(title, desc = "") {
    $("#ask-title").textContent = title;
    $("#ask-desc").textContent = desc || "";
    $("#ask-input").classList.add("hidden");
    $("#ask-input").value = "";
    $("#ask-modal").classList.remove("hidden");
    return new Promise((resolve) => { askResolver = { resolve, withInput: false }; });
  }
  function closeAsk(ok) {
    if (!askResolver) return;
    const { resolve, withInput } = askResolver;
    askResolver = null;
    $("#ask-modal").classList.add("hidden");
    resolve(ok ? (withInput ? $("#ask-input").value.trim() : true) : null);
  }

  async function api(path, opts = {}) {
    const init = { method: opts.method || "GET", headers: {} };
    if (opts.json !== undefined) {
      init.body = JSON.stringify(opts.json);
      init.headers["Content-Type"] = "application/json";
    } else if (opts.raw !== undefined) {
      init.body = opts.raw;
    }
    const res = await fetch(path, init);
    const text = await res.text();
    let data;
    try { data = text ? JSON.parse(text) : {}; } catch { data = { error: text }; }
    if (!res.ok) throw new Error(data.error || res.statusText);
    return data;
  }

  // ---------- tabs ----------
  function switchTab(tab) {
    state.tab = tab;
    document.querySelectorAll(".tab").forEach((b) => b.classList.toggle("active", b.dataset.tab === tab));
    $("#panel-projects").classList.toggle("hidden", tab !== "projects");
    $("#panel-docs").classList.toggle("hidden", tab !== "docs");
    $("#panel-sessions").classList.toggle("hidden", tab !== "sessions");
  }

  // ---------- projects ----------
  async function loadProjects() {
    state.projects = await api("/api/projects");
    renderProjects();
  }
  function renderProjects() {
    const box = $("#project-list");
    box.innerHTML = "";
    for (const p of state.projects) {
      const el = document.createElement("div");
      el.className = `item ${state.project === p.id ? "active" : ""}`;
      el.innerHTML = `<div>${esc(p.name)}</div><small>${p.doc_count} 份素材 · ${p.session_count} 个会话</small>`;
      el.onclick = () => selectProject(p.id);
      box.appendChild(el);
    }
    if (!state.projects.length) box.innerHTML = `<div class="hint">还没有项目。新建一个，把书、论文、评论放进去。</div>`;
  }
  async function selectProject(id) {
    state.project = id;
    const p = await api(`/api/projects/${id}`);
    $("#project-detail").classList.remove("hidden");
    $("#project-name").textContent = p.name;
    $("#project-desc").textContent = p.description || "";
    $("#project-doc-count").textContent = `${p.documents.length} 份`;
    const docsBox = $("#project-docs");
    docsBox.innerHTML = "";
    for (const d of p.documents) {
      const el = document.createElement("div");
      el.className = "item";
      el.innerHTML = `<div>${esc(d.title)}<span class="badge">${esc(d.project_role || "source")}</span></div>
        <small>${esc(d.author || "-")}</small>
        <div class="acts"><button data-del="${d.id}">移除</button></div>`;
      el.querySelector("[data-del]").onclick = async (e) => {
        e.stopPropagation();
        await api(`/api/projects/${id}/documents/${d.id}`, { method: "DELETE" });
        await selectProject(id); await loadSessions();
      };
      el.onclick = () => openDoc(d.id);
      docsBox.appendChild(el);
    }
    const linksBox = $("#project-links");
    linksBox.innerHTML = "";
    for (const l of p.links) {
      const el = document.createElement("div");
      el.className = "item";
      el.innerHTML = `<div>${esc(l.src_title || l.src_doc)} → ${esc(l.dst_title || l.dst_doc)}</div>
        <small>${esc(l.kind)} ${esc(l.note || "")}</small>
        <div class="acts"><button data-del="${l.id}">删除</button></div>`;
      el.querySelector("[data-del]").onclick = async (e) => {
        e.stopPropagation();
        await api(`/api/projects/${id}/links/${l.id}`, { method: "DELETE" });
        await selectProject(id);
      };
      linksBox.appendChild(el);
    }
    fillSelect($("#add-doc-select"), state.docs.filter((d) => !p.documents.some((x) => x.id === d.id))
      .map((d) => ({ value: d.id, label: d.title })), "选择要加入的素材");
    fillSelect($("#link-src"), p.documents.map((d) => ({ value: d.id, label: d.title })));
    fillSelect($("#link-dst"), p.documents.map((d) => ({ value: d.id, label: d.title })));
    await loadProjects();
    await loadSessions();
  }
  function fillSelect(el, items, placeholder) {
    el.innerHTML = "";
    if (placeholder) el.innerHTML = `<option value="">${esc(placeholder)}</option>`;
    for (const it of items) {
      const o = document.createElement("option");
      o.value = it.value; o.textContent = it.label; el.appendChild(o);
    }
  }
  async function openProjectSession() {
    if (!state.project) return;
    const p = await api(`/api/projects/${state.project}`);
    if (!p.documents.length) return setStatus("项目还没有素材");
    if (state.doc !== p.documents[0].id) await openDoc(p.documents[0].id, { keepSession: true });
    const payload = { project_id: state.project, persona_id: $("#persona").value,
      mode: $("#strictness").value, persona_mode: $("#mode").value };
    if (p.documents.some((d) => d.id === state.doc)) payload.doc_id = state.doc;
    const sid = await api("/api/sessions", { method: "POST", json: payload });
    await openSession(sid.session_id);
    setStatus("已进入项目对话");
  }

  // ---------- documents ----------
  async function loadDocs(q = "") {
    state.docs = await api(`/api/documents${q ? "?q=" + encodeURIComponent(q) : ""}`);
    renderDocs();
    return state.docs;
  }
  function renderDocs() {
    const box = $("#doc-list");
    box.innerHTML = "";
    for (const d of state.docs) {
      const el = document.createElement("div");
      el.className = `item ${state.doc === d.id ? "active" : ""}`;
      el.innerHTML = `<div>${esc(d.title)}</div><small>${esc(d.author || "-")} · ${d.block_count || 0} 块</small>
        <div class="acts"><button data-ren="1">改名</button><button data-tag="1">标签</button><button data-del="1">删除</button></div>`;
      el.querySelector("[data-ren]").onclick = async (e) => {
        e.stopPropagation();
        const t = await askText("重命名素材", d.title); if (!t) return;
        await api(`/api/documents/${d.id}`, { method: "PATCH", json: { title: t } });
        await loadDocs($("#doc-search").value.trim()); if (state.doc === d.id) openDoc(d.id);
      };
      el.querySelector("[data-tag]").onclick = async (e) => {
        e.stopPropagation();
        const t = await askText("标签（逗号分隔）", (d.tags || []).join(",")); if (t === null) return;
        await api(`/api/documents/${d.id}`, { method: "PATCH", json: { tags: t.split(",").map((x) => x.trim()).filter(Boolean) } });
        await loadDocs($("#doc-search").value.trim());
      };
      el.querySelector("[data-del]").onclick = async (e) => {
        e.stopPropagation();
        if (!(await askConfirm("删除素材", `《${d.title}》：素材、索引、人格都会删除。`))) return;
        await api(`/api/documents/${d.id}`, { method: "DELETE" });
        if (state.doc === d.id) { state.doc = null; $("#blocks").innerHTML = ""; $("#doc-title").textContent = "选择或导入一份素材"; }
        await loadDocs(); await loadProjects();
        if (state.project) await selectProject(state.project);
      };
      el.onclick = () => openDoc(d.id);
      box.appendChild(el);
    }
  }
  async function openDoc(id, opts = {}) {
    const [doc, outline, blocks, personas] = await Promise.all([
      api(`/api/documents/${id}`), api(`/api/documents/${id}/outline`),
      api(`/api/documents/${id}/blocks`), api(`/api/documents/${id}/personas`)]);
    state.doc = id;
    state.personas = personas;
    if (!opts.keepSession) state.session = null;
    $("#doc-title").textContent = doc.title;
    const ol = $("#outline"); ol.innerHTML = "";
    for (const s of outline.filter((x) => x.level > 0)) {
      const b = document.createElement("button");
      b.textContent = s.title;
      b.onclick = () => {
        const first = blocks.find((x) => x.section_id === s.id);
        const node = first && document.querySelector(`[data-id="${first.id}"]`);
        if (node && node.scrollIntoView) node.scrollIntoView({ behavior: "smooth" });
      };
      ol.appendChild(b);
    }
    const box = $("#blocks"); box.innerHTML = "";
    for (const b of blocks) {
      const el = document.createElement("div");
      el.className = `block kind-${b.kind}`;
      el.dataset.id = b.id;
      if (b.kind === "heading") el.dataset.level = (b.attrs && b.attrs.level) || 1;
      el.textContent = b.text;
      box.appendChild(el);
    }
    renderPersonaSelect(); renderPersonaAdmin();
    $("#persona-admin").classList.remove("hidden");
    renderDocs();
    if (!opts.keepSession) await loadSessions();
  }

  // ---------- personas ----------
  function renderPersonaSelect() {
    const sel = $("#persona"); sel.innerHTML = "";
    for (const p of state.personas) {
      const o = document.createElement("option");
      o.value = p.id; o.textContent = `${p.name}（${p.kind}）`; sel.appendChild(o);
    }
    const mp = $("#multi-panel"); mp.innerHTML = "";
    for (const p of state.personas) {
      const label = document.createElement("label");
      label.innerHTML = `<input type="checkbox" value="${p.id}"> ${esc(p.name)}`;
      mp.appendChild(label);
    }
  }
  function renderPersonaAdmin() {
    const box = $("#persona-list"); box.innerHTML = "";
    for (const p of state.personas) {
      const el = document.createElement("div");
      el.className = "item";
      el.innerHTML = `<div>${esc(p.name)} <span class="badge">${esc(p.kind)}</span>
        ${p.origin === "manual" ? '<span class="badge">手动</span>' : ""}</div>
        <div class="acts"><button data-rep="1">重解析</button><button data-del="1">删除</button></div>`;
      el.querySelector("[data-rep]").onclick = async (e) => {
        e.stopPropagation();
        await api(`/api/personas/${p.id}/reparse`, { method: "POST" });
        await openDoc(state.doc, { keepSession: true });
        setStatus("人格已重新解析");
      };
      el.querySelector("[data-del]").onclick = async (e) => {
        e.stopPropagation();
        if (!(await askConfirm("删除人格", `${p.name}：删除后不可恢复。`))) return;
        await api(`/api/personas/${p.id}`, { method: "DELETE" });
        await openDoc(state.doc, { keepSession: true });
      };
      box.appendChild(el);
    }
  }
  async function createPersona() {
    if (!state.doc) return setStatus("先打开一份素材");
    const name = $("#np-name").value.trim();
    if (!name) return setStatus("角色名不能为空");
    await api(`/api/documents/${state.doc}/personas`, { method: "POST", json: {
      name, kind: $("#np-kind").value, register: $("#np-register").value.trim(), doc_ids: [state.doc] } });
    $("#np-name").value = ""; $("#np-register").value = "";
    await openDoc(state.doc, { keepSession: true });
    setStatus("人格已创建");
  }

  // ---------- sessions ----------
  async function loadSessions() {
    const q = state.project ? `?project=${state.project}` : (state.doc ? `?doc=${state.doc}` : "");
    state.sessions = q ? await api(`/api/sessions${q}`) : [];
    const box = $("#session-list"); box.innerHTML = "";
    for (const s of state.sessions) {
      const el = document.createElement("div");
      el.className = `item ${state.session === s.id ? "active" : ""}`;
      const when = (s.updated_at || s.created_at || "").slice(5, 16).replace("T", " ");
      el.innerHTML = `<div>${esc(s.title || "会话")}</div><small>${s.message_count} 条 · ${when}</small>
        <div class="acts"><button data-ren="1">改名</button><button data-tag="1">标签</button><button data-del="1">删除</button></div>`;
      el.querySelector("[data-ren]").onclick = async (e) => {
        e.stopPropagation(); const t = await askText("会话标题", s.title); if (!t) return;
        await api(`/api/sessions/${s.id}`, { method: "PATCH", json: { title: t } }); await loadSessions();
      };
      el.querySelector("[data-del]").onclick = async (e) => {
        e.stopPropagation(); if (!(await askConfirm("删除会话", "只删除对话记录，不影响素材。"))) return;
        await api(`/api/sessions/${s.id}`, { method: "DELETE" });
        if (state.session === s.id) { state.session = null; $("#messages").innerHTML = ""; }
        await loadSessions();
      };
      el.onclick = () => openSession(s.id);
      box.appendChild(el);
    }
    if (!state.sessions.length) box.innerHTML = `<div class="hint">还没有会话。点「新会话」开始。</div>`;
  }
  async function newSession() {
    const payload = { persona_id: $("#persona").value || null, mode: $("#strictness").value,
                      persona_mode: $("#mode").value };
    if (state.project) payload.project_id = state.project;
    else if (state.doc) payload.doc_id = state.doc;
    else return setStatus("先选择项目或素材");
    const r = await api("/api/sessions", { method: "POST", json: payload });
    await openSession(r.session_id);
  }
  async function openSession(sid) {
    const s = await api(`/api/sessions/${sid}`);
    state.session = sid;
    const ids = Array.isArray(s.doc_ids) ? s.doc_ids : [];
    const primary = ids[0];
    if (primary && primary !== state.doc) {
      const pdoc = await api(`/api/documents/${primary}`);
      await openDoc(primary, { keepSession: true });
      $("#doc-title").textContent = pdoc.title;
    }
    if (s.persona_id) setSelect($("#persona"), s.persona_id);
    state.citeIndex = {};
    $("#messages").innerHTML = "";
    for (const m of s.messages) renderMessage(m);
    await loadSessions();
  }
  function renderMessage(m) {
    if (m.role === "user") {
      addBubble("user", esc(m.content), "你");
      return;
    }
    const turn = (m.meta && m.meta.turn_id) || "";
    for (const c of m.citations || []) state.citeIndex[`${turn}:${c.sid}`] = c;
    const who = (m.meta && m.meta.persona_name) || "Agent";
    const html = renderCited(m.content, turn);
    const cls = (m.meta && m.meta.abstained) ? "abstain" : "";
    const div = addBubble("assistant", `<span class="${cls}">${html}</span>`, who);
    if (m.citations && m.citations.length) {
      const bar = document.createElement("div");
      bar.className = "hint";
      bar.textContent = `引用 ${m.citations.length} 条`;
      div.appendChild(bar);
    }
  }
  function renderCited(text, turn) {
    return esc(text).replace(/\[(S\d+)\]/g,
      (_, sid) => `<button class="cite" data-sid="${sid}" data-turn="${turn}">${sid}</button>`);
  }
  function addBubble(role, html, who) {
    const div = document.createElement("div");
    div.className = `msg ${role}`;
    div.innerHTML = `<div class="who">${esc(who)}</div><div class="bubble">${html}</div>`;
    $("#messages").appendChild(div);
    $("#messages").scrollTop = $("#messages").scrollHeight;
    return div;
  }

  // ---------- ask ----------
  async function ask(question) {
    if (!question || state.busy) return;
    if (!state.session) { await newSession(); if (!state.session) return; }
    state.busy = true;
    addBubble("user", esc(question), "你");
    $("#question").value = "";
    setStatus("检索与校验中……");
    try {
      if (state.multi) {
        const pids = [...document.querySelectorAll("#multi-panel input")]
          .filter((x) => x.checked).map((x) => x.value);
        if (!pids.length) { setStatus("多角色模式至少选一个人格"); state.busy = false; return; }
        await api(`/api/sessions/${state.session}/ask_multi`, { method: "POST", json: { question, persona_ids: pids } });
      } else {
        await api(`/api/sessions/${state.session}/ask`, { method: "POST", json: { question } });
      }
      await openSession(state.session);
      setStatus("");
      if (state.devOpen) loadDev();
    } catch (e) {
      addBubble("assistant", `<span class="abstain">出错：${esc(e.message)}</span>`, "系统");
      setStatus("出错");
    } finally { state.busy = false; }
  }

  // ---------- selection ----------
  function offsetIn(el, node, offset) {
    const r = document.createRange(); r.selectNodeContents(el);
    try { r.setEnd(node, offset); } catch { return 0; }
    return r.toString().length;
  }
  function onSelect() {
    const sel = window.getSelection();
    if (!sel || !sel.rangeCount || sel.isCollapsed) return hideToolbar();
    const range = sel.getRangeAt(0);
    const startEl = (range.startContainer.nodeType === 3 ? range.startContainer.parentElement : range.startContainer).closest(".block");
    const endEl = (range.endContainer.nodeType === 3 ? range.endContainer.parentElement : range.endContainer).closest(".block");
    if (!startEl || startEl !== endEl) return hideToolbar();
    const start = offsetIn(startEl, range.startContainer, range.startOffset);
    const end = offsetIn(startEl, range.endContainer, range.endOffset);
    if (end <= start) return hideToolbar();
    state.selection = { block_id: startEl.dataset.id, start, end, text: range.toString() };
    const rect = range.getBoundingClientRect();
    const tb = $("#toolbar");
    tb.classList.remove("hidden");
    tb.style.left = Math.max(8, rect.left) + "px";
    tb.style.top = Math.max(8, rect.top - 42) + "px";
  }
  const hideToolbar = () => $("#toolbar").classList.add("hidden");

  async function makeHighlight() {
    if (!state.selection) return null;
    const r = await api("/api/highlights", { method: "POST", json: {
      doc_id: state.doc, block_id: state.selection.block_id,
      start: state.selection.start, end: state.selection.end, session_id: state.session } });
    return r.highlight_id;
  }
  async function pinSelection() {
    const hid = await makeHighlight();
    if (hid && state.session) {
      await api(`/api/sessions/${state.session}/pins`, { method: "POST", json: { highlight_id: hid } });
      setStatus("已钉入上下文");
    }
  }
  async function askSelection() {
    if (!state.selection) return;
    const text = state.selection.text;
    await pinSelection();
    await ask(`关于这段原文：“${text}”\n我的问题是：`);
  }
  async function explainSelection() {
    if (!state.selection) return;
    const text = state.selection.text;
    await pinSelection();
    await ask(`请解释这段原文：“${text}”`);
  }

  // ---------- citations ----------
  function findBlock(id) { return document.querySelector(`[data-id="${id}"]`); }
  function clearMarks() {
    document.querySelectorAll(".block.cited").forEach((b) => b.classList.remove("cited"));
    document.querySelectorAll(".block mark").forEach((m) => {
      const parent = m.parentNode;
      while (m.firstChild) parent.insertBefore(m.firstChild, m);
      parent.removeChild(m); parent.normalize();
    });
  }
  function markRange(el, start, end) {
    try {
      return markRangeInner(el, start, end);
    } catch (e) {
      el.classList.add("cited");
      return false;
    }
  }
  function markRangeInner(el, start, end) {
    if (!document.createTreeWalker || !document.createRange) { el.classList.add("cited"); return false; }
    const walker = document.createTreeWalker(el, NodeFilter.SHOW_TEXT);
    let pos = 0, startNode = null, startOff = 0, endNode = null, endOff = 0, node;
    while ((node = walker.nextNode())) {
      const len = node.textContent.length;
      if (!startNode && pos + len >= start) { startNode = node; startOff = start - pos; }
      if (!endNode && pos + len >= end) { endNode = node; endOff = end - pos; }
      pos += len;
      if (startNode && endNode) break;
    }
    if (!startNode || !endNode) return false;
    const range = document.createRange();
    if (!range.setStart) { el.classList.add("cited"); return false; }
    range.setStart(startNode, Math.max(0, Math.min(startOff, startNode.textContent.length)));
    range.setEnd(endNode, Math.max(0, Math.min(endOff, endNode.textContent.length)));
    try { const mark = document.createElement("mark"); range.surroundContents(mark); return true; }
    catch { el.classList.add("cited"); return false; }
  }
  function jump(sid, turn) {
    const c = state.citeIndex[`${turn}:${sid}`];
    if (!c) return;
    const el = findBlock(c.block_id); if (!el) return;
    clearMarks(); el.classList.add("cited");
    if (el.scrollIntoView) el.scrollIntoView({ behavior: "smooth", block: "center" });
    markRange(el, c.start || 0, c.end || (c.quote || "").length);
    setStatus(`${c.sid} · ${(c.quote || "").slice(0, 40)}…`);
  }

  // ---------- board ----------
  async function loadBoard() {
    if (!state.session) return;
    const r = await api(`/api/sessions/${state.session}/board`);
    const body = $("#board-body"); body.innerHTML = "";
    if (!r.ledger) { body.innerHTML = `<div class="hint">还没有对话。</div>`; return; }
    $("#board-tokens").textContent = `${r.ledger.budget.used}/${r.ledger.budget.total}`;
    for (const s of r.ledger.sections) {
      const d = document.createElement("details");
      d.className = "board-sec"; d.open = ["persona_card", "evidence"].includes(s.name);
      d.innerHTML = `<summary><span>${esc(s.name)}</span><span>${s.tokens} t</span></summary><pre>${esc(s.body)}</pre>`;
      body.appendChild(d);
    }
  }

  // ---------- developer ----------
  async function openDev() {
    state.devOpen = true; $("#dev-panel").classList.remove("hidden");
    await loadDev();
  }
  async function loadDev() {
    const q = state.session ? `?session_id=${state.session}` : "";
    const callsQ = q ? `${q}&limit=60` : "?limit=60";
    const [stats, calls] = await Promise.all([api(`/api/dev/stats${q}`), api(`/api/dev/calls${callsQ}`)]);
    $("#dev-stats").textContent = `调用 ${stats.total} 次 · ` +
      Object.entries(stats.by_kind).map(([k, v]) => `${k} ${v.count}（${v.avg_ms}ms）`).join(" · ");
    const tb = $("#dev-calls"); tb.innerHTML = "";
    for (const c of calls) {
      const tr = document.createElement("tr");
      tr.innerHTML = `<td>${(c.created_at || "").slice(11, 19)}</td><td>${esc(c.kind)}</td>
        <td>${Math.round(c.latency_ms)}ms</td><td>${esc(c.model || c.provider || "-")}</td>
        <td>${esc(c.status)}</td>`;
      tr.onclick = async () => {
        const d = await api(`/api/dev/calls/${c.id}`);
        $("#dev-detail").textContent = JSON.stringify(d, null, 2);
      };
      tb.appendChild(tr);
    }
  }

  // ---------- research ----------
  async function research() {
    if (!state.session) return setStatus("先开始一个会话");
    const subject = await askText("联网研究", "远程办公", "输入主题：作者、人物或论点");
    if (!subject) return;
    const raw = await askText("研究来源（可选）", "", "粘贴 1–3 个 http(s) URL，逗号分隔；留空则尝试自动搜索");
    if (raw === null) return;
    const urls = raw.split(/[\s,，]+/).filter((u) => /^https?:\/\//.test(u));
    setStatus("联网研究中……");
    try {
      const r = await api(`/api/sessions/${state.session}/research`, { method: "POST",
        json: urls.length ? { subject, urls } : { subject } });
      if (r.status === "offline") {
        addBubble("assistant", `<span class="abstain">${esc(r.reason || "离线，无法研究")}</span>`, "研究");
      } else {
        const list = (r.source_list || []).map((s) =>
          `<div class="hint">· ${esc(s.title || s.url)} <span class="badge">${esc(s.tier)}</span></div>`).join("");
        addBubble("assistant",
          `<div>研究完成：${r.sources} 个来源、${r.claims} 条外部 claim、${r.contradictions} 组矛盾。已加入上下文板。</div>
           <div>${esc(r.summary || "")}</div>${list}`, "研究");
      }
      setStatus(""); if (state.devOpen) loadDev();
    } catch (e) { setStatus("研究失败：" + e.message); }
  }

  // ---------- settings ----------
  async function openSettings() {
    const s = await api("/api/settings");
    setSelect($("#prov-mode"), s.mode || "offline");
    $("#prov-url").value = s.base_url || "";
    $("#prov-model").value = s.model || "";
    $("#prov-key").value = "";
    $("#prov-key").placeholder = s.has_key ? "已保存（留空则不修改）" : "sk-...";
    $("#modal").classList.remove("hidden");
  }
  async function saveSettings() {
    await api("/api/settings", { method: "POST", json: {
      mode: $("#prov-mode").value, base_url: $("#prov-url").value,
      api_key: $("#prov-key").value, model: $("#prov-model").value } });
    $("#modal").classList.add("hidden"); setStatus("设置已保存");
  }

  // ---------- files ----------
  async function uploadDoc(file) {
    if (!file) return;
    setStatus("解析中……");
    const buf = await file.arrayBuffer();
    const r = await api(`/api/documents?name=${encodeURIComponent(file.name)}`, { method: "POST", raw: buf });
    await loadDocs(); await openDoc(r.doc_id); setStatus("已导入");
  }
  async function uploadCorpus(file) {
    if (!file || !state.doc) return;
    setStatus("解析语料……");
    const buf = await file.arrayBuffer();
    await api(`/api/documents/${state.doc}/corpus?name=${encodeURIComponent(file.name)}&kind=interview`,
      { method: "POST", raw: buf });
    await loadDocs(); await openDoc(state.doc, { keepSession: true }); setStatus("语料已加入");
  }

  // ---------- wiring ----------
  document.addEventListener("mouseup", () => setTimeout(onSelect, 0));
  document.addEventListener("keydown", (e) => {
    if (e.key === "Escape") { hideToolbar(); $("#modal").classList.add("hidden"); closeAsk(false); }
    if (e.key === "Enter" && (e.metaKey || e.ctrlKey)) { e.preventDefault(); ask($("#question").value.trim()); }
  });
  document.querySelectorAll(".tab").forEach((b) => b.onclick = () => switchTab(b.dataset.tab));
  $("#messages").addEventListener("click", (e) => {
    if (e.target.dataset.sid) jump(e.target.dataset.sid, e.target.dataset.turn);
  });
  $("#toolbar").addEventListener("click", async (e) => {
    const act = e.target.dataset.act; if (!act) return;
    hideToolbar();
    if (act === "ask") await askSelection();
    if (act === "explain") await explainSelection();
    if (act === "check") await ask(`请核查这段说法是否有原文依据：“${state.selection ? state.selection.text : ""}”`);
    if (act === "pin") await pinSelection();
  });
  $("#send").onclick = () => ask($("#question").value.trim());
  $("#new-project").onclick = async () => {
    const name = await askText("新建项目", "新项目"); if (!name) return;
    const p = await api("/api/projects", { method: "POST", json: { name } });
    await loadProjects(); await selectProject(p.id);
  };
  $("#add-doc").onclick = async () => {
    const doc = $("#add-doc-select").value; if (!doc) return;
    await api(`/api/projects/${state.project}/documents`, { method: "POST", json: { doc_id: doc } });
    await selectProject(state.project);
  };
  $("#add-link").onclick = async () => {
    const src = $("#link-src").value, dst = $("#link-dst").value, kind = $("#link-kind").value;
    if (!src || !dst) return setStatus("选择两端素材");
    try { await api(`/api/projects/${state.project}/links`, { method: "POST", json: { src, dst, kind } }); await selectProject(state.project); }
    catch (e) { setStatus(e.message); }
  };
  $("#open-project").onclick = openProjectSession;
  $("#rename-project").onclick = async () => {
    if (!state.project) return;
    const t = await askText("重命名项目", $("#project-name").textContent);
    if (!t) return;
    await api(`/api/projects/${state.project}`, { method: "PATCH", json: { name: t } });
    await selectProject(state.project);
  };
  $("#del-project").onclick = async () => {
    if (!state.project || !(await askConfirm("删除项目", "素材会保留，会话会解除项目关联。"))) return;
    await api(`/api/projects/${state.project}`, { method: "DELETE" });
    state.project = null; $("#project-detail").classList.add("hidden");
    await loadProjects(); await loadSessions();
  };
  $("#new-session").onclick = newSession;
  $("#doc-search").oninput = () => { clearTimeout(window._dt); window._dt = setTimeout(() => loadDocs($("#doc-search").value.trim()), 250); };
  $("#persona").onchange = () => { if (state.session) newSession(); };
  $("#mode").onchange = () => { if (state.session) newSession(); };
  $("#strictness").onchange = () => { if (state.session) newSession(); };
  $("#multi").onchange = (e) => {
    state.multi = e.target.checked;
    $("#multi-panel").classList.toggle("hidden", !state.multi);
  };
  $("#np-create").onclick = createPersona;
  $("#file").onchange = (e) => uploadDoc(e.target.files[0]);
  $("#corpus-file").onchange = (e) => uploadCorpus(e.target.files[0]);
  $("#btn-research").onclick = research;
  $("#btn-board").onclick = () => {
    $("#board").classList.toggle("hidden");
    $("#layout").classList.toggle("with-board");
    if (!$("#board").classList.contains("hidden")) loadBoard();
  };
  $("#btn-dev").onclick = () => { if (state.devOpen) { state.devOpen = false; $("#dev-panel").classList.add("hidden"); } else openDev(); };
  $("#dev-close").onclick = () => { state.devOpen = false; $("#dev-panel").classList.add("hidden"); };
  $("#btn-settings").onclick = openSettings;
  $("#ask-ok").onclick = () => closeAsk(true);
  $("#ask-cancel").onclick = () => closeAsk(false);
  $("#ask-input").onkeydown = (e) => { if (e.key === "Enter") { e.preventDefault(); closeAsk(true); } };
  $("#prov-save").onclick = saveSettings;
  $("#prov-close").onclick = () => $("#modal").classList.add("hidden");
  $("#focus").onclick = () => document.body.classList.toggle("focus");
  let fontSize = 15;
  $("#font-inc").onclick = () => { fontSize = Math.min(22, fontSize + 1); document.documentElement.style.setProperty("--reader-size", fontSize + "px"); };
  $("#font-dec").onclick = () => { fontSize = Math.max(12, fontSize - 1); document.documentElement.style.setProperty("--reader-size", fontSize + "px"); };

  (async function init() {
    await loadProjects();
    await loadDocs();
    if (state.projects.length) await selectProject(state.projects[0].id);
  })();
})();
