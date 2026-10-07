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
    const hlHtml = (cls, text) => (cls ? `<span class="${cls}">${text}</span>` : String(text));

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
  });
}
