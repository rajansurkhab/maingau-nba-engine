/* NBA Marketing Cockpit – human-in-the-loop UI for the MAINGAU Next Best Action engine.
 * Plain JS, no build step. Talks to the FastAPI backend under /api. */
"use strict";

const STATUS = {
  proposed: { label: "Awaiting review", cls: "warn" },
  approved: { label: "Approved", cls: "good" },
  rejected: { label: "Rejected", cls: "bad" },
  held_out: { label: "Control group · not sent", cls: "" },
  no_action: { label: "No action", cls: "" },
};
const TYPE_LABEL = { sales: "Sales", upsell: "Upsell", retention: "Retention", one_off: "Shop", service: "Service" };
// Plain-language explanations, shown as "?" tooltips next to technical terms.
const GLOSSARY = {
  gain: "Expected change in the customer's 10-year value (CLV) from this action: chance of acceptance × gain if accepted, plus the churn risk if rejected, minus the contact cost. Computed exactly as in MAINGAU's clv_method.",
  acceptance: "Chance that the customer accepts. Starts from past campaign results for this offer, channel and tone (control group excluded). With profiling consent, trigger signals and channel habits adjust it.",
  control: "About 10% of sales recommendations are held out at random and never sent. Comparing them with sent actions proves whether our actions cause the result (uplift).",
  time: "Snapshot = the data as of 30 Sep, 23:59. Replay = the same data plus the live events of 1 Oct that had arrived by then (consent changes, new tickets, cancellations, failed charges).",
  noaction: "No channel with consent, or no action with a positive expected gain. 'No action' is a logged decision too.",
  content: "Every message template needs marketing approval before any action that uses it can be approved.",
  clv: "Customer lifetime value: expected margin over the next 10 years after cost to serve, discounted, weighted by the churn risk.",
  churn: "Probability that the customer leaves within the next year.",
  campaign: "Proposals with the same offer, channel and tone share one message, so they can be reviewed together.",
};
const RULE_LABEL = {
  "R-CONSENT-CHANNEL": "No consent for the channel",
  "R-CHANNEL-REACHABLE": "Not reachable on the channel",
  "R-EXCL-DUNNING": "Dunning blocks sales",
  "R-EXCL-COMPLAINT": "Open complaint blocks sales",
  "R-EXCL-CANCELLATION": "Cancellation blocks sales",
  "R-PRODUCT-FIT": "Product held or prerequisite missing",
  "R-CONTACT-LIMIT": "Contact limit reached",
};
const CONSENT_LABEL = {
  newsletter_email: "newsletter", marketing_email: "marketing e-mail", marketing_sms: "SMS",
  marketing_phone: "phone", portal_app_personalisation: "portal and app", profiling_analytics: "profiling",
};
const EDITABLE = [
  "disabled_offers", "offer_priority", "disabled_channels", "channel_capacity", "allow_du_tone",
  "max_sales_contacts_30d", "control_share", "service_p_accept_prior", "allow_retention_on_cancellation",
  "reject_penalty_on_any_open_ticket", "signal_strength", "solver", "exploration", "exploration_concentration",
];
const SIGNAL_LABEL = {
  gas_meter_deregistered: "Gas meter deregistered",
  heat_pump_interest: "Heat-pump story clicks",
  heat_pump_device: "Heat pump in HEMS",
  wallbox_purchase: "Wallbox bought",
};

const state = {
  meta: null,
  summary: null,
  route: "overview",
  filters: { status: "proposed", q: "", offer: "", channel: "", segment: "", tone: "" },
  queueView: "customer",  // "customer" or "campaign" (offer × channel × tone)
  decisions: [],
  allDecisions: [],
  total: 0,
  limit: 50,
  selected: new Set(),
  templates: [],
  tplFilter: "pending",
  config: null,
  draft: null,
  configHistory: [],
  drawerCustomer: null,
};

/* --- helpers ----------------------------------------------------------------------------- */
const $ = (sel, root = document) => root.querySelector(sel);
const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const eurFmt = new Intl.NumberFormat("en-GB", { style: "currency", currency: "EUR", maximumFractionDigits: 0 });
const eur = (n) => (n === null || n === undefined || Number.isNaN(n) ? "—" : eurFmt.format(n));
const signedEur = (n) => (n === null || n === undefined ? "—" : (n >= 0 ? "+" : "−") + eurFmt.format(Math.abs(n)));
const pct = (n, d = 0) => (n === null || n === undefined ? "—" : `${(n * 100).toFixed(d)}%`);
const num = (n) => new Intl.NumberFormat("en-GB").format(n);
const channelLabel = (c) => state.meta?.channels.find((x) => x.key === c)?.label ?? c ?? "—";
const offerName = (id) => state.meta?.offers.find((o) => o.offer_id === id)?.name ?? id;
const reviewer = () => $("#reviewer").value.trim() || "marketing";
const tone = (t) => (t ? t[0].toUpperCase() + t.slice(1) : "—");
const statusChip = (s) => `<span class="chip ${STATUS[s]?.cls ?? ""}"><span class="dot"></span>${esc(STATUS[s]?.label ?? s)}</span>`;
const TEMPLATE_STATUS = { pending: ["Pending", "warn"], approved: ["Approved", "good"], rejected: ["Rejected", "bad"] };
const templateChip = (s) => `<span class="chip ${TEMPLATE_STATUS[s][1]}"><span class="dot"></span>${TEMPLATE_STATUS[s][0]}</span>`;
const typeChip = (t) => (t ? `<span class="chip ${t === "service" ? "accent" : ""}">${esc(TYPE_LABEL[t] ?? t)}</span>` : "");
const tip = (key) => `<span class="tip" tabindex="0" role="img" aria-label="${esc(GLOSSARY[key])}" data-tip="${esc(GLOSSARY[key])}">?</span>`;
const timeLabel = (key) => state.meta.decision_times.find((t) => t.key === key)?.label ?? key;
// "marketing_email=not_given" -> "No marketing e-mail consent (not given)"
const blockDetail = (s) => {
  const m = /^(\w+)=(\w+)$/.exec(s ?? "");
  return m && CONSENT_LABEL[m[1]] ? `No ${CONSENT_LABEL[m[1]]} consent (${m[2].replace("_", " ")})` : s;
};
const countBy = (rows, key) => Object.entries(rows.reduce((m, r) => ((m[r[key]] = (m[r[key]] || 0) + 1), m), {}))
  .sort((a, b) => b[1] - a[1]);

async function api(path, options = {}) {
  const res = await fetch(path, {
    headers: { "Content-Type": "application/json" },
    ...options,
    body: options.body ? JSON.stringify(options.body) : undefined,
  });
  if (!res.ok) {
    let detail = res.statusText;
    try { detail = (await res.json()).detail ?? detail; } catch { /* plain error */ }
    throw new Error(typeof detail === "string" ? detail : JSON.stringify(detail));
  }
  return res.json();
}

function toast(message, kind = "") {
  const el = document.createElement("div");
  el.className = `toast ${kind}`;
  el.textContent = message;
  $("#toasts").appendChild(el);
  setTimeout(() => el.remove(), kind === "bad" ? 6000 : 3500);
}

async function withLoading(message, fn) {
  $("#loading-msg").textContent = message;
  const timer = setTimeout(() => { $("#loading").hidden = false; }, 150);
  try { return await fn(); } finally { clearTimeout(timer); $("#loading").hidden = true; }
}

function bars(items, valueKey, labelKey, format) {
  if (!items.length) return `<div class="empty">No actions for this selection.</div>`;
  const max = Math.max(...items.map((i) => i[valueKey]), 1);
  return `<div class="bars">${items.map((i) => `
    <div class="bar-row" title="${esc(i[labelKey])}: ${esc(format(i[valueKey]))}">
      <div class="lbl">${esc(i[labelKey])}</div>
      <div class="bar-track"><div class="bar-fill" style="width:${(100 * i[valueKey]) / max}%"></div></div>
      <div class="val">${esc(format(i[valueKey]))}</div>
    </div>`).join("")}</div>`;
}

/* --- chrome ------------------------------------------------------------------------------ */
function setHeader(title, subtitle) {
  $("#page-title").textContent = title;
  $("#page-subtitle").textContent = subtitle ?? "";
  document.title = `${title} · NBA Marketing Cockpit`;
}

function updateChrome() {
  const k = state.summary.kpis;
  const custom = state.configHistory.length > 0 && state.configHistory.at(-1).changed[0] !== "reset";
  const chip = $("#config-chip");
  chip.textContent = custom ? "Custom settings" : "Default settings";
  chip.className = `chip ${custom ? "accent" : ""}`;
  chip.title = `Settings version ${state.summary.config_version}. Every decision stores it, so you can trace which settings produced it.`;
  const banner = $("#time-banner");
  banner.hidden = state.summary.time_key === "snapshot";
  banner.innerHTML = banner.hidden ? "" : `
    <span class="grow"><strong>Replay mode · ${esc(timeLabel(state.summary.time_key))}.</strong>
      You see what the engine knew at that moment: the snapshot of 30 Sep plus the live events of 1 Oct that had arrived by then.</span>
    <button class="btn sm" data-action="snapshot">Back to snapshot</button>`;
  const q = $("#nav-queue-count");
  q.hidden = !k.proposed; q.textContent = k.proposed;
  const c = $("#nav-content-count");
  c.hidden = !k.content_keys_needed; c.textContent = k.content_keys_needed;
  c.title = "Templates needed by current proposals that are not approved yet";
  document.querySelectorAll("#nav a").forEach((a) => a.classList.toggle("active", a.dataset.route === state.route));
}

