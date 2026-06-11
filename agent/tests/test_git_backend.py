import os
import subprocess
import sys
import unittest
from unittest.mock import patch

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
