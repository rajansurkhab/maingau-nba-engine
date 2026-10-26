# Pitch script (8 minutes + 4 minutes Q&A)

Start the cockpit before the pitch (`.venv/Scripts/python -m uvicorn api.main:app --port 8000`),
open http://localhost:8000, approve all clean templates once (Content approval → "Approve all").
Keep the browser at 1440 px width or wider.

## 0:00 – 0:45 · The problem

> "MAINGAU has five businesses and one customer. Today a loyal customer with three failed
> charging sessions gets a home-electricity offer, in the same tone as someone who joined last
> week. We built the decision layer that fixes this: for every customer the next best action,
> ranked by expected CLV gain, compliant by construction, approved by marketing."

## 0:45 – 2:00 · How it decides (Overview page)

- Point at the four workflow steps: engine proposes → approve content → review → ready.
- "Every number here comes from the CLV method you gave us, exactly. CLV is a two-state Markov
  chain; an action changes its parameters; we score accept and reject."
- "Hard rules run before scoring. Blocked candidates are still scored, so we can show *why* a
  rule matters economically."

## 2:00 – 4:00 · Demo: the cases from your slides

1. **Slide 7** – Customer 360° → demo `MG-100009` (switches to replay 1 Oct 18:00).
   "Third failed charge, new ticket. Service fix +€85. The best sales offer would be −€15,
   because selling during an open case raises churn."
2. **Slide 9** – `MG-100001`: "Writes formally, opened 0 of 6 newsletters, visits the portal
   weekly → portal pop-up, Sie."
3. **Slide 6** – `MG-100060` gas meter deregistered → StromWärme; `MG-100266` wallbox → Autostrom.
   "A signal counts like a click: p moves to the learned after-click rate, only with profiling
   consent."
4. Approval queue → open a customer → "Approve this instead": "Marketing can change any action,
   but only to alternatives that already passed every hard rule."

## 4:00 – 5:30 · Impact & compliance

- "0 violations in 750 decisions, recomputed from the raw data by a second, independent
  implementation. Eight checks, including human approval and approved content."
- "Same customers, same formula: the legacy engine's last actions are worth €849 expected CLV,
  ours €24,604. 503 of the legacy actions would break today's rules; 297 were sent without any
  consent check."

## 5:30 – 6:45 · Learn (deep dive A)

- "Did the legacy campaigns work? +1.1 pp, but the 95% interval crosses zero: 5 control
  conversions are not evidence. We say that honestly, and we do not train per-customer uplift
  models on noise."
- "This A/B plan proves +2 pp with 7,630 actions at 10% control – one campaign wave at MAINGAU
  scale."
- "The feedback loop: every response is one Bayesian update. Thompson sampling is built in and
  priced: concentration 16 explores 181 decisions for 3.6% of expected CLV. Every decision logs
  its propensity, so off-policy evaluation works from day one."

## 6:45 – 7:30 · Architecture (Architecture page)

- "On Databricks: Delta master view, the engine as a job, MLflow for the models, the decision
  log as an append-only Delta table. The API you just saw is the MCP server."
- Optional live: ask Claude through MCP "Why did MG-100009 get a service fix?" – it answers from
  the decision log. "The AI can explain. It cannot approve or send."

## 7:30 – 8:00 · Close

> "Compliant by construction, explainable in one line, steered by marketing, and it already
> tells you how to prove it works. Thank you."

## Q&A preparation

| Likely question | Answer |
|---|---|
| Why not ML everywhere? | 59 conversions and 5 control conversions. We use Bayesian shrinkage that is honest about uncertainty, and the logging that ML needs later (propensities, control group). |
| How do you guarantee zero violations? | Rules run before scoring; overrides only to rule-checked alternatives; an independent audit recomputes everything from raw data; tests assert it for every run, including event replay. |
| Why does the service fix beat a sale? | dCLV: removing failed-charge and ticket churn drivers is worth more than a 3% chance of a sale, and a rejected sales contact during an open case raises churn by 0.05. |
| Personalised prices or urgency? | None. Templates are linted for prices, discounts, urgency and tone mismatch before they can be approved. |
| GDPR / AI Act? | Personalisation only with profiling consent, payment data only as exclusion, human approval for every action, full decision log. Credit-scoring-like use is excluded by design. |
| What happens with a consent withdrawal at 10:14? | Event replay applies it as soon as it is ingested; the next run cannot use that channel. |
| Greedy vs LP? | Greedy gives a scarce call to the highest value; the LP to the customer who loses most without it. +€100 here, and the shadow price tells you what one more call-centre slot is worth. |
| Is the CLV comparison fair to the legacy engine? | Same customers, same moment, same formula; legacy actions get the population acceptance rate of their own offer × channel × tone. |