async function refreshSummary() {
  state.summary = await api("/api/summary");
  updateChrome();
}

/* --- overview ---------------------------------------------------------------------------- */
async function showOverview() {
  state.allDecisions = (await api("/api/decisions?limit=1000")).items;
  renderOverview();
}

// What the engine does for one segment. Segments are only a view: every customer gets the
// action with the highest expected gain for them.
function segmentMix(segment) {
  const rows = state.allDecisions.filter((d) => d.segment === segment);
  const acted = rows.filter((d) => d.offer_id);
  const share = (pred) => (acted.length ? acted.filter(pred).length / acted.length : 0);
  const avg = (key) => (acted.length ? acted.reduce((sum, d) => sum + d[key], 0) / acted.length : 0);
  return {
    customers: rows.length, actions: acted.length,
    offers: countBy(acted, "offer_name"), channels: countBy(acted, "channel"),
    service: share((d) => d.action_type === "service"), retention: share((d) => d.action_type === "retention"),
    du: share((d) => d.tone === "du"), p: avg("p_accept"), gain: avg("dclv"),
    best: [...acted].sort((a, b) => b.dclv - a.dclv)[0],
  };
}

function topList(mix, key, n, label = (x) => x) {
  return mix[key].slice(0, n).map(([x, c]) => `<div>${esc(label(x))} <span class="muted">${pct(c / mix.actions)}</span></div>`).join("") || "—";
}

function renderOverview() {
  const s = state.summary, k = s.kpis;
  const proposals = k.proposed + k.approved + k.rejected;
  setHeader("Overview", `${num(k.customers)} customers scored · ${timeLabel(s.time_key)}`);
  const step = (n, cls, title, big, desc, route) => `
    <button class="step ${cls}" data-action="go" data-route="${route}">
      <span class="n">${n}</span>
      <span><div class="t">${title}</div><div class="big num">${big}</div><div class="d">${desc}</div></span>
    </button>`;
  const avgClv = Object.fromEntries(s.segments.map((g) => [g.segment, g.avg_clv]));
  const mixes = Object.fromEntries(state.meta.segments.map((g) => [g, segmentMix(g)]));
  const high = mixes["High value"], fresh = mixes["New customer"];
  const row = (label, a, b) => `<tr><td class="muted">${label}</td><td class="wrap">${a}</td><td class="wrap">${b}</td></tr>`;
  const example = (d) => (d ? `<button class="btn sm" data-action="open" data-customer="${esc(d.customer_id)}">${esc(d.customer_id)}</button>
    <span class="muted small">${esc(d.offer_name)} via ${esc(channelLabel(d.channel))}, ${esc(tone(d.tone))} · ${signedEur(d.dclv)}</span>` : "—");
  $("#page").innerHTML = `
    <div class="card headline">
      <div class="lead">For <strong>${num(k.customers)} customers</strong> the engine recommends <strong>${num(proposals + k.held_out)} actions</strong>
        worth <strong>${eur(k.expected_gain)}</strong> in expected customer value ${tip("gain")}</div>
      <div class="sub">Every action passed the hard rules (consent, dunning, complaints, cancellations, contact limits) before it was scored. Nothing is sent until marketing approves it.</div>
      <div class="chips">
        <span class="chip warn"><span class="dot"></span>${num(k.proposed)} awaiting your review</span>
        <span class="chip good"><span class="dot"></span>${num(k.approved)} approved · ${eur(k.approved_gain)}</span>
        ${k.rejected ? `<span class="chip bad"><span class="dot"></span>${num(k.rejected)} rejected</span>` : ""}
        <span class="chip"><span class="dot"></span>${num(k.held_out)} in the control group, not sent ${tip("control")}</span>
        <span class="chip"><span class="dot"></span>${num(k.no_action)} customers without action ${tip("noaction")}</span>
      </div>
    </div>

    <div class="workflow">
      ${step(1, "done", "Engine proposes", num(proposals), `actions with a positive expected gain · ${num(k.held_out)} more held out as control group`, "queue")}
      ${step(2, k.proposals_blocked_by_content ? "todo" : "done", "Approve content",
        k.proposals_blocked_by_content ? `${num(k.content_keys_needed)} templates` : "Done",
        k.proposals_blocked_by_content ? `needed by ${num(k.proposals_blocked_by_content)} proposals` : "all content used by proposals is approved", "content")}
      ${step(3, k.proposed ? "todo" : "done", "Review actions", num(k.proposed), "proposals awaiting your decision", "queue")}
      ${step(4, k.approved ? "done" : "", "Ready for delivery", num(k.approved), `approved · ${eur(k.approved_gain)} expected gain`, "log")}
    </div>

    <div class="grid grid-2">
      <div class="card">
        <div class="card-head"><h2>Expected gain by offer ${tip("gain")}</h2><span class="muted small">sum over all recommended actions</span></div>
        ${bars(s.by_offer, "gain", "label", eur)}
      </div>
      <div class="card">
        <div class="card-head"><h2>Actions by channel</h2><span class="muted small">number of recommended actions</span></div>
        ${bars(s.by_channel, "count", "label", num)}
      </div>
    </div>

    <div class="card">
      <div class="card-head"><h2>High-value vs. new customers</h2><span class="muted small">slide 6: a loyal high-value customer should not get the same action as someone who joined last week</span></div>
      <div class="table-wrap"><table class="compare">
        <thead><tr><th></th><th>High value</th><th>New customer</th></tr></thead>
        <tbody>
          ${row(`Customers · average CLV ${tip("clv")}`, `${num(high.customers)} · ${eur(avgClv["High value"])}`, `${num(fresh.customers)} · ${eur(avgClv["New customer"])}`)}
          ${row("Get an action", pct(high.actions / high.customers), pct(fresh.actions / fresh.customers))}
          ${row("Top actions", topList(high, "offers", 3), topList(fresh, "offers", 3))}
          ${row("Service first", pct(high.service), pct(fresh.service))}
          ${row("Renew price guarantee (retention)", pct(high.retention), pct(fresh.retention))}
          ${row("Main channels", topList(high, "channels", 3, channelLabel), topList(fresh, "channels", 3, channelLabel))}
          ${row("Addressed with Du", pct(high.du), pct(fresh.du))}
          ${row(`Average acceptance chance ${tip("acceptance")}`, pct(high.p), pct(fresh.p))}
          ${row(`Average expected gain per action ${tip("gain")}`, eur(high.gain), eur(fresh.gain))}
          ${row("Highest-value example", example(high.best), example(fresh.best))}
        </tbody>
      </table></div>
      <div class="muted small" style="margin-top:8px">Nobody is targeted by segment. Each customer gets the action with the highest expected gain for them, so the differences come from the value at stake, churn drivers, products held, consents and channel habits.</div>
    </div>

    <div class="card">
      <div class="card-head"><h2>Segments</h2><span class="muted small">CLV terciles; customers younger than one year are "New customer" · click a row to review its proposals</span></div>
      <div class="table-wrap"><table>
        <thead><tr><th>Segment</th><th class="right">Customers</th><th class="right">Avg CLV</th><th class="right">Actions</th><th class="right">Service first</th><th class="right">Retention</th><th class="right">Expected gain</th><th class="right">Avg gain / action</th><th>Top actions</th></tr></thead>
        <tbody>${s.segments.map((g) => `
          <tr class="clickable" data-action="segment" data-segment="${esc(g.segment)}">
            <td><strong>${esc(g.segment)}</strong></td>
            <td class="right num">${num(g.customers)}</td>
            <td class="right num">${eur(g.avg_clv)}</td>
            <td class="right num">${num(g.actions)}</td>
            <td class="right num">${g.actions ? pct(mixes[g.segment].service) : "—"}</td>
            <td class="right num">${g.actions ? pct(mixes[g.segment].retention) : "—"}</td>
            <td class="right num">${eur(g.expected_gain)}</td>
            <td class="right num">${eur(g.avg_gain)}</td>
            <td class="small">${topList(mixes[g.segment], "offers", 2)}</td>
          </tr>`).join("")}</tbody>
      </table></div>
    </div>

    <div class="card">
      <div class="card-head"><h2>Demo cases from the brief</h2><span class="muted small">opens the customer with the right decision time</span></div>
      <div class="grid grid-3">${state.meta.demo_cases.map((c) => `
        <button class="step" data-action="demo" data-customer="${esc(c.customer_id)}" data-time="${esc(c.time)}">
          <span class="n">${esc(c.customer_id.slice(-3))}</span>
          <span><div class="t">${esc(c.customer_id)}</div><div class="d">${esc(c.label)}</div></span>
        </button>`).join("")}
      </div>
    </div>`;
}

/* --- approval queue ---------------------------------------------------------------------- */
async function loadDecisions() {
  const f = state.filters;
  const params = new URLSearchParams({ limit: "1000" });
  if (f.status !== "all") params.set("status", f.status);
  for (const key of ["q", "offer", "channel", "segment"]) if (f[key]) params.set(key, f[key]);
  const data = await api(`/api/decisions?${params}`);
  // the API has no tone filter; tone is filtered here
  state.decisions = f.tone ? data.items.filter((d) => d.tone === f.tone) : data.items;
  state.total = f.tone ? state.decisions.length : data.total;
}

