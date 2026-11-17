import io

import pytest
from PIL import Image

from scripts.optimize_assets import Optimizer


def image_bytes(format, size=(8, 12), color="red"):
    buffer = io.BytesIO()
    Image.new("RGB", size, color).save(buffer, format=format)
    return buffer.getvalue()


class Response:
    def __init__(self, content=b"", location=""):
        self.content = content
        self.headers = {"Location": location}


def test_conversion_verifies_dimensions_and_resumes_without_new_requests(tmp_path, monkeypatch):
    source = tmp_path / "original"
    (source / "img").mkdir(parents=True)
    (source / "img/pose.png").write_bytes(image_bytes("PNG"))
    destination = tmp_path / "webp"
    worker = Optimizer(source, destination, "test-key-never-used", 1)
    calls = []
    def request(method, url, **kwargs):
        calls.append((method, url, kwargs))
        if url.endswith("/shrink"):
            return Response(location="https://api.tinify.com/output/test")
        return Response(content=image_bytes("WEBP"))
    monkeypatch.setattr(worker, "request", request)
    worker.optimize("img/pose.png")
    assert len(calls) == 2
    assert calls[-1][2]["json"] == {"convert": {"type": "image/webp"}}
    assert Image.open(destination / "img/pose.webp").size == (8, 12)
    worker.optimize("img/pose.png")
    assert len(calls) == 2
    resumed = Optimizer(source, destination, "test-key-never-used", 1)
    monkeypatch.setattr(resumed, "request", request)
    resumed.optimize("img/pose.png")
    assert len(calls) == 2
    assert "test-key-never-used" not in (destination / "tinify-receipts.jsonl").read_text()


def test_existing_webp_uses_download_without_an_extra_conversion(tmp_path, monkeypatch):
    source = tmp_path / "original"
    source.mkdir()
    original = image_bytes("WEBP")
    (source / "image.webp").write_bytes(original)
    worker = Optimizer(source, tmp_path / "webp", "test-key-never-used", 1)
    calls = []
    def request(method, url, **kwargs):
        calls.append(method)
        return Response(location="https://api.tinify.com/output/test", content=original)
    monkeypatch.setattr(worker, "request", request)
    worker.optimize("image.webp")
    assert calls == ["POST", "GET"]
    assert worker.records["image.webp"]["kept_smaller_original_webp"]


def test_output_with_changed_dimensions_is_not_accepted(tmp_path, monkeypatch):
    source = tmp_path / "original"
    source.mkdir()
    (source / "image.png").write_bytes(image_bytes("PNG"))
    worker = Optimizer(source, tmp_path / "webp", "test-key-never-used", 1)
    def request(method, url, **kwargs):
        return Response(location="https://api.tinify.com/output/test", content=image_bytes("WEBP", size=(4, 6)))
    monkeypatch.setattr(worker, "request", request)
    with pytest.raises(ValueError, match="dimensions"):
        worker.optimize("image.png")
    assert not (tmp_path / "webp/image.webp").exists()
    assert worker.records["image.png"]["status"] == "uploaded"


def test_authentication_is_never_sent_to_another_host(tmp_path):
    worker = Optimizer(tmp_path / "source", tmp_path / "output", "test-key-never-used", 1)
    with pytest.raises(ValueError, match="unexpected host"):
        worker.request("POST", "https://example.com/shrink")


def test_rate_limit_retries_but_monthly_quota_stops(tmp_path, monkeypatch):
    from types import SimpleNamespace
    from scripts import optimize_assets

    moment = [0.0]
    monkeypatch.setattr(optimize_assets.time, "monotonic", lambda: moment[0])
    monkeypatch.setattr(optimize_assets.time, "sleep", lambda seconds: moment.__setitem__(0, moment[0] + seconds))
    success = SimpleNamespace(status_code=200, headers={})
    limited = SimpleNamespace(status_code=429, headers={}, json=lambda: {"message": "Rate limit is exceeded. Please lower the rate of consecutive requests."})
    responses = iter([limited, success])
    worker = Optimizer(tmp_path / "source", tmp_path / "output", "test-key-never-used", 1)
    worker.local.session = SimpleNamespace(request=lambda *args, **kwargs: next(responses))
    assert worker.request("POST", "https://api.tinify.com/shrink") is success
    assert not worker.stop.is_set()
    assert worker.rate < 12
    quota = SimpleNamespace(status_code=429, headers={}, json=lambda: {"message": "Your monthly compression limit has been reached."})
    worker.local.session = SimpleNamespace(request=lambda *args, **kwargs: quota)
    with pytest.raises(RuntimeError, match="account error"):
        worker.request("POST", "https://api.tinify.com/shrink")
    assert worker.stop.is_set()
