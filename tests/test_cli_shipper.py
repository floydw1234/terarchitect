import os
import sys
from unittest.mock import patch

import pytest

from cli._shipper import remap_agenthub_url_for_host, run_local_shipper


@pytest.mark.parametrize(
    ("url", "expected"),
    [
        ("http://agenthub:8080", "http://127.0.0.1:8088"),
        ("http://agenthub:8088", "http://127.0.0.1:8088"),
        ("http://agenthub", "http://127.0.0.1:8088"),
        ("http://agenthub:8080/commits/abc?limit=1", "http://127.0.0.1:8088/commits/abc?limit=1"),
    ],
)
def test_remap_agenthub_url_for_host_docker_service(url, expected):
    remapped, warning = remap_agenthub_url_for_host(url)
    assert remapped == expected
    assert warning is not None
    assert "remapped AGENTHUB_URL" in warning


@pytest.mark.parametrize(
    "url",
    [
        "http://127.0.0.1:8088",
        "http://localhost:8088",
        "https://agenthub.example:443",
        "",
    ],
)
def test_remap_agenthub_url_for_host_leaves_localhost_alone(url):
    remapped, warning = remap_agenthub_url_for_host(url)
    assert remapped == url
    assert warning is None


def test_run_local_shipper_invokes_agent_shipper_with_env(capsys):
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
                "AGENTHUB_URL": "http://agenthub:8080",
            },
            clear=False,
        ):
            rc = run_local_shipper("http://localhost:5010/", "run-abc")

    assert rc == 0
    assert captured["cmd"] == [sys.executable, "-m", "agent.shipper"]
    assert captured["env"]["TERARCHITECT_API_URL"] == "http://localhost:5010"
    assert captured["env"]["SHIP_RUN_ID"] == "run-abc"
    assert captured["env"]["TERARCHITECT_WORKER_API_KEY"] == "worker-key"
    assert captured["env"]["AGENTHUB_URL"] == "http://127.0.0.1:8088"
    assert "agent" in captured["env"]["PYTHONPATH"]
    stderr = capsys.readouterr().err
    assert "remapped AGENTHUB_URL" in stderr


def test_run_local_shipper_leaves_localhost_agenthub_url(capsys):
    captured: dict = {}

    def fake_run(cmd, env, cwd):
        captured["env"] = env

        class Result:
            returncode = 0

        return Result()

    with patch("cli._shipper.subprocess.run", side_effect=fake_run):
        with patch.dict(
            os.environ,
            {"AGENTHUB_URL": "http://127.0.0.1:8088"},
            clear=False,
        ):
            run_local_shipper("http://localhost:5010/", "run-abc")

    assert captured["env"]["AGENTHUB_URL"] == "http://127.0.0.1:8088"
    assert capsys.readouterr().err == ""
