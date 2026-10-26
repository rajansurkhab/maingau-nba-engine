/* Additional cockpit pages: Service 360°, Learn (deep dive A), Impact & compliance,
 * Architecture, and the capacity card on the steering page.
 * Loaded before app.js; uses its helpers ($, esc, api, eur, …) at call time. */
"use strict";

/* --- small SVG charts -------------------------------------------------------------------- */

function lineBandChart(points, { truth, yLabel = "", width = 640, height = 240 }) {
  const m = { l: 48, r: 16, t: 14, b: 36 };
  const w = width - m.l - m.r, h = height - m.t - m.b;
  const xMax = Math.max(...points.map((p) => p.responses), 1);
  const yMax = Math.max(...points.map((p) => p.high), truth ?? 0) * 1.15;
  const x = (v) => m.l + (v / xMax) * w;
  const y = (v) => m.t + h - (v / yMax) * h;
  const band = points.map((p) => `${x(p.responses)},${y(p.high)}`).join(" ") + " " +
    [...points].reverse().map((p) => `${x(p.responses)},${y(p.low)}`).join(" ");
  const line = points.map((p) => `${x(p.responses)},${y(p.mean)}`).join(" ");
  const yTicks = [0, 0.25, 0.5, 0.75, 1].map((f) => f * yMax);
  const xTicks = [0, 0.25, 0.5, 0.75, 1].map((f) => Math.round(f * xMax));
  return `
  <svg viewBox="0 0 ${width} ${height}" class="chart" role="img" aria-label="${esc(yLabel)}">
    ${yTicks.map((t) => `<line x1="${m.l}" x2="${m.l + w}" y1="${y(t)}" y2="${y(t)}" class="grid-line"/>
      <text x="${m.l - 8}" y="${y(t) + 4}" text-anchor="end" class="tick">${(t * 100).toFixed(0)}%</text>`).join("")}
    ${xTicks.map((t) => `<text x="${x(t)}" y="${m.t + h + 18}" text-anchor="middle" class="tick">${t}</text>`).join("")}
    <text x="${m.l + w / 2}" y="${height - 4}" text-anchor="middle" class="axis-label">responses observed</text>
    <polygon points="${band}" class="band"/>
    ${truth != null ? `<line x1="${m.l}" x2="${m.l + w}" y1="${y(truth)}" y2="${y(truth)}" class="truth"/>
      <text x="${m.l + w - 4}" y="${y(truth) - 6}" text-anchor="end" class="tick">true rate ${(truth * 100).toFixed(0)}%</text>` : ""}
    <polyline points="${line}" class="series"/>
    ${points.map((p) => `<circle cx="${x(p.responses)}" cy="${y(p.mean)}" r="8" class="hit"><title>${p.responses} responses: estimate ${(p.mean * 100).toFixed(1)}%, 90% interval ${(p.low * 100).toFixed(1)}–${(p.high * 100).toFixed(1)}%</title></circle>`).join("")}
  </svg>`;
}

function ciChart(rows, width = 1080) {
  const rowH = 30, m = { l: 190, r: 70, t: 22, b: 26 };
  const height = m.t + m.b + rows.length * rowH;
  const lo = Math.min(...rows.map((r) => r.ci_low), -0.02), hi = Math.max(...rows.map((r) => r.ci_high), 0.02);
  const w = width - m.l - m.r;
  const x = (v) => m.l + ((v - lo) / (hi - lo)) * w;
  const ticks = [lo, lo / 2, 0, hi / 2, hi];
  return `
  <svg viewBox="0 0 ${width} ${height}" class="chart" role="img" aria-label="Uplift with 95% confidence intervals">
    ${ticks.map((t) => `<text x="${x(t)}" y="${m.t - 8}" text-anchor="middle" class="tick">${t >= 0 ? "+" : ""}${(t * 100).toFixed(1)} pp</text>`).join("")}
    <line x1="${x(0)}" x2="${x(0)}" y1="${m.t - 2}" y2="${height - m.b + 4}" class="zero"/>
    ${rows.map((r, i) => {
      const cy = m.t + i * rowH + rowH / 2;
      return `<g>
        <text x="${m.l - 10}" y="${cy + 4}" text-anchor="end" class="${r.pooled ? "row-label strong" : "row-label"}">${esc(r.label)}</text>
        <line x1="${x(r.ci_low)}" x2="${x(r.ci_high)}" y1="${cy}" y2="${cy}" class="${r.pooled ? "ci strong" : "ci"}"/>
        <circle cx="${x(r.uplift)}" cy="${cy}" r="${r.pooled ? 6 : 4.5}" class="${r.pooled ? "est strong" : "est"}"><title>${esc(r.label)}: ${(r.uplift * 100).toFixed(1)} pp (95% CI ${(r.ci_low * 100).toFixed(1)} to ${(r.ci_high * 100).toFixed(1)} pp), control n = ${r.control_n}</title></circle>
        <text x="${width - m.r + 8}" y="${cy + 4}" class="tick">n=${r.control_n}</text>
      </g>`;
    }).join("")}
    <text x="${m.l + w / 2}" y="${height - 6}" text-anchor="middle" class="axis-label">uplift in conversion rate (treated − control), 95% CI</text>
  </svg>`;
}

