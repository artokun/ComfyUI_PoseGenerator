import asyncio
import io
import json
import threading
import uuid
from pathlib import Path
from types import SimpleNamespace

from aiohttp import web
from aiohttp.test_utils import make_mocked_request
from PIL import Image
import pytest

from scripts import generate_pose_references as generation
from scripts import pose_studio as studio


def png(color="white"):
    output = io.BytesIO()
    Image.new("RGB", (12, 16), color).save(output, format="PNG")
    return output.getvalue()


class Client:
    key_fingerprint = "private-key-fingerprint"

    def __init__(self):
        self.creates = []
        self.price = "0.051"
        self.lose_response = False
        self.after_poll = lambda: None
        self.output = png()

    def upload(self, content):
        return {"uri": "spicy://f/fil_private", "expiresAt": "2099-01-01T00:00:00Z"}

    def api(self, method, path, **kwargs):
        if path == "/jobs/quote":
            return {"quoteId": "private-quote", "estimatedCost": self.price,
                    "maxCharge": self.price, "currency": "USD"}
        self.creates.append(kwargs)
        if self.lose_response:
            raise generation.BatchError("Response lost")
        return {"taskId": "private-task"}

    def poll(self, task_id, timeout, observe):
        record = {"state": "succeeded", "cost": self.price, "settled": True,
                  "output": {"assets": [{"url": "https://example.test/private-signature"}]}}
        observe(record)
        self.after_poll()
        return record

    def storage(self, method, url):
        return SimpleNamespace(content=self.output)


@pytest.fixture
def setup(tmp_path):
    client = Client()
    manager = studio.Studio(tmp_path, lambda: client)
    source = manager.new_source("Test folder")
    manager.add_image(source["id"], "nested/one.png", png())
    manager.add_image(source["id"], "two.png", png())
    body = {"source_id": source["id"], "files": ["nested/one.png", "two.png"],
            "prompt": "Edit source Image 1 using style Image 2.", "style_uri": "spicy://f/fil_style",
            "resolution": "2k", "image_order": "source-first", "per_image": "0.051", "total": "0.102"}
    return manager, client, body


@pytest.mark.parametrize("name", ["../secret.png", "/tmp/secret.png", "folder/../../secret.png", "a\\secret.png", "a//b.png", ".env", "nested/.hidden.png", "\x00.png"])
def test_upload_paths_cannot_escape_or_read_hidden_files(tmp_path, name):
    with pytest.raises(generation.BatchError):
        studio.child(tmp_path, name)


def test_symlinks_cannot_escape_upload_directory(tmp_path):
    root = tmp_path / "uploads"
    root.mkdir()
    (root / "escape").symlink_to(tmp_path, target_is_directory=True)
    with pytest.raises(generation.BatchError, match="outside"):
        studio.child(root, "escape/image.png")


def test_invalid_upload_does_not_change_manifest(setup):
    manager, _, body = setup
    original = manager.source(body["source_id"])
    with pytest.raises(generation.BatchError, match="readable"):
        manager.add_image(body["source_id"], "bad.png", b"not an image")
    assert manager.source(body["source_id"]) == original


def test_uploaded_folder_is_immutable_after_start(setup):
    manager, _, body = setup
    manager.create_run(body)
    with pytest.raises(generation.BatchError, match="saved run"):
        manager.add_image(body["source_id"], "new.png", png())


@pytest.mark.parametrize("changes", [{"files": ["unknown.png"]}, {"files": ["two.png", "two.png"]},
                                      {"prompt": ""}, {"prompt": "x" * 5001}, {"total": "NaN"},
                                      {"per_image": "-1"}, {"style_uri": "https://localhost/private"}])
def test_invalid_run_never_submits(setup, changes):
    manager, client, body = setup
    with pytest.raises(generation.BatchError):
        manager.create_run({**body, **changes})
    assert not client.creates
    assert not list(manager.outputs.glob("*/studio.json"))


async def test_live_status_results_and_resume_do_not_duplicate_charges(setup):
    manager, client, body = setup
    identity = manager.create_run(body)
    manager.start(identity)
    await manager.task
    result = manager.summary(identity)
    assert result["status"] == "completed"
    assert result["completed"] == 2
    assert result["reserved"] == result["settled"] == "0.102"
    assert all(row["output_url"] and row["state"] == "saved" for row in result["files"])
    assert len(client.creates) == 2
    manager.start(identity)
    await manager.task
    assert len(client.creates) == 2
    public = json.dumps(result)
    for private in ("private-key-fingerprint", "private-quote", "private-task", "private-signature", "idempotency_key", "key_fingerprint"):
        assert private not in public
    batch = generation.Batch(Path(manager.source(body["source_id"])["directory"]), manager.run_path(identity),
                             result["recipe"], client, "0.051", "0.102", 10)
    assert len(json.loads((batch.destination / "catalog.json").read_text())["pose"]) == 2


async def test_pause_finishes_accepted_image_and_resume_continues(setup):
    manager, client, body = setup
    identity = manager.create_run(body)
    client.after_poll = lambda: manager.pause(identity)
    manager.start(identity)
    await manager.task
    assert manager.summary(identity)["status"] == "paused"
    assert manager.summary(identity)["completed"] == 1
    assert len(client.creates) == 1
    client.after_poll = lambda: None
    manager.start(identity)
    await manager.task
    assert manager.summary(identity)["completed"] == 2
    assert len(client.creates) == 2


async def test_cap_stops_before_second_paid_submission(setup):
    manager, client, body = setup
    identity = manager.create_run({**body, "total": "0.051"})
    manager.start(identity)
    await manager.task
    result = manager.summary(identity)
    assert result["status"] == "error"
    assert "cap" in result["error"]
    assert result["completed"] == 1
    assert len(client.creates) == 1


