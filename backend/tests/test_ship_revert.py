"""Tests for ship revert API."""

from models.db import db, Project, ShipRun


def test_ship_revert_moves_frontier_back_one_step(client, project):
    pid = project["id"]
    base = "a" * 40
    shipped = "c" * 40

    with client.application.app_context():
        proj = db.session.get(Project, pid)
        proj.shipped_frontier = shipped
        run = ShipRun(
            project_id=pid,
            status="shipped",
            base_main_hash=base,
            composed_commit_hash=shipped,
            shipped_commit_hash=shipped,
        )
        db.session.add(run)
        db.session.commit()
        run_id = str(run.id)

    resp = client.post(f"/api/projects/{pid}/ship/revert", json={})
    assert resp.status_code == 200
    payload = resp.get_json()
    assert payload["shipped_frontier_after"] == base
    assert payload["revert"] is True

    with client.application.app_context():
        proj = db.session.get(Project, pid)
        assert proj.shipped_frontier == base
        revert_run = db.session.get(ShipRun, payload["ship_run_id"])
        assert revert_run is not None
        assert (revert_run.summary or "").startswith("revert")


def test_ship_revert_to_specific_run(client, project):
    pid = project["id"]
    base0 = "a" * 40
    tip1 = "b" * 40
    tip2 = "c" * 40

    with client.application.app_context():
        proj = db.session.get(Project, pid)
        proj.shipped_frontier = tip2
        run1 = ShipRun(
            project_id=pid,
            status="shipped",
            base_main_hash=base0,
            composed_commit_hash=tip1,
            shipped_commit_hash=tip1,
        )
        run2 = ShipRun(
            project_id=pid,
            status="shipped",
            base_main_hash=tip1,
            composed_commit_hash=tip2,
            shipped_commit_hash=tip2,
        )
        db.session.add_all([run1, run2])
        db.session.commit()
        anchor_id = str(run2.id)

    resp = client.post(f"/api/projects/{pid}/ship/revert", json={"to_ship_run_id": anchor_id})
    assert resp.status_code == 200
    assert resp.get_json()["shipped_frontier_after"] == tip1