/* --- service 360° ------------------------------------------------------------------------ */

async function showService(customerId) {
  setHeader("Customer 360°", "For service agents: status, value and the most favourable next action during the contact.");
  if (!state.allCustomers) {
    const all = await api("/api/decisions?limit=1000");
    state.allCustomers = all.items.map((d) => d.customer_id).sort();
  }
  const id = customerId || state.serviceCustomer || "MG-100009";
  state.serviceCustomer = id;
  $("#page").innerHTML = `
    <div class="filters">
      <input type="search" id="service-search" list="customer-ids" placeholder="Customer ID, e.g. MG-100009" value="${esc(id)}" aria-label="Customer ID">
      <datalist id="customer-ids">${state.allCustomers.map((c) => `<option value="${c}">`).join("")}</datalist>
      <button class="btn primary" data-action="service-open">Open</button>
      <span class="muted small">Demo:</span>
      ${state.meta.demo_cases.map((c) => `<button class="btn sm" data-action="service-demo" data-customer="${esc(c.customer_id)}" data-time="${esc(c.time)}" title="${esc(c.label)}">${esc(c.customer_id)}</button>`).join("")}
    </div>
    <div id="service-view"><div class="empty">Loading…</div></div>`;
  const c = await withLoading("Loading customer…", () => api(`/api/customers/${encodeURIComponent(id)}`));
  const { head, body } = customerParts(c, "service");
  $("#service-view").innerHTML = `<div class="card" style="padding:0"><div class="drawer-head" style="border-radius:var(--radius) var(--radius) 0 0">${head}</div><div class="drawer-body">${body}</div></div>`;
}

/* --- learn (deep dive A) ----------------------------------------------------------------- */

