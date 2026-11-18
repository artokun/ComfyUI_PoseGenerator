"""Read-only endpoints for the local thumbnail picker."""

from aiohttp import web

from .library import gallery_index, image_path, selected
from .prompts import compose_values, picture_order, render_prompt, with_picture_fields

IMAGE_TYPES = {".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".webp": "image/webp"}


async def get_catalog(request):
    try:
        galleries, data = gallery_index()
        payload = {"galleries": galleries}
        for kind, entries in data.items():
            payload[kind] = [
                {key: value for key, value in entry.items() if key not in ("image", "thumbnail", "_root")}
                for entry in entries.values()
            ]
        return web.json_response(payload)
    except (ValueError, OSError) as error:
        return web.json_response({"error": str(error)}, status=400)


async def get_image(request):
    kind = request.match_info["kind"]
    if kind not in ("pose", "outfit"):
        raise web.HTTPNotFound()
    try:
        entry = selected(kind, request.match_info["identity"])
        if entry is None:
            raise web.HTTPNotFound()
        field = "image" if request.query.get("full") == "1" else "thumbnail"
        path = image_path(entry[field], entry["_root"])
        if not path.is_file():
            raise web.HTTPNotFound()
        return web.FileResponse(path, headers={"X-Content-Type-Options": "nosniff", "Content-Type": IMAGE_TYPES[path.suffix.lower()]})
    except ValueError as error:
        raise web.HTTPNotFound(text=str(error)) from error


async def preview_prompt(request):
    try:
        body = await request.json()
        if not isinstance(body, dict):
            raise ValueError("Expected a prompt object.")
        pose = selected("pose", body.get("pose", "None"))
        outfit = selected("outfit", body.get("outfit", "None"))
        order = picture_order(bool(body.get("has_subject_image")), pose is not None, outfit is not None,
                              body.get("edit_target", "Subject"))
        values = compose_values(
            str(body.get("subject", "")), body.get("pose_override", "").strip() or (pose["prompt"] if pose else ""),
            body.get("outfit_override", "").strip() or (outfit["prompt"] if outfit else ""), str(body.get("extra", "")),
            order=order, picture_tag=str(body.get("picture_tag", "")), depth=body.get("pose_image_mode") == "Saved depth",
            describe=body.get("reference_text") != "Image only", location=str(body.get("location", "Custom")),
            pose_name=pose["label"] if pose else "", outfit_name=outfit["label"] if outfit else "",
            angle=pose.get("angle", "") if pose else "",
        )
        return web.json_response({"prompt": render_prompt(with_picture_fields(body.get("prompt_template", ""), values), values)})
    except (ValueError, TypeError, AttributeError) as error:
        return web.json_response({"error": str(error)}, status=400)


def register_routes():
    from server import PromptServer

    routes = PromptServer.instance.routes
    routes.get("/pose-generator/catalog")(get_catalog)
    routes.get("/pose-generator/image/{kind}/{identity}")(get_image)
    routes.post("/pose-generator/preview")(preview_prompt)