const gainBar = (value, max) => `<div class="inline-bar"><span class="num nowrap"><strong>${signedEur(value)}</strong></span><span class="track"><span class="fill" style="display:block;width:${(100 * Math.max(value, 0)) / max}%"></span></span></div>`;

function queueRow(d) {
  const reviewable = d.status === "proposed";
  const maxGain = Math.max(...state.decisions.map((x) => x.dclv ?? 0), 1);
  const sub = d.status === "held_out"
    ? "Recommended, but held out at random to measure uplift"
    : `${TYPE_LABEL[d.action_type] ?? ""}${d.signal ? ` · ${SIGNAL_LABEL[d.signal] ?? d.signal}` : ""}`;
  return `
    <tr class="clickable ${state.selected.has(d.decision_id) ? "selected" : ""}" data-action="open" data-customer="${esc(d.customer_id)}">
      <td data-stop="1"><input type="checkbox" data-action="select" data-id="${esc(d.decision_id)}" ${reviewable ? "" : "disabled"} ${state.selected.has(d.decision_id) ? "checked" : ""} aria-label="Select ${esc(d.customer_id)}"></td>
      <td><strong class="mono">${esc(d.customer_id)}</strong><div class="muted small">${esc(d.segment)}</div></td>
      <td class="offer-cell">
        ${d.offer_id ? `<strong>${esc(d.offer_name)}${d.overridden ? ' <span class="chip accent">changed</span>' : ""}</strong><div class="sub">${esc(sub)}</div>` : '<strong class="muted">No action</strong>'}
        <div class="reason-line" title="${esc(d.reason)}">${esc(d.reason)}</div>
      </td>
      <td>${d.channel ? `${esc(channelLabel(d.channel))}<div class="muted small">${esc(tone(d.tone))}</div>` : "—"}</td>
      <td class="right num">${pct(d.p_accept)}</td>
      <td class="right">${d.dclv != null ? gainBar(d.dclv, maxGain) : "—"}</td>
      <td>${statusChip(d.status)}${reviewable && d.offer_id && !d.content_approved ? '<div class="small warn-text">message not approved yet</div>' : ""}</td>
    </tr>`;
}

// Proposals that share offer, channel and tone also share one message: one campaign.
function campaigns() {
  const groups = new Map();
  for (const d of state.decisions) {
    if (!d.offer_id) continue;
    const key = `${d.offer_id}|${d.channel}|${d.tone}`;
    if (!groups.has(key)) groups.set(key, { key, offer_id: d.offer_id, offer_name: d.offer_name, action_type: d.action_type, channel: d.channel, tone: d.tone, rows: [] });
    groups.get(key).rows.push(d);
  }
  return [...groups.values()].map((g) => ({
    ...g,
    gain: g.rows.reduce((sum, d) => sum + d.dclv, 0),
    p: g.rows.reduce((sum, d) => sum + d.p_accept, 0) / g.rows.length,
    pending: g.rows.filter((d) => d.status === "proposed"),
    template: state.templates.find((t) => t.key === `${g.offer_id}:${g.tone}`),
  })).sort((a, b) => b.gain - a.gain);
}

function campaignTable() {
  const list = campaigns();
  if (!list.length) return `<div class="table-wrap"><div class="empty">Nothing here. Try another filter.</div></div>`;
  const maxGain = Math.max(...list.map((g) => g.gain), 1);
  return `<div class="table-wrap"><table>
    <thead><tr><th>Campaign ${tip("campaign")}</th><th>Channel · tone</th><th class="right">Customers</th><th class="right">Acceptance chance ${tip("acceptance")}</th><th class="right">Expected gain ${tip("gain")}</th><th>Message</th><th class="right">Review</th></tr></thead>
    <tbody>${list.map((g) => {
      const t = g.template, contentOk = t?.status === "approved";
      return `<tr>
        <td class="offer-cell"><strong>${esc(g.offer_name)}</strong><div class="sub">${esc(TYPE_LABEL[g.action_type] ?? "")}</div></td>
        <td>${esc(channelLabel(g.channel))}<div class="muted small">${esc(tone(g.tone))}</div></td>
        <td class="right num">${num(g.rows.length)}${g.pending.length !== g.rows.length ? `<div class="muted small">${num(g.pending.length)} awaiting review</div>` : ""}</td>
        <td class="right num">${pct(g.p)}</td>
        <td class="right">${gainBar(g.gain, maxGain)}</td>
        <td class="wrap msg-cell">${t ? `<details><summary>${templateChip(t.status)} <strong>${esc(t.subject_en)}</strong></summary>
          <div class="small" style="margin-top:6px">${esc(t.body_en)}</div>
          <div class="small muted" style="margin-top:6px">Sent in German: <strong>${esc(t.subject_de)}</strong> · ${esc(t.body_de)}</div></details>` : "—"}</td>
        <td class="right nowrap">
          ${g.pending.length && !contentOk && t && !t.issues.length ? `<button class="btn sm" data-action="campaign-content" data-key="${esc(t.key)}">Approve message</button>` : ""}
          ${g.pending.length ? `<button class="btn sm good" data-action="campaign-review" data-key="${esc(g.key)}" data-status="approved" ${contentOk ? "" : 'disabled title="Approve the message first"'}>Approve ${num(g.pending.length)}</button>
            <button class="btn sm bad" data-action="campaign-review" data-key="${esc(g.key)}" data-status="rejected">Reject</button>` : ""}
          <button class="btn sm ghost" data-action="campaign-show" data-key="${esc(g.key)}">Customers</button>
        </td>
      </tr>`;
    }).join("")}</tbody>
  </table></div>`;
}

function renderQueue() {
  const f = state.filters, k = state.summary.kpis;
  const byCampaign = state.queueView === "campaign";
  setHeader("Approval queue", "Review what the engine proposes. Only approved actions may be delivered.");
  const statuses = [["proposed", `Awaiting review (${k.proposed})`], ["approved", `Approved (${k.approved})`],
    ["rejected", `Rejected (${k.rejected})`], ["held_out", `Control group (${k.held_out})`], ["no_action", `No action (${k.no_action})`], ["all", "All"]];
  const visible = state.decisions.slice(0, state.limit);
  const blocked = state.decisions.filter((d) => d.status === "proposed" && d.offer_id && !d.content_approved).length;
  $("#page").innerHTML = `
    <div class="filters">
      <div class="segmented" role="tablist">${statuses.map(([key, label]) => `<button class="${f.status === key ? "on" : ""}" data-action="status" data-status="${key}">${esc(label)}</button>`).join("")}</div>
      <span class="grow"></span>
      <div class="segmented" aria-label="Group proposals">${[["customer", "By customer"], ["campaign", "By campaign"]].map(([key, label]) => `<button class="${state.queueView === key ? "on" : ""}" data-action="view" data-view="${key}">${label}</button>`).join("")}</div>
    </div>
    <div class="filters">
      <input type="search" id="q" placeholder="Search customer ID or reason…" value="${esc(f.q)}" aria-label="Search">
      <select data-filter="offer" aria-label="Offer"><option value="">All offers</option>${state.meta.offers.map((o) => `<option value="${esc(o.offer_id)}" ${f.offer === o.offer_id ? "selected" : ""}>${esc(o.name)}</option>`).join("")}</select>
      <select data-filter="channel" aria-label="Channel"><option value="">All channels</option>${state.meta.channels.map((c) => `<option value="${esc(c.key)}" ${f.channel === c.key ? "selected" : ""}>${esc(c.label)}</option>`).join("")}</select>
      <select data-filter="tone" aria-label="Tone"><option value="">Du and Sie</option>${["sie", "du"].map((t) => `<option value="${t}" ${f.tone === t ? "selected" : ""}>${tone(t)}</option>`).join("")}</select>
      <select data-filter="segment" aria-label="Segment"><option value="">All segments</option>${state.meta.segments.map((s) => `<option ${f.segment === s ? "selected" : ""}>${esc(s)}</option>`).join("")}</select>
      <span class="muted small">${num(state.total)} decisions${byCampaign ? ` in ${num(campaigns().length)} campaigns` : ""}</span>
    </div>
    ${blocked ? `<div class="banner"><span class="grow">${num(blocked)} of these proposals use a message that is not approved yet. Approve the message first, then the actions${byCampaign ? " (button in each row)" : ""}.</span>${byCampaign ? "" : '<button class="btn sm" data-action="go" data-route="content">Go to content approval</button>'}</div>` : ""}
    ${byCampaign ? campaignTable() : `
    <div class="table-wrap"><table>
      <thead><tr>
        <th><input type="checkbox" data-action="select-all" aria-label="Select all visible proposals"></th>
        <th>Customer</th><th>Recommended action and reason</th><th>Channel · tone</th><th class="right">Acceptance chance ${tip("acceptance")}</th><th class="right">Expected gain ${tip("gain")}</th><th>Status</th>
      </tr></thead>
      <tbody>${visible.length ? visible.map(queueRow).join("") : `<tr><td colspan="7"><div class="empty">Nothing here. Try another filter.</div></td></tr>`}</tbody>
    </table></div>
    ${state.decisions.length > state.limit ? `<div style="text-align:center"><button class="btn" data-action="more">Show more (${num(state.decisions.length - state.limit)} left)</button></div>` : ""}
    ${renderBulkbar()}`}`;
}