async function showLearn() {
  setHeader("Learn · deep dive A", "How reactions improve the engine, and how we prove that an action caused a result.");
  const L = await withLoading("Computing uplift and learning curve…", () => api("/api/learn"));
  const p = L.pooled;
  const rows = [{ label: "All offers (pooled)", pooled: true, ...p },
    ...L.by_offer.map((r) => ({ label: r.offer_name, ...r }))];
  const plan = L.ab_plan;
  const uplifts = [...new Set(plan.rows.map((r) => r.uplift))];
  const shares = [...new Set(plan.rows.map((r) => r.control_share))];
  const cell = (u, s) => plan.rows.find((r) => r.uplift === u && r.control_share === s);
  const lc = L.learning_curve;
  $("#page").innerHTML = `
    <div class="grid grid-3">
      <div class="card kpi"><div class="label">Uplift vs. control group</div><div class="value num">${p.uplift >= 0 ? "+" : ""}${(p.uplift * 100).toFixed(1)} pp</div><div class="hint">${pct(p.treated_rate, 1)} treated vs ${pct(p.control_rate, 1)} control · 95% CI ${(p.ci_low * 100).toFixed(1)} to ${(p.ci_high * 100).toFixed(1)} pp</div></div>
      <div class="card kpi"><div class="label">P(uplift &gt; 0)</div><div class="value num">${pct(p.prob_positive)}</div><div class="hint">Bayesian, Beta(1,1) priors · p-value ${p.p_value.toFixed(2)}</div></div>
      <div class="card kpi"><div class="label">Verdict</div><div class="value" style="font-size:18px">${p.significant ? "Proven" : "Not proven yet"}</div><div class="hint">${p.control_conv} conversions in ${p.control_n} control actions are too few. The A/B plan below fixes that.</div></div>
    </div>

    <div class="card">
      <div class="card-head"><h2>Causal effect of the legacy campaigns</h2><span class="muted small">randomised 10% hold-out per rule → treated vs. control is causal</span></div>
      ${ciChart(rows)}
      <div class="muted small">Per-offer control arms have about 20 actions each, so every per-offer interval crosses zero. That is why we do not train per-customer uplift models (X-learner) on this data: they would learn noise.</div>
    </div>

    <div class="grid grid-2">
      <div class="card">
        <div class="card-head"><h2>A/B plan to prove uplift</h2><span class="muted small">two-sided test, α = 5%, power 80%, baseline ${pct(plan.baseline, 1)}</span></div>
        <div class="table-wrap"><table>
          <thead><tr><th>Uplift to detect</th>${shares.map((s) => `<th class="right">${pct(s)} control</th>`).join("")}</tr></thead>
          <tbody>${uplifts.map((u) => `<tr><td><strong>+${(u * 100).toFixed(0)} pp</strong></td>${shares.map((s) => {
            const c = cell(u, s);
            return `<td class="right num" title="${num(c.n_control)} control + ${num(c.n_treated)} treated">${num(c.total)}<div class="muted small">${num(c.n_control)} control</div></td>`;
          }).join("")}</tr>`).join("")}</tbody>
        </table></div>
        <div class="muted small" style="margin-top:8px">Total actions needed. The mock data has ${num(plan.available_control)} control actions; at MAINGAU's scale one campaign wave is enough for +2 pp.</div>
      </div>
      <div class="card">
        <div class="card-head"><h2>The feedback loop</h2><span class="muted small">${esc(lc.offer_name)} via ${esc(channelLabel(lc.channel))}, simulated responses</span></div>
        ${lineBandChart(lc.points, { truth: lc.true_p, yLabel: "Acceptance estimate with 90% credible interval" })}
        <div class="muted small">Every response is one Bayesian update of the hierarchical Beta-Binomial model. The estimate starts at the sales prior (no history for this offer) and converges to the true rate as the interval narrows.</div>
      </div>
    </div>

    <div class="grid grid-2">
      <div class="card">
        <div class="card-head"><h2>Exploration (Thompson sampling)</h2><span class="chip ${L.policy.exploration === "thompson" ? "accent" : ""}">${L.policy.exploration === "thompson" ? "on" : "off"}</span></div>
        <p class="muted" style="margin-top:0">Ranks positive actions by a posterior draw instead of the point estimate, so uncertain offers get tried and learned. Hard rules and service actions are never affected, and nothing with negative expected value is proposed. The price is expected CLV today.</p>
        <div class="filters">
          <label class="control">Concentration <input type="number" id="concentration" min="1" max="256" step="1" value="${L.policy.concentration}" style="width:90px"></label>
          <button class="btn" data-action="exploration-cost">Measure the cost of learning</button>
          <button class="btn sm ghost" data-action="go" data-route="steering">Switch on in Steering</button>
        </div>
        <div id="exploration-result" class="muted small" style="margin-top:10px">1 = pure Thompson sampling; higher values explore less.</div>
      </div>
      <div class="card">
        <div class="card-head"><h2>Ready for off-policy evaluation</h2></div>
        <div class="kv small">
          <div class="k">Solver</div><div>${esc(L.policy.solver)}</div>
          <div class="k">Exploration</div><div>${esc(L.policy.exploration)}</div>
          <div class="k">Logged propensity</div><div>${L.policy.mean_propensity != null ? `mean ${Number(L.policy.mean_propensity).toFixed(2)} · ${Object.entries(L.policy.propensity_methods).map(([k, v]) => `${esc(k)}: ${v}`).join(", ")}` : "—"}</div>
          <div class="k">Also logged</div><div>p(treat) for the control split, sampled dCLV, channel shadow price, config version</div>
        </div>
        <p class="muted small">The legacy engine was deterministic, so its log cannot evaluate a new policy offline. Every decision we log now carries its propensity, which makes IPS / doubly-robust evaluation possible later.</p>
      </div>
    </div>`;
}

async function measureExplorationCost() {
  const k = Number($("#concentration").value) || 16;
  const r = await withLoading("Running the engine with exploration…", () => api("/api/learn/exploration-cost", { method: "POST", body: { concentration: k } }));
  $("#exploration-result").innerHTML = `At concentration <strong>${k}</strong>, exploration changes <strong>${num(r.changed_decisions)}</strong> decisions and costs <strong>${pct(r.cost_share, 1)}</strong> of expected CLV today (${eur(r.expected_gain_without)} → ${eur(r.expected_gain_with)}). Mean logged propensity ${Number(r.mean_propensity).toFixed(2)}.`;
}

