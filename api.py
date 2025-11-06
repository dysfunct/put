from __future__ import annotations

import os
import json
from pathlib import Path
from typing import Any, Dict, List

from aiohttp import web
from server import PromptServer

ROOT = Path(__file__).resolve().parents[1]  # ComfyUI root
COMFY_USER = os.environ.get("COMFY_USER", "default")
USER_DIR = Path(os.environ.get("COMFY_USER_DIR", str(ROOT / "user" / COMFY_USER)))
WORKFLOWS_DIR = Path(os.environ.get("COMFY_WORKFLOWS_DIR", str(USER_DIR / "workflows")))


def _is_api_graph(data: Any) -> bool:
    if not isinstance(data, dict):
        return False
    if "nodes" in data and isinstance(data.get("nodes"), list):
        return False
    for k, v in data.items():
        if isinstance(k, str) and isinstance(v, dict) and "class_type" in v and "inputs" in v:
            return True
    return False


def _load_json(path: Path) -> Dict[str, Any]:
    with path.open("r", encoding="utf-8") as f:
        return json.load(f)


def _meta_from(path: Path, data: Dict[str, Any]) -> Dict[str, Any]:
    meta = {}
    if isinstance(data, dict):
        meta = data.get("cvb_meta", {}) or {}
    title = meta.get("title") or path.stem.replace("-", " ").title()
    desc = meta.get("description", "")
    tags = meta.get("tags", [])
    version = meta.get("version", "1")
    return {
        "id": meta.get("id") or path.stem,
        "title": title,
        "description": desc,
        "tags": tags,
        "version": version,
    }


def _list_files() -> List[Path]:
    return sorted(p for p in WORKFLOWS_DIR.glob("*.json") if p.is_file())


async def list_workflows(_request: web.Request) -> web.Response:
    items: List[Dict[str, Any]] = []
    # Index all files and note api companions (foo.api.json)
    names = {p.name: p for p in _list_files()}
    for name, p in names.items():
        try:
            data = _load_json(p)
            meta = _meta_from(p, data)
            is_api = _is_api_graph(data) or p.stem.endswith(".api")

            # If UI file has a companion .api.json, prefer that in template_url
            companion = None
            if not is_api:
                api_name = f"{p.stem}.api.json"
                if api_name in names:
                    companion = names[api_name]
                    try:
                        is_api = _is_api_graph(_load_json(companion)) or True
                    except Exception:
                        pass

            # Build URLs
            if companion is not None:
                template_url = f"/cvb/workflows/{companion.name}?format=api"
            else:
                template_url = f"/cvb/workflows/{p.name}?format=api"

            entry = {
                **meta,
                "filename": p.name,
                "is_api": bool(is_api),
                "raw_url": f"/cvb/workflows/{p.name}?format=raw",
                "template_url": template_url,
            }
            items.append(entry)
        except Exception as e:
            items.append({
                "id": p.stem,
                "title": p.stem,
                "description": f"Failed to parse: {e}",
                "filename": p.name,
                "is_api": False,
                "raw_url": f"/cvb/workflows/{p.name}?format=raw",
                "template_url": f"/cvb/workflows/{p.name}?format=api",
                "error": True,
            })

    return web.json_response(items, headers={"Access-Control-Allow-Origin": "*"})


async def get_workflow(request: web.Request) -> web.Response:
    name = Path(request.match_info.get("name", "")).name
    fmt = request.query.get("format", "raw").lower()
    path = WORKFLOWS_DIR / name
    if not (path.exists() and path.is_file() and path.suffix == ".json"):
        return web.Response(status=404, text="Workflow not found")
    try:
        text = path.read_text(encoding="utf-8")
        data = json.loads(text)
        if fmt == "raw":
            return web.Response(text=text, content_type="application/json",
                                headers={"Access-Control-Allow-Origin": "*"})
        elif fmt == "api":
            if _is_api_graph(data):
                return web.Response(text=text, content_type="application/json",
                                    headers={"Access-Control-Allow-Origin": "*"})
            return web.Response(
                status=422,
                text="Workflow is a UI workflow, not an API graph. Export API (JSON) and save as .api.json.",
                headers={"Access-Control-Allow-Origin": "*"}
            )
        else:
            return web.Response(status=400, text="Invalid format; use raw|api")
    except Exception as e:
        return web.Response(status=500, text=f"Failed to read workflow: {e}")


async def get_template_alias(request: web.Request) -> web.Response:
    # /cvb/templates/{name}.json → /cvb/workflows/{name}.json?format=api
    name = Path(request.match_info.get("name", "")).name
    # Directly delegate to get_workflow with api format.
    # We construct a fake request by copying and adjusting the query.
    q = request.rel_url.query.copy()
    q["format"] = "api"
    request._rel_url = request.rel_url.with_query(q)
    request.match_info["name"] = name + ".json" if not name.endswith(".json") else name
    return await get_workflow(request)


# Register routes
routes = PromptServer.instance.routes
routes.get("/cvb/workflows")(list_workflows)
routes.get("/cvb/workflows/{name}.json")(get_workflow)
routes.get("/cvb/templates/{name}.json")(get_template_alias)