async def test_restart_and_lost_create_response_recover_same_submission(setup):
    manager, client, body = setup
    identity = manager.create_run({**body, "files": ["two.png"]})
    client.lose_response = True
    manager.start(identity)
    await manager.task
    original = client.creates[0]
    # Simulate a process terminated while its run was marked running.
    manager.update(identity, status="running")
    restarted = studio.Studio(manager.root, lambda: client)
    assert restarted.summary(identity)["status"] == "paused"
    assert len(client.creates) == 1  # Merely restarting never submits.
    client.lose_response = False
    restarted.start(identity)
    await restarted.task
    assert restarted.summary(identity)["completed"] == 1
    assert client.creates == [original, original]


async def test_active_run_blocks_parallel_submission(setup):
    manager, _, body = setup
    identity = manager.create_run(body)
    manager.active = identity
    with pytest.raises(generation.BatchError, match="Another run"):
        manager.start(identity)


@pytest.mark.parametrize("headers", [
    {"Host": "evil.example", "X-Pose-Studio": "1"},
    {"Host": "127.0.0.1:8765", "Origin": "https://evil.example", "X-Pose-Studio": "1"},
    {"Host": "127.0.0.1:8765"},
])
async def test_cross_site_requests_cannot_start_paid_jobs(headers):
    request = make_mocked_request("POST", "/api/runs", headers=headers)
    called = False

    async def handler(request):
        nonlocal called
        called = True
        return web.json_response({})

    with pytest.raises(web.HTTPForbidden):
        await studio.local_only(request, handler)
    assert not called


async def test_local_browser_request_succeeds_and_has_security_headers():
    request = make_mocked_request("POST", "/api/runs", headers={"Host": "127.0.0.1:8765",
        "Origin": "http://127.0.0.1:8765", "X-Pose-Studio": "1"})

    async def handler(request):
        return web.json_response({"ok": True})

    response = await studio.local_only(request, handler)
    assert response.status == 200
    assert response.headers["Cache-Control"] == "no-store"
    assert "frame-ancestors 'none'" in response.headers["Content-Security-Policy"]


async def test_streaming_body_enforces_limit_without_content_length():
    class Content:
        async def iter_chunked(self, size):
            yield b"12345"
            yield b"67890"

    with pytest.raises(generation.BatchError, match="size limit"):
        await studio.read_body(SimpleNamespace(content=Content()), limit=6)


async def test_result_route_serves_only_registered_finished_outputs(setup):
    manager, client, body = setup
    identity = manager.create_run(body)
    manager.start(identity)
    await manager.task
    app = web.Application()
    app[studio.STUDIO] = manager
    request = make_mocked_request("GET", f"/media/result/{identity}/0", app=app,
                                  match_info={"kind": "result", "id": identity, "index": "0"})
    response = await studio.media(request)
    assert response.status == 200
    request = make_mocked_request("GET", f"/media/result/{identity}/-1", app=app,
                                  match_info={"kind": "result", "id": identity, "index": "-1"})
    with pytest.raises(web.HTTPNotFound):
        await studio.media(request)


async def finished_run(manager, body):
    identity = manager.create_run(body)
    manager.start(identity)
    await manager.task
    assert manager.summary(identity)["status"] == "completed"
    return identity


async def test_regeneration_replaces_only_target_in_parent_and_keeps_backup(setup):
    manager, client, body = setup
    parent = await finished_run(manager, body)
    original_files = {p.relative_to(manager.run_path(parent)): p.read_bytes()
                      for p in manager.run_path(parent).rglob("*") if p.is_file()}
    original_state = json.loads((manager.run_path(parent) / "batch.json").read_text())
    target = Path(original_state["jobs"]["two.png"]["output"])
    client.output = png("black")
    request_id = uuid.uuid4().hex
    retry = manager.regenerate(parent, "two.png", request_id)
    record = manager.run(retry)
    assert record["files"] == ["two.png"]
    assert record["recipe"] == manager.run(parent)["recipe"]
    assert record["total"] == record["per_image"] == "0.051"
    assert record["source_id"] == body["source_id"]
    manager.start(retry)
    await manager.task
    assert len(client.creates) == 3  # Two original images, one regenerated image.
    assert client.creates[-1]["idempotency_key"] not in [c["idempotency_key"] for c in client.creates[:2]]
    assert client.creates[-1]["payload"]["input"] == client.creates[1]["payload"]["input"]
    assert manager.summary(retry)["completed"] == 1
    assert manager.summary(retry)["regenerated_from"]["id"] == parent
    for relative, content in original_files.items():
        if relative not in (target, Path("batch.json")):
            assert (manager.run_path(parent) / relative).read_bytes() == content
    updated = json.loads((manager.run_path(parent) / "batch.json").read_text())
    assert (manager.run_path(parent) / target).read_bytes() == client.output
    assert (manager.run_path(parent) / updated["jobs"]["two.png"]["output"]).read_bytes() == png()
    assert updated["jobs"]["two.png"]["task_id"] == original_state["jobs"]["two.png"]["task_id"]
    assert updated["replacements"]["two.png"]["retry_id"] == retry
    displayed = manager.summary(parent)
    assert displayed["count"] == displayed["completed"] == 2
    assert displayed["files"][1]["output_url"].endswith("?v=" + retry)
    assert displayed["settled"] == "0.153"
    # Resuming the original batch keeps the adopted result, without recharging.
    manager.start(parent)
    await manager.task
    assert (manager.run_path(parent) / target).read_bytes() == client.output
    assert manager.regenerate(parent, "two.png", request_id) == retry
    assert len(client.creates) == 3


async def test_interrupted_retry_recovers_same_job_even_with_new_browser_id(setup):
    manager, client, body = setup
    parent = await finished_run(manager, body)
    retry = manager.regenerate(parent, "two.png", uuid.uuid4().hex)
    client.lose_response = True
    manager.start(retry)
    await manager.task
    attempted = client.creates[-1]
    fresh_browser_request = uuid.uuid4().hex
    assert manager.regenerate(parent, "two.png", fresh_browser_request) == retry
    client.lose_response = False
    manager.start(retry)
    await manager.task
    assert client.creates[-1] == attempted
    assert manager.summary(retry)["status"] == "completed"
    # Repeating the second browser's request after completion is still a replay.
    assert manager.regenerate(parent, "two.png", fresh_browser_request) == retry
    assert len(list(manager.outputs.glob("*/studio.json"))) == 2


