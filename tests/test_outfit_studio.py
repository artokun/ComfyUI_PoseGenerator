import io
import json

from PIL import Image
import pytest

from scripts import generate_pose_references as generation
from scripts import outfit_studio


def png(size=(300, 450), colour=(230, 230, 228)):
    buffer = io.BytesIO()
    Image.new("RGB", size, colour).save(buffer, "PNG")
    return buffer.getvalue()


@pytest.fixture
def studio(tmp_path, monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    monkeypatch.setenv("XAI_API_KEY", "test-key")
    calls = []

    def fake_render(prompt, images, key, quality="high"):
        calls.append({"prompt": prompt, "images": len(images)})
        if "FAIL" in prompt:
            raise generation.BatchError("OpenAI refused this image (moderation). Try the SpicyAI provider.")
        return png((1024, 1536))

    def fake_caption(content, provider, key):
        return {"label": "Pink cardigan, white sneakers", "prompt": "a pink cardigan and white sneakers", "category": "Cute",
                "tag_groups": {"Top": ["Cardigan"], "Feet": ["sneakers"], "Nope": ["x"], "Colors": ["pink", "pink"]},
                "checks": {"logo": False, "body": False, "note": ""}}

    instance = outfit_studio.OutfitStudio(tmp_path, openai_render_fn=fake_render, caption_fn=fake_caption)
    instance.calls = calls
    return instance


def photo_run(studio, count=2, **settings):
    uploads = [{"id": studio.add_upload(png(colour=(10 * n, 0, 0)))["id"], "name": f"photo {n}.jpg"} for n in range(count)]
    return studio.create_run({"provider": "openai", "mode": "photo", "uploads": uploads, "use_style": False, **settings})


def test_photo_run_poses_then_extracts_every_item_at_three_by_four(studio):
    run = photo_run(studio, pose="i-pose")
    assert run["status"] == "queued" and run["queue_position"] == 0
    studio.work(run["id"])
    run = studio.public(studio.run(run["id"]))
    assert run["status"] == "completed"
    assert [(item["state"], item["posed"], item["outfit"], item["requests"]) for item in run["items"]] == [("saved", True, True, 2)] * 2
    assert "arms hanging straight down" in studio.calls[0]["prompt"] and "Remove the person completely" in studio.calls[1]["prompt"]
    with Image.open(studio.file(run["id"], 1, "outfit")) as image:
        assert image.size == outfit_studio.OUTPUT_SIZE
    assert run["items"][0]["urls"]["outfit"].endswith("/outfit?v=2") and "receipts" not in run["items"][0]


def test_skip_posing_and_text_runs_take_one_step(studio):
    run = photo_run(studio, count=1, pose="skip")
    studio.work(run["id"])
    assert len(studio.calls) == 1 and studio.run(run["id"])["items"][0]["posed"] is False
    run = studio.create_run({"provider": "openai", "mode": "text", "texts": ["a red knit dress and black boots"], "use_style": False})
    studio.work(run["id"])
    assert "The outfit: a red knit dress and black boots" in studio.calls[-1]["prompt"]
    assert studio.run(run["id"])["items"][0]["prompt"] == "wearing a red knit dress and black boots"


def test_a_failed_item_does_not_stop_the_run_and_can_be_regenerated(studio):
    run = studio.create_run({"provider": "openai", "mode": "text", "use_style": False,
                             "texts": ["a FAIL dress", "a white tee and jeans"]})
    studio.work(run["id"])
    saved = studio.run(run["id"])
    assert saved["status"] == "completed"
    assert [item["state"] for item in saved["items"]] == ["failed", "saved"]
    assert "moderation" in saved["items"][0]["error"]
    studio.calls.clear()
    studio.regenerate(run["id"], 1, "extract")
    studio.work(run["id"])
    assert len(studio.calls) == 1  # only the outfit that was reset is rendered again
    assert studio.run(run["id"])["items"][1]["revision"] == 3


def test_pause_stops_between_items_and_resume_finishes_without_repeating(studio):
    run = photo_run(studio, count=3, pose="skip")
    original = studio.openai_render

    def pausing(prompt, images, key, quality="high"):
        studio.pause_requested.add(run["id"])
        return original(prompt, images, key)

    studio.openai_render = pausing
    studio.work(run["id"])
    saved = studio.run(run["id"])
    assert saved["status"] == "paused" and [item["outfit"] for item in saved["items"]] == [True, False, False]
    studio.openai_render = original
    studio.resume(run["id"])
    studio.work(run["id"])
    assert len(studio.calls) == 3
    assert studio.run(run["id"])["status"] == "completed"


def test_restart_pauses_active_runs_instead_of_resuming_paid_work(studio, tmp_path):
    run = photo_run(studio, count=1)
    studio.change(run["id"], lambda saved: (saved.update(status="running"), saved["items"][0].update(state="posing")) and None)
    reopened = outfit_studio.OutfitStudio(tmp_path, openai_render_fn=studio.openai_render)
    saved = reopened.run(run["id"])
    assert saved["status"] == "paused" and "Resume" in saved["error"] and saved["items"][0]["state"] == "waiting"
    assert reopened.queue == []


def test_missing_key_pauses_the_run_with_the_reason(studio, monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY")
    run = photo_run(studio, count=1, pose="skip")
    studio.work(run["id"])
    saved = studio.run(run["id"])
    assert saved["status"] == "paused" and "OPENAI_API_KEY" in saved["error"] and saved["items"][0]["state"] == "waiting"


def test_spicy_receipt_is_saved_before_the_job_and_reused_on_resume(studio):
    class Client:
        def __init__(self):
            self.created = 0

        def upload(self, content):
            return {"uri": "spicy://f/x"}

        def api(self, method, path, payload=None, params=None, idempotency_key=None):
            if path == "/jobs/quote":
                return {"maxCharge": "0.05", "currency": "USD", "quoteId": "q", "estimatedCost": "0.04"}
            self.created += 1
            return {"taskId": "task-1"}

        def poll(self, task_id, timeout, observe):
            if getattr(self, "down", False):
                raise generation.BatchError("Task is still pending.")
            return {"output": {"assets": [{"url": "https://x"}]}}

        def storage(self, method, url):
            return type("R", (), {"content": png((1024, 1365))})()

    client = Client()
    studio.spicy_factory = lambda: client
    upload = studio.add_upload(png())
    run = studio.create_run({"provider": "spicy", "mode": "photo", "pose": "skip", "use_style": False,
                             "uploads": [{"id": upload["id"], "name": "a.jpg"}], "max_charge": "0.06", "total_cap": "1"})
    client.down = True
    studio.work(run["id"])
    saved = studio.run(run["id"])
    assert saved["items"][0]["state"] == "failed" and saved["items"][0]["receipts"]["extract"]["task_id"] == "task-1"
    client.down = False
    studio.change(run["id"], lambda r: r["items"][0].update(state="waiting"))
    studio.resume(run["id"])
    studio.work(run["id"])
    assert client.created == 1  # the accepted job is polled again, never submitted twice
    assert studio.public(studio.run(run["id"]))["items"][0]["charged"] == "0.05"


def test_spicy_total_cap_pauses_the_run(studio):
    class Client:
        def upload(self, content):
            return {"uri": "spicy://f/x"}

        def api(self, method, path, payload=None, params=None, idempotency_key=None):
            return {"maxCharge": "0.05", "currency": "USD", "quoteId": "q", "estimatedCost": "0.04", "taskId": "t"}

        def poll(self, task_id, timeout, observe):
            return {"output": {"assets": [{"url": "https://x"}]}}

        def storage(self, method, url):
            return type("R", (), {"content": png()})()

    studio.spicy_factory = Client
    uploads = [{"id": studio.add_upload(png(colour=(n, n, n)))["id"], "name": str(n)} for n in range(3)]
    run = studio.create_run({"provider": "spicy", "mode": "photo", "pose": "skip", "use_style": False, "uploads": uploads,
                             "max_charge": "0.06", "total_cap": "0.10"})
    studio.work(run["id"])
    saved = studio.run(run["id"])
    assert saved["status"] == "paused" and "total cap" in saved["error"]
    assert [item["outfit"] for item in saved["items"]] == [True, True, False]


def test_captions_fill_metadata_and_save_uses_the_naming_convention(studio, tmp_path):
    run = photo_run(studio, count=2, pose="skip")
    studio.work(run["id"])
    studio.start_caption(run["id"], "xai")
    saved = studio.run(run["id"])
    assert saved["caption"] == {"running": False, "done": 2, "total": 2, "error": None}
    item = saved["items"][0]
    assert item["prompt"] == "wearing a pink cardigan and white sneakers"
    assert item["tag_groups"] == {"Top": ["cardigan"], "Feet": ["sneakers"], "Colors": ["pink"]}
    studio.edit(run["id"], 1, {"label": "Navy blazer", "prompt": "a navy blazer", "category": "Work", "tags": "Navy, blazer"})
    studio.set_deleted(run["id"], 0, True)
    result = studio.save(run["id"], {"gallery": "My Outfits"})
    assert result["saved"] == 1
    catalog = json.loads((tmp_path / "assets/My Outfits/catalog.json").read_text())
    row = catalog["outfit"][0]
    assert (row["id"], row["prompt"], row["tags"]) == ("work_O001", "wearing a navy blazer", ["blazer", "navy"])
    assert (tmp_path / "assets/My Outfits" / row["image"]).is_file() and (tmp_path / "assets/My Outfits" / row["thumbnail"]).is_file()
    with pytest.raises(generation.BatchError, match="No finished outfits"):
        studio.save(run["id"], {"gallery": "My Outfits"})  # already saved; the deleted one stays out
    assert studio.galleries() == ["My Outfits"]


@pytest.mark.parametrize("name", ["gallery", "gallery-mannequin", "../escape", "registry", "/"])
def test_save_refuses_reserved_or_unsafe_gallery_names(studio, name):
    run = photo_run(studio, count=1, pose="skip")
    studio.work(run["id"])
    with pytest.raises(generation.BatchError):
        studio.save(run["id"], {"gallery": name})


def test_bad_input_is_rejected(studio):
    with pytest.raises(generation.BatchError):
        studio.run("../../etc")
    with pytest.raises(generation.BatchError):
        studio.create_run({"provider": "nope", "mode": "text", "texts": ["a dress"]})
    with pytest.raises(generation.BatchError):
        studio.create_run({"provider": "openai", "mode": "photo", "uploads": [{"id": "0" * 20}]})
    with pytest.raises(generation.BatchError):
        studio.add_upload(b"not an image")


def test_padding_continues_the_backdrop_instead_of_a_flat_corner_colour():
    image = Image.new("RGB", (960, 1536))  # narrower than the 2:3 output, so the sides are padded
    for y in range(1536):  # vertical vignette: darker at the top, lighter at the bottom
        image.paste((180 + y * 60 // 1536,) * 3, (0, y, 960, y + 1))
    buffer = io.BytesIO()
    image.save(buffer, "PNG")
    with Image.open(io.BytesIO(outfit_studio.fit_output(buffer.getvalue()))) as fitted:
        assert fitted.size == outfit_studio.OUTPUT_SIZE
        for y in (10, 768, 1520):
            edge, inside = fitted.getpixel((5, y)), fitted.getpixel((200, y))
            assert abs(edge[0] - inside[0]) <= 2


def test_collage_runs_assemble_the_pieces_in_one_step_and_strip_logos(studio):
    run = photo_run(studio, count=1, pose="collage")
    studio.work(run["id"])
    saved = studio.run(run["id"])
    assert len(studio.calls) == 1 and saved["items"][0]["state"] == "saved" and saved["items"][0]["posed"] is False
    prompt = studio.calls[0]["prompt"]
    assert "flat lay or collage" in prompt and "Replace every brand name, logo" in prompt and "perfume bottles" in prompt
    with pytest.raises(generation.BatchError):
        studio.regenerate(run["id"], 0, "pose")  # nothing to pose


def test_caption_check_holds_back_outfits_with_a_logo_until_confirmed_or_regenerated(studio, tmp_path):
    run = photo_run(studio, count=2, pose="collage")
    studio.work(run["id"])
    flagged = {"label": "Denim top", "prompt": "a denim top", "category": "Y2K", "tag_groups": {},
               "checks": {"logo": True, "body": False, "note": "emblem on the top"}}
    clean = {**flagged, "label": "Clean top", "checks": {"logo": False, "body": False, "note": ""}}
    replies = iter([flagged, clean])
    studio.caption_fn = lambda content, provider, key: next(replies)
    studio.start_caption(run["id"], "xai")
    items = studio.public(studio.run(run["id"]))["items"]
    assert [item["held"] for item in items] == [True, False] and items[0]["checks"]["note"] == "emblem on the top"
    result = studio.save(run["id"], {"gallery": "My Outfits"})
    assert (result["saved"], result["held"]) == (1, 1)
    with pytest.raises(generation.BatchError, match="held back"):
        studio.save(run["id"], {"gallery": "My Outfits"})
    studio.edit(run["id"], 0, {"label": "Denim top", "prompt": "a denim top", "cleared": True})
    assert studio.save(run["id"], {"gallery": "My Outfits"})["saved"] == 1
    # Regenerating throws the old verdict away.
    studio.regenerate(run["id"], 1, "extract")
    assert studio.run(run["id"])["items"][1]["checks"] is None


def test_results_match_the_bundled_gallery_size():
    assert outfit_studio.OUTPUT_SIZE == (1024, 1536)


def test_two_step_is_the_default_and_neither_step_may_invent_pieces(studio):
    upload = studio.add_upload(png())
    run = studio.create_run({"provider": "openai", "mode": "photo", "use_style": False, "uploads": [{"id": upload["id"], "name": "a.jpg"}]})
    assert run["pose"] == "i-pose"
    studio.work(run["id"])
    pose_prompt, extract_prompt = (call["prompt"] for call in studio.calls)
    assert "arms hanging straight down" in pose_prompt and "do not invent garments, accessories, socks or shoes" in pose_prompt
    assert "making them invisible" in extract_prompt and "only the body disappears" in extract_prompt
    assert [call["images"] for call in studio.calls] == [1, 1]  # no style reference on either step