/* --- impact & compliance ------------------------------------------------------------------ */

async function showImpact() {
  setHeader("Impact & compliance", "Zero violations, independently recomputed, and the value against the legacy rule engine.");
  const [audit, impact] = await withLoading("Auditing every decision and replaying the legacy engine…",
    () => Promise.all([api("/api/audit"), api("/api/impact")]));
  const c = impact.comparison, L = impact.legacy_audit;
  const ok = audit.violations === 0;
  const maxGain = Math.max(c.engine_expected_gain, c.legacy_expected_gain, 1);
  const rules = Object.entries(c.legacy_violations_by_rule).sort((a, b) => b[1] - a[1]).map(([rule, n]) => ({ rule, n }));
  $("#page").innerHTML = `
    <div class="grid grid-3">
      <div class="card kpi" style="border-color:${ok ? "var(--good)" : "var(--bad)"}"><div class="label">Hard-rule violations</div><div class="value num" style="color:${ok ? "var(--good)" : "var(--bad)"}">${num(audit.violations)}</div><div class="hint">in ${num(audit.decisions)} decisions (${num(audit.actions)} actions), recomputed from raw data</div></div>
      <div class="card kpi"><div class="label">Expected CLV gain vs. legacy</div><div class="value num">×${c.uplift_factor ? c.uplift_factor.toFixed(0) : "—"}</div><div class="hint">${eur(c.engine_expected_gain)} vs ${eur(c.legacy_expected_gain)} for the same ${num(c.customers)} customers</div></div>
      <div class="card kpi"><div class="label">Legacy actions breaking today's rules</div><div class="value num">${num(c.legacy_rule_violations_today)}</div><div class="hint">of ${num(c.customers)} last legacy actions · ours: 0</div></div>
    </div>

    <div class="grid grid-2">
      <div class="card">
        <div class="card-head"><h2>Compliance proof</h2><span class="chip ${ok ? "good" : "bad"}">${ok ? "all checks passed" : "violations found"}</span></div>
        <div class="trace">${audit.checks.map((ch) => `
          <div class="trace-row" style="grid-template-columns:22px 1fr auto"><span class="${ch.violations ? "no" : "ok"}">${ch.violations ? "✕" : "✓"}</span><span>${esc(ch.label)}</span><span class="num small">${ch.violations}</span></div>`).join("")}</div>
        <div class="muted small" style="margin-top:10px">Method: ${esc(audit.method)}. Decision time ${esc(audit.as_of)}. ${num(audit.approved)} actions approved so far.</div>
      </div>
      <div class="card">
        <div class="card-head"><h2>Same customers, same formula</h2><span class="muted small">each customer's last legacy action vs. our decision</span></div>
        <div class="bars">
          <div class="bar-row"><div class="lbl">Legacy rule engine</div><div class="bar-track"><div class="bar-fill" style="width:${(100 * Math.max(c.legacy_expected_gain, 0)) / maxGain}%;background:var(--muted)"></div></div><div class="val">${eur(c.legacy_expected_gain)}</div></div>
          <div class="bar-row"><div class="lbl">Next Best Action engine</div><div class="bar-track"><div class="bar-fill" style="width:${(100 * c.engine_expected_gain) / maxGain}%"></div></div><div class="val">${eur(c.engine_expected_gain)}</div></div>
        </div>
        <div class="kv small" style="margin-top:14px">
          <div class="k">Legacy actions with negative value</div><div>${num(c.legacy_negative_actions)}</div>
          <div class="k">Our action is equal or better</div><div>${pct(c.engine_better_or_equal_share)} of customers</div>
        </div>
        <div class="muted small" style="margin-top:8px">Legacy actions are valued with the population acceptance rate of their offer × channel × tone, as of ${esc(impact.as_of)}.</div>
      </div>
    </div>

    <div class="grid grid-2">
      <div class="card">
        <div class="card-head"><h2>Why legacy actions would be blocked today</h2></div>
        ${bars(rules, "n", "rule", num)}
      </div>
      <div class="card">
        <div class="card-head"><h2>Legacy engine audit</h2><span class="muted small">${num(L.actions_sent)} actions sent Jun–Sep</span></div>
        <div class="kv">
          <div class="k">Sent without consent check</div><div><strong>${num(L.without_consent_check)}</strong></div>
          <div class="k">Sales during open complaint</div><div>${num(L.sales_during_open_complaint)}</div>
          <div class="k">Sales after cancellation</div><div>${num(L.sales_after_cancellation)}</div>
          <div class="k">Consent not granted per table</div><div>${num(L.consent_not_granted)} <span class="muted small">(${num(L.consent_unverifiable)} unverifiable)</span></div>
        </div>
        <div class="muted small" style="margin-top:10px">${L.notes.map(esc).join("<br>")}</div>
      </div>
    </div>`;
}