async def test_regeneration_cap_rejects_a_higher_quote_without_charging(setup):
    manager, client, body = setup
    parent = await finished_run(manager, body)
    retry = manager.regenerate(parent, "two.png", uuid.uuid4().hex)
    client.price = "0.052"
    manager.start(retry)
    await manager.task
    assert len(client.creates) == 2
    assert manager.summary(retry)["status"] == "error"
    assert "cap" in manager.summary(retry)["error"]
    assert manager.summary(parent)["completed"] == 2


async def test_regeneration_refuses_changed_source_and_unknown_image(setup):
    manager, _, body = setup
    parent = await finished_run(manager, body)
    with pytest.raises(generation.BatchError, match="not in"):
        manager.regenerate(parent, "../outside.png", uuid.uuid4().hex)
    source = Path(manager.source(body["source_id"])["directory"])
    (source / "two.png").write_bytes(b"changed")
    with pytest.raises(generation.BatchError, match="source image has changed"):
        manager.regenerate(parent, "two.png", uuid.uuid4().hex)
    assert len(list(manager.outputs.glob("*/studio.json"))) == 1


async def test_regeneration_rejects_request_id_reuse_for_another_image(setup):
    manager, _, body = setup
    parent = await finished_run(manager, body)
    request_id = uuid.uuid4().hex
    manager.regenerate(parent, "two.png", request_id)
    with pytest.raises(generation.BatchError, match="different image"):
        manager.regenerate(parent, "nested/one.png", request_id)


async def test_regeneration_queues_without_interrupting_active_run(setup):
    manager, client, body = setup
    parent = await finished_run(manager, body)
    manager.active = parent
    retry = manager.regenerate(parent, "two.png", uuid.uuid4().hex)
    manager.enqueue(retry)
    assert manager.active == parent
    assert manager.queue == [retry]
    assert len(client.creates) == 2
    assert len(list(manager.outputs.glob("*/studio.json"))) == 2


async def test_regeneration_route_repeated_posts_do_not_submit_twice(setup):
    manager, client, body = setup
    parent = await finished_run(manager, body)
    data = {"name": "two.png", "request_id": uuid.uuid4().hex}

    class Content:
        async def iter_chunked(self, size):
            yield json.dumps(data).encode()

    request = SimpleNamespace(app={studio.STUDIO: manager}, match_info={"id": parent}, content=Content())
    first = json.loads((await studio.regenerate(request)).text)
    await manager.task
    second = json.loads((await studio.regenerate(request)).text)
    assert first["id"] == second["id"]
    assert first["id"] == parent
    assert first["count"] == second["count"] == 2
    assert second["status"] == "completed"
    assert len(client.creates) == 3


async def test_active_and_failed_retry_keep_old_image_in_same_gallery(setup):
    manager, client, body = setup
    parent = await finished_run(manager, body)
    original_url = manager.summary(parent)["files"][1]["output_url"]
    retry = manager.regenerate(parent, "two.png", uuid.uuid4().hex)
    manager.active = retry
    manager.update(retry, status="running")
    active = manager.summary(parent)
    assert active["status"] == "regenerating"
    assert active["control_run_id"] == retry
    assert active["files"][1]["output_url"] == original_url
    assert active["files"][1]["state"] == "regenerating"
    manager.active = None
    client.price = "0.052"
    manager.start(retry)
    await manager.task
    failed = manager.summary(parent)
    assert failed["files"][1]["output_url"] == original_url
    assert failed["files"][1]["retry_pending"]
    assert failed["files"][1]["retry_error"]
    assert len(client.creates) == 2


async def test_replacement_repair_and_older_retry_cannot_undo_newer_result(setup):
    manager, client, body = setup
    parent = await finished_run(manager, body)
    first = manager.regenerate(parent, "two.png", uuid.uuid4().hex)
    client.output = png("black")
    manager.start(first)
    await manager.task
    second = manager.regenerate(parent, "two.png", uuid.uuid4().hex)
    client.output = png("red")
    manager.start(second)
    await manager.task
    state = json.loads((manager.run_path(parent) / "batch.json").read_text())
    current = manager.run_path(parent) / state["replacements"]["two.png"]["output"]
    archive = manager.run_path(parent) / state["replacement_history"]["two.png"][0]["output"]
    assert archive.read_bytes() == png("black")
    assert not studio.apply_replacement(manager.outputs, first)
    assert current.read_bytes() == png("red")
    # Simulate an interruption after the new receipt was saved but before copy.
    current.write_bytes(png("black"))
    assert studio.apply_replacement(manager.outputs, second)
    assert current.read_bytes() == png("red")
    assert len(client.creates) == 4


async def test_saved_runs_hide_internal_retries_and_old_links_open_original_batch(setup):
    manager, client, body = setup
    parent = await finished_run(manager, body)
    retry = manager.regenerate(parent, "two.png", uuid.uuid4().hex)
    manager.start(retry)
    await manager.task
    request = SimpleNamespace(app={studio.STUDIO: manager}, method="GET", match_info={"id": retry})
    listed = json.loads((await studio.runs(request)).text)
    assert [run["id"] for run in listed] == [parent]
    detail = json.loads((await studio.run_detail(request)).text)
    assert detail["id"] == parent and detail["count"] == 2


async def test_startup_adopts_completed_legacy_retry_without_paid_requests(setup, monkeypatch):
    manager, client, body = setup
    parent = await finished_run(manager, body)
    retry = manager.regenerate(parent, "two.png", uuid.uuid4().hex)
    client.output = png("black")
    with monkeypatch.context() as patch:
        patch.setattr(studio, "apply_replacement", lambda *args: False)
        manager.start(retry)
        await manager.task
    assert "replacements" not in json.loads((manager.run_path(parent) / "batch.json").read_text())
    restarted = studio.Studio(manager.root, lambda: client)
    assert restarted.summary(parent)["files"][1]["output_url"].endswith("?v=" + retry)
    assert len(client.creates) == 3


