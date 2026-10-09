/* Search Audit report: interactivity for /r/<slug>.
 *
 * The page is server-rendered from the view model; this script only adds behaviour. The pure scoring
 * functions below mirror searchaudit/stats.py (prompt_scores, value_scores, headline) so the weight
 * sliders can recompute in the browser. tests/report/test_js_parity.py checks they agree. Change both together.
 */
"use strict";

const SA = (() => {
  const CREDIT = { c: 1, p: 0.5, w: 0 };
  const ratio = (best, x) => (x <= 0 ? 1 : Math.min(1, best / x));

  /** Value score in [0, 1] per provider index on one prompt. g/c/t: grade, cost, seconds per provider.
   *  skip[i] true = never sets the bar (the no-search baseline), unless nobody else answered. */
  function promptScores(g, c, t, w, skip) {
    const live = g.map((x, i) => i).filter((i) => g[i] !== "w"); // a wrong answer never sets the bar
    if (!live.length) return g.map(() => 0);
    const kept = live.filter((i) => !(skip && skip[i]));
    const setters = kept.length ? kept : live;
    const minC = Math.min(...setters.map((i) => c[i]));
    const minT = Math.min(...setters.map((i) => t[i]));
    const tot = w.a + w.c + w.s || 1;
    return g.map((grade, i) => {
      const credit = CREDIT[grade];
      if (!credit) return 0;
      return (credit * (w.a + w.c * ratio(minC, c[i]) + w.s * ratio(minT, t[i]))) / tot;
    });
  }

  /** Mean value score per provider id, 0-100. */
  function valueScores(data, w) {
    const tot = data.ids.map(() => 0);
    const skip = skipOf(data);
    for (const p of data.prompts) promptScores(p.g, p.c, p.t, w, skip).forEach((s, i) => (tot[i] += s));
    const n = data.prompts.length || 1;
    return Object.fromEntries(data.ids.map((id, i) => [id, (100 * tot[i]) / n]));
  }

  /** Same order as the view model's by_score: score desc, then correct desc, then cost asc. */
  function byScore(data, scores) {
    return [...data.ids].sort((a, b) => scores[b] - scores[a] || data.k[b] - data.k[a] || data.perPrompt[a] - data.perPrompt[b]);
  }

  /** Per provider index: true for the baseline (never sets the bar, never named in the headline). */
  function skipOf(data) {
    return data.ids.map((id) => (data.baseline || []).includes(id));
  }

  /** stats.headline for the weight-dependent branches (tradeoff / sole / tie). Ignores the baseline. */
  function headline(data, order) {
    const base = data.baseline || [];
    const L = data.labels, best = order.filter((id) => !base.includes(id))[0], leader = data.leader, n = data.n, co = data.company;
    if (!data.tied.includes(best)) {
      return `${L[best]} is the best value for ${co} at these weights, but ${L[leader]} answers significantly more prompts correctly (${data.k[leader]} vs ${data.k[best]} of ${n}).`;
    }
    if (data.tied.length === 1) {
      return `${L[best]} is the most accurate provider for ${co} (${data.k[best]} of ${n} correct) and the best value overall.`;
    }
    return `${L[best]} gives ${co} the best balance of accuracy, cost and speed. ${data.tied.length} providers tie on accuracy, so cost and speed decide.`;
  }

  /** 'best' / 'worst' per key, nothing when all equal (view model _extremes). */
  function extremes(vals, lowerIsBetter) {
    const xs = Object.values(vals);
    if (xs.length < 2) return {};
    const lo = Math.min(...xs), hi = Math.max(...xs);
    if (lo === hi) return {};
    const best = lowerIsBetter ? lo : hi, worst = lowerIsBetter ? hi : lo;
    return Object.fromEntries(Object.entries(vals).map(([k, v]) => [k, v === best ? "best" : v === worst ? "worst" : ""]));
  }

  return { promptScores, valueScores, byScore, headline, extremes, skipOf };
})();

if (typeof module !== "undefined") module.exports = SA;

let graphType = "all";
let graphJob = "all";

