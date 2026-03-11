"""
LLM-based utility for classifying PR review comments as pure approvals.
Kept outside routes.py so it can be unit-tested without Flask.
"""
import os
from typing import Optional


def _env(key: str) -> Optional[str]:
    v = (os.environ.get(key) or "").strip()
    return v or None


def classify_comment_is_approval(body: str, llm_settings: dict) -> bool:
    """Call the configured LLM with a single YES/NO question.

    Returns True  → comment is a pure approval, skip the agent.
    Returns False → comment needs action, fire the agent (also the safe default on error).

    `llm_settings` must have keys: model, url, api_key (any may be None/empty).
    """
    import requests as _requests

    if not body or not body.strip():
        return False

    model_name = (llm_settings.get("model") or "").strip()
    llm_url = (llm_settings.get("url") or "").rstrip("/")
    if not model_name or not llm_url:
        return False

    prompt = (
        "A bot posted this code review comment on a pull request. "
        "Does the comment indicate the code is APPROVED with NO actionable feedback or required changes? "
        "Answer only YES or NO.\n\n"
        f"Comment:\n{body[:2000]}"
    )

    headers = {"Content-Type": "application/json"}
    if llm_settings.get("api_key"):
        headers["Authorization"] = f"Bearer {llm_settings['api_key']}"

    use_responses_api = (
        model_name.startswith("gpt-5")
        or model_name.startswith("o3")
        or model_name.startswith("o4")
    )

    if use_responses_api:
        api_url = f"{llm_url}/responses"
        payload = {"model": model_name, "input": prompt, "max_output_tokens": 5}
    else:
        api_url = f"{llm_url}/chat/completions"
        payload = {
            "model": model_name,
            "messages": [{"role": "user", "content": prompt}],
            "temperature": 0,
            "max_tokens": 5,
        }

    resp = _requests.post(api_url, headers=headers, json=payload, timeout=30)
    resp.raise_for_status()
    raw = resp.json()

    if use_responses_api:
        content = ""
        for item in (raw.get("output") or []):
            if isinstance(item, dict):
                for part in (item.get("content") or []):
                    if isinstance(part, dict) and part.get("type") == "output_text":
                        content += part.get("text", "")
    else:
        content = raw.get("choices", [{}])[0].get("message", {}).get("content", "")

    answer = content.strip().upper()
    return answer.startswith("YES")