async def post_retry(manager, parent, name, request_id=None):
    data = {"name": name, "request_id": request_id or uuid.uuid4().hex}

    class Content:
        async def iter_chunked(self, size):
            yield json.dumps(data).encode()

    request = SimpleNamespace(app={studio.STUDIO: manager}, match_info={"id": parent}, content=Content())
    return json.loads((await studio.regenerate(request)).text)


def block_next_poll(client, manager):
    loop = asyncio.get_running_loop()
    entered = asyncio.Event()
    release = threading.Event()
    order = []

    def block():
        order.append(manager.current)
        loop.call_soon_threadsafe(entered.set)
        if not release.wait(5):
            raise RuntimeError("Test did not release the worker")

    client.after_poll = block
    return entered, release, order


async def test_clicking_multiple_retries_queues_fifo_and_deduplicates_pending_images(setup):
    manager, client, body = setup
    parent = await finished_run(manager, body)
    entered, release, order = block_next_poll(client, manager)
    try:
        await post_retry(manager, parent, "two.png")
        await asyncio.wait_for(entered.wait(), 3)
        current = manager.active
        result = await post_retry(manager, parent, "nested/one.png")
        assert manager.active == current
        assert len(manager.queue) == 2 and len(client.creates) == 3
        assert result["queued_retries"] == 1
        assert result["files"][0]["queue_position"] == 1
        assert result["files"][0]["state"] == "retry-queued"
        queued_id = manager.queue[1]
        await post_retry(manager, parent, "nested/one.png")
        await post_retry(manager, parent, "two.png")
        assert manager.queue == [current, queued_id]
    finally:
        release.set()
        await manager.task
    assert order == ["two.png", "nested/one.png"]
    assert len(client.creates) == 4
    assert not manager.queue
    assert manager.summary(parent)["status"] == "completed"
    assert all("?v=original" not in f["output_url"] for f in manager.summary(parent)["files"])


async def test_pause_and_restart_preserve_pending_queue_without_automatic_spending(setup):
    manager, client, body = setup
    parent = await finished_run(manager, body)
    entered, release, _ = block_next_poll(client, manager)
    try:
        await post_retry(manager, parent, "two.png")
        await asyncio.wait_for(entered.wait(), 3)
        await post_retry(manager, parent, "nested/one.png")
        queued = manager.queue[1]
        manager.pause(manager.active)
    finally:
        release.set()
        await manager.task
    assert manager.queue == [queued]
    assert len(client.creates) == 3
    assert manager.summary(parent)["status"] == "paused"
    restarted = studio.Studio(manager.root, lambda: client)
    assert restarted.queue == [queued] and restarted.queue_paused
    assert restarted.task is None
    assert len(client.creates) == 3
    client.after_poll = lambda: None
    restarted.start(queued)
    await restarted.task
    assert len(client.creates) == 4 and not restarted.queue
    assert restarted.summary(parent)["status"] == "completed"


async def test_failed_queued_attempt_does_not_block_next_image(setup):
    manager, client, body = setup
    parent = await finished_run(manager, body)
    original_api = client.api
    first_quote = True

    def api(method, path, **kwargs):
        nonlocal first_quote
        if path == "/jobs/quote" and first_quote:
            first_quote = False
            quote = original_api(method, path, **kwargs)
            return {**quote, "maxCharge": "0.052", "estimatedCost": "0.052"}
        return original_api(method, path, **kwargs)

    client.api = api
    first = manager.regenerate(parent, "two.png", uuid.uuid4().hex)
    second = manager.regenerate(parent, "nested/one.png", uuid.uuid4().hex)
    manager.enqueue(first)
    manager.enqueue(second)
    await manager.task
    assert manager.run(first)["status"] == "error"
    assert manager.run(second)["status"] == "completed"
    assert manager.summary(parent)["files"][1]["output_url"].endswith("?v=original")
    assert manager.summary(parent)["files"][0]["output_url"].endswith("?v=" + second)
    assert len(client.creates) == 3 and not manager.queue


async def test_retry_can_queue_while_original_batch_is_still_generating(setup):
    manager, client, body = setup
    parent = manager.create_run(body)
    entered, release, order = block_next_poll(client, manager)
    block = client.after_poll
    client.after_poll = lambda: block() if manager.current == "two.png" else None
    manager.start(parent)
    try:
        await asyncio.wait_for(entered.wait(), 3)
        result = await post_retry(manager, parent, "nested/one.png")
        assert manager.active == parent
        assert result["queued_retries"] == 1
        assert len(client.creates) == 2
    finally:
        release.set()
        await manager.task
    assert len(client.creates) == 3
    assert manager.summary(parent)["status"] == "completed"
    assert manager.summary(parent)["files"][0]["output_url"] != f"/media/result/{parent}/0?v=original"


async def test_crashed_active_queue_recovers_original_submission_then_runs_next(setup):
    manager, client, body = setup
    parent = await finished_run(manager, body)
    first = manager.regenerate(parent, "two.png", uuid.uuid4().hex)
    client.lose_response = True
    manager.start(first)
    await manager.task
    original_request = client.creates[-1]
    second = manager.regenerate(parent, "nested/one.png", uuid.uuid4().hex)
    # Simulate a crash before the active receipt is removed from the queue.
    manager.queue = [first, second]
    manager.update(first, status="running")
    manager.update(second, status="queued")
    manager.save_queue()
    restarted = studio.Studio(manager.root, lambda: client)
    assert restarted.queue == [first, second] and restarted.queue_paused
    assert len(client.creates) == 3
    client.lose_response = False
    restarted.start(first)
    await restarted.task
    assert client.creates[3] == original_request
    assert len(client.creates) == 5 and not restarted.queue
    assert restarted.summary(parent)["status"] == "completed"


