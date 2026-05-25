"""
Session-scoped fixtures for integration tests.

Behaviour:
  - By default, starts backend + postgres via docker compose (test overrides)
    then tears everything down after the session.
  - Pass --api-url http://... to skip compose and run against an existing backend.
  - Pass --no-compose to skip compose and use the default localhost:5011.

The `api` fixture provides a configured API client.
The `project` fixture creates a fresh project per test and deletes it on teardown.
"""

import json
import os
import subprocess
import sys
import time
import urllib.error
import urllib.request
import urllib.parse
from pathlib import Path
from typing import Optional

import pytest

# Root of the terarchitect repo (two levels up from this file)
REPO_ROOT = Path(__file__).parent.parent.parent
DEFAULT_TEST_URL = "http://localhost:5011"
COMPOSE_PROJECT = "terarchitect-test"
COMPOSE_FILES = [
    str(REPO_ROOT / "docker-compose.yml"),
    str(REPO_ROOT / "docker-compose.test.yml"),
]
HEALTH_TIMEOUT = 120  # seconds to wait for backend to become healthy


# ---------------------------------------------------------------------------
# pytest CLI options
# ---------------------------------------------------------------------------

def pytest_addoption(parser):
    parser.addoption(
        "--api-url",
        default=None,
        help="Backend URL to test against (skips docker compose startup)",
    )
    parser.addoption(
        "--no-compose",
        action="store_true",
        default=False,
        help="Skip docker compose startup (use --api-url or default localhost:5011)",
    )


# ---------------------------------------------------------------------------
# Session fixtures
# ---------------------------------------------------------------------------

@pytest.fixture(scope="session")
def api_url(request):
    """Resolve the backend URL for this test session."""
    url = request.config.getoption("--api-url")
    return (url or DEFAULT_TEST_URL).rstrip("/")


