"""Marketing dashboard (slide 10): top NBAs across customers and segments, steering of
offers, priorities and contact limits, approval of actions, channels and tone."""
import json
from dataclasses import replace

import pandas as pd
import streamlit as st

from common import config, current_run, dataset, decisions_frame, eur
from nba_engine.config import CHANNELS, EngineConfig
from nba_engine.decision_log import CHANNEL_LABELS

run = current_run()
cfg = config()
frame = decisions_frame(run)
actions = frame[frame["offer_id"].notna()]

st.title("Marketing dashboard")

k = st.columns(5)
k[0].metric("Customers scored", len(frame))
k[1].metric("Awaiting approval", int((frame["status"] == "Proposed").sum()))
k[2].metric("Approved", int((frame["status"] == "Approved").sum()))
k[3].metric("Control group", int((frame["status"] == "Control group (not sent)").sum()))
k[4].metric("Expected CLV gain", eur(actions["expected_dclv"].sum()),
            help="Sum of expected dCLV over all recommended actions, incl. the control group")

top, segment_tab, steering, approval, log_tab = st.tabs(
    ["Top NBAs", "Segments", "Steering", "Approval", "Decision log"]
)

# --- top NBAs -----------------------------------------------------------------------------
with top:
    f = st.columns(4)
    offers = f[0].multiselect("Offer", sorted(actions["offer"].unique()))
    channels = f[1].multiselect("Channel", sorted(actions["channel"].unique()))
    segs = f[2].multiselect("Segment", sorted(frame["segment"].unique()))
    statuses = f[3].multiselect("Status", sorted(frame["status"].unique()))
    view = frame
    for col, chosen in (("offer", offers), ("channel", channels), ("segment", segs), ("status", statuses)):
        if chosen:
            view = view[view[col].isin(chosen)]

    chart_left, chart_right = st.columns(2)
    acted = view[view["offer_id"].notna()]
    with chart_left:
        st.markdown("**Expected CLV gain by offer**")
        by_offer = acted.groupby("offer", as_index=False)["expected_dclv"].sum()
        st.bar_chart(by_offer, x="offer", y="expected_dclv", horizontal=True, sort="-expected_dclv",
                     x_label="", y_label="Expected CLV gain (€)")
    with chart_right:
        st.markdown("**Actions by channel**")
        by_channel = acted.groupby("channel", as_index=False).size().rename(columns={"size": "actions"})
        st.bar_chart(by_channel, x="channel", y="actions", horizontal=True, sort="-actions",
                     x_label="", y_label="Actions")

    st.dataframe(
        view.sort_values("expected_dclv", ascending=False, na_position="last")
        [["customer_id", "segment", "offer", "channel", "tone", "p_accept", "expected_dclv", "status", "reason"]],
        hide_index=True,
        width="stretch",
        column_config={
            "customer_id": "Customer",
            "p_accept": st.column_config.NumberColumn("p(accept)", format="percent"),
            "expected_dclv": st.column_config.NumberColumn("Expected CLV gain", format="€%.0f"),
            "reason": st.column_config.TextColumn("Reason", width="large"),
        },
    )

# --- segments -----------------------------------------------------------------------------
with segment_tab:
    st.caption("Value tiers are CLV terciles; customers younger than one year are shown as 'New customer'.")
    seg = frame.groupby("segment").agg(
        customers=("customer_id", "size"),
        avg_clv=("clv_now", "mean"),
        actions=("offer_id", "count"),
        service_actions=("type", lambda s: (s == "service").sum()),
        expected_gain=("expected_dclv", "sum"),
        avg_gain=("expected_dclv", "mean"),
    )
    seg["top_offer"] = actions.groupby("segment")["offer"].agg(lambda s: s.value_counts().index[0])
    st.dataframe(
        seg.reindex(["High value", "Mid value", "Low value", "New customer"]).reset_index(),
        hide_index=True,
        column_config={
            "segment": "Segment",
            "avg_clv": st.column_config.NumberColumn("Avg CLV", format="€%.0f"),
            "expected_gain": st.column_config.NumberColumn("Expected CLV gain", format="€%.0f"),
            "avg_gain": st.column_config.NumberColumn("Avg gain per action", format="€%.0f"),
            "top_offer": "Most frequent offer",
        },
    )
    st.markdown("**Offer mix per segment**")
    mix = actions.pivot_table(index="offer", columns="segment", values="customer_id", aggfunc="count", fill_value=0)
    st.dataframe(mix, width="stretch")