async def test_missing_key_pauses_queue_and_preserves_pending_items(setup):
    manager, client, body = setup
    parent = await finished_run(manager, body)

    def missing_key():
        raise generation.BatchError("private diagnostic")

    manager.client_factory = missing_key
    first = manager.regenerate(parent, "two.png", uuid.uuid4().hex)
    second = manager.regenerate(parent, "nested/one.png", uuid.uuid4().hex)
    manager.enqueue(first)
    manager.enqueue(second)
    await manager.task
    assert manager.queue == [first, second] and manager.queue_paused
    assert len(client.creates) == 2
    result = manager.summary(parent)
    assert "API key" in result["error"] and "private diagnostic" not in json.dumps(result)
    manager.client_factory = lambda: client
    manager.start(first)
    await manager.task
    assert len(client.creates) == 4 and not manager.queue


POSE = {"label": "Sitting with both hands resting on one knee", "count": "solo",
        "crop": "full", "expressive": False, "angle": "diag", "tag_groups": {
            "Camera position": ["three-quarter view"], "Character count": ["solo"],
            "Character stance": ["sitting"], "Gesture": ["hands on knee"]}}


async def test_catalog_metadata_uses_current_drawing_and_survives_restart(setup, monkeypatch):
    manager, client, body = setup
    client.output = png("blue")
    run = await finished_run(manager, body)
    dest = manager.run_path(run)
    seen = []
    monkeypatch.setattr(studio.pose_metadata, "api_key", lambda provider: "test")
    def describe(content, provider, key, caption_prompt, groups):
        assert caption_prompt == studio.pose_metadata.DEFAULT_PROMPT
        seen.append((content, provider))
        return POSE
    monkeypatch.setattr(studio.pose_metadata, "describe", describe)
    manager.start_metadata(run, "xai", 1)
    with pytest.raises(generation.BatchError, match="running"):
        manager.start_metadata(run, "xai")
    await manager.metadata_tasks[run]
    first = manager.summary(run)["files"][0]["metadata"]
    assert first["id"] == "full_single_P001_diag" and first["model"] == "grok-4.7"
    assert (dest / "img" / (first["id"] + ".png")).read_bytes() == client.output
    assert seen == [(client.output, "xai")]  # Uploaded source was white.
    manager = studio.Studio(manager.root, lambda: client)
    manager.start_metadata(run, "xai")
    await manager.metadata_tasks[run]
    catalog = studio.read_json(dest / "catalog.json")["pose"]
    assert [r["image"] for r in catalog] == ["img/full_single_P001_diag.png", "img/full_single_P002_diag.png"]
    assert len(list((dest / "img").glob("*.png"))) == 2
    assert set(catalog[0]["tags"]) == {"three-quarter view", "solo", "sitting", "hands on knee"}
    assert catalog[0]["prompt"] == "sitting with both hands resting on one knee, three-quarter view"
    with pytest.raises(generation.BatchError, match="already"):
        manager.start_metadata(run, "xai")
    client.output = png("red")
    retry = manager.regenerate(run, "two.png", uuid.uuid4().hex)
    manager.start(retry)
    await manager.task
    assert manager.summary(run)["files"][1]["metadata"] is None
    assert studio.read_json(dest / "catalog.json")["pose"][1]["category"] == "Unreviewed"
    monkeypatch.setattr(studio.pose_metadata, "describe", lambda *_: {**POSE, "count": "pair", "angle": "side",
        "tag_groups": {**POSE["tag_groups"], "Character count": ["pair"], "Camera position": ["side view"]}})
    manager.start_metadata(run, "xai")
    await manager.metadata_tasks[run]
    entry = studio.read_json(dest / "catalog.json")["pose"][1]
    assert entry["id"] == "pair60_P002_side" and entry["category"] == "Two people" and "pair" in entry["tags"]
    assert not (dest / "img/full_single_P002_diag.png").exists()
    assert studio.apply_replacement(manager.outputs, retry)
    assert (dest / entry["image"]).read_bytes() == client.output
    second_run = await finished_run(manager, body)
    manager.start_metadata(second_run, "xai", 1)
    await manager.metadata_tasks[second_run]
    assert manager.summary(second_run)["files"][0]["metadata"]["id"] == "pair60_P003_side"


async def test_delete_restore_and_retry_replay_keep_catalog_consistent(setup, monkeypatch):
    manager, client, body = setup
    run = await finished_run(manager, body)
    dest = manager.run_path(run)
    name = body["files"][0]
    client.output = png("blue")
    retry = manager.regenerate(run, name, uuid.uuid4().hex)
    manager.start(retry)
    await manager.task
    summary = manager.summary(run)
    old = dest / studio.read_json(dest / "batch.json")["replacements"][name]["output"]
    manager.set_deleted(run, name, True)
    manager.set_deleted(run, name, True)
    hidden = manager.summary(run)
    assert hidden["count"] == hidden["completed"] == len(hidden["files"]) == 1
    assert hidden["last_deleted"] == name and hidden["settled"] == summary["settled"]
    assert not old.exists() and len(studio.read_json(dest / "catalog.json")["pose"]) == 1
    assert not studio.apply_replacement(manager.outputs, retry)
    with pytest.raises(generation.BatchError, match="Restore"):
        manager.regenerate(run, name, uuid.uuid4().hex)
    restarted = studio.Studio(manager.root, lambda: client)
    assert restarted.summary(run)["last_deleted"] == name
    monkeypatch.setattr(studio.pose_metadata, "api_key", lambda _: "test")
    calls = []
    def describe(content, *_):
        calls.append(content)
        return {"review": True}
    monkeypatch.setattr(studio.pose_metadata, "describe", describe)
    restarted.start_metadata(run, "xai")
    await restarted.metadata_tasks[run]
    assert calls == [png()]  # Only the surviving drawing was sent.
    restarted.set_deleted(run, name, False)
    restored = restarted.summary(run)
    assert restored["count"] == 2 and not restored["last_deleted"]
    assert old.read_bytes() == client.output
    assert restored["files"][1]["metadata"]["review"]
    assert len(studio.read_json(dest / "catalog.json")["pose"]) == 2