/* --- architecture ------------------------------------------------------------------------- */

function archBox(x, y, w, h, title, lines, cls = "") {
  return `<g class="arch-box ${cls}"><rect x="${x}" y="${y}" width="${w}" height="${h}" rx="10"/>
    <text x="${x + 12}" y="${y + 22}" class="arch-title">${esc(title)}</text>
    ${lines.map((l, i) => `<text x="${x + 12}" y="${y + 42 + i * 17}" class="arch-line">${esc(l)}</text>`).join("")}</g>`;
}

function arrow(x1, y1, x2, y2, label = "") {
  const mx = (x1 + x2) / 2, my = (y1 + y2) / 2;
  return `<line x1="${x1}" y1="${y1}" x2="${x2}" y2="${y2}" class="arch-arrow" marker-end="url(#arrowhead)"/>
    ${label ? `<text x="${mx}" y="${my - 6}" text-anchor="middle" class="arch-label">${esc(label)}</text>` : ""}`;
}

function showArchitecture() {
  setHeader("Target architecture", "How this prototype runs on Databricks with MCP data access.");
  $("#page").innerHTML = `
    <div class="card">
      <svg viewBox="0 0 1180 560" class="chart arch" role="img" aria-label="Target architecture on Databricks with MCP">
        <defs><marker id="arrowhead" markerWidth="9" markerHeight="9" refX="8" refY="4.5" orient="auto"><path d="M0,0 L9,4.5 L0,9 z" class="arch-head"/></marker></defs>

        ${archBox(16, 40, 196, 300, "Source systems", ["CRM", "Billing ERP (IS-U)", "Telco backend", "Shopware 6", "HEMS cloud", "Autostrom + roaming hub", "Newsletter tool", "Ticket system", "Consent platform"])}

        <rect x="244" y="16" width="560" height="430" rx="14" class="arch-zone"/>
        <text x="262" y="40" class="arch-zone-title">Databricks Lakehouse · Unity Catalog</text>
        ${archBox(262, 56, 250, 112, "Delta: master view", ["Bronze → Silver → Gold", "one customer_id", "consent as row filter"], "data")}
        ${archBox(536, 56, 250, 112, "Event trigger", ["Structured Streaming", "consent / dunning / ticket", "events in minutes"], "data")}
        ${archBox(262, 196, 250, 132, "NBA engine job", ["nba_engine wheel", "eligibility → dCLV → LP", "batch + event-driven", "per-run config version"], "core")}
        ${archBox(536, 196, 250, 132, "MLflow", ["p_accept (Beta-Binomial)", "after-click intent model", "uplift reports, A/B plan", "model + data lineage"], "core")}
        ${archBox(262, 352, 524, 80, "Delta: decision_log (append-only)", ["inputs · rule trace · scores · reason · propensity · reviews · approvals"], "data")}

        ${archBox(836, 40, 328, 196, "MCP server (Databricks App)", ["recommend(customer_ids)", "customer_360(customer_id)", "explain_decision(customer_id)", "compliance_audit()", "impact_report() · get_config()", "no approve tool: humans only"], "mcp")}
        ${archBox(836, 262, 156, 112, "Marketing cockpit", ["Databricks App", "approve content,", "actions, settings"], "app")}
        ${archBox(1008, 262, 156, 112, "Service 360°", ["in the CRM", "agent assist", "during the call"], "app")}
        ${archBox(836, 400, 328, 72, "AI assistants via MCP", ['"Why did MG-100009 get a service fix?"'], "app")}
        ${archBox(836, 488, 328, 60, "Delivery: ESP · portal · app · call centre", ["only approved actions, control group held out"], "data")}

        ${arrow(212, 190, 262, 112, "ingest")}
        ${arrow(387, 168, 387, 196)}
        ${arrow(552, 168, 500, 196, "")}
        <text x="556" y="186" class="arch-label">trigger</text>
        ${arrow(536, 262, 512, 262)}
        ${arrow(387, 328, 387, 352)}
        ${arrow(786, 392, 836, 150, "read")}
        ${arrow(1000, 236, 914, 262)}
        ${arrow(1000, 236, 1086, 262)}
        ${arrow(1000, 400, 1000, 236)}
        ${arrow(914, 374, 914, 488, "approved")}

        <rect x="16" y="470" width="788" height="78" rx="12" class="arch-zone gov"/>
        <text x="32" y="496" class="arch-zone-title">Governance</text>
        <text x="32" y="518" class="arch-line">Hard rules before scoring · profiling consent gates personalisation · payment data only as exclusion · EU AI Act check before production</text>
        <text x="32" y="537" class="arch-line">Human in the loop: the engine proposes, marketing approves · every decision and review logged · 10% control group for uplift</text>
      </svg>
    </div>

    <div class="grid grid-2">
      <div class="card">
        <div class="card-head"><h2>From prototype to production</h2></div>
        <div class="table-wrap"><table>
          <thead><tr><th>Prototype today</th><th>On Databricks</th></tr></thead>
          <tbody>
            <tr><td>CSV / JSONL mock data</td><td>Delta tables in Unity Catalog</td></tr>
            <tr><td><span class="mono">loader.py</span> trap cleaning</td><td>Lakeflow / DLT pipeline with expectations</td></tr>
            <tr><td><span class="mono">nba_engine</span> package</td><td>Python wheel in a Databricks Job</td></tr>
            <tr><td>Event replay by ingestion time</td><td>Structured Streaming trigger</td></tr>
            <tr><td>Beta-Binomial + intent model</td><td>MLflow models, retrained on the decision log</td></tr>
            <tr><td><span class="mono">decision_log.jsonl</span></td><td>Delta table, append-only</td></tr>
            <tr><td>FastAPI + MCP server</td><td>Databricks App (MCP endpoint)</td></tr>
            <tr><td>Marketing cockpit (this page)</td><td>Databricks App</td></tr>
          </tbody>
        </table></div>
      </div>
      <div class="card">
        <div class="card-head"><h2>MCP tools</h2><span class="muted small">read-only by design</span></div>
        <div class="kv small">
          <div class="k mono">recommend</div><div>Current proposals with reason, channel, tone and expected gain</div>
          <div class="k mono">customer_360</div><div>Status, value, consents and every candidate with its block reason</div>
          <div class="k mono">explain_decision</div><div>Rule trace, scores and logged policy of one decision</div>
          <div class="k mono">compliance_audit</div><div>Independent re-check of every decision</div>
          <div class="k mono">impact_report</div><div>Value vs. the legacy rule engine</div>
          <div class="k mono">get_config</div><div>Current steering settings and history</div>
        </div>
        <p class="muted small">Approval stays a human action in the cockpit. An AI assistant can explain and analyse decisions, but it cannot approve or send anything.</p>
      </div>
    </div>`;
}

