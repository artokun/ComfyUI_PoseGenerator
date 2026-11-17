import io
import json

from PIL import Image


async def test_catalog_and_thumbnail_routes(http_client):
    response = await http_client.get("/pose-generator/catalog")
    assert response.status == 200
    data = await response.json()
    assert data["pose"][0]["id"] == "pose-a"
    assert "image" not in data["pose"][0]
    assert "thumbnail" not in data["pose"][0]
    for suffix, dimensions in (("", (6, 4)), ("?full=1", (12, 8))):
        response = await http_client.get("/pose-generator/image/pose/pose-a" + suffix)
        assert response.status == 200
        assert response.headers["X-Content-Type-Options"] == "nosniff"
        assert Image.open(io.BytesIO(await response.read())).size == dimensions


async def test_unknown_ids_and_kinds_do_not_serve_files(http_client):
    for path in ("outfit/pose-a", "pose/None", "pose/missing", "secret/pose-a", "pose/%2E%2E%2Fcatalog.json"):
        response = await http_client.get("/pose-generator/image/" + path)
        assert response.status == 404


async def test_prompt_preview_matches_node_output(http_client):
    from pose_generator.nodes import PoseOutfitReference
    from pose_generator.prompts import DEFAULT_TEMPLATE

    body = {"pose": "pose-a", "outfit": "outfit-a", "subject": "a traveler",
            "prompt_template": DEFAULT_TEMPLATE, "extra": "soft light"}
    response = await http_client.post("/pose-generator/preview", json=body)
    assert response.status == 200
    assert (await response.json())["prompt"] == PoseOutfitReference().generate(**body)[2]
    body["prompt_template"] = "{bad}"
    response = await http_client.post("/pose-generator/preview", json=body)
    assert response.status == 400
    assert "Unsupported" in (await response.json())["error"]


async def test_malformed_preview_request(http_client):
    for payload in ([], {"pose_override": None}, {"prompt_template": 4}):
        response = await http_client.post("/pose-generator/preview", json=payload)
        assert response.status == 400


async def test_webp_content_type_does_not_depend_on_system_mime_database(http_client, local_library):
    Image.new("RGB", (12, 8), "blue").save(local_library / "img/pose-a.webp", format="WEBP")
    path = local_library / "catalog.json"
    data = json.loads(path.read_text())
    data["pose"][0]["image"] = "img/pose-a.webp"
    path.write_text(json.dumps(data))
    response = await http_client.get("/pose-generator/image/pose/pose-a?full=1")
    assert response.status == 200
    assert response.headers["Content-Type"] == "image/webp"
    assert Image.open(io.BytesIO(await response.read())).format == "WEBP"