async def test_partial_metadata_pass_retries_only_unfinished(setup, monkeypatch):
    manager, _, body = setup
    run = await finished_run(manager, body)
    monkeypatch.setattr(studio.pose_metadata, "api_key", lambda _: "test")
    replies = iter([POSE, {"angle": "../bogus"}, POSE])
    monkeypatch.setattr(studio.pose_metadata, "describe", lambda *_: next(replies))
    manager.start_metadata(run, "xai")
    await manager.metadata_tasks[run]
    progress = manager.summary(run)["metadata_status"]
    assert progress["done"] == 1 and "invalid" in progress["error"]
    manager.start_metadata(run, "xai")
    await manager.metadata_tasks[run]
    progress = manager.summary(run)["metadata_status"]
    assert progress["done"] == progress["total"] == 1 and not progress["error"]


async def test_curation_recovers_after_move_before_catalog_commit(setup, monkeypatch):
    manager, client, body = setup
    run = await finished_run(manager, body)
    with monkeypatch.context() as patch:
        def interrupted(*args, **kwargs):
            raise OSError("Interrupted after moving PNG")
        patch.setattr(generation, "write_catalog", interrupted)
        with pytest.raises(OSError):
            manager.set_deleted(run, "two.png", True)
    restarted = studio.Studio(manager.root, lambda: client)
    dest = restarted.run_path(run)
    assert not studio.read_json(dest / "batch.json").get("curation_change")
    assert len(studio.read_json(dest / "catalog.json")["pose"]) == 1
    restarted.set_deleted(run, "two.png", False)
    assert len(studio.read_json(dest / "catalog.json")["pose"]) == 2


async def test_append_drawings_and_photos_in_same_folder(setup):
    manager, client, body = setup
    run = await finished_run(manager, body)
    original = manager.summary(run)
    drawings = manager.new_source("imports")
    manager.add_image(drawings["id"], "two.png", png("green"))
    manager.add_to_folder(run, drawings["id"], "drawing")
    manager.add_to_folder(run, drawings["id"], "drawing")
    imported = manager.summary(run)
    assert imported["count"] == 3 and len(client.creates) == 2
    entry = imported["files"][-1]
    assert entry["name"] == "two (2).png" and entry["imported"] and not entry["source_url"]
    assert imported["total"] == original["total"]
    manager.set_deleted(run, entry["name"], True)
    manager.set_deleted(run, entry["name"], False)
    upload = manager.new_source("photos")
    manager.add_image(upload["id"], "two.png", png("pink"))
    manager.add_to_folder(run, upload["id"], "photo")
    await manager.task
    parent = manager.summary(run)
    entry = parent["files"][-1]
    assert parent["count"] == parent["completed"] == 4 and len(client.creates) == 3
    assert entry["name"] == "two (3).png" and upload["id"] in entry["source_url"]
    assert parent["total"] == "0.153" and parent["status"] == "completed"
    manager.start(manager.regenerate(run, entry["name"], uuid.uuid4().hex))
    await manager.task
    assert len(client.creates) == 4 and manager.summary(run)["completed"] == 4
    before = Path(parent["destination"])
    manager.rename_folder(run, "Instagram Poses")
    after = Path(manager.summary(run)["destination"])
    assert not before.exists() and after.resolve() == manager.run_path(run)
    assert after.name == "Instagram Poses" and (after / "catalog.json").is_file()
    restarted = studio.Studio(manager.root, lambda: client)
    assert restarted.summary(run)["label"] == "Instagram Poses"


async def test_edit_caption_and_global_tags_invalidates_only_on_explicit_pass(setup, monkeypatch):
    manager, _, body = setup
    run = await finished_run(manager, body)
    monkeypatch.setattr(studio.pose_metadata, "api_key", lambda _: "test")
    prompts = []
    def describe(_, provider, key, prompt, groups):
        prompts.append(prompt)
        return {**POSE, "tag_groups": {k: [t for t in tags if t in POSE["tag_groups"].get(k, [])]
                                      for k, tags in groups.items()}}
    monkeypatch.setattr(studio.pose_metadata, "describe", describe)
    manager.start_metadata(run, "xai", 1)
    await manager.metadata_tasks[run]
    manager.save_caption_prompt(run, "Describe the weight-bearing leg first.")
    old = manager.summary(run)["files"][0]["metadata"].copy()
    assert len(prompts) == 1 and old["label"] == POSE["label"]
    req = SimpleNamespace(app={studio.STUDIO: manager})
    groups = {**manager.tag_groups, "Gesture": [], "Composition": ["centered", "off-center"]}
    async def new_groups(_):
        return {"groups": groups, "version": manager.summary(run)["tag_version"]}
    monkeypatch.setattr(studio, "body_json", new_groups)
    await studio.tag_settings(req)
    row = studio.read_json(manager.run_path(run) / "catalog.json")["pose"][0]
    assert "hands on knee" not in row["tags"] and len(prompts) == 1
    manager.start_metadata(run, "xai", 1)
    await manager.metadata_tasks[run]
    meta = manager.summary(run)["files"][0]["metadata"]
    assert prompts[-1] == manager.summary(run)["caption_prompt"]
    assert old["id"] == meta["id"] and old["tag_version"] != meta["tag_version"]
    assert "Composition" in meta["tag_groups"] and len(prompts) == 2
    assert studio.Studio(manager.root).tag_groups == groups


