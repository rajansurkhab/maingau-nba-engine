# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project state

Hackathon workspace (Futury Build Days #3, 26.–27.10.2026) for a **MAINGAU Next-Best-Action (NBA) engine**. The folder holds:

- `Hackathon_slides_for_MAINGAU.pptx`: the official brief (13 slides plus speaker notes). This is the source of truth for requirements.
- `PLANUNG_hackathon3.md`: the team's planning doc in German. It contains the requirement list (MUSS/SOLL/KANN with slide references), jury weighting, the chosen approach, the architecture variants, open questions (§6.1) and the working assumptions A1–A11 (§6.2). Read it before making design decisions. Update it when an assumption changes.
- `Re__Präsentation_Hackathon/`: synthetic mock data. `maingau_nba_mockdata.xlsx` and `maingau_nba_mockdata_csv_jsonl/csv/*.csv` hold the same tables. The event stream JSONL exists twice as identical copies. Read `README.txt` and `clv_method.txt` in that folder first.

## Commands

Python 3.12, venv in `.venv` (Windows paths shown; Git Bash works the same with `/`).

```bash
python -m venv .venv
.venv/Scripts/python -m pip install pandas openpyxl pytest
.venv/Scripts/python -m pytest -q                                  # all tests
.venv/Scripts/python -m pytest tests/test_eligibility.py -k consent # single test
```

## Code layout

- `nba_engine/config.py`: paths, `SNAPSHOT`, channel→consent map, `EngineConfig` (marketing-editable defaults).
- `nba_engine/loader.py`: `load_dataset()` / cached `default_dataset()` returns a `Dataset`. Trap cleaning lives here: event dedupe, `human_open` (no Apple MPP), guest orders split off, `usable_for_learning` on action history, `failed_charges()` counts by `started_at`.
- `nba_engine/state.py`: `build_states(ds, as_of)` returns one `CustomerState` per customer. It resolves consent (unsubscribe = withdrawal), holdings, exclusions and contact counts. For `as_of` after the snapshot it replays events by `ingested_at`.
- `nba_engine/eligibility.py`: `RULES` is an ordered tuple of `Rule` predicates. `evaluate()` returns an `EligibilityResult` with a `trace` that stops at the first failing rule. Product prerequisites are in `PRODUCT_PREREQUISITES`.
- `nba_engine/scoring.py`: `clv()` and `score()` implement `clv_method.txt` literally and return a `Score` with p, CLV_now/accept/reject, contact cost and dCLV. `customer_values()` parses `churn_drivers` text into additive driver keys (verified additive on the data). Score blocked candidates too, because their negative dCLV is the economic reason for the block.
- `nba_engine/acceptance.py`: `AcceptanceModel.fit()` builds hierarchical Beta-Binomial shrinkage (global → action_type → offer → channel → tone). `update()` is the Bayesian learning step for deep dive A. `uplift_by_offer()` compares treatment and control.
- `nba_engine/arbitration.py`: `arbitrate()` drops dCLV ≤ 0 and disabled offers, ranks by dCLV × `offer_priority`, and assigns at most one action per customer with a global greedy pass that respects `channel_capacity`.
- `nba_engine/decision_log.py`: `Decision` and the append-only `DecisionLog` (optional JSONL file). Statuses are proposed / held_out / no_action, and marketing review appends approved / rejected. `reason_for()` builds the one-line reason.
- `nba_engine/pipeline.py`: `Engine.recommend(customer_ids, as_of)` runs Eligibility → Scoring → Arbitration → Log. This is the API entry point. The module-level `recommend()` is a convenience wrapper. 10 % of sales decisions are held out by a stable hash, and service actions are never held out. Tone is still a stub (`tone_of` returns "sie").
- `tests/`: `test_scoring.py::test_slide7_autostrom_case` and `test_pipeline.py::test_slide7_case_end_to_end` pins the slide-7 demo case (MG-100009). The property tests in `test_eligibility.py` recompute consent and exclusions from the raw tables and assert zero violations. Keep them green.

Every outbound channel, including for service actions, needs its consent (see assumption A12 in the planning doc).

## Task in one paragraph

For each customer, recommend **offer + channel + tone (Du/Sie)**, together with expected CLV gain and a one-line reason. Also build a marketing dashboard (top NBAs, offers/priorities/contact limits editable, approval of content, channels and tone) and a Customer-360 service view. Deliverables also include one architecture slide (Databricks + MCP, concept only), an 8+4 min pitch **in English** and one deep dive from A–D. The jury checks hidden reference customers on three things: expected CLV gain, **zero consent or exclusion violations**, and a clear reason for every decision.

## Non-negotiable domain rules

These come from slide 12 and the data. A single violation counts as a hard failure.

- Apply hard rules **before** ranking. Consent per channel is binding, and an unsubscribe in `newsletter_performance` counts as a withdrawal. Dunning level ≥ 1, an open complaint or a received cancellation (`cancellation_received_at`) block all sales and upsell content. Service actions stay allowed.
- Personalisation is allowed only with `profiling_analytics` consent. Without it, use population or segment rates only.
- Payment data (dunning, `payment_method`) serves **only** as an exclusion criterion. Never use it as a model feature.
- No autonomous sending. The engine proposes and marketing approves (status proposed/approved/rejected).
- Personalised prices, artificial urgency and targeting customers in hardship are forbidden.
- Log every decision: inputs, rule trace, scores, chosen action, reason. "No action" is a valid logged decision when no candidate has dCLV > 0.
- Use mock data only. Customer-facing content must be in German.

## Scoring: follow `clv_method.txt` exactly

The jury most likely recomputes values with this method. Other CLV models may only appear as an additional view.

```
CLV  = Σ_{t=1..10} (margin − cost_to_serve) · (1 − churn)^(t−1) / 1.06^t
dCLV = p·(CLV_acc − CLV_now) + (1−p)·(CLV_rej − CLV_now) − contact_cost
```

- Sales/upsell accept: margin + `annual_margin_if_bought_eur`, churn + `churn_effect_if_bought` (from `product_catalog`). One-off shop products add their margin once, not as an annuity.
- Retention (P-EN-02) accept: remove the "price guarantee ends" churn driver. Service (P-SV-01) accept: remove the service churn drivers and subtract `service_resolution_cost_eur`.
- Reject: same as CLV_now, **except** for a sales contact during an open complaint or dunning, which adds churn of `contact_during_service_case` (+0.05).
- All parameters (churn drivers, multi-line factor 0.8 with a 0.5× floor, contact costs per channel, margins) are in `csv/value_assumptions.csv`. Read them from there rather than hard-coding. Precomputed per-customer CLV and churn drivers are in `customer_value.csv`.
- p_accept starts from `nba_action_history` rates by offer × channel × tone. Planned approach: Beta-Binomial shrinkage. Exclude `control_group` rows (they are the uplift baseline) and `consent_checked=False` legacy rows.

## Data notes

- `customer_id` (format `MG-1xxxxx`) joins all tables. There are 750 customers, 150 of them Autostrom-only. Snapshot: 2026-09-30 23:59. Event stream: 2026-10-01.
- `csv/columns.csv` documents non-obvious columns. `csv/sources.csv` gives the source system and freshness of each table.
- Intentional traps that eligibility and feature code must handle:
  - Apple MPP machine opens (`open_client_class == apple_mpp_prefetch`): not real opens.
  - Late roaming CDRs: count failed charges by `occurred_at` or `started_at`, not by arrival time.
  - Guest shop orders with no `customer_id`.
  - Consent withdrawn via unsubscribe.
  - Legacy actions with `consent_checked=False`.
  - Offline HEMS gateways.
  - Duplicate events: dedupe on `event_id` (647 lines, 635 unique).
- Tone is not labelled. Derive Du/Sie from `service_interactions.message_text` and `call_transcripts.transcript`. Default to "Sie".

## Planned architecture (Variant B in the planning doc)

The `nba_engine` Python package follows the reference pipeline from slide 9: **Trigger → Eligibility → Scoring → Arbitration → Channel & Tone → Content → Delivery & Log**. Rules are predicate objects that write to a `rule_trace`, and that trace feeds both the decision log and the reason text. A thin API (`recommend`, `customer_360`, `config`, `approve`) sits on top, with a Streamlit multipage UI for the marketing and 360° views. The API maps 1:1 to MCP tools on the Databricks architecture slide. If time runs short, the fallback is for the UI to import the package directly.

Build order by priority: eligibility (with tests against every trap) → dCLV scoring → arbitration + log → 360° view → marketing dashboard → deep dive (A: feedback loop & uplift is the preferred option) → ML refinement.

The demo must visibly solve these example cases:
- Gas meter deregistration leads to a heat-pump electricity offer.
- Wallbox purchase leads to an Autostrom offer.
- Three clicks on the heat-pump story lead to a follow-up.
- A high-value customer is handled differently from a new customer.
- Autostrom with 3 failed charges: the service fix scores positive and the sales offer scores negative.
- A formal customer who never opens newsletters but visits the portal weekly gets a portal pop-up addressed with "Sie".