# --- steering -----------------------------------------------------------------------------
with steering:
    catalog = dataset().product_catalog
    st.caption(f"Current config version `{cfg.version()}`. Changes re-run the engine for all customers.")
    with st.form("steering"):
        st.markdown("**Offers and priorities**")
        offer_table = st.data_editor(
            pd.DataFrame({
                "offer_id": catalog["product_id"],
                "offer": catalog["product_name"],
                "type": catalog["action_type"],
                "enabled": [o not in cfg.disabled_offers for o in catalog["product_id"]],
                "priority": [cfg.offer_priority.get(o, 1.0) for o in catalog["product_id"]],
            }),
            hide_index=True,
            disabled=["offer_id", "offer", "type"],
            column_config={"priority": st.column_config.NumberColumn(
                "Priority weight", min_value=0.1, max_value=5.0, step=0.1,
                help="Multiplies dCLV for ranking only. Negative actions stay dropped.")},
            key="offer_editor",
        )

        st.markdown("**Channels and contact limits**")
        channel_table = st.data_editor(
            pd.DataFrame({
                "channel": list(CHANNELS),
                "label": [CHANNEL_LABELS[c] for c in CHANNELS],
                "approved": [c not in cfg.disabled_channels for c in CHANNELS],
                "capacity_per_run": [cfg.channel_capacity.get(c) for c in CHANNELS],
            }),
            hide_index=True,
            disabled=["channel", "label"],
            column_config={"capacity_per_run": st.column_config.NumberColumn(
                "Max actions per run", min_value=0, step=1, help="Empty = unlimited")},
            key="channel_editor",
        )
        c = st.columns(3)
        max_contacts = c[0].number_input("Max sales contacts per customer in 30 days", 0, 20,
                                         cfg.max_sales_contacts_30d)
        control_share = c[1].slider("Control group share", 0.0, 0.3, cfg.control_share, 0.01)
        allow_du = c[2].toggle("Approve 'Du' tone", cfg.allow_du_tone,
                               help="Off: every customer is addressed with 'Sie'.")

        st.markdown("**Model assumptions**")
        c = st.columns(3)
        service_prior = c[0].slider("p(accept) of a proactive service fix", 0.05, 0.95, cfg.service_p_accept_prior, 0.05)
        retention_on_cancel = c[1].toggle("Allow retention offer after cancellation", cfg.allow_retention_on_cancellation)
        penalty_any_ticket = c[2].toggle("Sales contact during any open ticket raises churn",
                                         cfg.reject_penalty_on_any_open_ticket)
        st.markdown("**Trigger signal strength** (0 = ignore, 1 = as strong as a click)")
        c = st.columns(len(cfg.signal_strength))
        strengths = {
            sid: c[i].slider(sid.replace("_", " ").capitalize(), 0.0, 1.0, value, 0.1)
            for i, (sid, value) in enumerate(cfg.signal_strength.items())
        }

        applied = st.form_submit_button("Apply and re-run engine", type="primary")

    if applied:
        st.session_state.cfg = replace(
            cfg,
            disabled_offers=set(offer_table.loc[~offer_table["enabled"], "offer_id"]),
            offer_priority={r.offer_id: float(r.priority) for r in offer_table.itertuples() if r.priority != 1.0},
            disabled_channels=set(channel_table.loc[~channel_table["approved"], "channel"]),
            channel_capacity={r.channel: int(r.capacity_per_run) for r in channel_table.itertuples()
                              if pd.notna(r.capacity_per_run)},
            max_sales_contacts_30d=int(max_contacts),
            control_share=float(control_share),
            allow_du_tone=allow_du,
            service_p_accept_prior=float(service_prior),
            allow_retention_on_cancellation=retention_on_cancel,
            reject_penalty_on_any_open_ticket=penalty_any_ticket,
            signal_strength=strengths,
        )
        st.rerun()
    if st.button("Reset to defaults"):
        st.session_state.cfg = EngineConfig()
        st.rerun()

# --- approval -----------------------------------------------------------------------------
with approval:
    st.caption("Human in the loop: only approved actions may be delivered. Control-group decisions are never sent.")
    pending = frame[frame["status"] == "Proposed"].sort_values("expected_dclv", ascending=False)
    reviewer = st.text_input("Reviewer", value="marketing")
    # a fresh table key after each review clears the row selection, so positions never
    # silently point at other decisions once reviewed rows leave the table
    nonce = st.session_state.setdefault("approval_nonce", 0)
    selection = st.dataframe(
        pending[["customer_id", "segment", "offer", "channel", "tone", "expected_dclv", "reason", "decision_id"]],
        hide_index=True,
        width="stretch",
        on_select="rerun",
        selection_mode="multi-row",
        key=f"approval_table_{nonce}",
        column_config={
            "expected_dclv": st.column_config.NumberColumn("Expected CLV gain", format="€%.0f"),
            "reason": st.column_config.TextColumn("Reason", width="large"),
            "decision_id": None,
        },
    )
    chosen = pending.iloc[selection.selection.rows]["decision_id"].tolist()
    b = st.columns([1, 1, 4])
    for col, status, label in ((b[0], "approved", "Approve selected"), (b[1], "rejected", "Reject selected")):
        if col.button(f"{label} ({len(chosen)})", disabled=not chosen, type="primary" if status == "approved" else "secondary"):
            now = str(pd.Timestamp.now().floor("s"))
            for decision_id in chosen:
                run.engine.log.set_status(decision_id, status, actor=reviewer, ts=now)
            st.session_state.approval_nonce = nonce + 1
            st.rerun()

# --- decision log -------------------------------------------------------------------------
with log_tab:
    log = run.engine.log
    reviews = [e for e in log.entries if e["kind"] == "status"]
    st.caption(f"{len(log.entries)} append-only entries: {len(run.decisions)} decisions, {len(reviews)} reviews.")
    if reviews:
        st.dataframe(pd.DataFrame(reviews), hide_index=True, width="stretch")
    st.download_button(
        "Download decision log (JSONL)",
        "\n".join(json.dumps(e, ensure_ascii=False, default=str) for e in log.entries),
        file_name=f"decision_log_{run.as_of:%Y%m%d_%H%M}_{cfg.version()}.jsonl",
        mime="application/json",
    )
