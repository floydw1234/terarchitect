"""GitHub helpers for release PR creation. Only the minimal utility functions needed for
interacting with GitHub — everything PR-per-ticket related has been removed."""
import os
import re

from utils.app_settings import get_dashboard_git_env, get_gh_env_for_user


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
