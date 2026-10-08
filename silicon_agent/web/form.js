// New-run form (schema-driven), run actions (fork / re-run / delete), and the compare view.
// Loaded by app.js; shares its helpers through the exported `wire()` entry point.

export function wire({ $, api, post, esc, el, fmt, specs, state, select, refreshJobs, show }) {
  let schema = null, form = { design: "matmul", backend: "ecp5", task: "", constraints: {}, objective: [], locked: {}, initial_ir: {} };
  let estTimer = null;

  // ---------------------------------------------------------------- form
  async function initForm() {
    schema = await api("/schema");
    const ds = $("#nf-design"), bs = $("#nf-backend");
    ds.innerHTML = Object.entries(schema.designs).map(([k, v]) => `<option value="${k}">${k} · ${esc(v.title)}</option>`).join("");
    bs.innerHTML = Object.entries(schema.backends).map(([k, v]) => `<option value="${k}">${k} · ${esc(v)}</option>`).join("");
    ds.onchange = () => loadDefaults(ds.value, form.backend);
    bs.onchange = () => { form.backend = bs.value; renderKnobs(); estimate(); };
    $("#nf-task").oninput = (e) => { form.task = e.target.value; };
    $("#nf-cons").querySelectorAll("input").forEach((i) => { i.oninput = () => { const v = i.value === "" ? null : Number(i.value); if (v == null) delete form.constraints[i.dataset.c]; else form.constraints[i.dataset.c] = v; estimate(); }; });
    $("#nf-json-toggle").onclick = () => { const on = $("#nf-json").hidden; $("#nf-json").hidden = !on; $("#nf-form").hidden = on; $("#nf-json-toggle").textContent = on ? "edit as form ▸" : "edit as JSON ▸"; if (on) $("#spec").value = JSON.stringify(toSpec(), null, 2); };
    $("#nf-json-apply").onclick = () => { try { fromSpec(JSON.parse($("#spec").value)); $("#nf-json-msg").textContent = "applied"; $("#nf-json-toggle").click(); } catch (e) { $("#nf-json-msg").textContent = e.message; } };
    $("#submit").onclick = submit;
    // template specs seed the form; the first one is the default
    const tplRow = el(`<div class="frow wrap" id="nf-templates"></div>`);
    $("#nf-form .fcol .fsec").prepend(tplRow);
    tplRow.appendChild(el(`<span class="muted small-text">templates:</span>`));
    for (const sp of specs) { const b = el(`<button class="preset">${esc(sp.task)}</button>`); b.onclick = () => fromSpec(sp.spec); tplRow.appendChild(b); }
    fromSpec(specs[0]?.spec || { design: "matmul", backend: "ecp5" });
  }

  function loadDefaults(design, backend) {
    const d = schema.designs[design];
    fromSpec({ design, backend, task: "", constraints: { ...d.constraints_default, objective: ["maximize_throughput", "minimize_lut"] },
               locked: { ...d.locked_default }, initial_ir: { ...Object.values(d.presets)[0] } });
  }

  function fromSpec(spec) {
    form = { design: spec.design || "matmul", backend: spec.backend || "ecp5", task: spec.task || "",
             constraints: { ...spec.constraints }, objective: [...(spec.constraints?.objective || ["maximize_throughput", "minimize_lut"])],
             locked: { ...(spec.locked || {}) }, initial_ir: { ...(spec.initial_ir || {}) }, no_memory: !!spec.no_memory };
    delete form.constraints.objective;
    $("#nf-design").value = form.design; $("#nf-backend").value = form.backend; $("#nf-task").value = form.task;
    $("#nf-cons").querySelectorAll("input").forEach((i) => { i.value = form.constraints[i.dataset.c] ?? ""; });
    $("#usemem").checked = !form.no_memory;
    renderObjectives(); renderLocked(); renderPresets(); renderKnobs(); estimate();
    $("#nf-sub").textContent = `${form.design} · ${form.backend}`;
  }

  function toSpec() {
    const c = { ...form.constraints, objective: [...form.objective] };
    const spec = { task: form.task || `${form.design} · ${c.fmax_mhz} MHz`, design: form.design, backend: form.backend, locked: { ...form.locked }, constraints: c, initial_ir: { ...form.initial_ir } };
    if (!$("#usemem").checked) spec.no_memory = true;
    return spec;
  }

  function renderObjectives() {
    const ol = $("#nf-obj"); ol.innerHTML = "";
    const all = [...form.objective, ...schema.objectives.filter((o) => !form.objective.includes(o))];
    all.forEach((o) => {
      const on = form.objective.includes(o);
      const li = el(`<li class="${on ? "on" : ""}" draggable="true" data-o="${o}"><span class="grip">⋮⋮</span><span class="n">${on ? form.objective.indexOf(o) + 1 : "·"}</span><span>${o.replace("_", " ")}</span></li>`);
      li.onclick = () => { if (on) form.objective = form.objective.filter((x) => x !== o); else form.objective.push(o); renderObjectives(); estimate(); };
      li.ondragstart = (e) => { e.dataTransfer.setData("text/plain", o); li.classList.add("dragging"); };
      li.ondragend = () => li.classList.remove("dragging");
      li.ondragover = (e) => e.preventDefault();
      li.ondrop = (e) => { e.preventDefault(); const from = e.dataTransfer.getData("text/plain"); if (from === o || !form.objective.includes(from)) return; const arr = form.objective.filter((x) => x !== from); const idx = on ? arr.indexOf(o) : arr.length; arr.splice(idx, 0, from); form.objective = arr; renderObjectives(); };
      ol.appendChild(li);
    });
  }

  function renderLocked() {
    const box = $("#nf-locked"); box.innerHTML = "";
    const d = schema.designs[form.design];
    for (const [k, v] of Object.entries(form.locked)) {
      const chip = el(`<span class="lockchip">🔒 ${esc(k)} = ${esc(JSON.stringify(v))} <button title="unlock">×</button></span>`);
      chip.querySelector("button").onclick = () => { delete form.locked[k]; renderLocked(); renderKnobs(); };
      box.appendChild(chip);
    }
    const free = d.locked_candidates.filter((k) => !(k in form.locked));
    if (free.length) {
      const sel = el(`<select class="lockadd"><option value="">+ lock a field</option>${free.map((k) => `<option value="${k}">${k}</option>`).join("")}</select>`);
      sel.onchange = () => { if (sel.value) { form.locked[sel.value] = form.initial_ir[sel.value] ?? schema.designs[form.design].fields.find((f) => f.name === sel.value)?.choices?.[1] ?? 8; renderLocked(); renderKnobs(); } };
      box.appendChild(sel);
    }
  }

  function renderPresets() {
    const box = $("#nf-presets"); box.innerHTML = "";
    const d = schema.designs[form.design];
    for (const [name, ir] of Object.entries(d.presets)) {
      const b = el(`<button class="preset ${JSON.stringify({ ...ir, ...form.locked }) === JSON.stringify({ ...form.initial_ir, ...form.locked }) ? "on" : ""}">${esc(name)}</button>`);
      b.onclick = () => { form.initial_ir = { ...ir, ...form.locked }; renderPresets(); renderKnobs(); estimate(); };
      box.appendChild(b);
    }
    const best = state.jobs.filter((j) => j.best && (j.design || "matmul") === form.design && j.backend === form.backend)[0];
    if (best) { const b = el(`<button class="preset">best from memory (${fmt(best.best.metrics.fmax_mhz, 0)} MHz)</button>`); b.onclick = () => { form.initial_ir = { ...best.best.ir, ...form.locked }; delete form.initial_ir.coefs; renderPresets(); renderKnobs(); estimate(); }; box.appendChild(b); }
  }

  function renderKnobs() {
    const box = $("#nf-knobs"); box.innerHTML = "";
    const d = schema.designs[form.design];
    for (const f of d.fields) {
      if (f.backends && !f.backends.includes(form.backend)) continue;
      const locked = f.name in form.locked;
      const v = locked ? form.locked[f.name] : form.initial_ir[f.name];
      let input;
      if (f.type === "bool") input = `<select ${locked ? "disabled" : ""}><option value="true" ${v === true ? "selected" : ""}>true</option><option value="false" ${v === false ? "selected" : ""}>false</option></select>`;
      else if (f.type === "choice") input = `<select ${locked ? "disabled" : ""}>${f.choices.map((c) => `<option value="${c}" ${String(c) === String(v) ? "selected" : ""}>${c}</option>`).join("")}</select>`;
      else input = `<input type="number" min="${f.min}" max="${f.max}" value="${v ?? ""}" ${locked ? "disabled" : ""}>`;
      const row = el(`<div class="kr ${locked ? "locked" : ""}"><div class="kl"><span class="kk">${locked ? "🔒 " : ""}${esc(f.name)}</span><span class="kh" title="${esc(f.hint)}">${esc(f.hint)}</span></div>${input}</div>`);
      const ctl = row.querySelector("input, select");
      ctl.onchange = () => { let val = ctl.value; if (f.type === "bool") val = val === "true"; else if (f.type === "int" || typeof f.choices?.[0] === "number") val = Number(val); form.initial_ir[f.name] = val; renderPresets(); estimate(); };
      box.appendChild(row);
    }
  }

  function estimate() {
    clearTimeout(estTimer);
    estTimer = setTimeout(async () => {
      const box = $("#nf-est");
      try {
        const r = await post("/estimate", { spec: toSpec() });
        if (!r.valid) { box.innerHTML = `<span class="muted">screen estimate</span><span class="bad">${esc(r.errors.join("; "))}</span>`; $("#submit").disabled = true; return; }
        $("#submit").disabled = false;
        const e = r.estimate, v = r.est_violations;
        const bad = (k) => v.some((x) => x.startsWith(k));
        box.innerHTML = `<span class="muted">screen estimate${e.calibrated ? ` (calibrated on ${e.n_obs} runs)` : ""}</span><span class="${bad("fmax") ? "bad" : ""}">~${fmt(e.fmax_mhz, 0)} MHz</span><span class="${bad("dsp") ? "bad" : ""}">~${e.dsp} DSP</span><span class="${bad("lut") ? "bad" : ""}">~${e.lut} LUT</span><span class="muted">${v.length ? "violates " + v.map((x) => x.split("~")[0]).join(", ") + " — a good place to start the agent" : "looks feasible already; the agent will optimize from here"}</span>`;
      } catch (err) { box.innerHTML = `<span class="bad">${esc(err.message)}</span>`; }
    }, 250);
  }

  async function submit() {
    const msg = $("#submit-msg"); msg.className = "msg"; msg.textContent = "submitting…";
    try {
      const r = await post("/jobs", { spec: toSpec(), max_iter: Number($("#maxiter").value) });
      msg.textContent = `queued ${r.id}`;
      await refreshJobs(); select(r.id);
    } catch (e) { msg.className = "msg err"; msg.textContent = e.message; }
  }

  // ---------------------------------------------------------------- run actions
  function initActions() {
    const menu = $("#menu-list");
    $("#btn-more").onclick = (e) => { e.stopPropagation(); menu.hidden = !menu.hidden; };
    document.addEventListener("click", () => { menu.hidden = true; });
    $("#act-fork").onclick = () => {
      const d = state.detail; if (!d) return;
      const results = d.events.filter((e) => e.kind === "result");
      const best = d.summary?.best, c = d.spec.constraints;
      const dlg = $("#fork-dlg"), iters = $("#fk-iters"); iters.innerHTML = "";
      let pick = best ? null : results.length;  // null = best feasible
      const paint = () => iters.querySelectorAll("button").forEach((b) => b.classList.toggle("on", (b.dataset.i === "" ? null : Number(b.dataset.i)) === pick));
      if (best) { const b = el(`<button data-i="">best (feasible)</button>`); b.onclick = () => { pick = null; paint(); }; iters.appendChild(b); }
      for (const r of results) { const b = el(`<button data-i="${r.iteration}" title="${esc(r.violations.join("; ") || "feasible")}">${r.iteration} · ${fmt(r.metrics.fmax_mhz, 0)} MHz${r.violations.length ? " ✗" : " ✓"}</button>`); b.onclick = () => { pick = r.iteration; paint(); }; iters.appendChild(b); }
      paint();
      $("#fk-fmax").value = c.fmax_mhz; $("#fk-dsp").value = c.max_dsp ?? ""; $("#fk-lut").value = c.max_lut ?? ""; $("#fk-iter").value = d.max_iter || 8; $("#fk-task").value = (d.spec.task || "") + " (fork)";
      $("#fk-cancel").onclick = () => dlg.close();
      $("#fk-go").onclick = async () => {
        const over = { fmax_mhz: Number($("#fk-fmax").value) };
        if ($("#fk-dsp").value !== "") over.max_dsp = Number($("#fk-dsp").value);
        if ($("#fk-lut").value !== "") over.max_lut = Number($("#fk-lut").value);
        const body = { max_iter: Number($("#fk-iter").value), spec_overrides: { constraints: over }, task: $("#fk-task").value };
        if (pick != null) body.from_iteration = pick;
        dlg.close();
        const r = await post(`/jobs/${state.selected}/fork`, body);
        await refreshJobs(); select(r.id);
      };
      dlg.showModal();
    };
    $("#act-rerun").onclick = async () => { const d = state.detail; if (!d) return; const r = await post("/jobs", { spec: { ...d.spec, task: (d.spec.task || "") + " (re-run)" }, max_iter: d.max_iter || 8 }); await refreshJobs(); select(r.id); };
    $("#act-delete").onclick = async () => { if (!state.selected || !confirm("Delete this run and its artifacts?")) return; await api(`/jobs/${state.selected}`, { method: "DELETE" }); await refreshJobs(); show("empty"); };
    $("#act-compare").onclick = () => openCompare(state.selected);
    $("#cmp-back").onclick = () => { if (state.cmpA) select(state.cmpA); else show("empty"); };
  }

  // ---------------------------------------------------------------- compare
  async function openCompare(aId, bId) {
    state.cmpA = aId; state.cmpB = bId;
    show("compare");
    location.hash = `compare/${aId}${bId ? "/" + bId : ""}`;
    const body = $("#cmp-body"); body.innerHTML = "";
    const a = await api(`/jobs/${aId}`);
    const others = state.jobs.filter((j) => j.id !== aId);
    const pick = el(`<div class="cmp-pick"><span>A: <b>${esc(a.spec.task || aId)}</b></span><span>vs B:</span><select id="cmp-sel"><option value="">choose a run…</option>${others.map((j) => `<option value="${j.id}" ${j.id === bId ? "selected" : ""}>${esc(j.task || j.id)} · ${esc(j.backend)} · ${j.status}</option>`).join("")}</select></div>`);
    pick.querySelector("select").onchange = (e) => openCompare(aId, e.target.value || undefined);
    body.appendChild(pick);
    if (!bId) { body.appendChild(el(`<div class="muted">Pick a second run. Same spec with and without memory, or two targets for the same design, are the interesting pairs.</div>`)); return; }
    const b = await api(`/jobs/${bId}`);
    $("#cmp-sub").textContent = `${aId} vs ${bId}`;
    const rows = (d) => d.events.filter((e) => e.kind === "result");
    const ra = rows(a), rb = rows(b);
    const ba = a.summary?.best, bb = b.summary?.best;
    const unit = a.summary?.throughput_unit || "MAC/cycle";
    const win = (x, y, lower) => (x == null || y == null ? ["", ""] : lower ? [x < y ? "win" : "", y < x ? "win" : ""] : [x > y ? "win" : "", y > x ? "win" : ""]);
    const cell = (v, cls = "") => `<div class="v ${cls}">${v ?? "–"}</div>`;
    const lines = [["", `<div class="h">A · ${esc(a.spec.task || aId)}</div>`, `<div class="h">B · ${esc(b.spec.task || bId)}</div>`]];
    const add = (k, va, vb, cls = ["", ""]) => lines.push([`<div class="k">${k}</div>`, cell(va, cls[0]), cell(vb, cls[1])]);
    add("design / backend", `${a.spec.design || "matmul"} / ${a.spec.backend || "ecp5"}`, `${b.spec.design || "matmul"} / ${b.spec.backend || "ecp5"}`);
    add("constraints", `≥${a.spec.constraints.fmax_mhz} MHz · ≤${a.spec.constraints.max_dsp ?? "∞"} DSP · ≤${a.spec.constraints.max_lut ?? "∞"} LUT`, `≥${b.spec.constraints.fmax_mhz} MHz · ≤${b.spec.constraints.max_dsp ?? "∞"} DSP · ≤${b.spec.constraints.max_lut ?? "∞"} LUT`);
    add("memory", a.spec.no_memory ? "off" : "on", b.spec.no_memory ? "off" : "on");
    add("status", a.status, b.status);
    add("iterations", ra.length, rb.length, win(ra.length, rb.length, true));
    const feasA = ra.findIndex((r) => r.violations.length === 0) + 1 || null, feasB = rb.findIndex((r) => r.violations.length === 0) + 1 || null;
    add("first feasible at", feasA ?? "never", feasB ?? "never", win(feasA ?? 99, feasB ?? 99, true));
    add("EDA time", `${Math.round(ra.reduce((s, r) => s + r.eda_seconds, 0))} s`, `${Math.round(rb.reduce((s, r) => s + r.eda_seconds, 0))} s`);
    add("best fmax", ba ? `${fmt(ba.metrics.fmax_mhz)} MHz` : null, bb ? `${fmt(bb.metrics.fmax_mhz)} MHz` : null, win(ba?.metrics.fmax_mhz, bb?.metrics.fmax_mhz));
    add("best throughput", ba ? `${ba.throughput} ${unit}` : null, bb ? `${bb.throughput} ${unit}` : null, win(ba?.throughput, bb?.throughput));
    add("best LUT / DSP", ba ? `${ba.metrics.lut} / ${ba.metrics.dsp}` : null, bb ? `${bb.metrics.lut} / ${bb.metrics.dsp}` : null, win(ba?.metrics.lut, bb?.metrics.lut, true));
    add("best IR", ba ? `<span class="chips">${Object.entries(ba.ir).filter(([k]) => k !== "coefs" && !(k in (a.spec.locked || {}))).map(([k, v]) => `<span class="chip"><span class="k">${k}=</span>${JSON.stringify(v)}</span>`).join("")}</span>` : null, bb ? `<span class="chips">${Object.entries(bb.ir).filter(([k]) => k !== "coefs" && !(k in (b.spec.locked || {}))).map(([k, v]) => `<span class="chip"><span class="k">${k}=</span>${JSON.stringify(v)}</span>`).join("")}</span>` : null);
    const grid = el(`<div class="cmp-grid"></div>`);
    for (const [k, va, vb] of lines) { grid.insertAdjacentHTML("beforeend", k || "<div></div>"); grid.insertAdjacentHTML("beforeend", va); grid.insertAdjacentHTML("beforeend", vb); }
    body.appendChild(grid);
    // overlaid trajectories
    const ch = el(`<div><div class="cmp-legend"><span><i style="background:var(--accent)"></i>A</span><span><i style="background:var(--lut)"></i>B</span><span class="muted">hollow = violates</span></div><div class="cmp-charts"></div></div>`);
    body.appendChild(ch);
    for (const [title, get, target] of [["Fmax (MHz)", (r) => r.metrics.fmax_mhz, Math.max(a.spec.constraints.fmax_mhz, b.spec.constraints.fmax_mhz)], ["LUT", (r) => r.metrics.lut, null], ["DSP", (r) => r.metrics.dsp, null], [`Throughput (${unit})`, (r) => r.throughput, null]]) {
      const sec = el(`<div class="sec"><h3>${title}</h3><div class="chart"></div></div>`);
      ch.querySelector(".cmp-charts").appendChild(sec);
      requestAnimationFrame(() => overlay(sec.querySelector(".chart"), [ra, rb], get, target));
    }
    // side-by-side mini transcripts
    const mini = (rs) => `<div class="mini">${rs.map((r) => `<div class="t"><span class="n">${r.n ?? r.iteration}</span><div><div>${esc(r.action || "")}</div><div class="nums ${r.violations.length ? "bad" : ""}">${fmt(r.metrics.fmax_mhz)} MHz · ${r.metrics.lut} LUT · ${r.metrics.dsp} DSP${r.violations.length ? " · " + esc(r.violations.join("; ")) : " · feasible"}</div></div></div>`).join("")}</div>`;
    body.appendChild(el(`<div class="cmp-turns"><div class="col"><h4>A</h4>${mini(ra)}</div><div class="col"><h4>B</h4>${mini(rb)}</div></div>`));
  }

  function overlay(container, series, get, target) {
    container.innerHTML = "";
    const NS = "http://www.w3.org/2000/svg", svgEl = (t, at = {}) => { const e = document.createElementNS(NS, t); for (const [k, v] of Object.entries(at)) e.setAttribute(k, v); return e; };
    const W = container.clientWidth || 360, H = container.clientHeight || 180, L = 40, Rm = 12, T = 12, B = 22;
    const svg = svgEl("svg", { viewBox: `0 0 ${W} ${H}` }); container.appendChild(svg);
    const n = Math.max(...series.map((s) => s.length), 1), vals = series.flatMap((s) => s.map(get));
    if (!vals.length) return;
    const max = Math.max(target || 0, ...vals) * 1.12 || 1;
    const X = (i) => L + ((i + 0.5) / n) * (W - L - Rm), Y = (v) => H - B - (v / max) * (H - B - T);
    for (let i = 0; i <= 3; i++) { const v = (max * i) / 3; svg.appendChild(svgEl("line", { class: "grid", x1: L, x2: W - Rm, y1: Y(v), y2: Y(v), stroke: "var(--border-2)" })); const t = svgEl("text", { x: L - 5, y: Y(v) + 3, "text-anchor": "end", fill: "var(--muted)", "font-size": 10 }); t.textContent = max > 10 ? Math.round(v) : v.toFixed(2); svg.appendChild(t); }
    if (target) svg.appendChild(svgEl("line", { class: "target", x1: L, x2: W - Rm, y1: Y(target), y2: Y(target), stroke: "var(--text-2)", "stroke-dasharray": "4 4" }));
    series.forEach((s, si) => {
      const color = si === 0 ? "var(--accent)" : "var(--lut)";
      svg.appendChild(svgEl("path", { d: s.map((r, i) => `${i ? "L" : "M"}${X(i)},${Y(get(r))}`).join(" "), fill: "none", stroke: color, "stroke-width": 2 }));
      s.forEach((r, i) => svg.appendChild(svgEl("circle", { cx: X(i), cy: Y(get(r)), r: 4, fill: r.violations.length ? "var(--surface)" : color, stroke: color, "stroke-width": 2 })));
    });
    for (let i = 0; i < n; i++) { const t = svgEl("text", { x: X(i), y: H - 6, "text-anchor": "middle", fill: "var(--muted)", "font-size": 10 }); t.textContent = i + 1; svg.appendChild(t); }
  }

  return { initForm, initActions, openCompare };
}