/* --- steering: capacity card (ML-Upgrade V3) ----------------------------------------------- */

async function loadCapacityCard() {
  try {
    const r = await api("/api/capacity");
    state.capacityHtml = `
      <div class="kv small">
        ${r.channels.map((c) => `<div class="k">${esc(c.label)}: ${num(c.used)} / ${num(c.capacity)} used</div><div>one more slot is worth <strong>€${Number(c.shadow_price).toFixed(2)}</strong> expected CLV</div>`).join("") || "<div class=\"k\">No capacity limits set</div><div></div>"}
        <div class="k">Greedy</div><div>${eur(r.greedy_objective)}</div>
        <div class="k">LP optimum</div><div>${eur(r.lp_objective)} <span class="chip good">+${eur(r.lp_gain)}</span> · ${num(r.changed_customers)} customers reassigned</div>
      </div>`;
  } catch (err) {
    state.capacityHtml = `<div class="muted small">Could not compute capacity report: ${esc(err.message)}</div>`;
  }
  const target = $("#capacity-card");
  if (target) target.innerHTML = state.capacityHtml;
}

const pageHandlers = {
  "service-open": () => { location.hash = `#/service/${encodeURIComponent($("#service-search").value.trim())}`; },
  "service-demo": async (el) => {
    if (el.dataset.time !== state.summary.time_key) await changeTime(el.dataset.time);
    location.hash = `#/service/${el.dataset.customer}`;
  },
  "exploration-cost": () => measureExplorationCost(),
  solver: (el) => { state.draft.solver = el.dataset.solver; renderSteering(); },
};
