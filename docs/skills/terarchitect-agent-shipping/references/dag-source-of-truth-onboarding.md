# DAG Source Of Truth Onboarding

- Import the repo into AgentHub first and store the returned leaf as `accepted_frontier_id`.
- Seed new tickets from that imported base leaf instead of assuming local `HEAD` remains canonical.
- Verify follow-on jobs receive the expected AgentHub base leaf and hash.