@pytest.fixture(scope="session", autouse=True)
def compose_services(request, api_url):
    """Start backend + postgres via docker compose (skipped if --no-compose / --api-url)."""
    skip = request.config.getoption("--no-compose") or request.config.getoption("--api-url")
    if skip:
        yield
        return

    base_cmd = [
        "docker", "compose",
        "-f", COMPOSE_FILES[0],
        "-f", COMPOSE_FILES[1],
        "--project-name", COMPOSE_PROJECT,
    ]

    print("\n[conftest] Starting test containers…", flush=True)
    result = subprocess.run(
        base_cmd + ["up", "-d", "--wait", "backend", "postgres"],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        print(result.stdout)
        print(result.stderr, file=sys.stderr)
        pytest.fail("docker compose up failed — see output above")

    _wait_for_backend(api_url)

    yield

    print("\n[conftest] Tearing down test containers…", flush=True)
    subprocess.run(
        base_cmd + ["down", "-v", "--remove-orphans"],
        cwd=REPO_ROOT,
        capture_output=True,
    )


def _wait_for_backend(url: str) -> None:
    """Poll /health until the backend responds or timeout."""
    health_url = f"{url}/health"
    deadline = time.time() + HEALTH_TIMEOUT
    last_err = None
    while time.time() < deadline:
        try:
            with urllib.request.urlopen(health_url, timeout=3) as resp:
                if resp.status == 200:
                    print(f"[conftest] Backend ready at {url}", flush=True)
                    return
        except Exception as e:
            last_err = e
        time.sleep(2)
    pytest.fail(
        f"Backend at {url} did not become healthy within {HEALTH_TIMEOUT}s. "
        f"Last error: {last_err}"
    )


# ---------------------------------------------------------------------------
# Per-session API client
# ---------------------------------------------------------------------------

@pytest.fixture(scope="session")
def api(api_url, compose_services):
    """Return a configured API client for the test backend."""
    from cli._api import API
    return API(api_url)


# ---------------------------------------------------------------------------
# Per-test project (creates fresh, deletes on teardown)
# ---------------------------------------------------------------------------

@pytest.fixture
def project(api):
    """Create a minimal test project; yield its full dict; delete after the test."""
    from cli._api import APIError
    data = api.post("/api/projects", {
        "name": "smoke-test-project",
        "description": "Created by integration test",
        "execution_mode": "docker",
        "git_mode": "swarm",
        "is_existing_repo": True,
    })
    yield data
    try:
        api.delete(f"/api/projects/{data['id']}", {"confirm_name": data["name"]})
    except APIError:
        pass  # already deleted by the test itself


@pytest.fixture
def project_id(project):
    """Shorthand for just the project UUID string."""
    return project["id"]


# ---------------------------------------------------------------------------
# Stub LLM — started once per session, used by full-stack tests
# ---------------------------------------------------------------------------

STUB_LLM_PORT = 8099
STUB_LLM_URL = f"http://127.0.0.1:{STUB_LLM_PORT}"
STUB_AH_PORT = 8098
STUB_AH_URL = f"http://127.0.0.1:{STUB_AH_PORT}"
STUBS_DIR = REPO_ROOT / "tests" / "stubs"


@pytest.fixture(scope="session")
def stub_llm():
    """Start the stub LLM server as a subprocess. Returns its base URL."""
    proc = subprocess.Popen(
        [sys.executable, str(STUBS_DIR / "llm_server.py"), "--port", str(STUB_LLM_PORT)],
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
    )
    # Wait until it's accepting connections
    deadline = time.time() + 15
    while time.time() < deadline:
        try:
            with urllib.request.urlopen(f"{STUB_LLM_URL}/health", timeout=2):
                break
        except Exception:
            time.sleep(0.3)
    else:
        proc.terminate()
        pytest.fail(f"Stub LLM did not start within 15s on port {STUB_LLM_PORT}")

    yield STUB_LLM_URL

    proc.terminate()
    try:
        proc.wait(timeout=5)
    except subprocess.TimeoutExpired:
        proc.kill()


# ---------------------------------------------------------------------------
# Stub agenthub server — started once per session, used by swarm tests
# ---------------------------------------------------------------------------

@pytest.fixture(scope="session")
def stub_agenthub():
    """Start the stub agenthub server as a subprocess. Returns its base URL."""
    proc = subprocess.Popen(
        [sys.executable, str(STUBS_DIR / "ah_server.py"), "--port", str(STUB_AH_PORT)],
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
    )
    deadline = time.time() + 15
    while time.time() < deadline:
        try:
            with urllib.request.urlopen(f"{STUB_AH_URL}/health", timeout=2):
                break
        except Exception:
            time.sleep(0.3)
    else:
        proc.terminate()
        pytest.fail(f"Stub agenthub did not start within 15s on port {STUB_AH_PORT}")

    yield STUB_AH_URL

    proc.terminate()
    try:
        proc.wait(timeout=5)
    except subprocess.TimeoutExpired:
        proc.kill()


# ---------------------------------------------------------------------------
# Local git repo helper — creates a work repo with a local bare origin
# ---------------------------------------------------------------------------

def make_local_git_repo(tmp_path: Path) -> tuple:
    """Create a bare origin + cloned work repo in tmp_path.
    Returns (work_dir: Path, origin_dir: Path).
    """
    origin = tmp_path / "origin.git"
    work = tmp_path / "work"

    subprocess.run(["git", "init", "--bare", str(origin)], check=True, capture_output=True)
    subprocess.run(["git", "clone", str(origin), str(work)], check=True, capture_output=True)

    # Create an initial commit so the repo is non-empty
    (work / "README.md").write_text("# Test repo\n")
    subprocess.run(["git", "add", "README.md"], cwd=work, check=True, capture_output=True)
    subprocess.run(
        ["git", "-c", "user.name=Test", "-c", "user.email=test@test.com",
         "commit", "-m", "Initial commit"],
        cwd=work, check=True, capture_output=True,
    )
    # Push to set up origin/HEAD so incremental bundle push works correctly
    subprocess.run(
        ["git", "push", "-u", "origin", "HEAD"],
        cwd=work, check=True, capture_output=True,
    )
    return work, origin


# ---------------------------------------------------------------------------
# Real agenthub server — built from source, run as subprocess
# ---------------------------------------------------------------------------

AGENTHUB_REAL_PORT = 8097
AGENTHUB_REAL_URL = f"http://127.0.0.1:{AGENTHUB_REAL_PORT}"
AGENTHUB_REAL_ADMIN_KEY = "test-admin-key-phase4b"
AGENTHUB_SRC = REPO_ROOT / "agenthub"
AGENTHUB_IMAGE = "terarchitect-agenthub-test"
AGENTHUB_CONTAINER = "terarchitect-agenthub-test-server"


def _register_agent(ah_url: str, admin_key: str, agent_id: str) -> str:
    """Register an agent via admin API and return its API key."""
    body = json.dumps({"id": agent_id}).encode()
    req = urllib.request.Request(
        f"{ah_url}/api/admin/agents",
        data=body,
        method="POST",
        headers={
            "Authorization": f"Bearer {admin_key}",
            "Content-Type": "application/json",
        },
    )
    with urllib.request.urlopen(req, timeout=10) as resp:
        result = json.loads(resp.read())
    return result["api_key"]


def _ah_get(ah_url: str, api_key: str, path: str):
    """Authenticated GET against the real agenthub server."""
    req = urllib.request.Request(
        f"{ah_url}{path}",
        headers={"Authorization": f"Bearer {api_key}"},
    )
    with urllib.request.urlopen(req, timeout=10) as resp:
        return json.loads(resp.read())


@pytest.fixture(scope="session")
def agenthub_docker(tmp_path_factory):
    """Start the prod agenthub Docker image (terarchitect-agenthub:latest) as a container
    and extract the ah binary from it.  Skipped if the image is not present.
    Yields a dict with url, admin_key, ah_bin_dir.
    """
    prod_image = "terarchitect-agenthub:latest"
    port = 8094
    container_name = "terarchitect-agenthub-docker-test"
    admin_key = "test-admin-key-docker"
    url = f"http://127.0.0.1:{port}"

    # Check the image exists locally
    r = subprocess.run(
        ["docker", "image", "inspect", prod_image],
        capture_output=True,
    )
    if r.returncode != 0:
        pytest.skip(f"Docker image {prod_image!r} not found — run: docker compose build agenthub")

    build_dir = tmp_path_factory.mktemp("agenthub-docker-bin")
    ah_bin = build_dir / "ah"

    # Extract ah binary from the image
    create_r = subprocess.run(
        ["docker", "create", prod_image], capture_output=True, text=True,
    )
    cid = create_r.stdout.strip()
    try:
        cp_r = subprocess.run(
            ["docker", "cp", f"{cid}:/usr/local/bin/ah", str(ah_bin)],
            capture_output=True, text=True,
        )
        if cp_r.returncode != 0:
            pytest.fail(
                f"Could not extract ah from {prod_image} — rebuild with: docker compose build agenthub\n"
                f"{cp_r.stderr}"
            )
        ah_bin.chmod(0o755)
    finally:
        subprocess.run(["docker", "rm", cid], capture_output=True)

    # Start the server container
    subprocess.run(["docker", "rm", "-f", container_name], capture_output=True)
    r = subprocess.run(
        ["docker", "run", "-d",
         "--name", container_name,
         "-p", f"{port}:8080",
         "-e", f"AGENTHUB_ADMIN_KEY={admin_key}",
         prod_image],
        capture_output=True, text=True,
    )
    if r.returncode != 0:
        pytest.fail(f"docker run {prod_image} failed: {r.stderr}")

    # Wait for health
    deadline = time.time() + 30
    while time.time() < deadline:
        try:
            with urllib.request.urlopen(f"{url}/api/health", timeout=2):
                print(f"[conftest] agenthub_docker ready at {url}", flush=True)
                break
        except Exception:
            time.sleep(0.5)
    else:
        subprocess.run(["docker", "rm", "-f", container_name], capture_output=True)
        pytest.fail(f"agenthub Docker container did not become healthy within 30s")

    yield {
        "url": url,
        "admin_key": admin_key,
        "ah_bin_dir": str(build_dir),
    }

    subprocess.run(["docker", "stop", container_name], capture_output=True)
    subprocess.run(["docker", "rm", container_name], capture_output=True)


@pytest.fixture(scope="session")
def agenthub_real(tmp_path_factory):
    """Build agenthub-server and ah binaries from source (go build), then run
    the server as a subprocess.  Falls back to Docker build if `go` is absent.
    Yields a dict with url, admin_key, ah_bin_dir.
    """
    build_dir = tmp_path_factory.mktemp("agenthub-build")
    server_bin = build_dir / "agenthub-server"
    ah_bin = build_dir / "ah"

    # --- try native go build first ---
    go_ok = False
    try:
        go_ok = subprocess.run(["go", "version"], capture_output=True).returncode == 0
    except FileNotFoundError:
        pass

    if go_ok:
        for target, out in [("./cmd/agenthub-server", server_bin), ("./cmd/ah", ah_bin)]:
            r = subprocess.run(
                ["go", "build", "-o", str(out), target],
                cwd=AGENTHUB_SRC, capture_output=True, text=True,
            )
            if r.returncode != 0:
                pytest.fail(f"go build {target} failed:\n{r.stderr}")
        ah_bin.chmod(0o755)
        use_docker_server = False
    else:
        # --- Docker fallback: build image, extract ah, run server as container ---
        docker_ok = subprocess.run(["docker", "info"], capture_output=True).returncode == 0
        if not docker_ok:
            pytest.skip("Neither go nor Docker available — skipping real agenthub tests")

        print(f"\n[conftest] Building agenthub Docker image {AGENTHUB_IMAGE!r}…", flush=True)
        r = subprocess.run(
            ["docker", "build", "-t", AGENTHUB_IMAGE, "."],
            cwd=AGENTHUB_SRC, capture_output=True, text=True,
        )
        if r.returncode != 0:
            pytest.fail(f"docker build agenthub failed:\n{r.stderr}")

        create_r = subprocess.run(
            ["docker", "create", AGENTHUB_IMAGE], capture_output=True, text=True,
        )
        cid = create_r.stdout.strip()
        try:
            subprocess.run(
                ["docker", "cp", f"{cid}:/usr/local/bin/ah", str(ah_bin)],
                check=True, capture_output=True,
            )
            ah_bin.chmod(0o755)
        finally:
            subprocess.run(["docker", "rm", cid], capture_output=True)
        use_docker_server = True

    # --- start server ---
    data_dir = tmp_path_factory.mktemp("agenthub-data")
    server_env = {**os.environ, "AGENTHUB_ADMIN_KEY": AGENTHUB_REAL_ADMIN_KEY}

    if use_docker_server:
        subprocess.run(["docker", "rm", "-f", AGENTHUB_CONTAINER], capture_output=True)
        r = subprocess.run(
            ["docker", "run", "-d",
             "--name", AGENTHUB_CONTAINER,
             "-p", f"{AGENTHUB_REAL_PORT}:8080",
             "-e", f"AGENTHUB_ADMIN_KEY={AGENTHUB_REAL_ADMIN_KEY}",
             AGENTHUB_IMAGE],
            capture_output=True, text=True,
        )
        if r.returncode != 0:
            pytest.fail(f"docker run agenthub failed: {r.stderr}")
        proc = None
    else:
        proc = subprocess.Popen(
            [str(server_bin),
             "--listen", f":{AGENTHUB_REAL_PORT}",
             "--data", str(data_dir)],
            env=server_env,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
        )

    # wait for health
    deadline = time.time() + 30
    while time.time() < deadline:
        try:
            with urllib.request.urlopen(f"{AGENTHUB_REAL_URL}/api/health", timeout=2):
                print(f"[conftest] Agenthub ready at {AGENTHUB_REAL_URL}", flush=True)
                break
        except Exception:
            time.sleep(0.5)
    else:
        if proc:
            proc.terminate()
        else:
            subprocess.run(["docker", "rm", "-f", AGENTHUB_CONTAINER], capture_output=True)
        pytest.fail(f"Real agenthub did not start within 30s on port {AGENTHUB_REAL_PORT}")

    yield {
        "url": AGENTHUB_REAL_URL,
        "admin_key": AGENTHUB_REAL_ADMIN_KEY,
        "ah_bin_dir": str(build_dir),
    }

    if proc:
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()
    else:
        subprocess.run(["docker", "stop", AGENTHUB_CONTAINER], capture_output=True)
        subprocess.run(["docker", "rm", AGENTHUB_CONTAINER], capture_output=True)
