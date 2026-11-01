"""Run stream/parser and dashboard lifecycle regressions without external JS packages."""

import asyncio
import shutil
import subprocess
from types import SimpleNamespace

import pytest
import security_app.main as main_module
from fastapi.testclient import TestClient
from security_app.config import ROOT
from security_app.runtime import DomainError


def test_dashboard_stream_lifecycle():
    node = shutil.which("node")
    if node is None:
        pytest.skip("Node.js is required for dashboard stream lifecycle checks")
    result = subprocess.run(
        [node, "--test", "tests/javascript/camera_stream_test.cjs"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode == 0, result.stdout + result.stderr


def test_stream_script_is_loaded_and_cached_with_dashboard(cfg):
    with TestClient(main_module.create_app(cfg)) as client:
        html = client.get("/").text
        assert html.index("/static/camera-stream.js") < html.index("/static/app.js")
        assert client.get("/static/camera-stream.js").status_code == 200
        assert 'id="live-image"' not in html
        assert '"/static/camera-stream.js"' in client.get("/sw.js").text
        assert client.get("/api/v2/live.mjpg").status_code == 401


def test_diagnostic_button_uses_versioned_script_even_with_old_static_cache(cfg):
    import re

    with TestClient(main_module.create_app(cfg)) as client:
        html = client.get("/").text
        script = re.search(r'src="(/static/app\.js\?v=[^"]+)"', html)
        assert script and 'id="recognition-trace"' in html
        assert "/diagnostics/recognition/start" in client.get(script[1]).text


@pytest.mark.parametrize("accept", ["*/*", "application/octet-stream"])
def test_mjpeg_metadata_freshness_and_viewer_slots(cfg, accept):
    from test_api import login

    with TestClient(main_module.create_app(cfg)) as client:
        headers = login(client)
        leases = [
            client.post("/api/v2/video/viewers", json={}, headers=headers).json()["lease_id"]
            for _ in range(3)
        ]
        assert client.post("/api/v2/video/viewers", json={}, headers=headers).status_code == 429
        s = client.app.state.service
        endpoint = next(r.endpoint for r in client.app.routes if r.path == "/api/v2/live.mjpg")
        request = SimpleNamespace(
            cookies=client.cookies, app=client.app, headers={"accept": accept}
        )
        with s.lock:
            s.video.publish("frame-1", b"fixture-jpeg", s.now() - 250, s.mode_revision)

        async def exercise():
            responses = []
            try:
                for lease in leases:
                    response = await endpoint(request, lease, s)
                    assert response.media_type == (
                        "application/octet-stream"
                        if accept == "application/octet-stream"
                        else "multipart/x-mixed-replace; boundary=frame"
                    )
                    assert response.headers["vary"] == "Accept"
                    assert "no-store" in response.headers["cache-control"]
                    data = await anext(response.body_iterator)
                    assert b"Content-Length: 12\r\n" in data
                    assert b"X-Frame-Id: frame-1\r\n" in data
                    assert b"X-Video-Revision: 1\r\n" in data
                    responses.append(response)
                with pytest.raises(DomainError) as caught:
                    await endpoint(request, leases[0], s)
                assert caught.value.status == 429
            finally:
                for response in responses:
                    await response.body_iterator.aclose()
            with s.lock:
                s.latest["received_ms"] = s.now() - 4000
            response = await endpoint(request, leases[0], s)
            with pytest.raises(TimeoutError):
                await asyncio.wait_for(anext(response.body_iterator), timeout=0.05)
            await response.body_iterator.aclose()
            assert not s.video.listeners

        asyncio.run(exercise())
