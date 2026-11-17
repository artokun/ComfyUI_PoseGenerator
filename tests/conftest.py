import sys
import types
from pathlib import Path

import pytest
from aiohttp import web

ROOT = Path(__file__).resolve().parents[1]
# Load the package's real modules without its ComfyUI entry-point side effects.
package = types.ModuleType("pose_generator")
package.__path__ = [str(ROOT)]
sys.modules["pose_generator"] = package
sys.path.insert(0, str(ROOT))
# Pytest also imports the repository entry point during package collection.
# Only the host registration surface is stubbed; node and HTTP handlers are real.
host = types.ModuleType("server")
host.PromptServer = types.SimpleNamespace(instance=types.SimpleNamespace(routes=web.RouteTableDef()))
sys.modules.setdefault("server", host)


@pytest.fixture
def local_library(tmp_path, monkeypatch):
    import json
    from PIL import Image
    from pose_generator.library import _read_catalog

    monkeypatch.setenv("POSE_GENERATOR_ASSETS", str(tmp_path))
    _read_catalog.cache_clear()
    (tmp_path / "img").mkdir()
    (tmp_path / "thumbs").mkdir()
    for identity, color in (("pose-a", (255, 0, 0)), ("outfit-a", (0, 255, 0))):
        Image.new("RGB", (12, 8), color).save(tmp_path / "img" / f"{identity}.png")
        Image.new("RGB", (6, 4), color).save(tmp_path / "thumbs" / f"{identity}.png")
    def entry(identity, prompt):
        return {"id": identity, "label": identity, "prompt": prompt, "category": "Test",
                "image": f"img/{identity}.png", "thumbnail": f"thumbs/{identity}.png", "angle": "front view"}
    data = {"schema_version": 1, "pose": [entry("pose-a", "standing, front view")],
            "outfit": [entry("outfit-a", "wearing a blue jacket")]}
    (tmp_path / "catalog.json").write_text(json.dumps(data))
    yield tmp_path
    _read_catalog.cache_clear()


@pytest.fixture
async def http_client(local_library):
    from aiohttp import web
    from aiohttp.test_utils import TestClient, TestServer
    from pose_generator.routes import get_catalog, get_image, preview_prompt

    app = web.Application()
    app.router.add_get("/pose-generator/catalog", get_catalog)
    app.router.add_get("/pose-generator/image/{kind}/{identity}", get_image)
    app.router.add_post("/pose-generator/preview", preview_prompt)
    async with TestClient(TestServer(app)) as client:
        yield client