function renderBulkbar() {
  if (!state.selected.size) return "";
  return `<div class="bulkbar" role="region" aria-label="Bulk review">
    <strong class="grow">${num(state.selected.size)} selected</strong>
    <button class="btn good" data-action="bulk" data-status="approved">Approve</button>
    <button class="btn bad" data-action="bulk" data-status="rejected">Reject</button>
    <button class="btn ghost" data-action="clear">Clear</button>
  </div>`;
}

async function showQueue() {
  const [, templates] = await Promise.all([loadDecisions(), state.queueView === "campaign" ? api("/api/templates") : null]);
  if (templates) state.templates = templates;
  renderQueue();
}

async function reviewCampaign(key, status) {
  const g = campaigns().find((x) => x.key === key);
  const what = `${g.offer_name} via ${channelLabel(g.channel)} (${tone(g.tone)})`;
  if (!window.confirm(`${status === "approved" ? "Approve" : "Reject"} ${g.pending.length} actions: ${what}? Each review is logged with your name.`)) return;
  const res = await withLoading("Saving reviews…", () => api("/api/decisions/review", {
    method: "POST", body: { decision_ids: g.pending.map((d) => d.decision_id), status, actor: reviewer(), note: `campaign review: ${what}` },
  }));
  const failed = Object.keys(res.failed).length;
  toast(`${res.done.length} ${status}: ${what}${failed ? ` · ${failed} skipped` : ""}`, failed ? "bad" : "");
  await refreshSummary();
  await showQueue();
}

async function bulkReview(status) {
  const ids = [...state.selected];
  const res = await withLoading("Saving reviews…", () => api("/api/decisions/review", {
    method: "POST", body: { decision_ids: ids, status, actor: reviewer() },
  }));
  state.selected.clear();
  const failed = Object.keys(res.failed).length;
  toast(`${res.done.length} ${status}${failed ? ` · ${failed} skipped (content not approved)` : ""}`, failed ? "bad" : "");
  await refreshSummary();
  await showQueue();
}

/* --- drawer (customer + decision) -------------------------------------------------------- */
async function openCustomer(customerId) {
  state.drawerCustomer = customerId;
  const data = await withLoading("Loading customer…", () => api(`/api/customers/${encodeURIComponent(customerId)}`));
  renderDrawer(data);
}

function closeDrawer() {
  state.drawerCustomer = null;
  $("#drawer-root").innerHTML = "";
}

/* Customer view used by the marketing drawer and the service 360° page.
 * mode "marketing" adds the review box; "service" puts the customer status first and
 * shows talking points instead. */
