"""Setup does not send messages, expose credentials in argv, or overwrite another bot."""

import io
import json
import os
from types import SimpleNamespace

import pytest

from tools import telegram_setup


def test_ssh_keeps_token_in_stdin_and_checks_host_key(monkeypatch):
    seen = []

    def run(command, **kwargs):
        seen.append((command, kwargs))
        return SimpleNamespace(returncode=0, stdout='{"ok":true,"username":"security_demo_bot"}')

    monkeypatch.setattr(telegram_setup.subprocess, "run", run)
    args = SimpleNamespace(
        identity="private-key", ssh_host="user@host", remote_python="/safe/python"
    )
    data = {"token": "12345:secret-value", "phase": "inspect"}
    telegram_setup.remote_setup(args, data)
    assert "secret-value" not in str(seen[0][0])
    assert "StrictHostKeyChecking=yes" in seen[0][0]
    assert json.loads(seen[0][1]["input"]) == data


@pytest.mark.skipif(os.name == "nt", reason="Remote program requires POSIX secret-file permissions")
@pytest.mark.parametrize("existing", [False, True])
def test_remote_binds_only_matching_private_start_and_writes_real_env_lines(
    tmp_path, monkeypatch, capsys, existing
):
    import httpx

    tmp_path.chmod(0o700)
    payload = {
        "token": "12345:private-token",
        "phase": "bind",
        "private_dir": str(tmp_path),
        "nonce": "random-code",
        "photos": True,
        "replace": False,
    }
    monkeypatch.setattr("sys.stdin", io.StringIO(json.dumps(payload)))
    methods = []

    def get(url, **kwargs):
        method = url.rsplit("/", 1)[-1]
        methods.append(method)
        if method == "getMe":
            result = {"username": "security_demo_bot"}
        elif method == "getWebhookInfo":
            result = {"url": ""}
        else:
            assert "offset" not in kwargs["params"]
            result = [
                {
                    "message": {
                        "text": "unrelated-private-text",
                        "chat": {"id": 1, "type": "private"},
                    }
                },
                {"message": {"text": "/start random-code", "chat": {"id": -123, "type": "group"}}},
                {"message": {"text": "/start random-code", "chat": {"id": 456, "type": "private"}}},
            ]
        return SimpleNamespace(json=lambda: {"ok": True, "result": result})

    monkeypatch.setattr(httpx, "get", get)
    target = tmp_path / "telegram.env"
    if existing:
        target.write_text("previous-credentials")
    try:
        exec(compile(telegram_setup.REMOTE, "remote-setup", "exec"), {})
    except SystemExit as exc:
        assert exc.code == 0
    output = capsys.readouterr().out
    assert "private-token" not in output and "unrelated-private-text" not in output
    assert set(methods) == {"getMe", "getWebhookInfo", "getUpdates"}
    if existing:
        assert target.read_text() == "previous-credentials"
        assert json.loads(output)["reason"] == "CREDENTIAL_FILE_EXISTS"
    else:
        assert target.read_text().splitlines() == [
            "SSS_TELEGRAM_TOKEN=12345:private-token",
            "SSS_TELEGRAM_CHAT=456",
            "SSS_TELEGRAM_PHOTO_CONSENT=1",
        ]
        assert target.stat().st_mode & 0o077 == 0
        assert json.loads(output)["sent_messages"] == 0
