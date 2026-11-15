"""透過 GitHub Contents API 將監控航線寫回倉庫。"""
from __future__ import annotations

import base64
import json
from urllib.error import HTTPError
from urllib.parse import quote
from urllib.request import Request, urlopen

import yaml

CONFIG_PATH = "config/routes.yaml"
API_ROOT = "https://api.github.com"


def update_routes(
    repository: str,
    branch: str,
    token: str,
    routes: list[dict[str, str]],
) -> str:
    """更新 routes.yaml，並回傳該次提交網址。"""
    parts = repository.strip().split("/")
    if len(parts) != 2 or not all(parts):
        raise ValueError("GitHub repository 格式應為 owner/repository。")

    endpoint = f"{API_ROOT}/repos/{parts[0]}/{parts[1]}/contents/{CONFIG_PATH}"
    headers = {
        "Accept": "application/vnd.github+json",
        "Authorization": f"Bearer {token}",
        "X-GitHub-Api-Version": "2022-11-28",
        "User-Agent": "flight-deals-streamlit",
    }

    current = _request_json(
        Request(f"{endpoint}?ref={quote(branch)}", headers=headers)
    )
    content = base64.b64decode(current["content"]).decode("utf-8")
    config = yaml.safe_load(content) or {}
    config["routes"] = routes

    updated_content = yaml.safe_dump(
        config, allow_unicode=True, sort_keys=False, default_flow_style=False
    )
    payload = {
        "message": "Update monitored flight routes from Streamlit",
        "content": base64.b64encode(updated_content.encode("utf-8")).decode("ascii"),
        "sha": current["sha"],
        "branch": branch,
    }
    result = _request_json(
        Request(
            endpoint,
            data=json.dumps(payload).encode("utf-8"),
            headers={**headers, "Content-Type": "application/json"},
            method="PUT",
        )
    )
    return result.get("commit", {}).get("html_url", "")


def _request_json(request: Request) -> dict:
    try:
        with urlopen(request, timeout=20) as response:
            return json.loads(response.read().decode("utf-8"))
    except HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        try:
            detail = json.loads(detail).get("message", detail)
        except json.JSONDecodeError:
            pass
        raise RuntimeError(f"GitHub API 回應 {exc.code}: {detail}") from exc