function customerParts(c, mode = "marketing") {
  const d = c.decision;
  const service = mode === "service";
  const flags = [];
  if (c.flags.open_complaint) flags.push('<span class="chip bad">Open complaint · sales blocked</span>');
  else if (c.flags.open_service_case) flags.push('<span class="chip warn">Open service case</span>');
  if (c.flags.dunning) flags.push('<span class="chip bad">Dunning · sales blocked</span>');
  if (c.flags.cancellation_received) flags.push('<span class="chip bad">Cancellation received · sales blocked</span>');
  if (!c.profiling_allowed) flags.push('<span class="chip">No profiling consent · population rates only</span>');
  c.value.drivers.forEach((x) => flags.push(`<span class="chip">Churn driver: ${esc(x)}</span>`));

  const reviewable = d.status === "proposed";
  const contentOk = d.content?.status === "approved";
  const alternatives = (d.alternatives || []).filter((a) => !(a.offer_id === d.offer_id && a.channel === d.channel));

  const head = `
        <div class="grow">
          <h2 class="mono" style="font-size:18px">${esc(c.customer_id)}</h2>
          <div class="muted small">${esc(c.segment)} · ${esc(c.city)} · customer since ${esc(c.since)} · household of ${c.household_size} · address as <strong>${esc(tone(c.tone))}</strong> (${esc(c.tone_evidence)})</div>
          <div class="chips">${flags.join("")}</div>
        </div>`;
  const policy = d.inputs?.policy;
  const kpis = `
        <div class="grid grid-3">
          <div class="card kpi"><div class="label">CLV (10 years) ${tip("clv")}</div><div class="value num">${eur(c.value.clv)}</div></div>
          <div class="card kpi"><div class="label">Churn risk / year ${tip("churn")}</div><div class="value num">${pct(c.value.churn)}</div></div>
          <div class="card kpi"><div class="label">Annual margin</div><div class="value num">${eur(c.value.annual_margin)}</div><div class="hint">cost to serve ${eur(c.value.cost_to_serve)}</div></div>
        </div>`;

  const action = `
        <div class="action-card">
          ${d.offer_id ? `
          <div class="top">
            <div>
              <div class="muted small">${service ? "Next best action for this contact" : "Recommended action"}${d.overridden ? " · changed by marketing" : ""}</div>
              <h2 style="font-size:20px;margin-top:2px">${esc(d.offer_name)}</h2>
              <div style="margin-top:6px" class="chips">${typeChip(d.action_type)}<span class="chip">${service ? "planned outbound:" : "via"} ${esc(channelLabel(d.channel))}</span><span class="chip">${service ? "address as " : ""}${esc(tone(d.tone))}</span>${statusChip(d.status)}</div>
            </div>
            <div class="gain"><div class="muted small">Expected gain ${tip("gain")}</div><div class="v num ${d.dclv < 0 ? "neg" : ""}">${signedEur(d.dclv)}</div></div>
          </div>
          ${d.control_group ? `<div class="banner info" style="margin-top:12px"><span class="grow"><strong>Control group: not sent.</strong> The engine recommends this action, but the customer was picked at random (about 10% of sales recommendations) to measure whether our actions cause the result.</span></div>` : ""}
          ${d.original ? `
          <div class="reason">Changed by marketing. The engine recommended <strong>${esc(d.original.offer_name)}</strong> via ${esc(channelLabel(d.original.channel))} (${signedEur(d.original.dclv)}).
            <div class="muted small" style="margin-top:4px">Engine reason: ${esc(d.original.reason)}</div></div>`
          : `<div class="reason">${esc(d.reason)}</div>`}
          ${service ? "" : `
          <div class="breakdown">
            <div class="cell"><div class="k">Acceptance chance ${tip("acceptance")}</div><div class="v num">${pct(d.p_accept)}</div><div class="muted small">history ${pct(d.p_accept_base, 1)}</div></div>
            <div class="cell"><div class="k">Value if accepted</div><div class="v num">${eur(d.clv_accept)}</div></div>
            <div class="cell"><div class="k">Value if rejected</div><div class="v num">${eur(d.clv_reject)}</div></div>
            <div class="cell"><div class="k">Contact cost</div><div class="v num">€${(d.contact_cost ?? 0).toFixed(2)}</div></div>
          </div>
          <div class="formula">Expected gain = chance × (value if accepted − value now) + (1 − chance) × (value if rejected − value now) − contact cost · value now = ${eur(d.clv_now)}</div>`}
          ` : `<h2>No action</h2><div class="reason">${esc(d.reason)}</div>`}
        </div>`;

  const message = d.content ? `
        <div class="message">
          <div class="msg-head"><strong>${service ? "Talking points for this conversation" : "Message preview"}</strong><span class="chips" style="margin:0">${d.content.status === "approved" ? '<span class="chip good">Message approved</span>' : `<span class="chip warn">Message ${esc(d.content.status)}</span>`}</span></div>
          <div class="msg-body"><div class="subj">${esc(d.content.subject_en)}</div><div>${esc(d.content.body_en)}</div><div class="muted small" style="margin-top:6px">English translation for review</div></div>
          <details><summary>Customer-facing text (German, required by slide 12)</summary><div class="de"><div class="subj">${esc(d.content.subject_de)}</div>${esc(d.content.body_de)}</div></details>
        </div>` : "";

  const guidance = service && d.offer_id ? `
        <div class="banner info"><span class="grow">${d.control_group ? "Control group: do not offer this. It is held out to measure the effect of our actions."
          : d.status === "approved" ? "Approved by marketing: you may offer this in the conversation."
          : d.action_type === "service" ? "Service action: solve the issue first, no sales content."
          : "Not approved by marketing yet: mention it only if the customer asks."}</span></div>` : "";

  const others = c.candidates.filter((x) => x.eligible && x.dclv > 0 && x.offer_id !== d.offer_id).slice(0, 3);
  const ranked = service && others.length ? `
        <div class="card">
          <div class="card-head"><h2>Other favourable actions</h2><span class="muted small">ranked by expected gain · every hard rule passed</span></div>
          <div class="alt-list">${others.map((x, i) => `
            <div class="alt"><span class="muted num">${i + 2}.</span>
              <div class="grow"><strong>${esc(x.offer_name)}</strong> <span class="muted">via ${esc(channelLabel(x.channel))}</span><div class="muted small">${esc(TYPE_LABEL[x.action_type] ?? "")} · acceptance chance ${pct(x.p_accept)}</div></div>
              <span class="num"><strong>${signedEur(x.dclv)}</strong></span></div>`).join("")}</div>
        </div>` : "";

  const tickets = c.tickets.length ? c.tickets.map((t) => `
            <div class="small" style="margin-bottom:6px"><strong>${esc(t.created_at.slice(0, 10))}</strong> · ${esc(t.category)} via ${esc(t.channel)} ·
              ${t.status === "open" ? '<span class="chip warn">open</span>' : esc(t.status)}${t.message ? `<div class="muted">Original customer message (German source data): “${esc(t.message)}”</div>` : ""}</div>`).join("")
    : '<div class="muted small">No service cases.</div>';
  const products = [
    ...c.contracts.energy.map((e) => `<div class="k">${esc(e.commodity)}</div><div>${esc(e.tariff)} · ${esc(e.status)}${e.price_guarantee_until ? ` · price guarantee until ${esc(e.price_guarantee_until.slice(0, 10))}` : ""}</div>`),
    ...c.contracts.mobile.map((m) => `<div class="k">Mobile</div><div>${esc(m.plan)} · ${esc(m.status)} · ${m.avg_data_used_gb_3m} of ${m.data_volume_gb} GB used</div>`),
    ...c.contracts.autostrom.map((a) => `<div class="k">Autostrom</div><div>${esc(a.status)} · ${esc(a.car_brand)} ${esc(a.car_model)}</div>`),
    ...c.contracts.hems.map((h) => `<div class="k">HEMS device</div><div>${esc(h.device_type)} · ${esc(h.connection_status)}</div>`),
  ].join("");
  const statusInner = `
          <div class="status-grid">
            <div><h3>Service cases</h3>${tickets}</div>
            <div><h3>Products and contracts</h3>${products ? `<div class="kv">${products}</div>` : '<div class="muted small">No active products.</div>'}</div>
            <div><h3>Consents</h3><div class="chips">${Object.entries(c.consents).map(([k, v]) => `<span class="chip ${v === "granted" ? "good" : v === "withdrawn" ? "bad" : ""}">${esc(CONSENT_LABEL[k] ?? k)}: ${esc(v.replace("_", " "))}</span>`).join("")}</div></div>
            ${c.signals.length || c.events.length ? `<div>
              ${c.signals.length ? `<h3>Trigger signals</h3>${c.signals.map((s) => `<div class="small">${esc(s.label)}: ${esc(s.evidence)} ${s.used ? '<span class="chip good">used</span>' : '<span class="chip">not used · no profiling consent</span>'}</div>`).join("")}` : ""}
              ${c.events.length ? `<h3 style="margin-top:10px">Live events known at decision time</h3>${c.events.map((e) => `<div class="small mono">${esc(e.occurred_at)} · ${esc(e.event_type)}</div>`).join("")}` : ""}
            </div>` : ""}
          </div>`;
  const status = service
    ? `<div class="card"><div class="card-head"><h2>Status at a glance</h2><span class="muted small">products, contracts, open cases, consents</span></div>${statusInner}</div>`
    : `<details class="panel"><summary>Customer status</summary><div class="inner">${statusInner}</div></details>`;

  const review = reviewable && !service ? `
        <div class="card review-box">
          <div class="section-title"><h3>Your decision</h3><span class="muted small">reviewer: ${esc(reviewer())}</span></div>
          ${!contentOk ? `<div class="banner"><span class="grow">The message template is not approved yet, so this action cannot be approved.</span><button class="btn sm" data-action="approve-template" data-key="${esc(d.content.key)}">Approve this content</button></div>` : ""}
          <textarea id="review-note" rows="2" placeholder="Optional note for the log…" aria-label="Review note"></textarea>
          <div class="review-actions">
            <button class="btn good" data-action="review" data-id="${esc(d.decision_id)}" data-status="approved" ${contentOk ? "" : "disabled"}>Approve action</button>
            <button class="btn bad" data-action="review" data-id="${esc(d.decision_id)}" data-status="rejected">Reject</button>
          </div>
          ${alternatives.length ? `
          <div>
            <h3 style="margin:8px 0">Or approve an alternative instead</h3>
            <div class="alt-list">${alternatives.map((a) => `
              <div class="alt">
                <div class="grow"><strong>${esc(a.offer_name)}</strong> <span class="muted">via ${esc(a.channel_label)}</span><div class="muted small">p(accept) ${pct(a.p_accept)} · every hard rule passed${a.content_approved ? "" : " · content not approved"}</div></div>
                <span class="num"><strong>${signedEur(a.dclv)}</strong></span>
                <button class="btn sm" data-action="override" data-id="${esc(d.decision_id)}" data-offer="${esc(a.offer_id)}" data-channel="${esc(a.channel)}" ${a.content_approved ? "" : "disabled"}>Approve this instead</button>
              </div>`).join("")}</div>
          </div>` : ""}
        </div>` : ""}

        <details class="panel" open>
          <summary>Why not the other offers?</summary>
          <div class="inner"><div class="table-wrap"><table>
            <thead><tr><th>Offer</th><th>Allowed</th><th>Blocked by</th><th>Best channel</th><th class="right">p(accept)</th><th class="right">Expected gain</th></tr></thead>
            <tbody>${c.candidates.map((x) => `
              <tr><td>${esc(x.offer_name)} <span class="muted small">${esc(TYPE_LABEL[x.action_type] ?? "")}</span></td>
                <td>${x.eligible ? '<span class="ok">✓</span>' : '<span class="no">✕</span>'}</td>
                <td class="small wrap">${x.blocked_detail ? esc(x.blocked_detail) : '<span class="muted">—</span>'}</td>
                <td>${esc(channelLabel(x.channel))}</td>
                <td class="right num">${pct(x.p_accept)}</td>
                <td class="right num" style="color:${x.dclv < 0 ? "var(--bad)" : "inherit"}">${signedEur(x.dclv)}</td></tr>`).join("")}
            </tbody></table></div>
            <div class="muted small" style="margin-top:8px">Blocked candidates are scored too: a negative value is the economic reason behind the hard rule.</div>
          </div>
        </details>

        <details class="panel">
          <summary>Customer status</summary>
          <div class="inner grid">
            <div class="kv">
              ${c.contracts.energy.map((e) => `<div class="k">${esc(e.commodity)}</div><div>${esc(e.tariff)} · ${esc(e.status)}${e.price_guarantee_until ? ` · price guarantee until ${esc(e.price_guarantee_until.slice(0, 10))}` : ""}</div>`).join("")}
              ${c.contracts.mobile.map((m) => `<div class="k">Mobile</div><div>${esc(m.plan)} · ${esc(m.status)} · ${m.avg_data_used_gb_3m} of ${m.data_volume_gb} GB used</div>`).join("")}
              ${c.contracts.autostrom.map((a) => `<div class="k">Autostrom</div><div>${esc(a.status)} · ${esc(a.car_brand)} ${esc(a.car_model)}</div>`).join("")}
              ${c.contracts.hems.map((h) => `<div class="k">HEMS device</div><div>${esc(h.device_type)} · ${esc(h.connection_status)}</div>`).join("")}
            </div>
            ${c.signals.length ? `<div><h3 style="margin-bottom:6px">Trigger signals</h3>${c.signals.map((s) => `<div class="small">${esc(s.label)}: ${esc(s.evidence)} ${s.used ? '<span class="chip good">used</span>' : '<span class="chip">not used · no profiling consent</span>'}</div>`).join("")}</div>` : ""}
            <div><h3 style="margin-bottom:6px">Consents</h3><div class="chips">${Object.entries(c.consents).map(([k, v]) => `<span class="chip ${v === "granted" ? "good" : v === "withdrawn" ? "bad" : ""}">${esc(k.replaceAll("_", " "))}: ${esc(v.replace("_", " "))}</span>`).join("")}</div></div>
            ${c.tickets.length ? `<div><h3 style="margin-bottom:6px">Service cases</h3>${c.tickets.map((t) => `<div class="small" style="margin-bottom:6px"><strong>${esc(t.created_at.slice(0, 10))}</strong> · ${esc(t.category)} via ${esc(t.channel)} · ${esc(t.status)}${t.message ? `<div class="muted">Original customer message (German source data): “${esc(t.message)}”</div>` : ""}</div>`).join("")}</div>` : ""}
            ${c.events.length ? `<div><h3 style="margin-bottom:6px">Live events ingested by decision time</h3>${c.events.map((e) => `<div class="small mono">${esc(e.occurred_at)} · ${esc(e.event_type)}</div>`).join("")}</div>` : ""}
          </div>
        </details>

        ${d.rule_trace?.length ? `
        <details class="panel">
          <summary>Rule trace of the chosen action</summary>
          <div class="inner trace">${d.rule_trace.map((r) => `<div class="trace-row"><span class="${r.passed ? "ok" : "no"}">${r.passed ? "✓" : "✕"}</span><span class="mono">${esc(r.rule)}</span><span class="small">${esc(r.detail)}</span></div>`).join("")}</div>
        </details>` : ""}

        <details class="panel">
          <summary>Log entry</summary>
          <div class="inner kv small">
            <div class="k">Decision ID</div><div class="mono">${esc(d.decision_id)}</div>
            <div class="k">Decision time</div><div>${esc(d.as_of)}</div>
            <div class="k">Config version</div><div class="mono">${esc(d.config_version)}</div>
            <div class="k">Notes</div><div>${esc((d.notes || []).join("; ") || "—")}</div>
            <div class="k">Review history</div><div>${d.history.length ? d.history.map((h) => `${esc(h.ts)} · ${esc(h.actor)} · ${esc(h.status)}${h.note ? ` · “${esc(h.note)}”` : ""}`).join("<br>") : "not reviewed yet"}</div>
            ${policy ? `<div class="k">Policy</div><div>solver ${esc(policy.solver)} · exploration ${esc(policy.exploration)}${policy.propensity != null ? ` · propensity ${Number(policy.propensity).toFixed(2)} (${esc(policy.propensity_method)})` : ""}${policy.channel_shadow_price != null ? ` · channel shadow price ${eur(policy.channel_shadow_price)}` : ""}</div>` : ""}
          </div>
        </details>`;
  return { head, body };
}

function renderDrawer(c) {
  const { head, body } = customerParts(c, "marketing");
  $("#drawer-root").innerHTML = `
    <div class="scrim" data-action="close"></div>
    <aside class="drawer" role="dialog" aria-modal="true" aria-label="Customer ${esc(c.customer_id)}">
      <div class="drawer-head">${head}<button class="btn ghost" data-action="close" aria-label="Close">✕</button></div>
      <div class="drawer-body">${body}</div>
    </aside>`;
}

async function reviewDecision(id, status, override = null) {
  const note = $("#review-note")?.value ?? "";
  await withLoading("Saving review…", () => api(`/api/decisions/${id}/review`, {
    method: "POST", body: { status, actor: reviewer(), note, override },
  }));
  toast(override ? `Approved alternative: ${offerName(override.offer_id)}` : `Decision ${status}`);
  await refreshSummary();
  if (state.drawerCustomer) await openCustomer(state.drawerCustomer);
  if (state.route === "queue") await showQueue();
}

/* --- content approval -------------------------------------------------------------------- */
async function showContent() {
  const [templates, proposals] = await Promise.all([api("/api/templates"), api("/api/decisions?status=proposed&limit=1000")]);
  state.templates = templates;
  state.usage = {};
  for (const d of proposals.items) {
    if (!d.offer_id) continue;
    const key = `${d.offer_id}:${d.tone}`;
    state.usage[key] = (state.usage[key] || 0) + 1;
  }
  renderContent();
}

function renderContent() {
  setHeader("Content approval", "Every customer message needs marketing approval before any action using it can be approved.");
  const f = state.tplFilter;
  const counts = { pending: 0, approved: 0, rejected: 0 };
  state.templates.forEach((t) => { counts[t.status] += 1; });
  const list = state.templates
    .filter((t) => f === "all" || t.status === f)
    .sort((a, b) => (state.usage[b.key] || 0) - (state.usage[a.key] || 0));
  const cleanPending = state.templates.filter((t) => t.status === "pending" && !t.issues.length).length;
  const check = (ok, label) => `<span class="chip ${ok ? "good" : "bad"}">${ok ? "✓" : "✕"} ${label}</span>`;
  $("#page").innerHTML = `
    <div class="banner info"><span class="grow">Messages are checked automatically: no prices or discounts, no artificial urgency, and the wording must match the tone (Du or Sie). Customers receive the German text; the English translation is for review.</span>
      <button class="btn sm primary" data-action="approve-clean" ${cleanPending ? "" : "disabled"}>Approve all ${cleanPending} clean pending templates</button></div>
    <div class="filters"><div class="segmented">${[["pending", `Pending (${counts.pending})`], ["approved", `Approved (${counts.approved})`], ["rejected", `Rejected (${counts.rejected})`], ["all", "All"]]
      .map(([k, l]) => `<button class="${f === k ? "on" : ""}" data-action="tpl-filter" data-filter="${k}">${l}</button>`).join("")}</div></div>
    <div class="tpl-grid">${list.length ? list.map((t) => {
      const issues = t.issues.join(" ");
      return `
      <div class="card tpl">
        <div class="head">
          <div><strong>${esc(t.offer_name)}</strong><div class="muted small mono">${esc(t.offer_id)}</div></div>
          <div class="chips" style="margin:0"><span class="chip accent">${esc(tone(t.tone))}</span>${templateChip(t.status)}</div>
        </div>
        <div class="checks">${check(!/price/.test(issues), "No prices")}${check(!/urgency/.test(issues), "No urgency")}${check(!/wording/.test(issues), "Tone matches")}</div>
        <div><div class="subj"><strong>${esc(t.subject_en)}</strong></div><div class="body">${esc(t.body_en)}</div></div>
        <details><summary>Customer-facing text (German)</summary><div class="body" style="margin-top:6px"><strong>${esc(t.subject_de)}</strong><br>${esc(t.body_de)}</div></details>
        <div class="foot">
          <span class="grow muted small">${state.usage[t.key] ? `used by ${num(state.usage[t.key])} proposals` : "not used by current proposals"}</span>
          ${t.status !== "approved" ? `<button class="btn sm good" data-action="tpl-review" data-key="${esc(t.key)}" data-status="approved" ${t.issues.length ? "disabled" : ""}>Approve</button>` : ""}
          ${t.status !== "rejected" ? `<button class="btn sm bad" data-action="tpl-review" data-key="${esc(t.key)}" data-status="rejected">Reject</button>` : ""}
          ${t.status !== "pending" ? `<button class="btn sm ghost" data-action="tpl-review" data-key="${esc(t.key)}" data-status="pending">Reset</button>` : ""}
        </div>
      </div>`;
    }).join("") : `<div class="empty">No templates with this status.</div>`}</div>`;
}

async function reviewTemplate(key, status) {
  await api(`/api/templates/${encodeURIComponent(key)}/review`, { method: "POST", body: { status, actor: reviewer() } });
  await refreshSummary();
}

/* --- steering ---------------------------------------------------------------------------- */
async function showSteering() {
  const [cfg, all] = await Promise.all([api("/api/config"), api("/api/decisions?limit=1000")]);
  state.config = cfg.config;
  state.configHistory = cfg.history;
  updateChrome();
  state.draft = structuredClone(Object.fromEntries(EDITABLE.map((k) => [k, cfg.config[k]])));
  state.actionCounts = { offer: {}, channel: {} };
  for (const d of all.items) {
    if (!d.offer_id || d.status === "no_action") continue;
    state.actionCounts.offer[d.offer_id] = (state.actionCounts.offer[d.offer_id] || 0) + 1;
    state.actionCounts.channel[d.channel] = (state.actionCounts.channel[d.channel] || 0) + 1;
  }
  state.capacityHtml = null;
  renderSteering();
  loadCapacityCard();  // LP comparison takes a few seconds on a fresh run; the page stays usable
}

function changedKeys() {
  const norm = (v) => JSON.stringify(Array.isArray(v) ? [...v].sort() : v && typeof v === "object" ? Object.fromEntries(Object.entries(v).sort()) : v);
  return EDITABLE.filter((k) => norm(state.draft[k]) !== norm(state.config[k]));
}

function sw(id, checked, action, extra = "") {
  return `<label class="switch"><input type="checkbox" id="${id}" data-action="${action}" ${extra} ${checked ? "checked" : ""}><span></span></label>`;
}

function renderSteering() {
  setHeader("Steering", "Set offers, priorities, channels, tone and contact limits. Applying re-runs the engine for all customers.");
  const d = state.draft;
  const changed = changedKeys();
  const offers = state.meta.offers;
  $("#page").innerHTML = `
    <div class="grid grid-2">
      <div class="card">
        <div class="card-head"><h2>Offers and priorities</h2><span class="muted small">priority reorders, it never makes a negative action positive</span></div>
        <div class="table-wrap"><table>
          <thead><tr><th>Offer</th><th>On</th><th style="width:210px">Priority weight</th><th class="right">Actions now</th></tr></thead>
          <tbody>${offers.map((o) => {
            const prio = d.offer_priority[o.offer_id] ?? 1;
            return `<tr>
              <td><strong>${esc(o.name)}</strong> <span class="muted small">${esc(TYPE_LABEL[o.type] ?? o.type)}</span></td>
              <td>${sw(`off-${o.offer_id}`, !d.disabled_offers.includes(o.offer_id), "offer-toggle", `data-offer="${esc(o.offer_id)}"`)}</td>
              <td><div class="range-row"><input type="range" min="0.1" max="3" step="0.1" value="${prio}" data-action="priority" data-offer="${esc(o.offer_id)}" aria-label="Priority for ${esc(o.name)}"><span class="num">×${Number(prio).toFixed(1)}</span></div></td>
              <td class="right num">${num(state.actionCounts.offer[o.offer_id] || 0)}</td>
            </tr>`;
          }).join("")}</tbody>
        </table></div>
      </div>

      <div class="grid" style="align-content:start">
        <div class="card">
          <div class="card-head"><h2>Channels</h2><span class="muted small">approve channels and set capacity per run</span></div>
          <div class="table-wrap"><table>
            <thead><tr><th>Channel</th><th>Approved</th><th>Max actions per run</th><th class="right">Actions now</th></tr></thead>
            <tbody>${state.meta.channels.map((c) => `<tr>
              <td><strong>${esc(c.label)}</strong></td>
              <td>${sw(`ch-${c.key}`, !d.disabled_channels.includes(c.key), "channel-toggle", `data-channel="${esc(c.key)}"`)}</td>
              <td><input type="number" min="0" step="1" placeholder="unlimited" value="${d.channel_capacity[c.key] ?? ""}" data-action="capacity" data-channel="${esc(c.key)}" style="width:110px" aria-label="Capacity for ${esc(c.label)}"></td>
              <td class="right num">${num(state.actionCounts.channel[c.key] || 0)}</td></tr>`).join("")}</tbody>
          </table></div>
        </div>

        <div class="card">
          <div class="card-head"><h2>Tone and contact policy</h2></div>
          <div class="setting"><div><strong>Approve "Du" tone</strong><div class="help">Off: every customer is addressed with "Sie".</div></div>${sw("du", d.allow_du_tone, "bool", 'data-key="allow_du_tone"')}</div>
          <div class="setting"><div><strong>Max sales contacts per customer in 30 days</strong><div class="help">Fatigue limit. Service actions are not limited.</div></div><input type="number" min="0" max="20" value="${d.max_sales_contacts_30d}" data-action="number" data-key="max_sales_contacts_30d" style="width:80px" aria-label="Max sales contacts"></div>
          <div class="setting"><div><strong>Control group share</strong><div class="help">Share of sales decisions held out to measure uplift.</div></div><div class="range-row"><input type="range" min="0" max="0.3" step="0.01" value="${d.control_share}" data-action="range" data-key="control_share" aria-label="Control group share"><span class="num">${pct(d.control_share)}</span></div></div>
        </div>
      </div>
    </div>

    <div class="grid grid-2">
      <div class="card">
        <div class="card-head"><h2>Model assumptions</h2><span class="muted small">see assumptions A13, A15, A17 in the planning doc</span></div>
        <div class="setting"><div><strong>p(accept) of a proactive service fix</strong><div class="help">No history exists for service actions.</div></div><div class="range-row"><input type="range" min="0.05" max="0.95" step="0.05" value="${d.service_p_accept_prior}" data-action="range" data-key="service_p_accept_prior" aria-label="Service acceptance prior"><span class="num">${pct(d.service_p_accept_prior)}</span></div></div>
        <div class="setting"><div><strong>Allow retention offer after cancellation</strong><div class="help">Open question 5 for the mentors. Off = blocked like other sales content.</div></div>${sw("ret", d.allow_retention_on_cancellation, "bool", 'data-key="allow_retention_on_cancellation"')}</div>
        <div class="setting"><div><strong>Sales contact during any open ticket raises churn</strong><div class="help">Slide 7 wording. Off = only open complaint or dunning (clv_method).</div></div>${sw("pen", d.reject_penalty_on_any_open_ticket, "bool", 'data-key="reject_penalty_on_any_open_ticket"')}</div>
      </div>
      <div class="card">
        <div class="card-head"><h2>Trigger signal strength</h2><span class="muted small">0 = ignore · 1 = as strong as a click</span></div>
        ${Object.entries(d.signal_strength).map(([k, v]) => `
          <div class="setting"><div><strong>${esc(SIGNAL_LABEL[k] ?? k)}</strong><div class="help">Only used with profiling consent.</div></div>
          <div class="range-row"><input type="range" min="0" max="1" step="0.1" value="${v}" data-action="signal" data-signal="${esc(k)}" aria-label="${esc(SIGNAL_LABEL[k] ?? k)}"><span class="num">${Number(v).toFixed(1)}</span></div></div>`).join("")}
      </div>
    </div>

    <div class="grid grid-2">
      <div class="card">
        <div class="card-head"><h2>Optimisation</h2><span class="muted small">how scarce channel capacity is allocated</span></div>
        <div class="setting"><div><strong>Arbitration solver</strong><div class="help">Greedy gives a scarce slot to the highest value. The LP optimum gives it to the customer who loses most without it.</div></div>
          <div class="segmented">${[["greedy", "Greedy"], ["lp", "LP optimum"]].map(([k, l]) => `<button class="${d.solver === k ? "on" : ""}" data-action="solver" data-solver="${k}">${l}</button>`).join("")}</div></div>
        <div id="capacity-card" style="margin-top:10px">${state.capacityHtml ?? '<div class="muted small">Comparing greedy and LP on the current run…</div>'}</div>
      </div>
      <div class="card">
        <div class="card-head"><h2>Learning</h2><a class="muted small" href="#/learn">deep dive A →</a></div>
        <div class="setting"><div><strong>Exploration (Thompson sampling)</strong><div class="help">Tries uncertain offers to learn faster. Never touches hard rules or service actions, never proposes negative expected value.</div></div>${sw("explore", d.exploration === "thompson", "explore")}</div>
        <div class="setting"><div><strong>Concentration</strong><div class="help">1 = pure Thompson sampling. Higher = less exploration, lower cost.</div></div><div class="range-row"><input type="range" min="1" max="64" step="1" value="${d.exploration_concentration}" data-action="concentration" aria-label="Exploration concentration"><span class="num">${Number(d.exploration_concentration).toFixed(0)}</span></div></div>
      </div>
    </div>

    <div class="card">
      <div class="card-head"><h2>Change history</h2><button class="btn sm" data-action="reset-config">Reset to defaults</button></div>
      ${state.configHistory.length ? `<div class="table-wrap"><table><thead><tr><th>Time</th><th>Reviewer</th><th>Changed</th><th>Version</th></tr></thead><tbody>
        ${[...state.configHistory].reverse().map((h) => `<tr><td class="nowrap">${esc(h.ts)}</td><td>${esc(h.actor)}</td><td>${esc(h.changed.join(", "))}</td><td class="mono">${esc(h.from)} → ${esc(h.to)}</td></tr>`).join("")}
      </tbody></table></div>` : `<div class="muted">No changes yet. The engine runs with the default settings.</div>`}
    </div>

    ${changed.length ? `<div class="savebar"><strong class="grow">${changed.length} unsaved ${changed.length === 1 ? "change" : "changes"}: ${esc(changed.join(", ").replaceAll("_", " "))}</strong>
      <button class="btn" data-action="discard">Discard</button>
      <button class="btn primary" data-action="apply">Apply and re-run engine</button></div>` : ""}`;
}

async function applyConfig() {
  const before = state.summary.kpis;
  const changes = Object.fromEntries(changedKeys().map((k) => [k, state.draft[k]]));
  const res = await withLoading("Re-running the engine for all customers…", () => api("/api/config", {
    method: "PUT", body: { actor: reviewer(), changes },
  }));
  state.summary = res.summary;
  updateChrome();
  const after = res.summary.kpis;
  toast(`Engine re-run: ${num(before.proposed + before.approved)} → ${num(after.proposed + after.approved)} actions, expected gain ${eur(before.expected_gain)} → ${eur(after.expected_gain)}`);
  await showSteering();
}

/* --- decision log ------------------------------------------------------------------------ */
async function showLog() {
  setHeader("Decision log", "Append-only: every decision with inputs, rule trace and scores, plus every review.");
  const log = await api("/api/log/reviews");
  $("#page").innerHTML = `
    <div class="kpis" style="grid-template-columns:repeat(3,minmax(0,1fr))">
      <div class="card kpi"><div class="label">Log entries</div><div class="value num">${num(log.entries)}</div><div class="hint">decisions and reviews in this run</div></div>
      <div class="card kpi"><div class="label">Reviews</div><div class="value num">${num(log.reviews.length)}</div><div class="hint">approvals and rejections</div></div>
      <div class="card kpi"><div class="label">Config changes</div><div class="value num">${num(log.config_changes.length)}</div><div class="hint">each one re-runs the engine</div></div>
    </div>
    <div class="card">
      <div class="card-head"><h2>Reviews</h2><a class="btn sm" href="/api/log/download" download>Download full log (JSONL)</a></div>
      ${log.reviews.length ? `<div class="table-wrap"><table>
        <thead><tr><th>Time</th><th>Reviewer</th><th>Customer</th><th>Action</th><th>Status</th><th>Run</th><th>Note</th></tr></thead>
        <tbody>${log.reviews.map((r) => `<tr class="clickable" data-action="open" data-customer="${esc(r.customer_id)}">
          <td class="nowrap">${esc(r.ts)}</td><td>${esc(r.actor)}</td><td class="mono">${esc(r.customer_id)}</td>
          <td>${esc(r.offer_name ?? "—")}${r.override ? ' <span class="chip accent">alternative</span>' : ""}</td>
          <td>${statusChip(r.status)}</td>
          <td class="small"><span class="mono">${esc(r.config_version)}</span> · ${esc(state.meta.decision_times.find((t) => t.key === r.time_key)?.label ?? r.time_key)}</td>
          <td class="small wrap">${esc(r.note || "")}</td></tr>`).join("")}</tbody>
      </table></div>` : `<div class="empty">No reviews yet. Approve or reject proposals in the approval queue.</div>`}
    </div>`;
}

/* --- routing and events ------------------------------------------------------------------ */
async function route() {
  const [route, param] = (location.hash.replace("#/", "") || "overview").split("?")[0].split("/");
  state.route = route;
  state.selected.clear();
  document.body.classList.toggle("agent-mode", route === "agent");
  closeDrawer();
  updateChrome();
  try {
    if (route === "queue") await showQueue();
    else if (route === "content") await showContent();
    else if (route === "steering") await showSteering();
    else if (route === "log") await showLog();
    else if (route === "service" || route === "agent") await showService(param && decodeURIComponent(param));
    else if (route === "learn") await showLearn();
    else if (route === "impact") await showImpact();
    else if (route === "architecture") showArchitecture();
    else { state.route = "overview"; await showOverview(); }
  } catch (err) {
    toast(err.message, "bad");
  }
  updateChrome();
}

const handlers = {
  go: (el) => { location.hash = `#/${el.dataset.route}`; },
  segment: (el) => { state.filters = { ...state.filters, segment: el.dataset.segment, status: "proposed" }; location.hash = "#/queue"; },
  demo: async (el) => {
    if (await demoTime(el.dataset.time) && state.route === "overview") await showOverview();
    await openCustomer(el.dataset.customer);
  },
  snapshot: async () => { await changeTime("snapshot"); toast("Decision time: back to the snapshot of 30 Sep"); await route(); },
  open: (el, ev) => { if (!ev.target.closest("[data-stop]")) openCustomer(el.dataset.customer); },
  close: () => closeDrawer(),
  status: async (el) => { state.filters.status = el.dataset.status; state.limit = 50; state.selected.clear(); await showQueue(); },
  more: () => { state.limit += 100; renderQueue(); },
  view: async (el) => { state.queueView = el.dataset.view; state.selected.clear(); await showQueue(); },
  "campaign-content": async (el) => {
    await reviewTemplate(el.dataset.key, "approved");
    toast("Message approved. You can now approve the campaign.");
    await showQueue();
  },
  "campaign-review": (el) => reviewCampaign(el.dataset.key, el.dataset.status),
  "campaign-show": async (el) => {
    const [offer, channel, t] = el.dataset.key.split("|");
    Object.assign(state.filters, { offer, channel, tone: t });
    state.queueView = "customer"; state.limit = 50;
    await showQueue();
  },
  select: (el) => { el.checked ? state.selected.add(el.dataset.id) : state.selected.delete(el.dataset.id); renderQueue(); },
  "select-all": (el) => {
    state.decisions.slice(0, state.limit).filter((d) => d.status === "proposed")
      .forEach((d) => (el.checked ? state.selected.add(d.decision_id) : state.selected.delete(d.decision_id)));
    renderQueue();
  },
  clear: () => { state.selected.clear(); renderQueue(); },
  bulk: (el) => bulkReview(el.dataset.status),
  review: (el) => reviewDecision(el.dataset.id, el.dataset.status),
  override: (el) => reviewDecision(el.dataset.id, "approved", { offer_id: el.dataset.offer, channel: el.dataset.channel }),
  "approve-template": async (el) => {
    await reviewTemplate(el.dataset.key, "approved");
    toast("Content approved");
    if (state.drawerCustomer) await openCustomer(state.drawerCustomer);
    if (state.route === "queue") await showQueue();
  },
  "tpl-filter": (el) => { state.tplFilter = el.dataset.filter; renderContent(); },
  "tpl-review": async (el) => {
    await reviewTemplate(el.dataset.key, el.dataset.status);
    toast(`Template ${el.dataset.status === "pending" ? "reset to pending" : el.dataset.status}`);
    await showContent();
  },
  "approve-clean": async () => {
    const clean = state.templates.filter((t) => t.status === "pending" && !t.issues.length);
    await withLoading("Approving templates…", () => Promise.all(clean.map((t) => api(`/api/templates/${encodeURIComponent(t.key)}/review`, { method: "POST", body: { status: "approved", actor: reviewer() } }))));
    await refreshSummary();
    toast(`${clean.length} templates approved`);
    await showContent();
  },
  discard: () => showSteering(),
  apply: () => applyConfig(),
  "reset-config": async () => {
    const res = await withLoading("Re-running the engine with default settings…", () => api("/api/config/reset", { method: "POST", body: { actor: reviewer() } }));
    state.summary = res.summary;
    toast("Settings reset to defaults");
    await showSteering();
  },
};
Object.assign(handlers, pageHandlers);

