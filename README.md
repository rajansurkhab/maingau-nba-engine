# MAINGAU Next Best Action

Futury Build Days #3 (AI Edition), 26–27 Oct 2026.

For every customer the engine recommends **offer, channel and tone (Du/Sie)**, with the
**expected CLV gain** and a **one-line reason**. Marketing steers and approves in a web
cockpit, service agents see a Customer 360°, and AI assistants can query everything through
an MCP server. Nothing is sent automatically.

## Quick start

```bash
python -m venv .venv
.venv/Scripts/python -m pip install pandas openpyxl scipy streamlit fastapi uvicorn httpx mcp pytest
.venv/Scripts/python -m uvicorn api.main:app --port 8000     # cockpit + API on http://localhost:8000
.venv/Scripts/python -m pytest -q                             # tests (about 2 minutes)
```

Approvals, content status and settings are saved in `logs/cockpit_state.json` and survive a
restart. Delete that file for a clean demo.

## What is in the box

| Part | Where | Slide |
|---|---|---|
| Engine: eligibility → dCLV scoring → arbitration → channel & tone → content → log | `nba_engine/` | 9 |
| Marketing cockpit: approval queue, content approval, steering, decision log | `web/`, `api/` | 10, 12 |
| Customer 360° for service agents | cockpit → Customer 360° | 10 |
| Impact & compliance proof | cockpit → Impact & compliance | 11, 12 |
| Learn (deep dive A): uplift, A/B plan, feedback loop, exploration | cockpit → Learn | 8, 11 |
| Target architecture (Databricks + MCP) | cockpit → Architecture | 10 |
| MCP server (read-only tools) | `mcp_server/server.py` | 10 |
| Planning, requirements, assumptions A1–A33 | `PLANUNG_hackathon3.md` | all |

## The rules we never break

- Hard rules run **before** scoring: channel consent (an unsubscribe is a withdrawal),
  dunning, open complaint and cancellation block sales content. Service actions stay allowed.
- Personalisation (trigger signals, channel affinity, detected Du) only with profiling consent.
- Payment data is only an exclusion flag, never a model input.
- The engine proposes, marketing approves. An action can only be approved once its message
  template is approved. The MCP server has no approve or send tool.
- Every decision, including "no action", is logged with inputs, rule trace, scores, reason,
  policy and propensity.

`nba_engine/audit.py` re-checks every decision from the raw tables, independently of the
engine code. Result on the mock data: **0 violations** at the snapshot and in event replay.

## Scoring

Exactly as in `clv_method.txt`:

```
CLV  = Σ_{t=1..10} (margin − cost_to_serve) · (1 − churn)^(t−1) / 1.06^t
dCLV = p·(CLV_accept − CLV_now) + (1−p)·(CLV_reject − CLV_now) − contact_cost
```

The CLV is the value of a two-state Markov chain (active → churned); an action changes its
parameters. `p_accept` comes from hierarchical Beta-Binomial shrinkage over the action history
(global → action type → offer → channel → tone), without the control group and without legacy
rows that skipped the consent check. A trigger signal (gas meter deregistered, three heat-pump
clicks, wallbox bought) moves p towards the offer's learned after-click rate.

## ML upgrades

All optional and off by default except where noted; switchable in the cockpit (Steering).

- **V3 – LP arbitration** (`solver="lp"`): exact optimum of Σ expected gain under "one action
  per customer" and channel capacities. Totally unimodular, so the simplex solution is
  integral. Also reports the **shadow price** of each capacity (value of one more call slot).
  Mock data: +€100 expected CLV over greedy, 14 customers reassigned, one more call slot ≈ €2.
- **V1 – Thompson sampling** (`exploration="thompson"`): ranks positive actions by a posterior
  draw so uncertain offers get tried. Never samples service actions, never proposes negative
  expected value, deterministic per seed. `exploration_concentration` controls the cost:
  pure TS costs 18% expected CLV on this data, concentration 16 costs 3.6%.
- **V2-light – uplift**: pooled treated-vs-control uplift with a Newcombe interval, z-test and
  Bayesian P(uplift > 0), per-offer uplift flagged when data is insufficient, and an A/B
  sample-size plan. Mock data: +1.1 pp, 95% CI −2.1 to +2.7 pp, P(uplift > 0) = 74%:
  not proven. Per-customer uplift models (X-learner) are deliberately not trained on 5 control
  conversions.
- **V6-prep – propensity logging**: every decision logs solver, exploration, seed, propensity
  (Monte Carlo under exploration, never 0) and p(treat), so off-policy evaluation becomes
  possible once our own policy has produced data. The legacy policy was deterministic and
  cannot be evaluated offline.
- Not built, on purpose: survival models (no conversion timestamps in the data), hierarchical
  Bayesian regression with covariates (59 conversions; dunning as a feature would break the
  payment-data rule), a live contextual bandit (no overlap in the legacy log).

## MCP server

Read-only tools over the cockpit API: `recommend`, `list_actions`, `customer_360`,
`explain_decision`, `compliance_audit`, `impact_report`, `learning_report`, `get_config`.
Start the API first, then register the server, for example with Claude Code:

```bash
claude mcp add maingau-nba -- "<repo>/.venv/Scripts/python" "<repo>/mcp_server/server.py"
```

Set `NBA_API_URL` if the API is not on `http://localhost:8000`.

## Data

Synthetic mock data only (`Re__Präsentation_Hackathon/`). Customer-facing texts are German
(slide 12); every template carries an English translation for review.
