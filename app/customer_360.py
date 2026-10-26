"""Customer 360° view for service agents (slide 10): status, CLV, ranked NBAs with reasons."""
import pandas as pd
import streamlit as st

from common import DEMO_CASES, STATUS_LABELS, current_run, dataset, eur, segments
from nba_engine.decision_log import CHANNEL_LABELS, DRIVER_LABELS
from nba_engine.signals import SIGNAL_LABELS

run = current_run()
ds = dataset()
customer_ids = list(run.by_customer)

st.title("Customer 360°")

if st.session_state.get("customer_id") not in customer_ids:
    st.session_state.customer_id = customer_ids[0]


def _pick_demo() -> None:
    if st.session_state.demo_pick:
        st.session_state.customer_id = st.session_state.demo_pick


st.pills("Demo cases", list(DEMO_CASES), key="demo_pick", on_change=_pick_demo,
         format_func=lambda c: f"{c} · {DEMO_CASES[c].split(':')[0]}")
cid = st.selectbox(
    "Customer",
    customer_ids,
    key="customer_id",
    format_func=lambda c: f"{c} — {DEMO_CASES[c]}" if c in DEMO_CASES else c,
)

decision = run.by_customer[cid]
state = run.engine.states(run.as_of)[cid]
ctx = run.engine.evaluate_customer(state, run.as_of)
cv = ctx.value
master = ds.customer_master.set_index("customer_id").loc[cid]

# --- header -----------------------------------------------------------------------------
st.caption(
    f"Customer since {master.customer_since:%b %Y} · {master.city} · household of {master.household_size} · "
    f"segment: {segments().at[cid, 'segment']} · address as **{ctx.tone.tone.capitalize()}** ({ctx.tone.evidence})"
)

m = st.columns(4)
m[0].metric("CLV (10 years)", eur(cv.clv_reported))
m[1].metric("Churn risk (12 months)", f"{cv.churn:.0%}")
m[2].metric("Annual margin", eur(cv.annual_margin), help=f"Cost to serve: {eur(cv.cost_to_serve)} per year")
m[3].metric("Expected gain of best action", eur(decision.dclv) if decision.has_action else "No action")

flags = []
if state.open_complaint:
    flags.append(":red-badge[Open complaint · sales blocked]")
elif state.open_service_case:
    flags.append(":orange-badge[Open service case]")
if state.dunning_level >= 1:
    flags.append(":red-badge[Dunning · sales blocked]")
if state.cancellation_received:
    flags.append(":red-badge[Cancellation received · sales blocked]")
if not state.profiling_allowed:
    flags.append(":gray-badge[No profiling consent · population rates only]")
for d in cv.drivers:
    if d in DRIVER_LABELS and d != "dunning_ge2":
        flags.append(f":gray-badge[Churn driver: {DRIVER_LABELS[d]}]")
if flags:
    st.markdown(" ".join(flags))

# --- next best action -------------------------------------------------------------------
st.subheader("Next best action")
with st.container(border=True):
    status = STATUS_LABELS[run.engine.log.status(decision.decision_id)]
    if decision.has_action:
        left, right = st.columns([3, 1])
        left.markdown(f"#### {decision.offer_name}")
        left.markdown(
            f"via **{CHANNEL_LABELS[decision.channel]}** · addressed with **{decision.tone.capitalize()}** · "
            f"type: {decision.action_type}"
        )
        right.metric("Expected CLV gain", eur(decision.dclv))
        st.info(decision.reason, icon=":material/lightbulb:")
        c = st.columns(4)
        c[0].metric("p(accept)", f"{decision.p_accept:.0%}",
                    help=f"History rate {decision.inputs['p_accept_base']:.1%} before signals and channel affinity")
        c[1].metric("CLV if accepted", eur(decision.clv_accept))
        c[2].metric("CLV if rejected", eur(decision.clv_reject))
        c[3].metric("Contact cost", f"€{decision.contact_cost:.2f}")
        st.caption(f"Status: {status}")
    else:
        st.markdown("#### No action")
        st.info(decision.reason, icon=":material/do_not_disturb_on:")

if decision.alternatives:
    st.markdown("**Ranked alternatives**")
    st.dataframe(
        pd.DataFrame(decision.alternatives).assign(channel=lambda f: f["channel"].map(CHANNEL_LABELS))
        [["offer_name", "channel", "p_accept", "dclv"]],
        hide_index=True,
        column_config={
            "offer_name": "Offer",
            "channel": "Channel",
            "p_accept": st.column_config.NumberColumn("p(accept)", format="percent"),
            "dclv": st.column_config.NumberColumn("Expected CLV gain", format="€%.0f"),
        },
    )

