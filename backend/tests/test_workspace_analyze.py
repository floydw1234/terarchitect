def test_workspace_analyze_returns_report_for_accepted_attempt(client, project, accepted_ticket_and_attempt):
    _, attempt_id = accepted_ticket_and_attempt

    response = client.post(
        f"/api/projects/{project['id']}/workspaces/analyze",
        json={"attempt_ids": [attempt_id]},
    )

    assert response.status_code == 200
    payload = response.get_json()
    assert payload["ok"] is True
    assert payload["dep_order"] == [attempt_id]
    assert len(payload["selected_attempts"]) == 1
    assert payload["selected_attempts"][0]["attempt_id"] == attempt_id
