const $ = (id) => document.getElementById(id);
const money = (value) => (value == null ? "—" : `$${Number(value).toFixed(4)}`);

let lastTasks = [];
let passwordRequired = false;

async function boot() {
  const status = await fetch("/api/status").then((r) => r.json());
  passwordRequired = status.password_required;
  $("password").hidden = !passwordRequired;
  $("password-label").hidden = !passwordRequired;
  const exa = status.exa_configured ? "Exa key is on the server." : "No Exa key. Live Exa calls will fail.";
  const keen = status.keenable_configured
    ? "Keenable key is on the server."
    : "No Keenable key. Live runs use the public endpoint and still price it at $" +
      status.keenable_usd_per_request +
      " per request.";
  $("hint").textContent = `${exa} ${keen} Context is estimated at $${status.context_usd_per_million}/M tokens.`;
  const latest = await fetch("/api/runs/latest").then((r) => r.json());
  if (latest.status === "done") render(latest);
}

$("load-sample").onclick = async () => {
  const body = await fetch("/api/sample").then((r) => r.json());
  $("queries").value = body.queries.map((item) => item.query).join("\n");
  $("hint").textContent = body.label;
};

$("run-preview").onclick = () => start("preview");
$("composer").onsubmit = (event) => {
  event.preventDefault();
  start("live");
};

$("rows").onclick = (event) => {
  const row = event.target.closest("tr[data-index]");
  if (!row) return;
  openDetail(lastTasks[Number(row.dataset.index)]);
};

async function start(mode) {
  $("error").hidden = true;
  const lines = $("queries").value.split("\n").map((line) => line.trim()).filter(Boolean);
  const queries = lines.map((query, index) => ({ id: `q${index + 1}`, query }));
  setBusy(true);
  const response = await fetch("/api/runs", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      mode,
      queries: mode === "preview" ? [] : queries,
      password: $("password").value,
    }),
  });
  const body = await response.json();
  if (!response.ok) {
    fail(body.detail || "Run failed");
    setBusy(false);
    return;
  }
  poll(body.id);
}

async function poll(id) {
  const body = await fetch(`/api/runs/${id}`).then((r) => r.json());
  if (body.status === "queued") {
    setTimeout(() => poll(id), 400);
    return;
  }
  setBusy(false);
  if (body.status === "error") {
    fail(body.error || "Run failed");
    return;
  }
  render(body);
}

function render(run) {
  const summary = run.summary;
  $("numbers").hidden = false;
  $("note").textContent = run.note || "";
  $("exa-figure").textContent = money(summary.exa.cost_per_sourced);
  $("keen-figure").textContent = money(summary.keenable.cost_per_sourced);
  $("exa-meta").textContent = armMeta(summary.exa);
  $("keen-meta").textContent = armMeta(summary.keenable);
  $("verdict").textContent = verdict(summary);
  $("wins").textContent = `${summary.wins.keenable} Keenable · ${summary.wins.exa} Exa · ${summary.wins.tie} tie · ${summary.wins.neither} neither`;
  lastTasks = run.tasks || [];
  $("rows").innerHTML = lastTasks.map((task, index) => `
    <tr class="clickable" data-index="${index}">
      <td>${escapeHtml(task.query)}</td>
      <td>${cell(task.exa, task.exa_total, task.exa_sourced)}</td>
      <td>${cell(task.keenable, task.keenable_total, task.keenable_sourced)}</td>
      <td class="pill win-${task.winner}">${task.winner}</td>
    </tr>
  `).join("");
}

function armMeta(arm) {
  const rate = Math.round(arm.sourced_rate * 100);
  const latency = arm.latency_ms_p50 == null ? "" : ` · p50 ${arm.latency_ms_p50} ms`;
  return `${arm.sourced}/${arm.queries} sourced · search ${money(arm.search_cost)} · context ${money(arm.context_cost)}${latency}`;
}

function verdict(summary) {
  const exa = summary.exa.cost_per_sourced;
  const keen = summary.keenable.cost_per_sourced;
  if (exa == null && keen == null) return "Neither side returned a sourced answer.";
  if (exa == null) return "Keenable is the only sourced side.";
  if (keen == null) return "Exa is the only sourced side.";
  if (Math.abs(exa - keen) / Math.max(exa, keen) <= 0.1) return "About the same cost per sourced answer.";
  return keen < exa ? "Keenable is cheaper per sourced answer." : "Exa is cheaper per sourced answer.";
}

function cell(arm, total, sourced) {
  if (arm.error && !arm.ok) return escapeHtml(arm.error);
  const mark = sourced ? "sourced" : "not sourced";
  return `${mark}<br>${money(total)}`;
}

function openDetail(task) {
  $("detail-title").textContent = task.query;
  $("detail-body").innerHTML = ["exa", "keenable"].map((name) => {
    const arm = task[name];
    const links = (arm.urls || []).map((url) => `<div><a href="${escapeHtml(url)}" target="_blank" rel="noreferrer">${escapeHtml(url)}</a></div>`).join("");
    return `<section><p class="label">${name}</p><p>${money(task[name + "_total"])} · ${arm.priced_as || ""}</p>${links}<pre>${escapeHtml(arm.text || arm.error || "")}</pre></section>`;
  }).join("");
  $("detail").showModal();
}

function fail(message) {
  $("error").hidden = false;
  $("error").textContent = typeof message === "string" ? message : JSON.stringify(message);
}

function setBusy(busy) {
  $("run-live").disabled = busy;
  $("run-preview").disabled = busy;
  $("run-live").textContent = busy ? "Running" : "Run live";
}

function escapeHtml(value) {
  return String(value).replace(/[&<>"']/g, (char) => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
  })[char]);
}

boot();