# --- why not the others ---------------------------------------------------------------
with st.expander("Why not the other offers? (every candidate, best channel per offer)"):
    rows = {}
    for result, score in ctx.scored:
        offer = result.candidate.offer
        current = rows.get(offer.offer_id)
        better = current is None or (result.eligible, score.dclv) > (current["eligible"], current["dclv"])
        if better:
            rows[offer.offer_id] = {
                "offer": offer.name,
                "type": offer.action_type,
                "eligible": result.eligible,
                "blocked by": f"{result.blocked_by.rule_id}: {result.blocked_by.detail}" if result.blocked_by else "",
                "channel": CHANNEL_LABELS[score.channel],
                "p_accept": score.p_accept,
                "dclv": score.dclv,
            }
    st.dataframe(
        pd.DataFrame(rows.values()).sort_values(["eligible", "dclv"], ascending=False),
        hide_index=True,
        column_config={
            "p_accept": st.column_config.NumberColumn("p(accept)", format="percent"),
            "dclv": st.column_config.NumberColumn("Expected CLV gain", format="€%.0f"),
        },
    )
    st.caption("Blocked candidates are scored too: a negative value is the economic reason behind the hard rule.")

# --- status: products, cases, consents ----------------------------------------------------
st.subheader("Current status")
col1, col2 = st.columns(2)

with col1:
    st.markdown("**Products and contracts**")
    products = []
    for r in ds.energy_contracts[ds.energy_contracts["customer_id"] == cid].itertuples():
        guarantee = f", price guarantee until {r.price_guarantee_until:%d %b %Y}" if pd.notna(r.price_guarantee_until) else ""
        products.append((f"{r.commodity.capitalize()} · {r.tariff}", f"{r.status}{guarantee}"))
    for r in ds.mobile_contracts[ds.mobile_contracts["customer_id"] == cid].itertuples():
        products.append((f"Mobile · {r.plan}", f"{r.status}, {r.avg_data_used_gb_3m} of {r.data_volume_gb} GB used"))
    for r in ds.autostrom_contracts[ds.autostrom_contracts["customer_id"] == cid].itertuples():
        products.append((f"Autostrom · {r.car_brand} {r.car_model}", f"{r.status}, {state.failed_charges_30d} failed charges in 30 days"))
    for r in ds.hems_devices[ds.hems_devices["customer_id"] == cid].itertuples():
        products.append((f"HEMS · {r.device_type}", r.connection_status))
    if products:
        st.dataframe(pd.DataFrame(products, columns=["Product", "Status"]), hide_index=True)
    else:
        st.caption("No contracts.")

    signals = ctx.signals
    if signals:
        st.markdown("**Trigger signals**")
        st.dataframe(
            pd.DataFrame([{"Signal": SIGNAL_LABELS[s.signal_id], "Evidence": s.evidence,
                           "Used": "yes" if s.used else "no (no profiling consent)"} for s in signals]),
            hide_index=True,
        )

with col2:
    st.markdown("**Service cases**")
    tickets = ds.service_interactions[(ds.service_interactions["customer_id"] == cid)
                                      & (ds.service_interactions["created_at"] <= run.as_of)]
    if len(tickets):
        st.dataframe(
            tickets.sort_values("created_at", ascending=False)
            [["created_at", "category", "channel", "status", "csat_1to5", "message_text"]].head(6),
            hide_index=True,
            column_config={"created_at": st.column_config.DatetimeColumn("Created", format="D MMM YYYY"),
                           "message_text": "Customer message (German)", "csat_1to5": "CSAT"},
        )
    else:
        st.caption("No tickets.")

    events = ds.events[(ds.events["customer_id"] == cid) & (ds.events["ingested_at"] <= run.as_of)]
    if len(events):
        st.markdown("**Live events (1 Oct, ingested by decision time)**")
        st.dataframe(events[["occurred_at", "event_type", "source_system"]], hide_index=True)

    st.markdown("**Consents**")
    st.dataframe(
        pd.DataFrame(sorted(state.consents.items()), columns=["Consent", "Status"]),
        hide_index=True,
    )

with st.expander("Decision log entry (inputs, rule trace, scores)"):
    st.json({k: v for k, v in decision.__dict__.items() if k not in ("alternatives",)}, expanded=False)
