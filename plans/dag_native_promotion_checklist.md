# DAG-native promotion (completed)

Shipping is promotion-candidate review plus `ShipRun` compose/ship. Numbered waves are not an operator, API, UI, or schema concept.

Happy path:

```text
Ticket → TicketAttempt → accept/integrate → promotion candidate → ShipRun → ship → shipped_frontier
```

Operator surfaces:

- Ship Room lists candidates, previews compose, shows composed diff and AgentHub timeline, then ships.
- CLI: `ta ship candidates|candidate|dry-compose|diff|timeline|compose-candidate|run|ship-run|ship-candidate|feedback`.

Schema: `ticket_attempts` and `ship_runs` have no `wave_num` column. Alembic `022_drop_wave_num` drops the column only if an older checkout created it.
