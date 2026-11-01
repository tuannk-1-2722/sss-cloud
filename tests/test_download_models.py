import hashlib
import io

import pytest

from tools import download_models


class Response:
    def __init__(self, body):
        self.body = io.BytesIO(body)

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        self.body.close()

    def read(self, size):
        return self.body.read(size)


def test_fetch_downloads_and_verifies_model(monkeypatch, tmp_path):
    body = b"pinned model bytes"
    monkeypatch.setattr(
        download_models.urllib.request, "urlopen", lambda *_args, **_kwargs: Response(body)
    )
    target = tmp_path / "nested" / "model.onnx"

    download_models.fetch(
        "https://models.example/model.onnx",
        target,
        hashlib.sha256(body).hexdigest(),
        len(body),
    )

    assert target.read_bytes() == body
    assert not target.with_suffix(".onnx.part").exists()


def test_fetch_rejects_wrong_checksum_without_replacing_target(monkeypatch, tmp_path):
    monkeypatch.setattr(
        download_models.urllib.request, "urlopen", lambda *_args, **_kwargs: Response(b"bad")
    )
    target = tmp_path / "model.onnx"

    with pytest.raises(ValueError, match="CHECKSUM/SIZE FAILED"):
        download_models.fetch("https://models.example/model.onnx", target, "0" * 64, 3)

    assert not target.exists()
    assert not target.with_suffix(".onnx.part").exists()
