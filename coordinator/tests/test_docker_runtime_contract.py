import os
import sys
import unittest
from unittest.mock import patch

_COORDINATOR_PARENT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _COORDINATOR_PARENT not in sys.path:
    sys.path.insert(0, _COORDINATOR_PARENT)

from coordinator.coordinator import _docker_run_args, _runtime_pythonpath, job_to_env


class TestDockerRuntimeContract(unittest.TestCase):
    def _job(self) -> dict:
        return {
            "ticket_id": "ticket-1",
            "project_id": "project-1",
            "job_id": "job-1",
            "kind": "ticket",
            "repo_url": "https://github.com/example/repo",
            "execution_mode": "docker",
        }

    def test_runtime_pythonpath_includes_repo_and_agent_roots(self):
        pythonpath = _runtime_pythonpath("/tmp/custom")
        parts = pythonpath.split(os.pathsep)
        repo_root = os.path.dirname(_COORDINATOR_PARENT)
        self.assertIn("/tmp/custom", parts)
        self.assertIn(repo_root, parts)
        self.assertIn(os.path.join(repo_root, "agent"), parts)

    def test_job_to_env_rewrites_host_urls_and_forwards_swarm_env(self):
        job = {
            "ticket_id": "ticket-1",
            "project_id": "project-1",
            "job_id": "job-1",
            "kind": "ticket",
            "repo_url": "https://github.com/example/repo",
            "execution_mode": "docker",
            "base_leaf_id": "leaf_123",
            "base_hash": "leaf_123",
            "agenthub_root_hash": "leaf_123",
        }
        env_overrides = {
            "TERARCHITECT_API_URL": "http://localhost:5010",
            "AGENTHUB_URL": "http://127.0.0.1:8088",
            "AGENTHUB_API_KEY": "agenthub-secret",
            "AGENTHUB_API_KEY_PATH": "/run/secrets/agenthub_api_key",
            "WORKER_API_KEY": "worker-secret",
            "OPENROUTER_API_KEY": "openrouter-secret",
            "CODEX_EXTRA_FLAGS": "--max-turns,50",
            "CODEX_SANDBOX": "workspace-write",
        }
        with patch.dict(os.environ, env_overrides, clear=False):
            env = job_to_env(job, for_docker=True)

        self.assertEqual(env["TERARCHITECT_API_URL"], "http://host.docker.internal:5010")
        self.assertEqual(env["AGENTHUB_URL"], "http://host.docker.internal:8088")
        self.assertEqual(env["BASE_LEAF_ID"], "leaf_123")
        self.assertEqual(env["BASE_HASH"], "leaf_123")
        self.assertEqual(env["AGENTHUB_ROOT_HASH"], "leaf_123")
        self.assertEqual(env["AGENTHUB_API_KEY_PATH"], "/run/secrets/agenthub_api_key")
        self.assertEqual(env["CODEX_EXTRA_FLAGS"], "--max-turns,50")
        self.assertEqual(env["CODEX_SANDBOX"], "workspace-write")

    def test_job_to_env_forwards_optional_attempt_metadata(self):
        env = job_to_env(
            {
                "ticket_id": "ticket-1",
                "project_id": "project-1",
                "job_id": "job-1",
                "kind": "ticket",
                "repo_url": "https://github.com/example/repo",
                "execution_mode": "docker",
                "metadata": {
                    "attempt_batch_id": "batch-123",
                    "attempt_index": 2,
                    "attempt_count": 4,
                    "attempt_strategy": "architecture-cleanup",
                    "attempt_strategy_description": "Improve structure where it directly clarifies the ticket.",
                },
            },
            for_docker=False,
        )

        self.assertEqual(env["ATTEMPT_BATCH_ID"], "batch-123")
        self.assertEqual(env["ATTEMPT_INDEX"], "2")
        self.assertEqual(env["ATTEMPT_COUNT"], "4")
        self.assertEqual(env["ATTEMPT_STRATEGY"], "architecture-cleanup")
        self.assertEqual(
            env["ATTEMPT_STRATEGY_DESCRIPTION"],
            "Improve structure where it directly clarifies the ticket.",
        )
        self.assertEqual(env["TERARCHITECT_ATTEMPT_BATCH_ID"], "batch-123")
        self.assertEqual(env["TERARCHITECT_ATTEMPT_STRATEGY"], "architecture-cleanup")

    def test_docker_run_args_use_env_file_for_secrets_and_attach_compose_network(self):
        job = self._job()
        job["base_leaf_id"] = "leaf_123"
        job["attempt_metadata"] = {
            "attempt_batch_id": "batch-xyz",
            "attempt_index": 2,
            "attempt_count": 4,
            "attempt_strategy": "product-polish",
            "attempt_strategy_description": "Bias toward user-facing clarity and finish quality.",
        }
        env_overrides = {
            "TERARCHITECT_API_URL": "http://backend:5010",
            "AGENTHUB_URL": "http://agenthub:8080",
            "AGENTHUB_API_KEY": "agenthub-secret",
            "AGENTHUB_API_KEY_PATH": "/run/secrets/agenthub_api_key",
            "WORKER_API_KEY": "worker-secret",
            "OPENROUTER_API_KEY": "openrouter-secret",
            "DOCKER_NETWORK": "terarchitect_default",
            "AGENT_DOCKER_MODE": "dind",
        }
        with patch.dict(os.environ, env_overrides, clear=False):
            args, secret_env_path = _docker_run_args("terarchitect-agent", job)
            try:
                self.assertIn("--network", args)
                self.assertIn("terarchitect_default", args)
                self.assertIn("--privileged", args)
                self.assertIn("--name", args)
                self.assertIn("--env-file", args)
                self.assertEqual(args[-1], "terarchitect-agent")

                joined_args = " ".join(args)
                self.assertNotIn("AGENTHUB_API_KEY=agenthub-secret", joined_args)
                self.assertNotIn("WORKER_API_KEY=worker-secret", joined_args)
                self.assertNotIn("OPENROUTER_API_KEY=openrouter-secret", joined_args)
                self.assertIn("-e AGENTHUB_URL=http://agenthub:8080", joined_args)
                self.assertIn("-e ATTEMPT_BATCH_ID=batch-xyz", joined_args)
                self.assertIn("-e ATTEMPT_INDEX=2", joined_args)
                self.assertIn("-e ATTEMPT_COUNT=4", joined_args)
                self.assertIn("-e ATTEMPT_STRATEGY=product-polish", joined_args)
                self.assertIn(
                    "-e TERARCHITECT_ATTEMPT_STRATEGY_DESCRIPTION=Bias toward user-facing clarity and finish quality.",
                    joined_args,
                )
                self.assertIn("strategy-product-polish", args[args.index("--name") + 1])
                self.assertIsNotNone(secret_env_path)

                with open(secret_env_path, encoding="utf-8") as handle:
                    secret_env = handle.read()
                self.assertIn("AGENTHUB_API_KEY=agenthub-secret", secret_env)
                self.assertIn("AGENTHUB_API_KEY_PATH=/run/secrets/agenthub_api_key", secret_env)
                self.assertIn("WORKER_API_KEY=worker-secret", secret_env)
                self.assertIn("OPENROUTER_API_KEY=openrouter-secret", secret_env)
            finally:
                if secret_env_path and os.path.exists(secret_env_path):
                    os.unlink(secret_env_path)

    def test_docker_run_args_mount_codex_config_dir_for_codex_workers(self):
        env_overrides = {
            "WORKER_MODE": "codex",
            "CODEX_CONFIG_DIR": "/tmp/codex-config",
            "AGENT_DOCKER_MODE": "dind",
        }
        with patch.dict(os.environ, env_overrides, clear=False):
            args, secret_env_path = _docker_run_args("terarchitect-agent", self._job())
            try:
                self.assertIn("-v", args)
                self.assertIn("/tmp/codex-config:/root/.codex", args)
            finally:
                if secret_env_path and os.path.exists(secret_env_path):
                    os.unlink(secret_env_path)

    def test_docker_run_args_mount_default_codex_config_dir_for_codex_workers(self):
        env_overrides = {
            "WORKER_MODE": "codex",
            "AGENT_DOCKER_MODE": "dind",
            "HOME": "/tmp/fake-home",
        }
        with patch.dict(os.environ, env_overrides, clear=False):
            os.environ.pop("CODEX_CONFIG_DIR", None)
            args, secret_env_path = _docker_run_args("terarchitect-agent", self._job())
            try:
                self.assertIn("/tmp/fake-home/.codex:/root/.codex", args)
            finally:
                if secret_env_path and os.path.exists(secret_env_path):
                    os.unlink(secret_env_path)

    def test_docker_run_args_skip_codex_config_mount_for_non_codex_workers(self):
        env_overrides = {
            "WORKER_MODE": "opencode",
            "CODEX_CONFIG_DIR": "/tmp/codex-config",
            "AGENT_DOCKER_MODE": "dind",
        }
        with patch.dict(os.environ, env_overrides, clear=False):
            args, secret_env_path = _docker_run_args("terarchitect-agent", self._job())
            try:
                self.assertNotIn("/tmp/codex-config:/root/.codex", args)
            finally:
                if secret_env_path and os.path.exists(secret_env_path):
                    os.unlink(secret_env_path)


if __name__ == "__main__":
    unittest.main()
