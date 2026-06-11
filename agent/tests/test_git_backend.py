import os
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import MagicMock, patch

_AGENT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _AGENT_DIR not in sys.path:
    sys.path.insert(0, _AGENT_DIR)

from middle_agent import git_backend


def _cp(args, returncode=0, stdout="", stderr=""):
    return subprocess.CompletedProcess(args=args, returncode=returncode, stdout=stdout, stderr=stderr)


class TestSwarmPublishFallback(unittest.TestCase):
    def test_swarm_publish_retries_with_full_bundle_on_missing_prerequisites(self):
        project_path = "/tmp/project"
        head_hash = "a" * 40
        calls = []

        def fake_run(args, **kwargs):
            calls.append({"args": list(args), "cwd": kwargs.get("cwd")})
            if args[:3] == ["git", "add", "-A"]:
                return _cp(args)
            if args[:3] == ["git", "status", "--porcelain"]:
                return _cp(args, stdout="")
            if args[:3] == ["git", "rev-parse", "HEAD"]:
                return _cp(args, stdout=f"{head_hash}\n")
            if args[:2] == ["ah", "push"] and kwargs.get("cwd") == project_path:
                return _cp(
                    args,
                    returncode=1,
                    stderr="push failed: Repository lacks these prerequisite commits: be142414\n",
                )
            if args[:2] == ["git", "clone"]:
                return _cp(args)
            if args[:4] == ["git", "remote", "remove", "origin"]:
                return _cp(args)
            if args[:2] == ["ah", "push"]:
                return _cp(args, stdout="pushed aaaaaaaaaaaa\n")
            raise AssertionError(f"Unexpected subprocess call: args={args} kwargs={kwargs}")

        with patch("middle_agent.git_backend.subprocess.run", side_effect=fake_run), \
             patch("middle_agent.git_backend.post_ticket_event") as post_event:
            result = git_backend.swarm_publish(
                project_path=project_path,
                commit_message="test commit",
                ticket_id="ticket-123",
                summary="publish summary",
            )

        self.assertEqual(result, head_hash)
        self.assertEqual(
            [call["args"][:2] for call in calls if call["args"][:2] == ["ah", "push"]],
            [["ah", "push"], ["ah", "push"]],
        )
        self.assertIn(["git", "clone"], [call["args"][:2] for call in calls])
        self.assertIn(
            ["git", "remote", "remove", "origin"],
            [call["args"][:4] for call in calls if call["args"][:2] == ["git", "remote"]],
        )
        post_event.assert_called_once()

    def test_swarm_publish_does_not_retry_non_lineage_push_errors(self):
        project_path = "/tmp/project"
        calls = []

        def fake_run(args, **kwargs):
            calls.append({"args": list(args), "cwd": kwargs.get("cwd")})
            if args[:3] == ["git", "add", "-A"]:
                return _cp(args)
            if args[:3] == ["git", "status", "--porcelain"]:
                return _cp(args, stdout="")
            if args[:3] == ["git", "rev-parse", "HEAD"]:
                return _cp(args, stdout=f"{'b' * 40}\n")
            if args[:2] == ["ah", "push"]:
                return _cp(args, returncode=1, stderr="push failed: unauthorized")
            raise AssertionError(f"Unexpected subprocess call: args={args} kwargs={kwargs}")

        with patch("middle_agent.git_backend.subprocess.run", side_effect=fake_run), \
             patch("middle_agent.git_backend.post_ticket_event") as post_event:
            result = git_backend.swarm_publish(
                project_path=project_path,
                commit_message="test commit",
                ticket_id="ticket-123",
                summary="publish summary",
            )

        self.assertIsNone(result)
        self.assertEqual(
            [call["args"][:2] for call in calls if call["args"][:2] == ["ah", "push"]],
            [["ah", "push"]],
        )
        self.assertNotIn(["git", "clone"], [call["args"][:2] for call in calls])
        post_event.assert_called_once()


class TestAgentHubMaterialization(unittest.TestCase):
    def test_materialize_workspace_fetches_bundle_and_checks_out_base_leaf(self):
        base_leaf_id = "leaf_01HZX3BASE0123456789ABCDEFG"
        bundle_bytes = [b"# v2 git bundle\n", b"payload"]
        calls = []

        def fake_run(args, **kwargs):
            calls.append({"args": list(args), "cwd": kwargs.get("cwd")})
            if args[:2] == ["git", "init"]:
                return _cp(args)
            if args[:3] == ["git", "bundle", "unbundle"]:
                return _cp(args)
            if args[:3] == ["git", "checkout", "-B"]:
                return _cp(args)
            raise AssertionError(f"Unexpected subprocess call: args={args} kwargs={kwargs}")

        response = MagicMock()
        response.ok = True
        response.iter_content.return_value = bundle_bytes

        with tempfile.TemporaryDirectory(prefix="git-backend-test-") as tmp_dir, \
             patch("middle_agent.git_backend.requests.get", return_value=response) as mock_get, \
             patch("middle_agent.git_backend.subprocess.run", side_effect=fake_run):
            with patch.dict(os.environ, {
                "AGENTHUB_URL": "http://agenthub:8088",
                "AGENTHUB_API_KEY": "secret",
                "TICKET_ID": "ticket-123",
            }, clear=False):
                workspace = git_backend.materialize_workspace_from_agenthub(
                    base_leaf_id,
                    parent_dir=tmp_dir,
                )
                self.assertTrue(os.path.isdir(workspace))

        mock_get.assert_called_once()
        self.assertIn(f"/api/git/fetch/{base_leaf_id}", mock_get.call_args.args[0])
        self.assertEqual([call["args"][:2] for call in calls], [
            ["git", "init"],
            ["git", "bundle"],
            ["git", "checkout"],
        ])

    def test_materialize_workspace_fails_clearly_when_base_is_unfetchable(self):
        base_leaf_id = "leaf_01HZX3BASE0123456789ABCDEFG"
        response = MagicMock()
        response.ok = False
        response.status_code = 404

        with tempfile.TemporaryDirectory(prefix="git-backend-test-"), \
             patch("middle_agent.git_backend.requests.get", return_value=response), \
             patch.dict(os.environ, {
                 "AGENTHUB_URL": "http://agenthub:8088",
                 "AGENTHUB_API_KEY": "secret",
             }, clear=False):
            with self.assertRaises(git_backend.AgentHubMaterializationError) as ctx:
                git_backend.materialize_workspace_from_agenthub(base_leaf_id)

        self.assertIn(base_leaf_id[:12], str(ctx.exception))
        self.assertIn("404", str(ctx.exception))
