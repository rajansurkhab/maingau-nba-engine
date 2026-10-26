All data is synthetic. No real customers. Prices and tariff details are fictional.
Snapshot of the master view: 2026-09-30 23:59. Event stream (separate JSONL): 2026-10-01.
customer_id links every sheet. 750 customers (150 of them Autostrom-only), 15 source tables.
Sheet 'sources' tells you where each table comes from and how fresh it is – use it for your data freshness concept.
Sheet 'columns' describes every non-obvious column.
Traps are intentional: machine opens (Apple MPP), late roaming CDRs, guest orders, consent withdrawn by unsubscribe, legacy actions that ignored consent, offline HEMS gateways, duplicate events.
Customer message texts are German, like the real ones. Tonality (Du/Sie, formal/informal) is not labelled – derive it.
Files: maingau_nba_mockdata.xlsx, csv/ (same tables), event_stream_2026-10-01.jsonl
NEW (service): resolution codes, first-contact resolution, goodwill and repeat contacts in service_interactions; call_transcripts; service_nba_feedback (agent assist pilot); gas meter deregistrations in energy_contracts.
NEW: customer_value (CLV/Barwert incl. negative service impact), value_assumptions, clv_method. Rank NBAs by expected CLV change, see clv_method.