if (typeof document !== "undefined") {
  document.addEventListener("DOMContentLoaded", () => {
    const $ = (sel, root = document) => root.querySelector(sel);
    const $$ = (sel, root = document) => [...root.querySelectorAll(sel)];

    // ---------------- provider rows: click / Enter / Space toggles the answer row
    function setRow(row, open) {
      row.setAttribute("aria-expanded", open ? "true" : "false");
      document.getElementById(row.getAttribute("aria-controls")).hidden = !open;
    }
    for (const row of $$("tr.sel")) {
      row.addEventListener("click", (e) => { if (!e.target.closest("a")) setRow(row, row.getAttribute("aria-expanded") !== "true"); });
      row.addEventListener("keydown", (e) => {
        if (e.key === "Enter" || e.key === " ") { e.preventDefault(); setRow(row, row.getAttribute("aria-expanded") !== "true"); }
      });
    }

    // ---------------- toast and clipboard
    function toast(text) {
      const d = document.createElement("div");
      d.className = "toast";
      d.setAttribute("role", "status");
      d.textContent = text;
      document.body.appendChild(d);
      setTimeout(() => d.remove(), 1800);
    }
    function copyText(text, msg) {
      const fallback = () => {
        const ta = document.createElement("textarea");
        ta.value = text;
        document.body.appendChild(ta);
        ta.select();
        let ok = false;
        try { ok = document.execCommand("copy"); } catch (e) { ok = false; }
        ta.remove();
        toast(ok ? msg : "Press ⌘C / Ctrl+C to copy");
      };
      if (navigator.clipboard && window.isSecureContext) navigator.clipboard.writeText(text).then(() => toast(msg), fallback);
      else fallback();
    }
    const abs = (path) => location.origin + path;

    $$("[data-copy]").forEach((b) => b.addEventListener("click", () => copyText(abs(b.dataset.copy), "Link copied")));

    const dataEl = document.getElementById("report-data");
    if (!dataEl) return; // the per-prompt page needs only the rows and copy links above
    const data = JSON.parse(dataEl.textContent);
    const round1 = (x) => Math.round(x * 10) / 10;
    const hlHtml = (cls, text) => (cls && cls !== "worst" ? `<span class="${cls}">${text}</span>` : String(text));

    // ---------------- tabs (roving tabindex, arrow keys)
    const tabs = $$('[role="tab"]');
    function selectTab(id, focus) {
      for (const t of tabs) {
        const on = t.id === "tab-" + id;
        t.setAttribute("aria-selected", on ? "true" : "false");
        t.tabIndex = on ? 0 : -1;
        $("#" + t.getAttribute("aria-controls")).hidden = !on;
        if (on && focus) t.focus();
      }
    }
    tabs.forEach((t, i) => {
      t.addEventListener("click", () => selectTab(t.id.slice(4)));
      t.addEventListener("keydown", (e) => {
        const step = { ArrowRight: 1, ArrowLeft: -1 }[e.key];
        if (step) { e.preventDefault(); selectTab(tabs[(i + step + tabs.length) % tabs.length].id.slice(4), true); }
        if (e.key === "Home") { e.preventDefault(); selectTab(tabs[0].id.slice(4), true); }
        if (e.key === "End") { e.preventDefault(); selectTab(tabs[tabs.length - 1].id.slice(4), true); }
      });
    });

    // ---------------- prompt filters
    const state = { filter: "disagree", type: "all" };
    function applyFilters() {
      let shown = 0;
      for (const d of $$(".plist details")) {
        const okFilter = state.filter === "all" || (state.filter === "disagree" ? d.dataset.disagree === "1" : d.dataset.nobody === "1");
        const okType = state.type === "all" || d.dataset.type === state.type;
        d.hidden = !(okFilter && okType);
        if (!d.hidden) shown++;
      }
      $("#no-prompts").hidden = shown > 0;
      $$("[data-filter]").forEach((c) => c.setAttribute("aria-pressed", c.dataset.filter === state.filter ? "true" : "false"));
      $$(".filters [data-type]").forEach((c) => c.setAttribute("aria-pressed", c.dataset.type === state.type ? "true" : "false"));
    }
    $$("[data-filter]").forEach((c) => c.addEventListener("click", () => { state.filter = c.dataset.filter; applyFilters(); }));
    $$(".filters [data-type]").forEach((c) => c.addEventListener("click", () => { state.type = c.dataset.type; applyFilters(); }));

    let sortKey = "", sortDir = 1;
    function sortPrompts(key) {
      const lower = key === "id" || key.startsWith("c") || key.startsWith("s");
      if (sortKey === key) sortDir *= -1;
      else { sortKey = key; sortDir = lower ? 1 : -1; }
      const box = $(".plist");
      const tail = $("#no-prompts");
      const rows = $$(".plist details");
      rows.sort((a, b) => {
        const av = a.dataset[key] ?? "", bv = b.dataset[key] ?? "";
        const aEmpty = av === "", bEmpty = bv === "";
        if (aEmpty !== bEmpty) return aEmpty ? 1 : -1;
        const an = Number(av), bn = Number(bv);
        const cmp = av !== "" && !Number.isNaN(an) && !Number.isNaN(bn) ? an - bn : String(av).localeCompare(String(bv));
        return cmp * sortDir || Number(a.dataset.id) - Number(b.dataset.id);
      });
      for (const row of rows) box.insertBefore(row, tail);
      $$(".plist .sort").forEach((b) => b.setAttribute("aria-sort", b.dataset.sort === key ? (sortDir < 0 ? "descending" : "ascending") : "none"));
    }
    $$(".plist .sort").forEach((b) => b.addEventListener("click", () => sortPrompts(b.dataset.sort)));
    $("#prompt-sort")?.addEventListener("change", (e) => { sortKey = ""; sortPrompts(e.target.value); });

    // ---------------- deep links: open a prompt (and some answers) from any tab or from #p<id>
    function openPrompt(id, answers) {
      const d = document.getElementById("p" + id);
      if (!d) return;
      selectTab("prompts");
      state.filter = "all"; state.type = "all"; applyFilters();
      d.open = true;
      for (const pid of answers) {
        const row = $(`tr.sel[aria-controls="a${id}-${pid}"]`, d);
        if (row) setRow(row, true);
      }
      d.scrollIntoView({ block: "start" });
      $("summary", d).focus({ preventScroll: true });
    }
    document.addEventListener("click", (e) => {
      const el = e.target.closest("[data-open]");
      if (!el) return;
      e.preventDefault();
      openPrompt(el.dataset.open, (el.dataset.ans || "").split(",").filter(Boolean));
      history.replaceState(null, "", "#p" + el.dataset.open);
    });
    const hash = location.hash.match(/^#p(\d+)$/);
    if (hash) openPrompt(hash[1], new URLSearchParams(location.search).getAll("ans"));


    // ---------------- head-to-head pair selector
    const selA = $("#h2h-a"), selB = $("#h2h-b");
    function showPair() {
      if (selA.value === selB.value) selB.value = data.ids.find((id) => id !== selA.value);
      const key = selA.value + "|" + selB.value;
      $$(".h2h-pair").forEach((p) => (p.hidden = p.dataset.pair !== key));
    }
    if (selA && selB) { selA.addEventListener("change", showPair); selB.addEventListener("change", showPair); }

    // ---------------- value-score sliders
    const weights = { ...data.weights };
    const headerFav = Object.fromEntries(data.ids.map((id, i) => [id, $$(".plist .head .c")[i]?.innerHTML || id]));
    let currentOrder = SA.byScore(data, SA.valueScores(data, weights));
    function recompute() {
      const scores = SA.valueScores(data, weights);
      const order = SA.byScore(data, scores);
      currentOrder = order;
      const tbody = $("#ranking tbody");
      const maxS = scores[order[0]] || 1;
      const hl = SA.extremes(Object.fromEntries(data.ids.map((id) => [id, round1(scores[id])])), false);
      order.forEach((id, i) => {
        const tr = $(`tr[data-id="${id}"]`, tbody);
        tbody.appendChild(tr);
        $(".rank", tr).textContent = i + 1;
        $(".scorebar i", tr).style.width = (scores[id] / maxS) * 100 + "%";
        $(".sc", tr).innerHTML = hlHtml(hl[id], Math.round(scores[id]));
      });
      if (!data.headlineFixed) $("#headline").textContent = SA.headline(data, order);
      for (const p of data.prompts) {
        const s = SA.promptScores(p.g, p.c, p.t, weights, SA.skipOf(data));
        const vals = Object.fromEntries(data.ids.map((id, i) => [id, Math.round(s[i] * 1000) / 1000]));
        const phl = SA.extremes(vals, false);
        data.ids.forEach((id, i) => {
          const cell = $(`table[data-prompt="${p.id}"] td[data-score="${id}"]`);
          if (cell) cell.innerHTML = hlHtml(phl[id], Math.round(s[i] * 100));
        });
        const best = s.indexOf(Math.max(...s));
        const bv = $(`[data-best="${p.id}"]`);
        if (bv) bv.innerHTML = s[best] > 0 ? headerFav[data.ids[best]] : '<span class="hint">—</span>';
      }
    }
    $$("input[data-w]").forEach((inp) => inp.addEventListener("input", () => {
      weights[inp.dataset.w] = Number(inp.value);
      $("#wv" + inp.dataset.w).textContent = inp.value;
      recompute();
    }));

    // ---------------- share: link + plain-text summary (E4)
    $("#share")?.addEventListener("click", () => {
      const scores = SA.valueScores(data, weights);
      const lines = [`${data.company} · Search Audit: ${$("#headline").textContent}`,
        ...currentOrder.map((id, i) => `${i + 1}. ${data.labels[id]}: score ${Math.round(scores[id])} · ${data.k[id]}/${data.n} correct · $${data.perPrompt[id].toFixed(3)}/prompt`),
        location.origin + location.pathname];
      copyText(lines.join("\n"), "Link and summary copied");
    });

    // ---------------- download dialog with focus trap (R4)
    const modal = $("#dl-modal"), opener = $("#download");
    const focusables = () => $$("a[href], button:not([disabled])", modal);
    function closeModal() { modal.hidden = true; opener.focus(); }
    opener?.addEventListener("click", () => { modal.hidden = false; focusables()[0].focus(); });
    modal?.addEventListener("click", (e) => { if (e.target === modal || e.target.closest("[data-close]")) closeModal(); });
    modal?.addEventListener("keydown", (e) => {
      if (e.key === "Escape") { e.preventDefault(); closeModal(); return; }
      if (e.key !== "Tab") return;
      const f = focusables(), first = f[0], last = f[f.length - 1];
      if (e.shiftKey && document.activeElement === first) { e.preventDefault(); last.focus(); }
      else if (!e.shiftKey && document.activeElement === last) { e.preventDefault(); first.focus(); }
    });

    drawGraphs(data);
    document.querySelectorAll("[data-view]").forEach((button) => {
      button.addEventListener("click", () => showView(button.dataset.view, data));
    });
    document.querySelectorAll("[data-graph-type]").forEach((button) => {
      button.addEventListener("click", () => {
        graphType = button.dataset.graphType;
        document.querySelectorAll("[data-graph-type]").forEach((other) => {
          other.setAttribute("aria-pressed", other === button ? "true" : "false");
        });
        graphJob = "all";
        document.querySelectorAll("[data-graph-job]").forEach((other) => {
          other.setAttribute("aria-pressed", other.dataset.graphJob === "all" ? "true" : "false");
        });
        noteDots(data);
        drawGraphs(data);
        const panel = document.querySelector("[data-panel='compare']");
        if (panel && !panel.hidden) drawMap(document.getElementById("map-3d"), data, pointsOf(data).pts);
      });
    });
    document.querySelectorAll("[data-graph-job]").forEach((button) => {
      button.addEventListener("click", () => {
        graphJob = button.dataset.graphJob;
        graphType = "all";
        document.querySelectorAll("[data-graph-job]").forEach((other) => {
          other.setAttribute("aria-pressed", other === button ? "true" : "false");
        });
        document.querySelectorAll("[data-graph-type]").forEach((other) => {
          other.setAttribute("aria-pressed", other.dataset.graphType === "all" ? "true" : "false");
        });
        noteDots(data);
        drawGraphs(data);
        const panel = document.querySelector("[data-panel='compare']");
        if (panel && !panel.hidden) drawMap(document.getElementById("map-3d"), data, pointsOf(data).pts);
      });
    });
  });

  function noteDots(data) {
    const note = document.getElementById("graph-note");
    if (!note) return;
    const shown = visiblePrompts(data);
    if (graphJob === "all" && graphType === "all") {
      note.textContent = "One dot is one prompt. The tables under the charts stay the full run.";
      return;
    }
    note.textContent = `Dots are ${shown.length} prompts. The tables under the charts stay the full run.`;
  }

  function visiblePrompts(data) {
    return data.prompts.filter((p) => {
      if (graphType !== "all" && p.type && p.type !== graphType) return false;
      if (graphJob !== "all" && p.job && p.job !== graphJob) return false;
      if (graphJob !== "all" && !p.job) return false;
      return true;
    });
  }

  function showView(name, data) {
    document.querySelectorAll("[data-view]").forEach((button) => {
      button.setAttribute("aria-pressed", button.dataset.view === name ? "true" : "false");
    });
    document.querySelectorAll("[data-panel]").forEach((panel) => {
      panel.hidden = panel.dataset.panel !== name;
    });
    if (name === "compare" && data) {
      const { pts } = pointsOf(data);
      drawMap(document.getElementById("map-3d"), data, pts);
    }
  }

  function pointsOf(data) {
    const ids = data.ids.filter((id) => id !== "none");
    const index = Object.fromEntries(data.ids.map((id, i) => [id, i]));
    const prompts = visiblePrompts(data);
    const pts = [];
    for (const p of prompts) {
      for (const id of ids) {
        const i = index[id];
        pts.push({
          id, pid: p.id, grade: p.g[i],
          cost: Math.max(p.c[i], 0.001), time: Math.max(p.t[i], 1),
          searches: Math.max(p.q ? p.q[i] : 0, 0), fails: p.f ? p.f[i] : 0,
        });
      }
    }
    return { ids, pts };
  }

  function dot(p, data, x, y) {
    const wrong = p.grade === "w";
    const fill = wrong ? "none" : data.hex[p.id];
    return `<circle cx="${x}" cy="${y}" r="4.4" fill="${fill}" fill-opacity="0.85" stroke="${data.hex[p.id]}" stroke-width="${wrong ? 1.6 : 0}"><title>${data.labels[p.id]} · prompt ${p.pid}</title></circle>`;
  }

  function drawGraphs(data) {
    if (!data.hex) return;
    const { ids, pts } = pointsOf(data);
    if (!pts.length) return;
    strip(document.getElementById("graph-cost"), ids, data, pts, (p) => p.cost, [0.01, 0.02, 0.05, 0.1, 0.2, 0.4], (v) => "$" + v.toFixed(2), true, "Cost");
    strip(document.getElementById("graph-time"), ids, data, pts, (p) => p.time, [5, 10, 30, 60, 120, 180], (v) => (v >= 60 ? Math.round(v / 60) + "m" : v + "s"), true, "Time");
    strip(document.getElementById("graph-acc"), ids, data, pts, (p) => (p.grade === "c" ? 2 : p.grade === "p" ? 1 : 0), [0, 1, 2], (v) => ["Wrong", "Partial", "Correct"][v], false, "Accuracy");
    strip(document.getElementById("graph-fail"), ids, data, pts, (p) => p.fails, [0, 1, 2, 3, 5], (v) => String(v), false, "Failed tool calls");
    strip(document.getElementById("graph-search"), ids, data, pts, (p) => Math.max(p.searches, 0.5), [1, 2, 3, 5, 8, 12], (v) => String(v), true, "Searches");
    drawScatter(document.getElementById("scatter"), ids, data, pts);
    drawGradeScatter(document.getElementById("scatter-ca"), ids, data, pts);
  }

  function strip(host, ids, data, pts, valueOf, ticks, format, log, axisName) {
    if (!host) return;
    const W = 860, laneH = 58, L = 156, R = 20, T = 12, B = 40;
    const H = T + ids.length * laneH + B;
    const vals = pts.map(valueOf);
    const pos = log ? (v) => Math.log10(Math.max(v, 1e-4)) : (v) => v;
    const raw = vals.map(pos);
    const pad = (Math.max(...raw) - Math.min(...raw)) * 0.08 || 0.4;
    const x0 = Math.min(...raw) - pad, x1 = Math.max(...raw) + pad;
    const sx = (v) => L + ((pos(v) - x0) / (x1 - x0)) * (W - L - R);
    const shown = ticks.filter((v) => pos(v) > x0 && pos(v) < x1);
    const grid = shown.map((v) => `<line class="grid" x1="${sx(v)}" x2="${sx(v)}" y1="${T}" y2="${H - B}"/><text class="tick" x="${sx(v)}" y="${H - 16}" text-anchor="middle">${format(v)}</text>`).join("");
    const names = ids.map((id, i) => `<text class="name" x="${L - 12}" y="${T + i * laneH + laneH / 2 + 4}" text-anchor="end" fill="${data.hex[id]}">${data.labels[id]}</text>`).join("");
    const dots = pts.map((p) => {
      const li = ids.indexOf(p.id);
      const j = ((p.pid * 13 + p.id.length) % 21) - 10;
      return dot(p, data, sx(valueOf(p)), T + li * laneH + laneH / 2 + j);
    }).join("");
    host.innerHTML = `<svg viewBox="0 0 ${W} ${H}" role="img" aria-label="${axisName}, one dot per prompt"><style>.grid{stroke:var(--grid);stroke-dasharray:2 4} .tick{font:12px var(--sans);fill:var(--muted)} .name{font:12px var(--sans)} .axis{font:13px var(--sans);fill:var(--ink)}</style>${grid}${names}${dots}<text class="axis" x="${(W + L - R) / 2}" y="${H - 2}" text-anchor="middle">${axisName}</text></svg>`;
  }

  function legendBox(ids, data, x, y) {
    const row = 18, w = 210, h = 14 + ids.length * row + 22;
    const rows = ids.map((id, i) => `<circle cx="${x + 16}" cy="${y + 16 + i * row}" r="4.5" fill="${data.hex[id]}"/><text class="tick" x="${x + 28}" y="${y + 20 + i * row}">${data.labels[id]}</text>`).join("");
    const open = `<circle cx="${x + 16}" cy="${y + 16 + ids.length * row}" r="4.5" fill="none" stroke="var(--muted)" stroke-width="1.5"/><text class="tick" x="${x + 28}" y="${y + 20 + ids.length * row}">Hollow dot: wrong answer</text>`;
    return `<rect x="${x}" y="${y}" width="${w}" height="${h}" rx="8" fill="var(--panel)" stroke="var(--line)"/>${rows}${open}`;
  }

  function drawScatter(host, ids, data, pts) {
    if (!host) return;
    const W = 860, H = 520, L = 64, R = 246, T = 18, B = 48;
    const lx = pts.map((p) => Math.log10(p.cost));
    const ly = pts.map((p) => Math.log10(p.time));
    const pad = (a, b) => { const d = (b - a) * 0.08 || 0.1; return [a - d, b + d]; };
    const [x0, x1] = pad(Math.min(...lx), Math.max(...lx));
    const [y0, y1] = pad(Math.min(...ly), Math.max(...ly));
    const sx = (v) => L + ((Math.log10(v) - x0) / (x1 - x0)) * (W - L - R);
    const sy = (v) => T + (1 - (Math.log10(v) - y0) / (y1 - y0)) * (H - T - B);
    const inside = (v, a, b) => Math.log10(v) > a && Math.log10(v) < b;
    const costTicks = [0.01, 0.02, 0.05, 0.1, 0.2, 0.4].filter((v) => inside(v, x0, x1));
    const timeTicks = [5, 10, 30, 60, 120, 180].filter((v) => inside(v, y0, y1));
    const grid = [
      ...costTicks.map((v) => `<line class="grid" x1="${sx(v)}" x2="${sx(v)}" y1="${T}" y2="${H - B}"/><text class="tick" x="${sx(v)}" y="${H - B + 18}" text-anchor="middle">$${v.toFixed(2)}</text>`),
      ...timeTicks.map((v) => `<line class="grid" x1="${L}" x2="${W - R}" y1="${sy(v)}" y2="${sy(v)}"/><text class="tick" x="${L - 8}" y="${sy(v) + 4}" text-anchor="end">${v >= 60 ? Math.round(v / 60) + "m" : v + "s"}</text>`),
    ].join("");
    const dots = pts.map((p) => dot(p, data, sx(p.cost), sy(p.time))).join("");
    const legend = legendBox(ids, data, W - R + 16, T);
    host.innerHTML = `<svg viewBox="0 0 ${W} ${H}" role="img" aria-label="Cost and time, one dot per prompt"><style>.grid{stroke:var(--grid);stroke-dasharray:2 4} .tick{font:12px var(--sans);fill:var(--muted)} .axis{font:13px var(--sans);fill:var(--ink)}</style>${grid}<text class="axis" x="${(L + W - R) / 2}" y="${H - 8}" text-anchor="middle">Cost</text><text class="axis" x="16" y="${(T + H - B) / 2}" text-anchor="middle" transform="rotate(-90 16 ${(T + H - B) / 2})">Time</text>${dots}${legend}</svg>`;
  }

  function drawGradeScatter(host, ids, data, pts) {
    if (!host) return;
    const W = 860, H = 420, L = 84, R = 246, T = 18, B = 48;
    const lx = pts.map((p) => Math.log10(p.cost));
    const pad = (a, b) => { const d = (b - a) * 0.08 || 0.1; return [a - d, b + d]; };
    const [x0, x1] = pad(Math.min(...lx), Math.max(...lx));
    const sx = (v) => L + ((Math.log10(v) - x0) / (x1 - x0)) * (W - L - R);
    const sy = (g) => T + (1 - g / 2) * (H - T - B);
    const inside = (v) => Math.log10(v) > x0 && Math.log10(v) < x1;
    const costTicks = [0.01, 0.02, 0.05, 0.1, 0.2, 0.4].filter(inside);
    const grades = [["Wrong", 0], ["Partial", 1], ["Correct", 2]];
    const grid = [
      ...costTicks.map((v) => `<line class="grid" x1="${sx(v)}" x2="${sx(v)}" y1="${T}" y2="${H - B}"/><text class="tick" x="${sx(v)}" y="${H - B + 18}" text-anchor="middle">$${v.toFixed(2)}</text>`),
      ...grades.map(([label, g]) => `<line class="grid" x1="${L}" x2="${W - R}" y1="${sy(g)}" y2="${sy(g)}"/><text class="tick" x="${L - 8}" y="${sy(g) + 4}" text-anchor="end">${label}</text>`),
    ].join("");
    const dots = pts.map((p) => {
      const g = p.grade === "c" ? 2 : p.grade === "p" ? 1 : 0;
      const j = ((p.pid * 13 + p.id.length) % 17) - 8;
      return dot(p, data, sx(p.cost), sy(g) + j);
    }).join("");
    const legend = legendBox(ids, data, W - R + 16, T);
    host.innerHTML = `<svg viewBox="0 0 ${W} ${H}" role="img" aria-label="Cost and accuracy, one dot per prompt"><style>.grid{stroke:var(--grid);stroke-dasharray:2 4} .tick{font:12px var(--sans);fill:var(--muted)} .axis{font:13px var(--sans);fill:var(--ink)}</style>${grid}<text class="axis" x="${(L + W - R) / 2}" y="${H - 8}" text-anchor="middle">Cost</text><text class="axis" x="16" y="${(T + H - B) / 2}" text-anchor="middle" transform="rotate(-90 16 ${(T + H - B) / 2})">Accuracy</text>${dots}${legend}</svg>`;
  }

  function drawMap(canvas, data, pts) {
    if (!canvas) return;
    let yaw = -0.7, pitch = 0.42, drag = null;
    const cost = pts.map((p) => Math.log10(p.cost));
    const time = pts.map((p) => Math.log10(p.time));
    const acc = pts.map((p) => (p.grade === "c" ? 1 : p.grade === "p" ? 0.5 : 0));
    const bounds = (xs) => [Math.min(...xs), Math.max(...xs)];
    const [c0, c1] = bounds(cost), [t0, t1] = bounds(time);
    const norm = (v, a, b) => ((v - a) / (b - a || 1)) * 2 - 1;
    const ink = getComputedStyle(document.body).color || "#111";
    function frame() {
      const dpr = window.devicePixelRatio || 1;
      const w = canvas.clientWidth || 860;
      const h = 460;
      canvas.width = Math.round(w * dpr);
      canvas.height = Math.round(h * dpr);
      const ctx = canvas.getContext("2d");
      ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
      ctx.clearRect(0, 0, w, h);
      const cy = Math.cos(yaw), syaw = Math.sin(yaw), cp = Math.cos(pitch), sp = Math.sin(pitch);
      const drawn = pts.map((p, i) => {
        const x = norm(cost[i], c0, c1), y = acc[i] * 2 - 1, z = norm(time[i], t0, t1);
        const x1 = x * cy - z * syaw;
        const z1 = x * syaw + z * cy;
        const y1 = y * cp - z1 * sp;
        return { p, x: w * 0.46 + x1 * w * 0.28, y: h * 0.52 - y1 * h * 0.28, z: y * sp + z1 * cp };
      }).sort((a, b) => a.z - b.z);
      ctx.font = "13px IBM Plex Sans, system-ui, sans-serif";
      ctx.fillStyle = ink;
      ctx.fillText("Cost →     Time →     Accuracy ↑", 18, 26);
      for (const d of drawn) {
        ctx.beginPath();
        ctx.strokeStyle = data.hex[d.p.id];
        ctx.fillStyle = data.hex[d.p.id];
        ctx.lineWidth = 1.6;
        ctx.globalAlpha = 0.88;
        ctx.arc(d.x, d.y, 4.2, 0, Math.PI * 2);
        if (d.p.grade === "w") ctx.stroke();
        else ctx.fill();
      }
      ctx.globalAlpha = 1;
    }
    if (canvas.dataset.bound) { canvas._frame = frame; frame(); return; }
    canvas.dataset.bound = "1";
    canvas._frame = frame;
    canvas.addEventListener("pointerdown", (e) => { drag = { x: e.clientX, y: e.clientY, yaw, pitch }; canvas.setPointerCapture(e.pointerId); });
    canvas.addEventListener("pointermove", (e) => {
      if (!drag) return;
      yaw = drag.yaw + (e.clientX - drag.x) * 0.008;
      pitch = Math.max(-1.2, Math.min(1.2, drag.pitch + (e.clientY - drag.y) * 0.008));
      frame();
    });
    canvas.addEventListener("pointerup", () => { drag = null; });
    frame();
  }
}

if (typeof document !== "undefined") document.querySelectorAll(".sidetoc").forEach((toc) => {
  const links = [...toc.querySelectorAll("a[href^='#']")];
  const sections = links.map((a) => document.getElementById(a.getAttribute("href").slice(1))).filter(Boolean);
  if (!sections.length) return;
  const mark = (id) => links.forEach((a) => a.toggleAttribute("aria-current", a.getAttribute("href") === "#" + id));
  links.forEach((a) => a.addEventListener("click", () => mark(a.getAttribute("href").slice(1))));
  const seen = new IntersectionObserver((entries) => {
    const hit = entries.filter((e) => e.isIntersecting).sort((a, b) => a.boundingClientRect.top - b.boundingClientRect.top)[0];
    if (hit) mark(hit.target.id);
  }, { rootMargin: "-72px 0px -55% 0px", threshold: 0 });
  sections.forEach((section) => seen.observe(section));
  const top = sections.filter((section) => section.getBoundingClientRect().top < 120).pop();
  if (top) mark(top.id);
});