const inputHandlers = {
  "offer-toggle": (el) => {
    const set = new Set(state.draft.disabled_offers);
    el.checked ? set.delete(el.dataset.offer) : set.add(el.dataset.offer);
    state.draft.disabled_offers = [...set];
  },
  "channel-toggle": (el) => {
    const set = new Set(state.draft.disabled_channels);
    el.checked ? set.delete(el.dataset.channel) : set.add(el.dataset.channel);
    state.draft.disabled_channels = [...set];
  },
  priority: (el) => {
    const v = Number(el.value);
    if (Math.abs(v - 1) < 1e-9) delete state.draft.offer_priority[el.dataset.offer];
    else state.draft.offer_priority[el.dataset.offer] = v;
  },
  capacity: (el) => {
    if (el.value === "") delete state.draft.channel_capacity[el.dataset.channel];
    else state.draft.channel_capacity[el.dataset.channel] = Math.max(0, parseInt(el.value, 10) || 0);
  },
  bool: (el) => { state.draft[el.dataset.key] = el.checked; },
  number: (el) => { state.draft[el.dataset.key] = parseInt(el.value, 10) || 0; },
  range: (el) => { state.draft[el.dataset.key] = Number(el.value); },
  signal: (el) => { state.draft.signal_strength[el.dataset.signal] = Number(el.value); },
  explore: (el) => { state.draft.exploration = el.checked ? "thompson" : "off"; },
  concentration: (el) => { state.draft.exploration_concentration = Number(el.value); },
};

