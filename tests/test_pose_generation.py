import io
import json
from types import SimpleNamespace

from PIL import Image
import pytest
import requests

from scripts import generate_pose_references as generation


def png():
    output = io.BytesIO()
    Image.new("RGB", (12, 16), "white").save(output, format="PNG")
    return output.getvalue()


class FakeClient:
    key_fingerprint = "test-fingerprint"

    def __init__(self):
        self.creates = []
        self.uploads = 0
        self.quotes = 0
        self.price = "0.051"
        self.fail_create = False
        self.fail_download = False

    def upload(self, content):
        self.uploads += 1
        return {"uri": "spicy://f/fil_source", "expiresAt": "2099-01-01T00:00:00Z"}

    def api(self, method, path, **kwargs):
        if path == "/jobs/quote":
            self.quotes += 1
            return {"quoteId": "quote-test", "estimatedCost": self.price, "maxCharge": self.price, "currency": "USD"}
        assert path == "/jobs/createTask"
        self.creates.append(kwargs)
        if self.fail_create:
            raise generation.BatchError("Response lost after acceptance")
        return {"taskId": "job-test"}

    def poll(self, task_id, timeout, observe):
        record = {"state": "succeeded", "cost": self.price, "settled": True,
                  "output": {"assets": [{"url": "https://storage.example/result.png?private-signature"}]}}
        observe(record)
        return record

    def storage(self, method, url):
        if self.fail_download:
            raise generation.BatchError("Download interrupted")
        return SimpleNamespace(content=png())


@pytest.fixture
def setup_batch(tmp_path):
    source, destination = tmp_path / "source", tmp_path / "destination"
    source.mkdir()
    destination.mkdir()
    (source / "pose.png").write_bytes(png())
    recipe = {"model": generation.MODEL, "style": "spicy://f/fil_style", "prompt": "Mannequin", "resolution": "2k"}
    client = FakeClient()

    def worker(total="1.50", per_image="0.051"):
        return generation.Batch(source, destination, recipe, client, per_image, total, 10)

    return source, destination, client, worker


def test_lost_create_response_reuses_persisted_payload_and_key(setup_batch):
    source, destination, client, worker = setup_batch
    client.fail_create = True
    with pytest.raises(generation.BatchError, match="Response lost"):
        worker().process(source / "pose.png")
    persisted = json.loads((destination / "batch.json").read_text())["jobs"]["pose.png"]
    assert persisted["idempotency_key"] == client.creates[0]["idempotency_key"]
    client.fail_create = False
    worker().process(source / "pose.png")
    assert client.creates[0] == client.creates[1]
    assert client.quotes == 1
    assert client.uploads == 1
    payload = persisted["payload"]
    assert payload["input"]["image_urls"] == ["spicy://f/fil_style", "spicy://f/fil_source"]
    assert payload["input"]["aspect_ratio"] == "3:4"
    assert "private-signature" not in (destination / "batch.json").read_text()


def test_download_interruption_and_completed_run_never_recreate(setup_batch):
    source, destination, client, worker = setup_batch
    client.fail_download = True
    with pytest.raises(generation.BatchError, match="Download interrupted"):
        worker().process(source / "pose.png")
    client.fail_download = False
    worker().process(source / "pose.png")
    worker().process(source / "pose.png")
    assert len(client.creates) == 1
    assert len(list((destination / "img").glob("*.png"))) == 1


def test_old_unknown_submission_cannot_be_replayed(setup_batch, monkeypatch):
    source, destination, client, worker = setup_batch
    client.fail_create = True
    with pytest.raises(generation.BatchError):
        worker().process(source / "pose.png")
    record = json.loads((destination / "batch.json").read_text())["jobs"]["pose.png"]
    monkeypatch.setattr(generation.time, "time", lambda: record["first_submission"] + 24 * 3600)
    with pytest.raises(generation.BatchError, match="over 23 hours"):
        worker().process(source / "pose.png")
    assert len(client.creates) == 1


def test_quote_and_cumulative_caps_stop_before_create(setup_batch):
    source, _, client, worker = setup_batch
    client.price = "0.052"
    with pytest.raises(generation.BatchError, match="spending cap"):
        worker().process(source / "pose.png")
    assert not client.creates
    client.price = "0.051"
    worker(total="0.051").process(source / "pose.png")
    (source / "another.png").write_bytes(png())
    with pytest.raises(generation.BatchError, match="spending cap"):
        worker(total="0.051").process(source / "another.png")
    assert len(client.creates) == 1


def test_reduced_cap_blocks_unknown_submission(setup_batch):
    source, _, client, worker = setup_batch
    client.fail_create = True
    with pytest.raises(generation.BatchError):
        worker().process(source / "pose.png")
    with pytest.raises(generation.BatchError, match="current spending cap"):
        worker(total="0.01").process(source / "pose.png")
    assert len(client.creates) == 1


def test_pending_assets_timeout_even_when_job_succeeded(monkeypatch):
    clock = [0.0]
    monkeypatch.setattr(generation.time, "monotonic", lambda: clock[0])
    monkeypatch.setattr(generation.time, "sleep", lambda seconds: clock.__setitem__(0, clock[0] + seconds))
    client = generation.SpicyClient("secret-never-sent")
    monkeypatch.setattr(client, "api", lambda *args, **kwargs: {
        "state": "succeeded", "output": {"assets": [{"pending": True}]},
    })
    with pytest.raises(generation.BatchError, match="assets are still pending"):
        client.poll("job-test", 3, lambda record: None)


