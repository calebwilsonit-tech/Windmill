"use strict";
// Windmill desktop app. Talks to the local Windmill server (same code as the CLI).
const $ = (s, el = document) => el.querySelector(s);
const $$ = (s, el = document) => [...el.querySelectorAll(s)];
const api = async (path, body) => {
  const r = await fetch(path, body === undefined ? {} : {
    method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
  const j = await r.json().catch(() => ({}));
  if (!r.ok && !j.error) j.error = `HTTP ${r.status}`;
  return j;
};
const esc = s => String(s ?? "").replace(/[&<>"']/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const toast = (msg, ms = 2600) => { const t = $("#toast"); t.textContent = msg; t.classList.remove("hidden"); clearTimeout(toast.t); toast.t = setTimeout(() => t.classList.add("hidden"), ms); };
const ago = ts => { const d = Date.now() / 1000 - ts; return d < 60 ? "just now" : d < 3600 ? `${Math.floor(d / 60)}m ago` : d < 86400 ? `${Math.floor(d / 3600)}h ago` : `${Math.floor(d / 86400)}d ago`; };

let S = { state: {}, chat: null, es: null, task: null, taskEs: null, view: "home", consoleText: "" };

// ---------------------------------------------------------------- theme
const setTheme = t => { document.documentElement.dataset.theme = t; try { localStorage.setItem("wm-theme", t); } catch { } };
setTheme((() => { try { return localStorage.getItem("wm-theme"); } catch { return null; } })() ||
  (matchMedia("(prefers-color-scheme: dark)").matches ? "dark" : "light"));
$("#theme-btn").onclick = () => setTheme(document.documentElement.dataset.theme === "dark" ? "light" : "dark");

// ---------------------------------------------------------------- views
const TITLES = { projects: "Projects", library: "Code library", home: "Home", console: "Commands", models: "AI models", plugins: "Plugins", skills: "Skills", settings: "Settings", updates: "Updates" };
function show(view) {
  S.view = view;
  $$(".view").forEach(v => v.classList.add("hidden"));
  $(`#view-${view}`).classList.remove("hidden");
  $$(".nav").forEach(n => n.classList.toggle("active", n.dataset.view === view));
  if (view !== "chat") { $("#view-title").textContent = TITLES[view] || "Windmill"; $$(".chat-item").forEach(c => c.classList.remove("active")); }
  ({ projects: loadProjects, library: loadLibrary, models: loadModels, plugins: loadPlugins, skills: loadSkills, settings: loadSettings, updates: loadUpdates }[view] || (() => { }))();
}
$$(".nav").forEach(n => n.onclick = () => show(n.dataset.view));

// ---------------------------------------------------------------- state
async function refresh() {
  const st = await api("/api/state");
  if (st.error) return;
  S.state = st;
  $("#project-name").textContent = st.project.split(/[\\/]/).filter(Boolean).pop() || st.project;
  $("#project-name").title = st.project;
  $("#composer-project").textContent = st.project.split(/[\\/]/).filter(Boolean).pop() || st.project; $("#composer-folder").title = st.project + " - click to change folder or GitHub repo";
  $("#project-kinds").innerHTML = (st.kinds.length ? st.kinds : ["empty folder"]).map(k => `<span class="badge">${esc(k)}</span>`).join("");
  $("#model-name").textContent = st.model || "No model - choose one";
  $("#model-dot").classList.toggle("on", !!st.server);
  $("#model-pill").title = st.server ? "AI model is running" : "Starts automatically when needed";
  $("#version").textContent = `Windmill ${st.version}`;
  $("#build-btn").classList.toggle("hidden", !st.roadmap);
  $("#card-build").classList.toggle("hidden", !st.roadmap);
  $("#android-btn").classList.toggle("hidden", !st.android);
  $("#tip").classList.toggle("hidden", !st.tip);
  if (st.tip) $("#tip").innerHTML = `💡 ${esc(st.tip.replace(/ - see .*/, ""))} - <a href="#" data-task="get-model">see models</a>`;
}
setInterval(refresh, 15000);
const beat = () => api("/api/beat", { visible: !document.hidden });
setInterval(beat, 15000); document.addEventListener("visibilitychange", beat); beat();

// ---------------------------------------------------------------- chats list
async function loadChats() {
  const q = $("#chat-search").value.trim();
  const chats = await api("/api/chats" + (q ? `?q=${encodeURIComponent(q)}` : `?here=${S.here === false ? 0 : 1}`));
  if (!Array.isArray(chats)) return;
  const list = $("#chat-list");
  list.innerHTML = "";
  let lastGroup = "";
  for (const c of chats) {
    const d = Date.now() / 1000 - c.updated;
    const g = q ? "Results" : d < 86400 ? "Today" : d < 7 * 86400 ? "This week" : "Older";
    if (g !== lastGroup) { list.insertAdjacentHTML("beforeend", `<div class="group-label">${g}</div>`); lastGroup = g; }
    const b = document.createElement("button");
    b.className = "chat-item" + (S.chat === c.id ? " active" : "");
    b.innerHTML = `<span class="t">${esc(c.title)}</span><span class="m">${esc(c.project.split(/[\\/]/).pop())} · ${ago(c.updated)}</span>`;
    b.onclick = () => openChat(c.id);
    b.oncontextmenu = e => { e.preventDefault(); chatMenu(c); };
    list.appendChild(b);
  }
  if (!chats.length) list.innerHTML = `<div class="muted small" style="padding:8px 10px">${q ? "No chats match." : "No chats yet."}</div>`;
}
let searchT; $("#chat-search").oninput = () => { clearTimeout(searchT); searchT = setTimeout(loadChats, 250); };
async function chatMenu(c) {
  const t = prompt(`Rename "${c.title}" (leave empty to delete the chat):`, c.title);
  if (t === null) return;
  if (t === "") { if (confirm("Delete this chat?")) await api(`/api/chats/${c.id}/delete`, {}); }
  else await api(`/api/chats/${c.id}/rename`, { title: t });
  loadChats();
}

// ---------------------------------------------------------------- markdown (small and safe: escape first)
function md(src) {
  const blocks = [];
  let s = esc(src).replace(/```(\w*)\n([\s\S]*?)```/g, (_, lang, code) => { blocks.push(`<pre><code>${code.replace(/\n$/, "")}</code></pre>`); return `\u0000${blocks.length - 1}\u0000`; });
  s = s.replace(/`([^`\n]+)`/g, "<code>$1</code>").replace(/\*\*([^*\n]+)\*\*/g, "<b>$1</b>")
       .replace(/^#{1,4} (.*)$/gm, "<b>$1</b>");
  const out = s.split(/\n{2,}/).map(p => {
    if (/^\u0000\d+\u0000$/.test(p.trim())) return p;
    if (/^(\s*([-*]|\d+\.) .*(\n|$))+$/.test(p)) {
      const ordered = /^\s*\d+\./.test(p);
      const items = p.trim().split(/\n/).map(l => `<li>${l.replace(/^\s*([-*]|\d+\.) /, "")}</li>`).join("");
      return ordered ? `<ol>${items}</ol>` : `<ul>${items}</ul>`;
    }
    return `<p>${p.replace(/\n/g, "<br>")}</p>`;
  }).join("");
  return out.replace(/\u0000(\d+)\u0000/g, (_, i) => blocks[+i]);
}

// ---------------------------------------------------------------- chat
const TOOL_LABEL = {
  web_search: a => `🔍 search the web: ${a.query || ""}`, fetch_url: a => `🌐 open ${a.url || ""}`,
  read_file: a => `→ read ${a.path || ""}`, write_file: a => `+ write ${a.path || ""}`, edit_file: a => `✎ edit ${a.path || ""}`,
  list_files: a => `▤ list ${a.path || "."}`, search: a => `⌕ search /${a.pattern || ""}/`, run: a => `▶ ${a.command || ""}`,
  project_map: () => "◇ project map", find_code: a => `⌕ find ${a.name || ""}`, references: a => `◇ library: ${a.topic || ""}`,
  check: () => "◆ checking the project", design_brief: () => "◇ design brief", ask_user: () => "? question", phone: a => `▣ phone: ${a.action}`,
  use_skill: a => `✦ skill: ${a.name}`,
};
const toolLabel = (n, a) => (TOOL_LABEL[n] || (() => n.startsWith("mcp__") ? `⧉ ${n.split("__").slice(1).join(": ")}` : n))(a || {});

function addMsg(role, html, cls = "") {
  const t = $("#transcript");
  const near = t.scrollHeight - t.scrollTop - t.clientHeight < 120;
  const d = document.createElement("div");
  d.className = `msg ${role} ${cls}`;
  d.innerHTML = html;
  t.appendChild(d);
  if (near) t.scrollTop = t.scrollHeight;
  return d;
}
function addTool(name, args) {
  const code = name === "write_file" ? args?.content : name === "edit_file" ? args?.new : null;
  const open = code ? " open" : "";
  const codeHtml = code ? `<pre class="code"><code>${esc(code)}</code></pre>` : "";
  const d = addMsg("tool", `<details${open}><summary>${esc(toolLabel(name, args))} <span class="res"></span></summary>${codeHtml}<pre class="out hidden"></pre></details>`, "tool");
  return d;
}
function setResult(el, name, text) {
  if (!el) return;
  const first = (text || "").split("\n")[0];
  const bad = /^(ERROR|BLOCKED|TIMEOUT|FAIL)/.test(text);
  const res = $(".res", el);
  if (name === "check") res.innerHTML = text.startsWith("PASS") ? `<span class="ok">PASS</span>` : `<span class="bad">FAIL</span>`;
  else if (bad) res.innerHTML = `<span class="bad">${esc(first.slice(0, 120))}</span>`;
  const pre = $(".out", el); pre.textContent = text; pre.classList.remove("hidden");
}

async function openChat(id) {
  S.chat = id;
  show("chat");
  $$(".chat-item").forEach(c => c.classList.remove("active"));
  const data = await api(`/api/chats/${id}`);
  if (data.error) { toast(data.error); return; }
  $("#view-title").textContent = data.title;
  const t = $("#transcript"); t.innerHTML = "";
  S.chatBuild = !!data.build;
  cur = { text: null, tool: null, think: null, thought: null };
  let lastTool = null;
  for (const m of data.messages) {
    if (m.role === "event") { handleEvent(m.ev, false); continue; }
    cur = { text: null, tool: null, think: null, thought: null };
    if (m.role === "user") addMsg("user", esc(m.text));
    else if (m.role === "assistant") addMsg("assistant", md(m.text));
    else if (m.role === "tool") lastTool = Object.assign(addTool(m.name, m.args), { _n: m.name });
    else if (m.role === "result") { setResult(lastTool, lastTool?._n, m.text); lastTool = null; }
    else if (m.role === "debugger") addMsg("debugger", "⚑ " + esc(m.text.split("\n").slice(0, 3).join("\n")), "debugger");
  }
  if (!data.messages.length) addMsg("note", `New chat in <b>${esc(data.project)}</b>. Ask for anything - a feature, a fix, a question about the code.`, "note");
  t.scrollTop = t.scrollHeight;
  cur = { text: null, tool: null, think: null, thought: null };
  setBusy(data.busy);
  if (data.busy) listen(data.since || 0);
  loadChats();
  $("#input").focus();
}

async function newChat() {
  const r = await api("/api/chats", {});
  if (r.id) await openChat(r.id);
}
$("#new-chat").onclick = newChat;

function setBusy(b) {
  S.busy = b;
  $("#stop-btn").classList.toggle("hidden", !b);
  $("#send-btn").textContent = b ? "Queue" : "Send";
  renderQueue();
}

let cur = { text: null, tool: null, think: null, thought: null };
let actT0 = 0, actTimer = null;
function activity(text) {  // "what is it doing right now" bar above the input
  const bar = $("#activity");
  if (!text) { bar.classList.add("hidden"); clearInterval(actTimer); actTimer = null; return; }
  if (!actTimer) { actT0 = Date.now(); actTimer = setInterval(() => { $("#activity-time").textContent = `${Math.round((Date.now() - actT0) / 1000)}s`; }, 1000); }
  bar.classList.remove("hidden");
  $("#activity-text").textContent = text;
}
const PHASE = { web_search: a => `Searching the web for "${a.query || ""}"`, fetch_url: a => `Reading ${a.url || ""}`, run: a => `Running: ${a.command || ""}`, check: () => "Checking the project (build, tests, sloppy-code guard)…",
  edit_file: a => `Editing ${a.path}`, write_file: a => `Writing ${a.path}`, read_file: a => `Reading ${a.path}`,
  search: a => `Searching for /${a.pattern}/`, references: a => `Looking up "${a.topic}" in the library`, project_map: () => "Reading the project map",
  find_code: a => `Finding ${a.name}`, design_brief: () => "Reading the design brief", phone: a => `Phone: ${a.action}`, use_skill: a => `Loading skill ${a.name}` };
function handleEvent(ev, live = true) {
  const thinkOff = () => { if (cur.think) { cur.think.remove(); cur.think = null; } };
  const act = t => { if (live) activity(t); };
  if (ev.type !== "log") cur.log = null;
  switch (ev.type) {
    case "thinking": thinkOff(); cur.text = null; cur.thought = null; if (live) cur.think = addMsg("thinking", `<div class="spinner"></div> thinking…`, "thinking"); act("Thinking…"); break;
    case "think":  // the model's reasoning, streamed into a collapsible block (like Claude's)
      thinkOff();
      if (!cur.thought) { cur.thought = addMsg("thinkblock", `<details><summary>💭 Thinking…</summary><div class="body"></div></details>`, "thinkblock"); cur.thought._raw = ""; }
      cur.thought._raw += ev.text;
      $(".body", cur.thought).textContent = cur.thought._raw;
      $("summary", cur.thought).textContent = "💭 " + (cur.thought._raw.trim().split("\n").pop() || "Thinking…").slice(0, 140);
      act("Thinking: " + cur.thought._raw.trim().split("\n").pop().slice(0, 120)); break;
    case "text":
      thinkOff(); act("Writing…");
      if (!cur.text) { cur.text = addMsg("assistant", ""); cur.text._raw = ""; }
      cur.text._raw += ev.text; cur.text.innerHTML = md(cur.text._raw);
      if (live) $("#transcript").scrollTop = $("#transcript").scrollHeight; break;
    case "tool": thinkOff(); cur.text = null; cur.thought = null; cur.tool = Object.assign(addTool(ev.name, ev.args), { _n: ev.name });
      act((PHASE[ev.name] || (() => ev.name.startsWith("mcp__") ? `Using ${ev.name.split("__").slice(1).join(": ")}` : ev.name))(ev.args || {})); break;
    case "result": setResult(cur.tool, ev.name, ev.text); break;
    case "debugger": thinkOff(); act("Debugger: " + ev.text.split("\n")[0].slice(0, 120)); addMsg("debugger", "⚑ debugger: " + esc(ev.text.split("\n").slice(0, 4).join("\n")), "debugger"); break;
    case "phase": thinkOff(); cur.text = null; addMsg("phase", esc(ev.text), "phase"); act(ev.text); break;
    case "step": thinkOff(); cur.text = null; addMsg("step", esc(ev.text.replace(/^\d+ (?=[A-Z])/, "")), "step"); act(ev.text); break;
    case "log": {  // raw program output (cargo etc.): one quiet, collapsible block
      if (!cur.log) { cur.log = addMsg("logblock", `<details><summary>Output</summary><pre></pre></details>`, "logblock"); cur.log._n = 0; }
      $("pre", cur.log).textContent += ev.text + "\n"; cur.log._n++;
      $("summary", cur.log).textContent = `Output (${cur.log._n} line${cur.log._n === 1 ? "" : "s"}) · ${ev.text.slice(0, 90)}`;
      break;
    }
    case "activity": act(ev.text + "…"); break;
    case "note": addMsg("note", esc(ev.text), "note"); act(ev.text.replace(/[()]/g, "")); break;
    case "ok": addMsg("note", "✓ " + esc(ev.text), "note ok"); break;
    case "warn": addMsg("note", "! " + esc(ev.text), "note warn"); break;
    case "error": thinkOff(); addMsg("note", "⚠ " + esc(ev.text), "note error"); break;
    case "ask": thinkOff(); cur.ask = askCard(ev, live); act(live ? "Waiting for your answer" : null); break;
    case "answered": if (cur.ask && cur.ask._qid === ev.qid) cur.ask._done(ev.text); else if (!live) addMsg("note", "→ " + esc(ev.text || "(default)"), "note"); break;
    case "project": if (live) api("/api/project", { path: ev.path }).then(() => { refresh(); loadChats(); }); break;
    case "final": thinkOff(); cur = { text: null, tool: null, think: null }; break;
    case "summary": thinkOff(); cur = { text: null, tool: null, think: null }; addMsg("assistant", md(ev.text)); break;
    case "idle": thinkOff(); activity(null); setBusy(false); if (S.es) S.es.close(); loadChats(); refresh(); sendQueued(); break;
  }
}
function listen(since) {
  if (S.es) S.es.close();
  const es = S.es = new EventSource(`/api/chats/${S.chat}/events?since=${since}`);
  es.onmessage = e => handleEvent(JSON.parse(e.data), true);
}
function askCard(ev, live = true) {
  const q = ev.text.replace(/\s*\(yes\/no\)\s*$/, "");
  let body = "";
  if (ev.options) {
    body = `<div class="choices">` + ev.options.map(([k, label, hint]) => {
      const sw = (ev.swatches || {})[k];
      const swHtml = sw ? `<span class="swatches">${sw.map(c => `<i style="background:${esc(c)}"></i>`).join("")}</span>` : "";
      return `<button data-a="${esc(k)}" class="${label === "Cancel" ? "choice-cancel" : ""}">${swHtml}<b>${esc(label)}</b>${hint ? `<small>${esc(hint)}</small>` : ""}</button>`;
    }).join("") + `</div>`;
  } else if (ev.yn || /\(yes\/no\)/.test(ev.text)) {
    body = `<div class="row"><button class="primary small" data-a="yes">Yes</button><button class="ghost small" data-a="no">No</button></div>`;
  } else {
    const accept = /press enter to accept/i.test(q);
    const def = ev.default != null && ev.default !== "" ? String(ev.default) : null;
    body = `<div class="row"><input placeholder="${accept ? "Or type what to change…" : def ? `Enter for “${esc(def)}”` : "Your answer…"}">` +
      `<button class="primary small" data-a="__input">${accept ? "Change it" : "Send"}</button>` +
      (accept ? `<button class="primary small" data-a="">✓ Accept the plan</button>` : def ? `<button class="ghost small" data-a="">Use “${esc(def)}”</button>` : "") + `</div>`;
  }
  const title = q.replace(/^Press Enter to accept, or type what to change$/i, "Does this plan look right?");
  const d = addMsg("ask", `<div class="ask-q">${esc(title)}</div>${body}`, "ask");
  d._qid = ev.qid;
  d._done = a => {
    const label = ev.options ? (ev.options.find(o => o[0] === a) || [a, a])[1] : (a || (ev.default != null ? String(ev.default) : "OK"));
    d.innerHTML = `<div class="muted small">${esc(title)} → <b>${esc(label || "OK")}</b></div>`;
  };
  if (!live) { d.innerHTML = `<div class="muted small">${esc(title)}</div>`; return d; }
  const send = async a => {
    if (S.chatBuild && ev.qid) { const r = await api(`/api/chats/${S.chat}/answer`, { qid: ev.qid, text: a }); if (r.ok) d._done(a); return; }
    await api(`/api/chats/${S.chat}/answer`, { qid: ev.qid, text: a }); d._done(a);
  };
  $$("button", d).forEach(b => b.onclick = () => send(b.dataset.a === "__input" ? $("input", d).value : b.dataset.a));
  const inp = $("input", d); if (inp) { inp.focus(); inp.onkeydown = e => { if (e.key === "Enter") send(inp.value); }; }
  return d;
}

// ---------------------------------------------------------------- message queue (type while Windmill works)
S.queue = {};
function renderQueue() {
  const box = $("#queue"), q = S.queue[S.chat] || [];
  box.classList.toggle("hidden", !q.length);
  box.innerHTML = q.map((t, i) => `<div class="queued"><span class="qi">${i + 1}</span><span class="qt">${esc(t)}</span>` +
    (S.chatBuild && S.busy ? `<button class="ghost small" data-plan="${i}" title="Add it to the build plan - the build picks it up as a new milestone">＋ Add to build plan</button>` : "") +
    `<button class="icon-btn" data-rm="${i}" title="Remove">✕</button></div>`).join("");
  $$("[data-rm]", box).forEach(b => b.onclick = () => { q.splice(+b.dataset.rm, 1); renderQueue(); });
  $$("[data-plan]", box).forEach(b => b.onclick = async () => {
    const [t] = q.splice(+b.dataset.plan, 1);
    await api("/api/roadmap", { action: "add", text: t });
    addMsg("note", "＋ Added to the build plan: " + esc(t), "note ok"); renderQueue();
  });
  $("#input").placeholder = S.busy ? "Windmill is working - type to queue your next message (Enter)" : "Tell Windmill what you want…  (Enter to send, Shift+Enter for a new line)";
}
function sendQueued() {
  const q = S.queue[S.chat] || [];
  renderQueue();
  if (q.length && !S.busy) { const t = q.shift(); renderQueue(); sendText(t); }
}

async function send() {
  const text = $("#input").value.trim();
  if (!text) return;
  $("#input").value = ""; autosize();
  if (S.busy) { (S.queue[S.chat] = S.queue[S.chat] || []).push(text); renderQueue(); return; }
  sendText(text);
}
async function sendText(text) {
  if (!S.chat) { const r = await api("/api/chats", {}); S.chat = r.id; show("chat"); $("#transcript").innerHTML = ""; }
  addMsg("user", esc(text));
  const r = await api(`/api/chats/${S.chat}/send`, { text });
  if (r.error) { toast(r.error); return; }
  setBusy(true);
  listen(r.since);
}
$("#send-btn").onclick = send;
$("#stop-btn").onclick = () => api(`/api/chats/${S.chat}/stop`, {});
const autosize = () => { const i = $("#input"); i.style.height = "auto"; i.style.height = Math.min(i.scrollHeight, 240) + "px"; };
$("#input").oninput = autosize;
$("#input").onkeydown = e => { if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); send(); } };

// ---------------------------------------------------------------- console: every CLI command
const CHIPS = [["connect", "Connect apps"], ["doctor", "Health check"], ["fix", "Repair llama.cpp"], ["models", "Switch model"], ["get-model", "Get a model"],
  ["benchmark", "Benchmark"], ["history", "Chat history"], ["library stats", "Library"], ["deps", "Index dependencies"],
  ["map", "Project map"], ["plan", "Careful mode…"], ["best", "Best-of-N…"], ["design", "Design"], ["android status", "Android"],
  ["update", "Update"], ["version", "Version"], ["setup", "Setup"], ["help", "All commands"]];
$("#cmd-chips").innerHTML = CHIPS.map(([c, l]) => `<button class="chip" data-cmd="${esc(c)}">${esc(l)}</button>`).join("");
$$("#cmd-chips .chip").forEach(b => b.onclick = () => {
  if (b.dataset.cmd.endsWith("plan") || b.dataset.cmd.endsWith("best")) { $("#cmd-input").value = b.dataset.cmd + ' "'; $("#cmd-input").focus(); return; }
  runTask(splitArgs(b.dataset.cmd), { console: true });
});
function splitArgs(s) { return (s.match(/"[^"]*"|\S+/g) || []).map(a => a.replace(/^"|"$/g, "")); }
$("#cmd-run").onclick = () => { const v = $("#cmd-input").value.trim(); if (v) runTask(splitArgs(v.replace(/^windmill\s+/, "")), { console: true }); };
$("#cmd-input").onkeydown = e => { if (e.key === "Enter") $("#cmd-run").click(); };

function ansiToHtml(text) {
  let out = "", open = 0, st = { b: 0, d: 0, i: 0, c: 0, bg: null };
  const span = () => { const cls = [st.b && "b", st.d && "d", st.i && "i", st.c && `c${st.c}`].filter(Boolean).join(" ");
    const style = st.bg ? ` style="background:rgb(${st.bg})"` : ""; return cls || style ? `<span class="${cls}"${style}>` : null; };
  const re = /\x1b\[([\d;]*)m/g; let last = 0, m;
  while ((m = re.exec(text))) {
    out += esc(text.slice(last, m.index)); last = re.lastIndex;
    const codes = (m[1] || "0").split(";").map(Number);
    for (let i = 0; i < codes.length; i++) {
      const c = codes[i];
      if (c === 0) st = { b: 0, d: 0, i: 0, c: 0, bg: null };
      else if (c === 1) st.b = 1; else if (c === 2) st.d = 1; else if (c === 3) st.i = 1;
      else if ((c >= 30 && c <= 37) || c === 90) st.c = c;
      else if (c === 48 && codes[i + 1] === 2) { st.bg = codes.slice(i + 2, i + 5).join(","); i += 4; }
    }
    while (open) { out += "</span>"; open--; }
    const s = span(); if (s) { out += s; open++; }
  }
  out += esc(text.slice(last));
  while (open) { out += "</span>"; open--; }
  return out;
}
function applyCR(text) {  // "\r" rewrites the current line (download progress bars)
  return text.split("\n").map(l => { const parts = l.split("\r"); return parts.filter(p => p !== "").pop() ?? ""; }).join("\n");
}
const strip = t => t.replace(/\x1b\[[\d;]*[A-Za-z]/g, "");

// ---------------------------------------------------------------- background tasks
// Every command runs in the background. A small tray shows what's running, questions pop up as dialogs,
// and the full command output is one click away ("Details").
S.tasks = {}; S.consoleTask = null; S.askHidden = {};
const lastLines = (t, n) => strip(applyCR(t)).split("\n").filter(l => l.trim()).slice(-n);

function promptOf(text) {  // is the program waiting for an answer? -> {question, def, choices, yn}
  const tail = strip(applyCR(text)).slice(-4000);
  if (/\n$/.test(tail)) return null;
  const lastLine = tail.split("\n").pop();
  if (!/^\? .*\S\s*$|\(Y\/n\)|\(y\/N\)/.test(lastLine)) return null;
  const yn = /\(Y\/n\)|\(y\/N\)/.test(lastLine);
  const def = (lastLine.match(/\[([^\]]+)\]\s*$/) || [])[1];
  let choices = [];
  if (yn) choices = [["y", "Yes"], ["n", "No"]];
  else {
    const block = tail.split(/── .* ─+\n/).pop();  // the most recent menu
    const opts = [...block.matchAll(/^ {1,4}(\d+|[a-z0-9]) {2}(.+)$/gm)].map(m => [m[1], m[2]]);
    if (opts.length >= 2 && /Choose|Pick|number|open|1, 2 or 3/i.test(lastLine)) choices = opts;
  }
  return { question: lastLine.replace(/^\?\s*/, "").replace(/\s*\(Y\/n\)|\s*\(y\/N\)/, ""), def, choices, yn };
}
const choiceButtons = choices => choices.map(([k, label]) => {
  if (/^back \/ exit$/.test(label.trim())) return `<button data-v="${esc(k)}" class="choice-cancel"><b>Cancel</b></button>`;
  const [main, ...rest] = label.split(/ {2,}/);
  return `<button data-v="${esc(k)}"><b>${esc(main)}</b>${rest.length ? `<small>${esc(rest.join("  "))}</small>` : ""}</button>`;
}).join("");

function renderConsole() {
  const t = S.tasks[S.consoleTask];
  const text = t ? t.text : S.consoleText;
  const pre = $("#console");
  pre.innerHTML = ansiToHtml(applyCR(text));
  pre.scrollTop = pre.scrollHeight;
  $("#cmd-stop").classList.toggle("hidden", !t || t.done);
  const choices = $("#console-choices"); choices.innerHTML = "";
  const p = t && !t.done && promptOf(text);
  $("#console-input-row").classList.toggle("hidden", !p);
  if (!p) return;
  $("#console-prompt").textContent = p.question;
  $("#console-input").placeholder = p.def ? `Enter for "${p.def}"` : "";
  choices.innerHTML = choiceButtons(p.choices);
  $$("button", choices).forEach(b => b.onclick = () => taskInput(t.id, b.dataset.v));
  setTimeout(() => $("#console-input").focus(), 0);
}
async function taskInput(id, v) { if (id && S.tasks[id]) { delete S.askHidden[id]; await api(`/api/tasks/${id}/input`, { text: v }); $("#console-input").value = ""; $("#ask-input").value = ""; } }
$("#console-send").onclick = () => taskInput(S.consoleTask, $("#console-input").value);
$("#console-input").onkeydown = e => { if (e.key === "Enter") taskInput(S.consoleTask, $("#console-input").value); };
$("#cmd-stop").onclick = () => S.consoleTask && api(`/api/tasks/${S.consoleTask}/stop`, {});

function taskTitle(args) {
  const c = args.join(" ");
  const hit = COMMANDS.find(([k]) => k === c) || COMMANDS.find(([k]) => k === args[0]);
  return hit ? hit[1].replace(/…$/, "") : "windmill " + c;
}
function showDetails(id) { S.consoleTask = id; const t = S.tasks[id]; $("#cmd-input").value = t ? t.args.join(" ") : ""; show("console"); renderConsole(); }

async function startBuild(args, extra = {}) {
  const r = await api("/api/build", { args, ...extra });
  if (r.error) return toast(r.error, 8000);
  S.chatBuild = true;
  await openChat(r.id);
}
async function runTask(args, opts = {}) {
  if (args[0] === "new" && args.length === 1) return openNewProject();
  if (args[0] === "build" && args.length === 1 && !opts.console) return startBuild(["build"]);
  const title = opts.title || taskTitle(args);
  const r = await api("/api/tasks", { args, cwd: S.state.project, title });
  if (r.error) { toast(r.error); return; }
  const t = S.tasks[r.id] = { id: r.id, args, title, started: Date.now(), done: false, code: null,
    text: `\x1b[2m$ windmill ${args.map(a => /\s/.test(a) ? `"${a}"` : a).join(" ")}\x1b[0m\n`, onExit: opts.onExit, onOut: opts.onOut };
  if (opts.console) showDetails(r.id);
  const es = new EventSource(`/api/tasks/${r.id}/events?since=0`);
  let pending = false;
  es.onmessage = e => {
    const ev = JSON.parse(e.data);
    if (ev.type === "out") { t.text += ev.text; if (t.onOut) t.onOut(t); }
    if (ev.type === "in") t.text += ev.text + "\n";
    if (ev.type === "exit") {
      t.done = true; t.code = ev.code; es.close();
      t.text += `\n\x1b[2m(finished${ev.code ? `, exit code ${ev.code}` : ""})\x1b[0m\n`;
      if (t.onExit) t.onExit(t);
      refresh(); loadChats();
      if (!ev.code) setTimeout(() => { if (S.consoleTask !== t.id) { delete S.tasks[t.id]; renderJobs(); } }, 4000);
    }
    if (!pending) { pending = true; requestAnimationFrame(() => { pending = false; renderAll(); }); }
  };
  renderJobs();
  return t;
}
function renderAll() {
  if (S.view === "console") renderConsole();
  renderJobs(); renderAsk();
}

function renderJobs() {
  const list = Object.values(S.tasks);
  const box = $("#jobs");
  const running = list.filter(t => !t.done).length, waiting = list.filter(t => !t.done && promptOf(t.text)).length;
  const failed = list.filter(t => t.done && t.code).length;
  const btn = $("#tasks-btn");
  btn.classList.toggle("hidden", !list.length);
  btn.classList.toggle("ask", waiting > 0); btn.classList.toggle("bad", !running && failed > 0);
  $("#tasks-spin").classList.toggle("hidden", !running || waiting > 0);
  $("#tasks-label").textContent = waiting ? "Needs your answer" : running ? `${running} running` : failed ? "Had a problem" : "Done";
  if (!list.length) S.jobsOpen = false;
  box.classList.toggle("hidden", !S.jobsOpen);
  box.innerHTML = list.map(t => {
    const waiting = !t.done && promptOf(t.text);
    const line = waiting ? "Waiting for your answer" : t.done ? (t.code ? `Stopped with a problem - see details` : "Done")
      : (lastLines(t.text, 1)[0] || "starting…");
    const icon = t.done ? `<span class="job-icon ${t.code ? "bad" : "ok"}">${t.code ? "!" : "✓"}</span>` : waiting ? `<span class="job-icon ask">?</span>` : `<div class="spinner"></div>`;
    return `<div class="job" data-id="${t.id}">${icon}<div class="job-main"><b>${esc(t.title)}</b><span title="${esc(line)}">${esc(line.slice(0, 140))}</span></div>
      <button class="ghost small" data-act="details">Details</button>
      <button class="icon-btn" data-act="${t.done ? "close" : "stop"}" title="${t.done ? "Dismiss" : "Stop"}">${t.done ? "✕" : "■"}</button></div>`;
  }).join("");
  $$(".job", box).forEach(el => el.onclick = e => {
    const id = el.dataset.id, act = e.target.dataset.act;
    if (act === "stop") return api(`/api/tasks/${id}/stop`, {});
    if (act === "close") { delete S.tasks[id]; return renderJobs(); }
    S.jobsOpen = false; box.classList.add("hidden");
    if (act === "details") return showDetails(id);
    if (S.tasks[id] && !S.tasks[id].done && promptOf(S.tasks[id].text)) { delete S.askHidden[id]; S.asking = null; renderAsk(); }
  });
}

// questions from background tasks pop up as a dialog (not when you're already looking at that task's console)
function renderAsk() {
  const t = Object.values(S.tasks).find(t => !t.done && !S.askHidden[t.id] && promptOf(t.text) && !(S.view === "console" && S.consoleTask === t.id));
  const m = $("#ask-modal");
  if (!t) { m.classList.add("hidden"); S.asking = null; return; }
  const p = promptOf(t.text);
  const key = t.id + ":" + t.text.length;
  if (S.asking === key) return;
  S.asking = key;
  $("#ask-title").textContent = t.title;
  // A clean question, not a terminal: the menu's title as the heading, the choices as buttons, and (only for
  // plain questions) the information printed just before it - e.g. the project plan - as normal text.
  const plain = strip(applyCR(t.text));
  const titles = [...plain.matchAll(/── (.+?) ─{2,}/g)];
  const section = titles.length ? plain.slice(titles[titles.length - 1].index) : plain;
  const lines = section.split("\n").slice(1, -1).filter(l => l.trim() && !/^\$ windmill /.test(l) && !/^[⠁-⣿]\s/.test(l));
  const question = p.question.replace(/\s*\[[^\]]*\]\s*$/, "").trim();
  $("#ask-heading").textContent = titles.length ? titles[titles.length - 1][1] : question;
  $("#ask-question").textContent = titles.length && question !== $("#ask-heading").textContent && !/^(Choose|Pick)$/i.test(question) ? question : "";
  const info = p.choices.length ? [] : lines.slice(-30);
  $("#ask-context").textContent = info.join("\n");
  $("#ask-context").classList.toggle("hidden", !info.length);
  $("#ask-choices").innerHTML = choiceButtons(p.choices);
  $$("#ask-choices button").forEach(b => b.onclick = () => taskInput(t.id, b.dataset.v));
  const free = !p.yn && !p.choices.length;
  $("#ask-free").classList.toggle("hidden", !free);
  $("#ask-input").value = ""; $("#ask-input").placeholder = p.def ? `Enter for "${p.def}"` : (p.choices.length ? "or type your answer" : "Type your answer");
  $("#ask-send").onclick = () => taskInput(t.id, $("#ask-input").value);
  $("#ask-input").onkeydown = e => { if (e.key === "Enter") taskInput(t.id, $("#ask-input").value); };
  $("#ask-details").onclick = () => showDetails(t.id);
  $("#ask-stop").onclick = () => api(`/api/tasks/${t.id}/stop`, {});
  $("#ask-close").onclick = () => { S.askHidden[t.id] = true; S.asking = null; m.classList.add("hidden"); renderJobs(); };
  m.classList.remove("hidden");
  if (free) setTimeout(() => $("#ask-input").focus(), 0);
}

// ---------------------------------------------------------------- new project (a form, then it runs in the background)
async function openNewProject() {
  const f = $("#new-form"); f.reset();
  if (!f.where.value) f.where.value = (await api("/api/projects-home")).path || "";
  $("#new-modal").classList.remove("hidden");
  setTimeout(() => f.idea.focus(), 0);
}
$("#new-cancel").onclick = () => $("#new-modal").classList.add("hidden");
$("#new-browse").onclick = async () => {
  const f = $("#new-form");
  const r = await browseFolder(f.where.value, "Where should new projects go?");
  if (r) f.where.value = r;
};
$("#new-form").onsubmit = e => {
  e.preventDefault();
  const f = e.target;
  const args = ["new", "--idea", f.idea.value.trim()];
  if (f.who.value.trim()) args.push("--who", f.who.value.trim());
  if (f.extra.value.trim()) args.push("--extra", f.extra.value.trim());
  if (f.where.value.trim()) args.push("--in", f.where.value.trim());
  $("#new-modal").classList.add("hidden");
  const idea = f.idea.value.trim();
  startBuild(args, { title: "New: " + (idea.length > 48 ? idea.slice(0, 46) + "…" : idea), intro: idea +
    (f.who.value.trim() ? `\n\nFor: ${f.who.value.trim()}` : "") + (f.extra.value.trim() ? `\n\nMust have: ${f.extra.value.trim()}` : "") });
};

document.addEventListener("click", e => {
  const t = e.target.closest("[data-task]");
  if (t) { e.preventDefault(); runTask(splitArgs(t.dataset.task)); }
  const a = e.target.closest("[data-action=chat]");
  if (a) newChat();
});

// ---------------------------------------------------------------- command palette (Ctrl+K)
const COMMANDS = [
  ["chat", "New chat in this project", () => newChat()], ["new", "Start a new project (guided)"], ["build", "Build the roadmap on autopilot"],
  ["run", "Run / open the app"], ["debug", "Fix a problem"], ["check", "Check the project"], ["plan", "Careful mode for a big task…", "arg"],
  ["best", "Several attempts, keep the best…", "arg"], ["design", "Look & feel"], ["history", "Chat history"], ["models", "Switch AI model"],
  ["get-model", "Get a new AI model"], ["benchmark", "Benchmark the AI"], ["tune", "Tune for my GPU"], ["server status", "Model server status"],
  ["library stats", "Reference library"], ["library search", "Search the library…", "arg"], ["deps", "Index this project's dependencies"],
  ["android setup", "Install the Android toolchain"], ["android status", "Android status"], ["apk", "Build the Android .apk"],
  ["android install", "Install on my phone"], ["android logs", "Phone crash log"], ["android init", "Add Android to this project"],
  ["init", "Set up Windmill in this folder"], ["map", "Project map"], ["find", "Find code…", "arg"], ["plugins", "Plugins (MCP)"],
  ["skills", "Skills"], ["connect", "Connect an app or service (Blender, Ludo, Gmail, browser testing…)"],
  ["connect", "Search connectors…", "arg"], ["update", "Update Windmill"], ["version", "Version"], ["doctor", "Health check"], ["fix", "Repair everything"],
  ["setup", "Setup wizard"], ["settings", "Settings"], ["help", "All commands"],
];
let palSel = 0;
function openPalette() { $("#palette").classList.remove("hidden"); $("#palette-input").value = ""; palSel = 0; renderPalette(); $("#palette-input").focus(); }
function closePalette() { $("#palette").classList.add("hidden"); }
function palMatches() { const q = $("#palette-input").value.toLowerCase().trim(); return COMMANDS.filter(([c, d]) => !q || (c + " " + d).toLowerCase().includes(q)); }
function renderPalette() {
  const m = palMatches();
  $("#palette-list").innerHTML = m.map(([c, d], i) => `<button class="pal${i === palSel ? " sel" : ""}" data-i="${i}"><b>${esc(c)}</b><span>${esc(d)}</span></button>`).join("");
  $$(".pal").forEach(b => b.onclick = () => runPal(m[+b.dataset.i]));
}
function runPal(cmd) {
  if (!cmd) return; closePalette();
  const [c, , extra] = cmd;
  if (typeof extra === "function") return extra();
  if (extra === "arg") { show("console"); $("#cmd-input").value = c + ' "'; $("#cmd-input").focus(); return; }
  runTask(splitArgs(c));
}
$("#palette-input").oninput = () => { palSel = 0; renderPalette(); };
$("#palette-input").onkeydown = e => {
  const m = palMatches();
  if (e.key === "ArrowDown") { palSel = Math.min(palSel + 1, m.length - 1); renderPalette(); e.preventDefault(); }
  if (e.key === "ArrowUp") { palSel = Math.max(palSel - 1, 0); renderPalette(); e.preventDefault(); }
  if (e.key === "Enter") runPal(m[palSel]);
  if (e.key === "Escape") closePalette();
};
$("#palette").onclick = e => { if (e.target.id === "palette") closePalette(); };
document.addEventListener("keydown", e => {
  if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === "k") { e.preventDefault(); openPalette(); }
  if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === "n") { e.preventDefault(); newChat(); }
});

// ---------------------------------------------------------------- project
async function browseFolder(start, title) {
  const r = await api("/api/pick-folder", { start, title });
  if (r.error) toast("The folder window couldn't open - type or paste the path instead. (" + r.error + ")", 6000);
  return r.path || null;
}
// ---------------------------------------------------------------- project folder menu (like the folder picker in Claude's chat box)
const baseName = p => (p || "").split(/[\\/]/).filter(Boolean).pop() || p;
async function switchProject(path) {
  path = (path || "").trim().replace(/^"|"$/g, "");
  if (!path) return;
  const st = await api("/api/project", { path });
  if (st.error) { $("#proj-error").textContent = st.error; return false; }
  closeProjPop();
  S.chat = null; await refresh(); if (S.view !== "chat") show("home");
  return true;
}
function closeProjPop() { $("#proj-pop").classList.add("hidden"); }
async function openProjPop(anchor) {
  const pop = $("#proj-pop");
  $("#proj-error").textContent = ""; $("#proj-path").value = "";
  $("#pp-name").textContent = baseName(S.state.project); $("#pp-path").textContent = S.state.project;
  pop.classList.remove("hidden");
  const place = () => {
    const r = anchor.getBoundingClientRect(), w = pop.offsetWidth, h = pop.offsetHeight;
    pop.style.left = Math.max(8, Math.min(r.left, innerWidth - w - 8)) + "px";
    pop.style.top = (r.top > innerHeight / 2 ? Math.max(8, r.top - h - 6) : Math.max(8, Math.min(r.bottom + 6, innerHeight - h - 8))) + "px";
  };
  place();
  const [pr, g] = await Promise.all([api("/api/projects"), api("/api/git")]);
  $("#pp-recent-sec").classList.toggle("hidden", !(pr.recent || []).length);
  $("#pp-recent").innerHTML = (pr.recent || []).map(p => `<div class="pop-row"><button class="pop-item" data-path="${esc(p)}" title="${esc(p)}">📁 ${esc(baseName(p))}<small>${esc(p)}</small></button><button class="icon-btn" data-forget="${esc(p)}" title="Remove from list">✕</button></div>`).join("");
  $$("#pp-recent [data-path]").forEach(b => b.onclick = () => switchProject(b.dataset.path));
  $$("#pp-recent [data-forget]").forEach(b => b.onclick = async e => { e.stopPropagation(); await api("/api/projects/forget", { path: b.dataset.forget }); b.parentElement.remove(); });
  renderGit(g);
  place();
  renderAccount(await api("/api/github"));
  place();
}
function renderAccount(a) {
  const box = $("#pp-account");
  box.innerHTML = a.signed_in
    ? `${a.avatar ? `<img src="${esc(a.avatar)}" alt="" width="20" height="20" class="avatar">` : ""}<span>Signed in as <b>${esc(a.login)}</b></span>
       <span class="spacer"></span><button class="ghost small" id="gh-repos">Your repos…</button><button class="icon-btn" id="gh-logout" title="Sign out of GitHub">⎋</button>`
    : `<span class="muted">Sign in so Windmill can see your repos and push to them.</span><span class="spacer"></span><button class="primary small" id="gh-login">Sign in to GitHub</button>`;
  if ($("#gh-login")) $("#gh-login").onclick = () => { closeProjPop(); runTask(["git", "login"], { title: "Sign in to GitHub", onExit: () => api("/api/github?refresh=1") }); };
  if ($("#gh-logout")) $("#gh-logout").onclick = () => { closeProjPop(); runTask(["git", "logout"], { title: "Sign out of GitHub" }); };
  if ($("#gh-repos")) $("#gh-repos").onclick = () => { closeProjPop(); openRepos(); };
}
async function openRepos() {
  $("#repos-modal").classList.remove("hidden");
  $("#repos-search").value = "";
  $("#repos-list").innerHTML = `<div class="muted">Loading your repos…</div>`;
  const r = await api("/api/github/repos");
  if (r.error) { $("#repos-list").innerHTML = `<div class="bad-text">${esc(r.error)}</div>`; return; }
  S.repos = r.repos;
  renderRepos();
  $("#repos-search").focus();
}
function renderRepos() {
  const q = $("#repos-search").value.toLowerCase().trim();
  const list = (S.repos || []).filter(r => !q || (r.full_name + " " + r.description).toLowerCase().includes(q));
  $("#repos-list").innerHTML = list.map(r => `<div class="repo-row"><div class="repo-main"><b>${r.private ? "🔒 " : ""}${esc(r.full_name)}</b>
      <small>${esc(r.description || "")}${r.updated ? ` · updated ${ago(Date.parse(r.updated) / 1000)}` : ""}</small></div>
      <button class="primary small" data-open="${esc(r.full_name)}" title="Download it and work in it">Open</button>
      <button class="ghost small" data-connect="${esc(r.full_name)}" title="Make the current folder sync with this repo">Use for this folder</button></div>`).join("")
    || `<div class="muted">No repos match.</div>`;
  $$("#repos-list [data-open]").forEach(b => b.onclick = () => { $("#repos-modal").classList.add("hidden"); gitTask(["clone", b.dataset.open], "Open " + b.dataset.open); });
  $$("#repos-list [data-connect]").forEach(b => b.onclick = () => { $("#repos-modal").classList.add("hidden"); gitTask(["connect", b.dataset.connect], "Connect " + b.dataset.connect); });
}
$("#repos-search").oninput = renderRepos;
$("#tasks-btn").onclick = () => { S.jobsOpen = !S.jobsOpen; renderJobs(); };
document.addEventListener("mousedown", e => {
  if (S.jobsOpen && !e.target.closest("#jobs,#tasks-btn")) { S.jobsOpen = false; renderJobs(); }
});
$("#repos-close").onclick = () => $("#repos-modal").classList.add("hidden");
function renderGit(g) {
  const conn = g.repo && g.remote;
  $("#pp-git").innerHTML = !g.repo ? `<span class="muted">Not using GitHub yet - connect a repo below (it starts version history for you).</span>`
    : conn ? `<a href="${esc(g.web)}" target="_blank" rel="noopener">${esc(g.web.replace(/^https?:\/\//, ""))}</a> · branch <b>${esc(g.branch || "?")}</b><br>
       <span class="muted">${g.changes} unsaved change${g.changes === 1 ? "" : "s"} · ${g.ahead} to send · ${g.behind} to get</span>`
    : `<span class="muted">Has version history, but isn't connected to GitHub - connect a repo below.</span>`;
  $("#pp-git-actions").classList.toggle("hidden", !conn);
  $("#pp-repo").placeholder = conn ? "Change repo: owner/name or URL" : "Connect a repo: owner/name or URL";
}
function gitTask(args, title) {
  closeProjPop();
  runTask(["git", ...args], { title, onExit: t => {
    const m = strip(t.text).match(/Project ready at (.+)/);
    if (m) switchProject(m[1].trim());
  } });
}
$("#pick-project").onclick = e => openProjPop(e.currentTarget);
$("#project-card .name").onclick = e => openProjPop(e.currentTarget);
$("#composer-folder").onclick = e => openProjPop(e.currentTarget);
$("#pp-browse").onclick = async () => { const p = await browseFolder(S.state.project, "Choose a project folder"); if (p) switchProject(p); };
$("#proj-open").onclick = () => switchProject($("#proj-path").value);
$("#proj-path").onkeydown = e => { if (e.key === "Enter") switchProject($("#proj-path").value); };
$$("[data-git]").forEach(b => b.onclick = () => gitTask([b.dataset.git], { sync: "Sync with GitHub", pull: "Get latest from GitHub", push: "Send to GitHub" }[b.dataset.git]));
$("#pp-connect").onclick = () => { const v = $("#pp-repo").value.trim(); if (v) gitTask(["connect", v], "Connect GitHub repo"); };
$("#pp-repo").onkeydown = e => { if (e.key === "Enter") $("#pp-connect").click(); };
$("#pp-clone-btn").onclick = () => { const v = $("#pp-clone").value.trim(); if (v) gitTask(["clone", v], "Open GitHub repo"); };
$("#pp-clone").onkeydown = e => { if (e.key === "Enter") $("#pp-clone-btn").click(); };
document.addEventListener("mousedown", e => {
  const pop = $("#proj-pop");
  if (!pop.classList.contains("hidden") && !pop.contains(e.target) && !e.target.closest("#pick-project,#composer-folder,#project-card .name")) closeProjPop();
});
document.addEventListener("keydown", e => { if (e.key === "Escape") closeProjPop(); });
$("#model-pill").onclick = () => show("models");

// ---------------------------------------------------------------- models
async function loadModels() {
  const d = await api("/api/models");
  const hw = d.hw || {};
  $("#hw").textContent = `This PC: ${Math.round(hw.ram_gb || 0)} GB RAM` + (hw.gpu ? ` · ${hw.gpu} (${Math.round(hw.vram_gb)} GB)` : " · no NVIDIA GPU");
  $("#models-list").innerHTML = (d.models || []).map(m => `
    <div class="item ${m.selected ? "selected" : ""}">
      <div class="grow"><div class="t">${esc(m.name)} ${m.selected ? '<span class="badge">in use</span>' : ""}</div>
      <div class="s">${m.size_gb} GB · ${esc(m.quant)} · ${m.experts ? `MoE ${m.experts}×` : "dense"} · <span class="fit-${m.fit === "fast" ? "fast" : m.fit === "ok" ? "ok" : "too"}">${esc(m.why)}</span>
      ${m.bench ? ` · benchmark ${m.bench[0]}/${m.bench[1]}` : ""} · <span title="${esc(m.path)}">${esc(m.source)}</span></div></div>
      ${m.selected ? "" : `<button class="ghost small" data-path="${esc(m.path)}">Use this</button>`}
    </div>`).join("") || `<div class="muted">No models found yet. <a href="#" data-task="get-model">Get one</a>.</div>`;
  $$("#models-list [data-path]").forEach(b => b.onclick = async () => { await api("/api/models/select", { path: b.dataset.path }); loadModels(); refresh(); });
  const sv = await api("/api/server");
  $("#server-log").textContent = sv.log || "(no log yet)";
  $("#server-toggle").textContent = sv.healthy ? "■ Stop the model (frees memory)" : "▶ Start the model now";
  $("#server-toggle").onclick = async () => { await api(sv.healthy ? "/api/server/stop" : "/api/server/start", {}); setTimeout(loadModels, 1500); };
}

// ---------------------------------------------------------------- plugins
async function loadPlugins() {
  const d = await api("/api/plugins");
  const servers = Object.entries(d.mcpServers || {});
  $("#plugin-import").classList.toggle("hidden", !d.claude_desktop);
  $("#plugins-list").innerHTML = servers.map(([name, spec]) => {
    const on = !(d.disabled || []).includes(name);
    const what = spec.url || [spec.command, ...(spec.args || [])].join(" ");
    return `<div class="item"><div class="grow"><div class="t">${esc(name)} ${on ? '<span class="badge">on</span>' : '<span class="muted small">off</span>'}</div>
      <div class="s">${esc(what)}</div><div class="s test-out" data-name="${esc(name)}"></div></div>
      <button class="ghost small" data-act="test" data-name="${esc(name)}">Test</button>
      <button class="ghost small" data-act="${on ? "disable" : "enable"}" data-name="${esc(name)}">${on ? "Turn off" : "Turn on"}</button>
      <button class="ghost small" data-act="remove" data-name="${esc(name)}">Remove</button></div>`;
  }).join("") || `<div class="muted">No plugins yet. Add one, or import the ones you use in Claude Desktop.</div>`;
  $$("#plugins-list [data-act]").forEach(b => b.onclick = async () => {
    const { act, name } = b.dataset;
    if (act === "remove" && !confirm(`Remove ${name}?`)) return;
    if (act === "test") { const out = $(`.test-out[data-name="${CSS.escape(name)}"]`); out.textContent = "testing…"; const r = await api("/api/plugins", { action: "test", name }); out.innerHTML = `<span class="${r.ok ? "fit-fast" : "fit-too"}">${esc(r.info)}</span>`; return; }
    await api("/api/plugins", { action: act, name }); loadPlugins();
  });
}
$("#plugin-add-btn").onclick = () => $("#plugin-form").classList.remove("hidden");
$("#plugin-cancel").onclick = () => $("#plugin-form").classList.add("hidden");
$("#plugin-form").onsubmit = async e => {
  e.preventDefault();
  const f = new FormData(e.target);
  const cmd = f.get("command").trim();
  const env = Object.fromEntries(f.get("env").split("\n").map(l => l.trim()).filter(l => l.includes("=")).map(l => [l.slice(0, l.indexOf("=")), l.slice(l.indexOf("=") + 1)]));
  const spec = /^https?:/.test(cmd) ? { type: "http", url: cmd } : { command: cmd, args: f.get("args").split("\n").map(s => s.trim()).filter(Boolean), env };
  await api("/api/plugins", { action: "save", name: f.get("name").trim(), spec });
  e.target.reset(); e.target.classList.add("hidden");
  await loadPlugins();
  $(`[data-act=test][data-name="${CSS.escape(f.get("name").trim())}"]`)?.click();
};
$("#plugin-import").onclick = async () => { const r = await api("/api/plugins", { action: "import" }); toast(`Imported ${r.added} plugin(s)`); loadPlugins(); };

// ---------------------------------------------------------------- skills
async function loadSkills() {
  const sk = await api("/api/skills");
  $("#skills-list").innerHTML = (Array.isArray(sk) ? sk : []).map(s => `<div class="item"><div class="grow"><div class="t">${esc(s.name)} <span class="badge">${esc(s.source)}</span></div><div class="s">${esc(s.description)}</div><div class="s">${esc(s.path)}</div></div></div>`).join("")
    || `<div class="muted">No skills yet.</div>`;
}

// ---------------------------------------------------------------- settings
const SETTING_KEYS = ["auto_update", "permission", "web_access", "show_thinking", "auto_fix", "ctx", "max_steps", "library_refresh_days", "cache_type", "port"];
const CHOICES = { auto_update: ["auto", "ask", "off"], permission: ["auto", "ask"], cache_type: ["q8_0", "f16", "q4_0"] };
async function loadSettings() {
  const { values, help } = await api("/api/settings");
  $("#settings-form").innerHTML = SETTING_KEYS.map(k => {
    const v = values[k], h = `<span class="help">${esc(help[k] || "")}</span>`;
    if (typeof v === "boolean") return `<label class="switch"><input type="checkbox" name="${k}" ${v ? "checked" : ""}> ${k.replace(/_/g, " ")} ${h}</label>`;
    if (CHOICES[k]) return `<label>${k.replace(/_/g, " ")} ${h}<select name="${k}">${CHOICES[k].map(c => `<option ${c === v ? "selected" : ""}>${c}</option>`).join("")}</select></label>`;
    return `<label>${k.replace(/_/g, " ")} ${h}<input name="${k}" value="${esc(v ?? "")}" ${typeof v === "number" ? 'type="number"' : ""}></label>`;
  }).join("") + `<div class="row"><button class="primary" type="submit">Save</button><span class="muted small">More options: <a href="#" data-task="settings">all settings</a></span></div>`;
  $("#settings-form").onsubmit = async e => {
    e.preventDefault();
    const vals = {};
    for (const k of SETTING_KEYS) {
      const el = e.target.elements[k]; if (!el) continue;
      vals[k] = el.type === "checkbox" ? el.checked : el.type === "number" ? Number(el.value) : el.value;
    }
    await api("/api/settings", { values: vals }); toast("Saved");
  };
}

// ---------------------------------------------------------------- updates (one version for the app + the CLI)
async function loadUpdates(force) {
  $("#update-box").innerHTML = `<div class="muted">Checking…</div>`;
  const u = await api("/api/update" + (force ? "?force=1" : ""));
  $("#update-dot").classList.toggle("hidden", !u.available);
  $("#update-box").innerHTML = `
    <p>You have <b>Windmill ${esc(u.current)}</b> (app and command line share one version).</p>
    ${!u.configured ? `<p class="muted">Updates aren't set up for this copy - reinstall from GitHub to get automatic updates.</p>` :
      u.available ? `<h3>Windmill ${esc(u.latest)} is available (${esc(u.level)} update)</h3><pre class="notes">${esc(u.notes || "")}</pre>
        <div class="row"><button class="primary" id="upd-now">Update now</button></div>` :
      `<p class="fit-fast">✓ You're on the latest version.</p>`}
    <div class="row" style="margin-top:14px"><button class="ghost" id="upd-check">Check again</button>
    <button class="ghost" data-task="update --rollback">Undo the last update</button></div>`;
  $("#upd-check").onclick = () => loadUpdates(true);
  const now = $("#upd-now"); if (now) now.onclick = applyUpdate;
  return u;
}
async function applyUpdate() {
  $("#update-modal").classList.add("hidden");
  const r = await api("/api/update/apply", {});
  if (!r.ok) return toast(r.error || "Update failed", 8000);
  const box = $("#updating"); box.classList.remove("hidden");
  const say = t => { $("#updating-text").textContent = t; };
  say("Downloading the update…");
  const started = Date.now();
  while (Date.now() - started < 5 * 60 * 1000) {  // show each step; reload once the new version answers
    await new Promise(res => setTimeout(res, 1000));
    let st = null;
    try { const h = await fetch("/api/update/status"); if (h.ok) st = await h.json(); } catch { }
    if (!st) { say("Restarting Windmill…"); continue; }
    if (st.error) { box.classList.add("hidden"); toast(st.error, 15000); return; }
    if (!st.step && st.version) {  // a fresh engine (no update in progress): the new version is running
      say(`Updated to ${st.version} - reloading…`); location.reload(); return; }
    if (st.step) say(st.step);
  }
  say("This is taking unusually long. Close Windmill and open it again - the update is probably already installed.");
}
async function startupUpdateCheck() {
  const u = await api("/api/update");
  $("#update-dot").classList.toggle("hidden", !u.available);
  let dismissed = null; try { dismissed = sessionStorage.getItem("wm-update-later"); } catch { }
  if (u.available && dismissed !== u.latest) {
    $("#um-title").textContent = `Windmill ${u.latest} is available (${u.level} update)`;
    $("#um-notes").textContent = u.notes || "";
    $("#update-modal").classList.remove("hidden");
    $("#um-now").onclick = applyUpdate;
    $("#um-later").onclick = () => { try { sessionStorage.setItem("wm-update-later", u.latest); } catch { } $("#update-modal").classList.add("hidden"); setupCheck(); };
    return;
  }
  setupCheck();
}

// ---------------------------------------------------------------- first start: quietly check what's missing, offer to install it
async function setupCheck(force) {
  let later = null; try { later = sessionStorage.getItem("wm-setup-later"); } catch { }
  if (later && !force) return;
  const r = await api("/api/setup/check");
  if (r.error || !r.items) return;
  const missing = r.items.filter(i => !i.ok);
  if (!missing.length && !force) return;
  const m = $("#setup-modal");
  $("#setup-title").textContent = missing.length ? "Finish setting up Windmill" : "Windmill is set up";
  $("#setup-intro").textContent = missing.length
    ? "A few things Windmill needs aren't on this PC yet. Pick what to install - it all happens in the background, nothing to do in a terminal."
    : "Everything Windmill needs is installed.";
  $("#setup-list").innerHTML = r.items.map(i => `<label class="setup-item${i.ok ? " ok" : ""}">
      ${i.ok ? `<span class="job-icon ok">✓</span>` : `<input type="checkbox" value="${i.id}" checked>`}
      <span><b>${esc(i.name)}</b>${i.required && !i.ok ? ` <span class="badge">needed</span>` : ""}${i.ok ? ` <span class="muted small">installed</span>` : ""}<br>
      <span class="muted small">${esc(i.why)}</span></span></label>`).join("") +
    (r.model ? "" : `<div class="setup-item"><span class="job-icon ask">i</span><span><b>An AI model</b> <span class="muted small">- you choose this next</span><br>
      <span class="muted small">The AI itself (a few GB). Windmill recommends the best one for this PC.</span></span></div>`);
  $("#setup-progress").classList.add("hidden");
  $("#setup-actions").classList.remove("hidden");
  $("#setup-install").classList.toggle("hidden", !missing.length || !r.can_install);
  $("#setup-note").textContent = r.can_install ? "Windows may ask for permission for some installers - that's normal." :
    "Windows' installer isn't available on this PC: get 'App Installer' from the Microsoft Store, then reopen Windmill.";
  $("#setup-later").textContent = missing.length ? "Not now" : "Close";
  $("#setup-model").classList.toggle("hidden", !!r.model || !!missing.filter(i => i.required).length);
  m.classList.remove("hidden");
  $("#setup-later").onclick = () => { try { sessionStorage.setItem("wm-setup-later", "1"); } catch { } m.classList.add("hidden"); };
  $("#setup-model").onclick = () => { m.classList.add("hidden"); runTask(["get-model"]); };
  $("#setup-install").onclick = () => {
    const ids = $$("#setup-list input:checked").map(c => c.value);
    if (!ids.length) return;
    $("#setup-actions").classList.add("hidden");
    $("#setup-progress").classList.remove("hidden");
    $("#setup-step").textContent = "Starting…";
    runTask(["install", ...ids], { title: "Setting up Windmill", onExit: async t => {
      clearInterval(S.setupTimer);
      await setupCheck(true);
      if (t.code) $("#setup-intro").textContent = "Some things couldn't be installed - see below, or click Details in the top bar to see why.";
    } }).then(t => {
      S.setupTimer = setInterval(() => {
        const last = t && lastLines(t.text, 1)[0];
        if (last) $("#setup-step").textContent = last.replace(/^[›✓!✗]\s*/, "").slice(0, 140);
      }, 400);
    });
  };
}

// ---------------------------------------------------------------- start
(async () => {
  await refresh();
  await loadChats();
  show("home");
  startupUpdateCheck();
})();


// ---------------------------------------------------------------- chats: this project / all
$$("#chat-scope button").forEach(b => b.onclick = () => {
  S.here = b.dataset.here === "1";
  $$("#chat-scope button").forEach(x => x.classList.toggle("on", x === b));
  loadChats();
});

// ---------------------------------------------------------------- projects
async function loadProjects() {
  $("#project-page").classList.add("hidden"); $("#projects-grid").classList.remove("hidden");
  const r = await api("/api/projects/list");
  $("#projects-grid").innerHTML = `<button class="proj-card new" data-task="new"><b>＋ New project</b><span>Describe an idea - Windmill plans and builds it</span></button>` +
    (r.projects || []).map(p => `<button class="proj-card${p.current ? " current" : ""}" data-path="${esc(p.path)}">
      <b>${esc(p.name)}</b><span class="muted small">${esc(p.kinds.join(", ") || "folder")} · ${p.chats} chat${p.chats === 1 ? "" : "s"} · ${ago(p.updated)}</span>
      ${p.total ? `<div class="bar"><i style="width:${Math.round(100 * p.done / p.total)}%"></i></div><span class="muted small">${p.done}/${p.total} milestones built</span>` : `<span class="muted small">No build plan yet</span>`}
      ${p.current ? `<span class="badge">current</span>` : ""}</button>`).join("");
  $$("#projects-grid [data-path]").forEach(b => b.onclick = () => openProject(b.dataset.path));
}
async function openProject(path) {
  const d = await api("/api/project/detail?path=" + encodeURIComponent(path));
  if (d.error) return toast(d.error);
  S.projPage = path;
  $("#projects-grid").classList.add("hidden"); $("#project-page").classList.remove("hidden");
  $("#view-title").textContent = d.name;
  $("#pp2-name").textContent = d.name; $("#pp2-path").textContent = d.path;
  $("#pp2-progress").textContent = d.total ? `${d.done}/${d.total} built` : "";
  $("#pp2-roadmap").innerHTML = d.roadmap.map(m => `<div class="rm${m.done ? " done" : ""}"><input type="checkbox" ${m.done ? "checked" : ""} data-line="${m.line}" title="Mark built / not built">
    <span>${esc(m.text)}</span><button class="icon-btn" data-del="${m.line}" title="Remove">✕</button></div>`).join("") || `<div class="muted small">No milestones yet - add one below, or start a new project.</div>`;
  $$("#pp2-roadmap [data-line]").forEach(c => c.onchange = async () => { await api("/api/roadmap", { path, action: "toggle", line: +c.dataset.line }); openProject(path); });
  $$("#pp2-roadmap [data-del]").forEach(c => c.onclick = async () => { await api("/api/roadmap", { path, action: "remove", line: +c.dataset.del }); openProject(path); });
  $("#pp2-chats").innerHTML = d.chats.map(c => `<button class="chat-item" data-id="${c.id}"><span class="t">${esc(c.title)}</span><span class="m">${ago(c.updated)}</span></button>`).join("") || `<div class="muted small">No chats yet.</div>`;
  $$("#pp2-chats [data-id]").forEach(b => b.onclick = async () => { await useProject(path); openChat(b.dataset.id); });
  $("#pp2-instr").value = d.instructions; $("#pp2-instr-msg").textContent = "";
}
async function useProject(path) { if (S.state.project !== path) { await api("/api/project", { path }); await refresh(); loadChats(); } }
$("#proj-back").onclick = loadProjects;
$("#pp2-use").onclick = async () => { await useProject(S.projPage); show("home"); };
$("#pp2-chat").onclick = async () => { await useProject(S.projPage); newChat(); };
$("#pp2-build").onclick = async () => { await useProject(S.projPage); startBuild(["build"]); };
const addMilestone = async () => { const t = $("#pp2-add").value.trim(); if (!t) return; await api("/api/roadmap", { path: S.projPage, action: "add", text: t }); $("#pp2-add").value = ""; openProject(S.projPage); };
$("#pp2-add-btn").onclick = addMilestone;
$("#pp2-add").onkeydown = e => { if (e.key === "Enter") addMilestone(); };
$("#pp2-instr-save").onclick = async () => { await api("/api/project/instructions", { path: S.projPage, text: $("#pp2-instr").value }); $("#pp2-instr-msg").textContent = "Saved."; };

// ---------------------------------------------------------------- code library
async function loadLibrary() {
  const r = await api("/api/library");
  const cats = Object.entries(r.cats || {}).sort((a, b) => b[1] - a[1]);
  $("#lib-stats").innerHTML = `<div><b>${(r.sources || []).length}</b><span>sources</span></div><div><b>${(r.chunks || 0).toLocaleString()}</b><span>code pieces</span></div><div><b>${r.mb || 0} MB</b><span>on disk</span></div>` +
    `<div class="cats">${cats.slice(0, 14).map(([c, n]) => `<button class="chip" data-cat="${esc(c)}">${esc(c)} <span class="muted">${n}</span></button>`).join("")}</div>`;
  $$("#lib-stats [data-cat]").forEach(b => b.onclick = () => { $("#lib-q").value = b.dataset.cat + " "; $("#lib-q").focus(); });
  $("#lib-mine").innerHTML = (r.mine || []).map(m => `<div class="row lib-src"><b>${esc(m.name)}</b><span class="muted small" style="flex:1">${esc(m.url)} · ${m.indexed ? "in the library" : "not downloaded yet"}</span>
    <button class="icon-btn" data-rm="${esc(m.name)}" title="Remove from the library">✕</button></div>`).join("") || `<div class="muted small">None yet - add a repo or one of your own project folders above.</div>`;
  $$("#lib-mine [data-rm]").forEach(b => b.onclick = async () => { if (confirm(`Remove ${b.dataset.rm} from the library?`)) { await api("/api/library/remove", { name: b.dataset.rm }); loadLibrary(); } });
  $("#lib-all").innerHTML = (r.sources || []).map(s => `<div class="lib-row"><b>${esc(s.name)}</b><span class="muted small">${s.chunks.toLocaleString()} pieces · ${esc(s.cats.join(", "))} · ${esc(s.license)}</span></div>`).join("") || `<div class="muted small">The library is empty - click "Download / update all sources" below (or run Setup).</div>`;
}
async function librarySearch() {
  const q = $("#lib-q").value.trim(); if (!q) return;
  $("#lib-results").innerHTML = `<div class="muted">Searching…</div>`;
  const r = await api("/api/library/search?q=" + encodeURIComponent(q));
  $("#lib-results").innerHTML = (r.results || []).map(x => `<details class="lib-hit"><summary><b>${esc(x.name || x.path.split("/").pop())}</b> <span class="muted small">${esc(x.repo)} · ${esc(x.path)}:${x.line} · ${esc(x.license)}</span></summary>
    <pre class="code"><code>${esc(x.snippet)}</code></pre>${x.more ? `<div class="muted small">…${x.more} more lines</div>` : ""}</details>`).join("")
    || `<div class="muted">${r.error ? esc(r.error) : "Nothing matched - try other words, or add a source below."}</div>`;
}
$("#lib-go").onclick = librarySearch;
$("#lib-q").onkeydown = e => { if (e.key === "Enter") librarySearch(); };
$("#lib-browse").onclick = async () => { const p = await browseFolder(S.state.project, "Choose a folder to add to the library"); if (p) $("#lib-add").value = p; };
$("#lib-add-btn").onclick = () => {
  let v = $("#lib-add").value.trim(); if (!v) return;
  if (/^[\w.-]+\/[\w.-]+$/.test(v)) v = `https://github.com/${v}`;
  runTask(["library", "add", v], { title: "Add to the library: " + v.split(/[\\/]/).filter(Boolean).pop(), onExit: () => { if (S.view === "library") loadLibrary(); } });
  $("#lib-add").value = "";
};