document.addEventListener("click", async (ev) => {
  const el = ev.target.closest("[data-action]");
  if (!el || !handlers[el.dataset.action]) return;
  if (el.type === "checkbox" && inputHandlers[el.dataset.action]) return;
  try { await handlers[el.dataset.action](el, ev); } catch (err) { toast(err.message, "bad"); }
});

document.addEventListener("input", (ev) => {
  const el = ev.target;
  const handler = inputHandlers[el.dataset?.action];
  if (!handler) return;
  handler(el);
  if (el.type === "range") {
    const out = el.parentElement.querySelector(".num");
    const a = el.dataset.action;
    if (out) out.textContent = a === "priority" ? `×${Number(el.value).toFixed(1)}` : a === "signal" ? Number(el.value).toFixed(1)
      : a === "concentration" ? Number(el.value).toFixed(0) : pct(Number(el.value));
  }
});

document.addEventListener("change", (ev) => {
  const el = ev.target;
  if (inputHandlers[el.dataset?.action]) renderSteering();  // shows the save bar with the diff
  if (el.dataset?.filter && state.route === "queue") {
    state.filters[el.dataset.filter] = el.value; state.limit = 50; state.selected.clear(); showQueue().catch((e) => toast(e.message, "bad"));
  }
});

let searchTimer;
document.addEventListener("input", (ev) => {
  if (ev.target.id !== "q") return;
  clearTimeout(searchTimer);
  searchTimer = setTimeout(async () => {
    state.filters.q = ev.target.value.trim();
    await showQueue();
    const q = $("#q"); q.focus(); q.setSelectionRange(q.value.length, q.value.length);
  }, 250);
});

