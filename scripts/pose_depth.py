"""Create a grayscale DA3 map using an existing ComfyUI server."""
import io
import json
import time
import uuid
from pathlib import PurePosixPath

from PIL import Image
import requests

try:
    from . import generate_pose_references as generation
except ImportError:
    import generate_pose_references as generation


def workflow(image, model):
    return {
        "1": {"class_type": "LoadImage", "inputs": {"image": image}},
        "2": {"class_type": "LoadDA3Model", "inputs": {"model_name": model, "weight_dtype": "default"}},
        "3": {"class_type": "DA3Inference", "inputs": {"image": ["1", 0], "da3_model": ["2", 0],
              "mode": "mono", "resolution": 756, "resize_method": "upper_bound_resize"}},
        "4": {"class_type": "DA3Render", "inputs": {"da3_geometry": ["3", 0], "output": "depth",
              "output.normalization": "v2_style", "output.apply_sky_clip": False}},
        "5": {"class_type": "SaveImage", "inputs": {"images": ["4", 0], "filename_prefix": "pose-studio/depth"}},
    }


def choose_model(session, url):
    reply = session.get(url + "/object_info/LoadDA3Model", timeout=(5, 20))
    reply.raise_for_status()
    definition = reply.json().get("LoadDA3Model", {})
    field = definition.get("input", {}).get("required", {}).get("model_name", [])
    options = []
    if field and isinstance(field[0], list):
        options = field[0]
    elif len(field) > 1:
        options = field[1].get("options", [])
    for stem in ("mono_large", "base", "small", "metric_large", "large"):
        for name in options:
            if PurePosixPath(name.replace("\\", "/")).name.lower() == f"depth_anything_3_{stem}.safetensors":
                return name
    raise generation.BatchError("Install a Depth Anything 3 checkpoint in the ComfyUI server's models/geometry_estimation folder.")


def generate(content, digest, folder, url):
    url = url.rstrip("/")
    output = folder / (digest + ".png")
    receipt = folder / "jobs" / (digest + ".json")
    if output.is_file():
        return
    with requests.Session() as session:
        if receipt.exists():
            saved = json.loads(receipt.read_text())
            if saved["server"] != url:
                raise generation.BatchError("Resume this depth job on the ComfyUI server that accepted it.")
            job = saved["id"]
            queue = session.get(url + "/queue", timeout=(5, 20))
            queue.raise_for_status()
            active = queue.json()
            if not any(row[1] == job for row in active.get("queue_running", []) + active.get("queue_pending", [])):
                history = session.get(url + "/history/" + job, timeout=(5, 20))
                history.raise_for_status()
                if job not in history.json():
                    receipt.unlink()
                    raise generation.BatchError("ComfyUI has no record of the previous job. Run the depth pass again to submit it.")
        else:
            model = choose_model(session, url)
            filename = digest + ".png"
            upload = session.post(url + "/upload/image", files={"image": (filename, content, "image/png")},
                                  data={"subfolder": "pose-studio", "type": "input", "overwrite": "true"}, timeout=(5, 60))
            upload.raise_for_status()
            stored = upload.json()
            job = str(uuid.uuid4())
            graph = workflow(str(PurePosixPath(stored.get("subfolder", ""), stored["name"])), model)
            generation.write_json(receipt, {"id": job, "server": url})
            response = session.post(url + "/prompt", json={"prompt": graph, "prompt_id": job}, timeout=(5, 30))
            if 400 <= response.status_code < 500:
                receipt.unlink()
            response.raise_for_status()
            job = response.json()["prompt_id"]
            generation.write_json(receipt, {"id": job, "server": url})
        deadline = time.monotonic() + 600
        while time.monotonic() < deadline:
            response = session.get(url + "/history/" + job, timeout=(5, 20))
            response.raise_for_status()
            record = response.json().get(job, {})
            if record.get("status", {}).get("status_str") == "error":
                receipt.unlink(missing_ok=True)
                raise generation.BatchError("The ComfyUI depth workflow failed. See its execution history.")
            images = record.get("outputs", {}).get("5", {}).get("images", [])
            if images:
                params = {key: images[0][key] for key in ("filename", "subfolder", "type")}
                result = session.get(url + "/view", params=params, timeout=(5, 60))
                result.raise_for_status()
                with Image.open(io.BytesIO(content)) as source, Image.open(io.BytesIO(result.content)) as depth:
                    if depth.format != "PNG" or depth.size != source.size:
                        raise generation.BatchError("The depth map dimensions do not match the drawing.")
                    depth.verify()
                generation.atomic_write(output, result.content)
                receipt.unlink(missing_ok=True)
                return
            time.sleep(2)
    raise generation.BatchError("Depth request timed out. Run it again to check the saved ComfyUI job.")
