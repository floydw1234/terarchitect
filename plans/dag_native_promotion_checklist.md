# DAG-native promotion (completed)

Shipping is promotion-candidate review plus `ShipRun` compose/ship. Numbered waves are not an operator, API, or UI concept.

Happy path:

```text
Ticket → TicketAttempt → accept/integrate → promotion candidate → ShipRun → ship → shipped_frontier
```

Leftover implementation detail only: unused `wave_num` columns on `ticket_attempts` and `ship_runs` remain for existing databases and always write `0`. They are not serialized to operator JSON and must not drive behavior. Do not rewrite Alembic history to drop them in this pass.