@pytest.mark.parametrize("provider", ["xai", "spicy"])
def test_caption_request_uses_one_inline_drawing_and_editable_vocabulary(monkeypatch, provider):
    metadata = studio.pose_metadata
    groups = {"Character count": ["solo", "pair"]}
    raw = {**POSE, "tag_groups": {"Character count": ["solo"]}}
    reply = json.dumps({"pose": raw})
    captured = {}
    class Session:
        def __enter__(self): return self
        def __exit__(self, *args): pass
        def post(self, url, **kwargs):
            captured.update(endpoint=url, **kwargs)
            result = {"output": [{"type": "message", "content": [{"type": "output_text", "text": reply}]}]} if provider == "xai" else {"choices": [{"message": {"content": reply}}]}
            return SimpleNamespace(status_code=200, json=lambda: result)
    monkeypatch.setattr(metadata.requests, "Session", Session)
    result = metadata.describe(png("purple"), provider, "unit-test", "Focus on the hands.", groups)
    assert metadata.catalog_fields(result, 17, groups)["id"] == "full_single_P017_diag"
    payload = captured["json"]
    if provider == "xai":
        assert captured["endpoint"] == "https://api.x.ai/v1/responses"
        assert payload["store"] is False and payload["model"] == "grok-4.7"
        image, instruction = payload["input"][0]["content"]
        assert image["detail"] == "high" and image["image_url"].startswith("data:image/jpeg;base64,")
        spec = payload["text"]["format"]["schema"]["properties"]["pose"]["anyOf"][0]
        assert spec["properties"]["tag_groups"]["properties"]["Character count"]["items"]["enum"] == ["solo", "pair"]
    else:
        instruction, image = payload["messages"][0]["content"]
        assert image["image_url"]["url"].startswith("data:image/jpeg;base64,")
    assert "Focus on the hands." in instruction["text"] and "Character count" in instruction["text"]
    with pytest.raises(generation.BatchError):
        metadata.catalog_fields({**raw, "tag_groups": {"Character count": ["made-up"]}}, 17, groups)


async def test_manual_edits_and_targeted_recaptions_survive_restart(setup, monkeypatch):
    manager, client, body = setup
    run = await finished_run(manager, body)
    name = "two.png"
    dest = manager.run_path(run)
    def file():
        return manager.summary(run)["files"][1]
    groups = {row: [] for row in manager.tag_groups}
    groups["Character stance"] = ["kneeling"]
    edit = {"name": name, "version": file()["edit_version"], "label": "Resting on one knee", "tag_groups": groups}
    manager.edit_drawing(run, edit)
    manager = studio.Studio(manager.root, lambda: client)
    assert file()["metadata"]["label"] == edit["label"]
    catalog = studio.read_json(dest / "catalog.json")["pose"]
    assert catalog[1]["prompt"] == "resting on one knee" and catalog[1]["tags"] == ["kneeling"]
    with pytest.raises(generation.BatchError, match="changed"):
        manager.edit_drawing(run, {**edit, "label": "From a stale tab"})

    calls = []
    def describe(data, provider, key, prompt, vocabulary):
        calls.append((data, provider, prompt))
        return POSE
    monkeypatch.setattr(studio.pose_metadata, "api_key", lambda _: "test")
    monkeypatch.setattr(studio.pose_metadata, "describe", describe)
    manager.start_metadata(run, "xai")
    await manager.metadata_tasks[run]
    assert len(calls) == 2 and file()["metadata"]["label"] == edit["label"]
    assert file()["metadata"]["tag_groups"]["Character stance"] == ["kneeling"]
    assert file()["metadata"]["prompt"] == "resting on one knee, three-quarter view"
    first = manager.summary(run)["files"][0]
    previous = file()["metadata"]
    manager.start_metadata(run, "xai", target=name)
    with pytest.raises(generation.BatchError, match="running"):
        manager.start_metadata(run, "xai", target=name)
    await manager.metadata_tasks[run]
    assert len(calls) == 3 and manager.summary(run)["files"][0] == first
    assert manager.summary(run)["metadata_status"]["total"] == 1
    assert file()["metadata"]["id"] == previous["id"]
    assert file()["metadata"]["label"] == POSE["label"]
    assert file()["metadata"]["tag_groups"]["Character stance"] == ["kneeling"]
    assert studio.read_json(dest / "catalog.json")["pose"][1]["label"] == POSE["label"]
    monkeypatch.setattr(studio.pose_metadata, "describe", lambda *_: {"review": True})
    manager.start_metadata(run, "xai", target=name)
    await manager.metadata_tasks[run]
    assert manager.summary(run)["metadata_status"]["error"]
    assert file()["metadata"]["label"] == POSE["label"]
    client.output = png("blue")
    manager.start(manager.regenerate(run, name, uuid.uuid4().hex))
    await manager.task
    assert file()["metadata"] is None
    with pytest.raises(generation.BatchError, match="changed"):
        manager.edit_drawing(run, edit)


async def test_manual_tags_without_caption_and_single_drawing_guards(setup, monkeypatch):
    manager, _, body = setup
    run = await finished_run(manager, body)
    stage = manager.new_source("finished drawings")
    manager.add_image(stage["id"], "drawing.png", png("purple"))
    manager.add_to_folder(run, stage["id"], "drawing")
    def drawing():
        return manager.summary(run)["files"][-1]
    groups = {row: [] for row in manager.tag_groups}
    groups["Gesture"] = ["pointing"]
    name = drawing()["name"]
    edit = {"name": name, "version": drawing()["edit_version"], "label": "", "tag_groups": groups}
    with pytest.raises(generation.BatchError, match="unlisted"):
        manager.edit_drawing(run, {**edit, "tag_groups": {**groups, "Gesture": ["other"]}})
    manager.edit_drawing(run, edit)
    assert drawing()["metadata"]["tags"] == ["pointing"]
    manager.set_deleted(run, name, True)
    monkeypatch.setattr(studio.pose_metadata, "api_key", lambda _: "test")
    with pytest.raises(generation.BatchError, match="finished"):
        manager.start_metadata(run, "xai", target=name)
    with pytest.raises(generation.BatchError, match="Unknown"):
        manager.start_metadata(run, "xai", target="missing")
    with pytest.raises(generation.BatchError, match="finished"):
        manager.edit_drawing(run, edit)
    manager.set_deleted(run, name, False)
    assert drawing()["metadata"]["tags"] == ["pointing"]
    manager.edit_drawing(run, {**edit, "version": drawing()["edit_version"], "tag_groups": {**groups, "Gesture": []}})
    assert drawing()["metadata"]["tags"] == []
    seen = []
    def describe(data, *_):
        seen.append(data)
        return POSE
    monkeypatch.setattr(studio.pose_metadata, "describe", describe)
    manager.start_metadata(run, "xai", target=name)
    await manager.metadata_tasks[run]
    assert seen == [png("purple")]
    assert drawing()["metadata"]["tag_groups"]["Gesture"] == []