@pytest.mark.parametrize("assets", [[], [{"unavailable": True}], [{"pending": False}]])
def test_missing_or_unavailable_outputs_are_not_success(monkeypatch, assets):
    client = generation.SpicyClient("secret-never-sent")
    monkeypatch.setattr(client, "api", lambda *args, **kwargs: {"state": "succeeded", "output": {"assets": assets}})
    with pytest.raises(generation.BatchError, match="complete downloadable output"):
        client.poll("job-test", 3, lambda record: None)


def test_storage_never_receives_bearer_credentials(monkeypatch):
    client = generation.SpicyClient("secret-never-sent")
    assert "Authorization" not in client.storage_session.headers
    assert client.storage_session.trust_env is False
    with pytest.raises(generation.BatchError, match="Authorization"):
        client.storage("PUT", "https://storage.example/upload", headers={"Authorization": "Bearer forbidden"})
    with pytest.raises(generation.BatchError, match="Invalid API path"):
        client.api("GET", "https://elsewhere.example/")


def test_upload_uses_exact_ticket_headers_then_commits(monkeypatch):
    client = generation.SpicyClient("secret-never-sent")
    calls = []
    content = png()
    headers = {"Content-Type": "image/png", "Content-Length": str(len(content))}

    def api(method, path, **kwargs):
        calls.append(path)
        if path == "/common/upload-url":
            assert kwargs["payload"] == {"contentType": "image/png", "bytes": len(content)}
            return {"fileId": "fil-test", "method": "PUT", "maxBytes": 100000,
                    "uploadUrl": "https://storage.example/upload", "headers": headers}
        return {"status": "ready", "uri": "spicy://f/fil_test", "expiresAt": "2099-01-01T00:00:00Z",
                "sha256": generation.sha256(content)}

    def storage(method, url, **kwargs):
        calls.append(method)
        assert kwargs == {"headers": headers, "data": content}

    monkeypatch.setattr(client, "api", api)
    monkeypatch.setattr(client, "storage", storage)
    assert client.upload(content)["uri"] == "spicy://f/fil_test"
    assert calls == ["/common/upload-url", "PUT", "/files/fil-test/commit"]


def test_create_network_retries_keep_same_idempotency_header(monkeypatch):
    client = generation.SpicyClient("secret-never-sent")
    calls = []

    def request(*args, **kwargs):
        calls.append(kwargs)
        if len(calls) == 1:
            raise requests.Timeout()
        return SimpleNamespace(status_code=202, json=lambda: {"code": 200, "data": {"taskId": "job-test"}})

    monkeypatch.setattr(client.api_session, "request", request)
    monkeypatch.setattr(generation.time, "sleep", lambda seconds: None)
    assert client.api("POST", "/jobs/createTask", payload={"model": generation.MODEL}, idempotency_key="persisted-key")["taskId"] == "job-test"
    assert calls[0] == calls[1]


def test_caption_edits_survive_catalog_refresh(setup_batch):
    source, destination, _, worker = setup_batch
    worker().process(source / "pose.png")
    catalog = json.loads((destination / "catalog.json").read_text())
    assert catalog["pose"][0]["prompt"] == ""
    catalog["pose"][0].update(label="Seated", prompt="seated, knees raised", category="Seated")
    (destination / "catalog.json").write_text(json.dumps(catalog))
    worker().catalog()
    assert json.loads((destination / "catalog.json").read_text()) == catalog


def test_key_alias_and_environment_precedence(tmp_path, monkeypatch):
    monkeypatch.setattr(generation, "ROOT", tmp_path)
    monkeypatch.delenv("SPICY_API_KEY", raising=False)
    monkeypatch.delenv("SAI_API_TOKEN", raising=False)
    (tmp_path / ".env").write_text("SAI_API_TOKEN='file-alias'\n")
    assert generation.read_key() == "file-alias"
    monkeypatch.setenv("SPICY_API_KEY", "environment-key")
    assert generation.read_key() == "environment-key"


@pytest.mark.parametrize("field", ["prompt", "style", "resolution", "image_order", "source", "key_fingerprint"])
def test_mismatch_identifies_changed_field_without_modifying_batch(setup_batch, field):
    source, destination, client, worker = setup_batch
    worker().process(source / "pose.png")
    original = (destination / "batch.json").read_bytes()
    config = json.loads(original)["configuration"]
    if field in ("source", "key_fingerprint"):
        config[field] = "changed-private-value"
    else:
        config["recipe"][field] = "changed-private-value"
    client.key_fingerprint = config["key_fingerprint"]
    with pytest.raises(generation.BatchError) as failure:
        generation.Batch(source if field != "source" else source / "moved", destination,
                         config["recipe"], client, "0.051", "2", 10)
    message = str(failure.value)
    assert ("API key" if field == "key_fingerprint" else field) in message
    if field in ("prompt", "key_fingerprint"):
        assert "changed-private-value" not in message
    assert (destination / "batch.json").read_bytes() == original
    assert len(client.creates) == 1


def test_source_first_order_is_preserved_after_a_lost_response(setup_batch):
    source, destination, client, _ = setup_batch
    recipe = {"model": generation.MODEL, "style": "spicy://f/fil_style",
              "prompt": "Edit Image 1; Image 2 supplies linework only.",
              "resolution": "2k", "image_order": "source-first"}
    client.fail_create = True
    with pytest.raises(generation.BatchError, match="Response lost"):
        generation.Batch(source, destination, recipe, client, "0.051", "2", 10).process(source / "pose.png")
    persisted = json.loads((destination / "batch.json").read_text())
    request = persisted["jobs"]["pose.png"]["payload"]
    assert request["input"]["image_urls"] == ["spicy://f/fil_source", "spicy://f/fil_style"]
    assert request["input"]["prompt"] == recipe["prompt"]
    client.fail_create = False
    generation.Batch(source, destination, recipe, client, "0.051", "2", 10).process(source / "pose.png")
    assert client.creates[0] == client.creates[1]
    assert client.quotes == 1
