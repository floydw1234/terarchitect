"""GitHub helpers for release PR creation. Only the minimal utility functions needed for
interacting with GitHub — everything PR-per-ticket related has been removed."""
import os
import re
from urllib.parse import urlparse

try:
    from utils.app_settings import get_dashboard_git_env, get_gh_env_for_user
except (ModuleNotFoundError, ImportError):
    from backend.utils.app_settings import get_dashboard_git_env, get_gh_env_for_user


def env_for_gh_user():
    """Env for gh CLI calls (release PR, merge). Uses stored user token and dashboard git identity."""
    return {**os.environ, **get_gh_env_for_user(), **get_dashboard_git_env()}


def repo_slug_from_github_url(url):
    """Extract owner/repo from https://github.com/owner/repo. Returns None if not parseable."""
    if not url or not isinstance(url, str):
        return None
    url = url.strip().rstrip("/")
    if "github.com" not in url:
        return None
    path = url.split("github.com")[-1].strip("/")
    parts = path.split("/")
    if len(parts) < 2:
        return None
    slug = "/".join(parts[:2])
    if not re.match(r'^[a-zA-Z0-9_.-]+/[a-zA-Z0-9_.-]+$', slug):
        return None
    return slug


def normalize_github_repo_url(url):
    """Normalize common GitHub remote forms to a comparable owner/repo slug."""
    if not url or not isinstance(url, str):
        return None

    raw = url.strip()
    if not raw:
        return None

    ssh_match = re.match(r"^git@github\.com:(?P<slug>[^/]+/[^/]+?)(?:\.git)?/?$", raw, re.IGNORECASE)
    if ssh_match:
        return ssh_match.group("slug").lower()

    parsed = urlparse(raw)
    if parsed.scheme and parsed.netloc.lower() == "github.com":
        path = (parsed.path or "").strip("/")
    elif raw.lower().startswith("github.com/"):
        path = raw.split("/", 1)[1].strip("/")
    else:
        return None

    if path.endswith(".git"):
        path = path[:-4]
    parts = [part for part in path.split("/") if part]
    if len(parts) < 2:
        return None
    slug = "/".join(parts[:2])
    if not re.match(r'^[a-zA-Z0-9_.-]+/[a-zA-Z0-9_.-]+$', slug):
        return None
    return slug.lower()
