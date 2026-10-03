import os
import stat
import subprocess
import tempfile
import textwrap
import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]
ENTRYPOINT = REPO_ROOT / "agent" / "agent_runner" / "entrypoint.sh"


class TestAgentEntrypoint(unittest.TestCase):
    def _write_executable(self, path: Path, content: str) -> None:
        path.write_text(content)
        path.chmod(path.stat().st_mode | stat.S_IEXEC)

    def _make_stub_bin(self, root: Path, *, curl_success: bool) -> Path:
        bin_dir = root / "bin"
        bin_dir.mkdir()

        self._write_executable(
            bin_dir / "python",
            textwrap.dedent(
                """\
                #!/usr/bin/env bash
                set -e
                echo "$*" >> "$TEST_PYTHON_LOG"
                if [ "${1:-}" = "/app/agent_runner/build_opencode_config.py" ]; then
                  printf '%s\n' '{"provider":"test"}'
                  exit 0
                fi
                exit 0
                """
            ),
        )
        self._write_executable(
            bin_dir / "opencode",
            textwrap.dedent(
                """\
                #!/usr/bin/env bash
                echo "$*" >> "$TEST_OPENCODE_LOG"
                exit 0
                """
            ),
        )
        self._write_executable(
            bin_dir / "curl",
            textwrap.dedent(
                f"""\
                #!/usr/bin/env bash
                echo "$*" >> "$TEST_CURL_LOG"
                exit {0 if curl_success else 1}
                """
            ),
        )
        self._write_executable(
            bin_dir / "docker",
            "#!/usr/bin/env bash\nexit 0\n",
        )
        self._write_executable(
            bin_dir / "dockerd",
            "#!/usr/bin/env bash\nexit 0\n",
        )

        return bin_dir

    def _run_entrypoint(self, worker_mode: str, *, curl_success: bool) -> tuple[subprocess.CompletedProcess[str], Path]:
        tempdir = tempfile.TemporaryDirectory()
        self.addCleanup(tempdir.cleanup)
        root = Path(tempdir.name)
        bin_dir = self._make_stub_bin(root, curl_success=curl_success)
        logs_dir = root / "logs"
        logs_dir.mkdir()

        env = os.environ.copy()
        env.update(
            {
                "DOCKER_HOST": "unix:///tmp/fake-docker.sock",
                "PATH": f"{bin_dir}:{env['PATH']}",
                "TEST_PYTHON_LOG": str(logs_dir / "python.log"),
                "TEST_OPENCODE_LOG": str(logs_dir / "opencode.log"),
                "TEST_CURL_LOG": str(logs_dir / "curl.log"),
                "WORKER_MODE": worker_mode,
                "WORKER_LLM_URL": "http://llm.example",
                "WORKER_MODEL": "gpt-test",
            }
        )

        result = subprocess.run(
            ["bash", str(ENTRYPOINT)],
            cwd=str(REPO_ROOT),
            env=env,
            text=True,
            capture_output=True,
        )
        return result, logs_dir

    def test_codex_mode_skips_opencode_server(self):
        result, logs_dir = self._run_entrypoint("codex", curl_success=False)

        self.assertEqual(result.returncode, 0, msg=result.stderr)
        self.assertFalse((logs_dir / "opencode.log").exists())
        self.assertFalse((logs_dir / "curl.log").exists())
        self.assertEqual((logs_dir / "python.log").read_text().strip(), "-m agent_runner ticket")

    def test_opencode_mode_starts_opencode_server(self):
        result, logs_dir = self._run_entrypoint("opencode", curl_success=True)

        self.assertEqual(result.returncode, 0, msg=result.stderr)
        self.assertIn(
            "serve --port 4096 --hostname 127.0.0.1",
            (logs_dir / "opencode.log").read_text(),
        )
        self.assertIn(
            "http://127.0.0.1:4096/global/health",
            (logs_dir / "curl.log").read_text(),
        )
        self.assertEqual(
            (logs_dir / "python.log").read_text().splitlines(),
            ["/app/agent_runner/build_opencode_config.py", "-m agent_runner ticket"],
        )
