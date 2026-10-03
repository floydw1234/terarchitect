import json
import os
import subprocess
import sys
import unittest
from pathlib import Path


class BuildOpencodeConfigTests(unittest.TestCase):
    def test_includes_allow_all_permissions(self):
        script = Path(__file__).resolve().parents[1] / "agent_runner" / "build_opencode_config.py"
        env = {
            **os.environ,
            "WORKER_LLM_URL": "http://localhost:8080/v1",
            "WORKER_MODEL": "test-model",
        }
        proc = subprocess.run(
            [sys.executable, str(script)],
            capture_output=True,
            text=True,
            env=env,
            check=True,
        )
        payload = json.loads(proc.stdout.strip())
        self.assertEqual(payload.get("permission", {}).get("question"), "allow")
        self.assertEqual(payload.get("permission", {}).get("external_directory"), "allow")


if __name__ == "__main__":
    unittest.main()