document.addEventListener("keydown", (ev) => { if (ev.key === "Escape" && state.drawerCustomer) closeDrawer(); });

// One floating tooltip for every [data-tip], positioned in the viewport so that table and
// card overflow never clips it.
function showTip(el) {
  const box = $("#tip");
  box.textContent = el.dataset.tip;
  box.hidden = false;
  const r = el.getBoundingClientRect(), w = box.offsetWidth, h = box.offsetHeight;
  box.style.left = `${Math.min(Math.max(8, r.left + r.width / 2 - w / 2), window.innerWidth - w - 8)}px`;
  box.style.top = `${r.top - h - 8 < 8 ? r.bottom + 8 : r.top - h - 8}px`;
}
const hideTip = () => { $("#tip").hidden = true; };
document.addEventListener("mouseover", (ev) => { const el = ev.target.closest?.("[data-tip]"); el ? showTip(el) : hideTip(); });
document.addEventListener("focusin", (ev) => { if (ev.target.dataset?.tip) showTip(ev.target); });
document.addEventListener("focusout", hideTip);
window.addEventListener("scroll", hideTip, true);

async function changeTime(key) {
  state.summary = await withLoading("Running the engine for all customers…", () => api("/api/time", { method: "PUT", body: { time_key: key } }));
  $("#time-select").value = key;
  updateChrome();
}

// Demo cases need their own decision time (slide 7 happens on 1 Oct). Switching is global,
// so say so instead of changing the queue behind the user's back. Returns true if it switched.
async function demoTime(key) {
  if (key === state.summary.time_key) return false;
  await changeTime(key);
  toast(`Decision time switched to "${timeLabel(key)}" for this demo case. All pages now show that moment.`);
  return true;
}

$("#time-select").addEventListener("change", async (ev) => {
  try { await changeTime(ev.target.value); await route(); } catch (err) { toast(err.message, "bad"); }
});

$("#reviewer").addEventListener("change", (ev) => {
  try { localStorage.setItem("nba-reviewer", ev.target.value.trim()); } catch { /* storage unavailable */ }
});

async function boot() {
  try { const saved = localStorage.getItem("nba-reviewer"); if (saved) $("#reviewer").value = saved; } catch { /* storage unavailable */ }
  try {
    state.meta = await api("/api/meta");
    $("#time-select").innerHTML = state.meta.decision_times.map((t) => `<option value="${esc(t.key)}">${esc(t.label)}</option>`).join("");
    $("#time-select").value = state.meta.time_key;
    Object.assign($("#time-tip").dataset, { tip: GLOSSARY.time });
    $("#time-tip").setAttribute("aria-label", GLOSSARY.time);
    state.configHistory = (await api("/api/config")).history;
    state.summary = await withLoading("Running the engine for all customers…", () => api("/api/summary"));
    window.addEventListener("hashchange", route);
    await route();
  } catch (err) {
    $("#page").innerHTML = `<div class="banner">Could not reach the API: ${esc(err.message)}</div>`;
  }
}

boot();