async def test_caption_route_requires_a_single_image(setup, monkeypatch):
    manager, client, body = setup
    run = await finished_run(manager, body)
    app = studio.create_app(manager.root, lambda: client)
    live = app[studio.STUDIO]
    sent = []
    monkeypatch.setattr(studio.pose_metadata, "api_key", lambda _: "test")
    def describe(data, *_):
        sent.append(data)
        return POSE
    monkeypatch.setattr(studio.pose_metadata, "describe", describe)
    async def request(action, payload):
        req = make_mocked_request("POST", f"/api/runs/{run}/{action}", app=app)
        match = await app.router.resolve(req)
        async def read_payload(_):
            return payload
        monkeypatch.setattr(studio, "body_json", read_payload)
        return json.loads((await match.handler(SimpleNamespace(app=app, match_info=match))).text)
    with pytest.raises(generation.BatchError, match="one drawing"):
        await request("caption", {"provider": "xai"})
    assert not live.metadata_tasks
    response = await request("caption", {"name": "two.png", "provider": "xai"})
    assert response["caption_queue"] == ["two.png"]
    await live.caption_workers[run]
    assert len(sent) == 1
    file = live.summary(run)["files"][1]
    result = await request("edit-drawing", {"name": file["name"], "label": "Edited in detail view",
                "tag_groups": file["metadata"]["tag_groups"], "version": file["edit_version"]})
    assert result["files"][1]["metadata"]["label"] == "Edited in detail view"


async def test_caption_queue_orders_deduplicates_continues_and_recovers(setup, monkeypatch):
    manager, client, body = setup
    run = await finished_run(manager, body)
    first, second = body["files"]
    entered, release = threading.Event(), threading.Event()
    calls = []
    monkeypatch.setattr(studio.pose_metadata, "api_key", lambda _: "key")
    def describe(content, provider, key, prompt, groups):
        calls.append(prompt)
        if len(calls) == 1:
            entered.set()
            assert release.wait(5)
            raise generation.BatchError("Provider unavailable")
        return POSE
    monkeypatch.setattr(studio.pose_metadata, "describe", describe)
    request = {"name": first, "provider": "xai", "caption_prompt": "First", "request_id": uuid.uuid4().hex}
    manager.enqueue_caption(run, request)
    assert await asyncio.to_thread(entered.wait, 5)
    later = {**request, "name": second, "caption_prompt": "Second", "request_id": uuid.uuid4().hex}
    manager.enqueue_caption(run, later)
    manager.enqueue_caption(run, {**request, "request_id": uuid.uuid4().hex})
    pending = manager.summary(run)
    assert pending["caption_active"] == first and pending["caption_queue"] == [second]
    release.set()
    await manager.caption_workers[run]
    manager.enqueue_caption(run, request)
    assert calls == ["First", "Second"]
    assert "Provider unavailable" in manager.summary(run)["caption_errors"][first]
    assert manager.summary(run)["files"][1]["metadata"]["label"] == POSE["label"]
    queue = [{"name": n, "prompt": n, "digest": generation.sha256(client.output), "provider": "xai"} for n in (first, second)]
    queue[0]["started"] = True
    manager.update(run, caption_queue=queue)
    restarted = studio.Studio(manager.root)
    paused = restarted.summary(run)
    assert paused["captions_paused"] and paused["caption_queue"] == [second] and not paused["caption_active"]
    restarted.resume_captions(run)
    await restarted.caption_workers[run]
    assert calls == ["First", "Second", second]


async def test_depth_pass_uses_current_drawing_and_reuses_saved_map(setup, monkeypatch):
    manager, client, body = setup
    client.output = png("purple")
    run = await finished_run(manager, body)
    captured = []
    class Reply:
        def __init__(self, data=None, content=None):
            self.data = data
            self.content = content
            self.status_code = 200
        def json(self):
            return self.data
        def raise_for_status(self):
            pass
    class Session:
        def __enter__(self):
            return self
        def __exit__(self, *args):
            pass
        def get(self, url, **kwargs):
            if "/object_info/" in url:
                return Reply({"LoadDA3Model": {"input": {"required": {"model_name":
                    ["COMBO", {"options": ["depth_anything_3_mono_large.safetensors"]}]}}}})
            if "/history/" in url:
                return Reply({"job": {"outputs": {"5": {"images": [{"filename": "d.png", "subfolder": "", "type": "output"}]}}}})
            assert url.endswith("/view")
            return Reply(content=png("grey"))
        def post(self, url, **kwargs):
            if url.endswith("/upload/image"):
                assert kwargs["files"]["image"][1] == client.output
                return Reply({"subfolder": "pose-studio", "name": "drawing.png"})
            captured.append(kwargs["json"]["prompt"])
            return Reply({"prompt_id": "job"})
    monkeypatch.setattr(generation, "read_key", lambda _: "http://test")
    monkeypatch.setattr(studio.pose_depth.requests, "Session", Session)
    manager.start_depth(run, 1)
    await manager.depth_tasks[run]
    files = manager.summary(run)["files"]
    assert all(file["has_depth"] for file in files)  # The fixture generated identical images.
    assert captured[0]["1"]["inputs"]["image"] == "pose-studio/drawing.png"
    assert captured[0]["4"]["inputs"]["output"] == "depth"
    with pytest.raises(generation.BatchError, match="already"):
        manager.start_depth(run, None)
    client.output = png("orange")
    manager.start(manager.regenerate(run, body["files"][0], uuid.uuid4().hex))
    await manager.task
    assert [f["has_depth"] for f in manager.summary(run)["files"]] == [False, True]
    manager.start_depth(run, None)
    await manager.depth_tasks[run]
    assert len(captured) == 2
