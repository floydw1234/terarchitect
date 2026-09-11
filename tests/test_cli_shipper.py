import os
import sys
from unittest.mock import patch

from cli._shipper import run_local_shipper


def test_run_local_shipper_invokes_agent_shipper_with_env():
    captured: dict = {}

    def fake_run(cmd, env, cwd):
        captured["cmd"] = cmd
        captured["env"] = env
        captured["cwd"] = cwd

        class Result:
            returncode = 0

        return Result()

    with patch("cli._shipper.subprocess.run", side_effect=fake_run):
        with patch.dict(
            os.environ,
            {
                "TERARCHITECT_WORKER_API_KEY": "worker-key",
                "AGENTHUB_URL": "http://agenthub",
            },
            clear=False,
        ):
            rc = run_local_shipper("http://localhost:5010/", "run-abc")

    assert rc == 0
    assert captured["cmd"] == [sys.executable, "-m", "agent.shipper"]
    assert captured["env"]["TERARCHITECT_API_URL"] == "http://localhost:5010"
    assert captured["env"]["SHIP_RUN_ID"] == "run-abc"
    assert captured["env"]["TERARCHITECT_WORKER_API_KEY"] == "worker-key"
    assert captured["env"]["AGENTHUB_URL"] == "http://agenthub"
    assert "agent" in captured["env"]["PYTHONPATH"]
