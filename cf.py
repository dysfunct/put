from __future__ import annotations

import os
import json
from pathlib import Path
from typing import Any, Dict, List, Tuple

try:
    # ComfyUI server glue
    from aiohttp import web
    from server import PromptServer
except Exception:  # pragma: no cover - makes this importable outside Comfy
    web = None
    PromptServer = None


# Resolve ComfyUI root (this file expected at ComfyUI/custom_nodes/...)
ROOT = Path(__file__).resolve().parents[1]
COMFY_USER = os.environ.get("COMFY_USER", "default")
USER_DIR = Path(os.environ.get("COMFY_USER_DIR", str(ROOT / "user" / COMFY_USER)))
SRC_DIR = Path(os.environ.get("COMFY_WORKFLOWS_DIR", str(USER_DIR / "workflows")))
DST_DIR = Path(os.environ.get("COMFY_WORKFLOWS_API_DIR", str(USER_DIR / "workflows_api")))


def _is_api_graph(data: Any) -> bool:
    """Heuristic to detect Comfy API graph vs UI workflow.

    API: top-level mapping of node-id -> {class_type, inputs}
    UI: has top-level 'nodes' (list), 'links', positions, etc.
    """
    if not isinstance(data, dict):
        return False
    if "nodes" in data and isinstance(data.get("nodes"), list):
        return False
    # look for at least one node-like entry
    for k, v in data.items():
        if isinstance(k, str) and isinstance(v, dict) and "class_type" in v and "inputs" in v:
            return True
    return False


def _target_name(src_name: str) -> str:
    p = Path(src_name)
    if p.suffix == ".json" and p.stem.endswith(".api"):
        # already foo.api.json
        return p.name
    # produce foo.api.json next to original
    return f"{p.stem}.api.json"


def convert_all(
    source_dir: Path = SRC_DIR,
    dest_dir: Path = DST_DIR,
    overwrite: bool = False,
    glob: str = "*.json",
) -> Tuple[List[str], List[str]]:
    """Scan source_dir for JSON workflows and copy API-form files to dest_dir.

    Returns (converted, skipped_ui)
    - converted: list of written filenames (in dest_dir)
    - skipped_ui: list of source filenames detected as UI workflows (need Export API)
    """
    source_dir = Path(source_dir)
    dest_dir = Path(dest_dir)
    dest_dir.mkdir(parents=True, exist_ok=True)

    converted: List[str] = []
    skipped: List[str] = []

    for path in sorted(source_dir.glob(glob)):
        if not path.is_file():
            continue
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            skipped.append(path.name)
            continue

        if _is_api_graph(data):
            out_name = _target_name(path.name)
            out_path = dest_dir / out_name
            if out_path.exists() and not overwrite:
                continue
            out_path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
            converted.append(out_name)
        else:
            skipped.append(path.name)

    return converted, skipped


# ---- Custom Node ----

class CVBConvertWorkflows:
    @classmethod
    def INPUT_TYPES(cls):
        return {
            "required": {
                "source_dir": ("STRING", {"default": str(SRC_DIR)}),
                "dest_dir": ("STRING", {"default": str(DST_DIR)}),
            },
            "optional": {
                "overwrite": ("BOOLEAN", {"default": False}),
                "glob": ("STRING", {"default": "*.json"}),
            },
        }

    RETURN_TYPES = ("STRING", "STRING")  # summary, details_json
    RETURN_NAMES = ("summary", "details")
    FUNCTION = "run"
    CATEGORY = "CVB/Utils"

    def run(self, source_dir: str, dest_dir: str, overwrite: bool = False, glob: str = "*.json"):
        converted, skipped = convert_all(Path(source_dir), Path(dest_dir), overwrite, glob)
        summary = f"Converted: {len(converted)} | UI-only (needs API export): {len(skipped)}"
        details = json.dumps({
            "converted": converted,
            "skipped_ui": skipped,
            "source_dir": source_dir,
            "dest_dir": dest_dir,
        }, ensure_ascii=False)
        return (summary, details)


NODE_CLASS_MAPPINGS = {
    "CVBConvertWorkflows": CVBConvertWorkflows,
}

NODE_DISPLAY_NAME_MAPPINGS = {
    "CVBConvertWorkflows": "CVB: Convert Workflows (copy API)",
}


# ---- HTTP Endpoint (optional) ----

if PromptServer is not None and web is not None:

    async def http_convert_all(request: "web.Request") -> "web.Response":
        try:
            body = await request.json()
        except Exception:
            body = {}

        source_dir = Path(body.get("source_dir", str(SRC_DIR)))
        dest_dir = Path(body.get("dest_dir", str(DST_DIR)))
        overwrite = bool(body.get("overwrite", False))
        pattern = body.get("glob", "*.json")

        converted, skipped = convert_all(source_dir, dest_dir, overwrite, pattern)
        resp = {
            "source_dir": str(source_dir),
            "dest_dir": str(dest_dir),
            "converted": converted,
            "skipped_ui": skipped,
        }
        return web.json_response(resp, headers={"Access-Control-Allow-Origin": "*"})

    routes = PromptServer.instance.routes
    routes.post("/cvb/convert_all")(http_convert_all)

