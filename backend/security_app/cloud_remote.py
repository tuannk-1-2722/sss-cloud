"""Optional HTTPS cloud inference adapter, never an implicit fallback for alarm decisions.

Only enable after informed biometric consent and validation of cloud latency/security.
"""

from __future__ import annotations

import base64
import os
from urllib.parse import urlsplit

import httpx
import numpy as np

from .contracts import Observation


def cloud_credentials() -> tuple[str, str]:
    url = os.environ.get("SSS_CLOUD_API_URL", "").strip().rstrip("/")
    token = os.environ.get("SSS_CLOUD_API_TOKEN", "")
    parts = urlsplit(url)
    if (
        parts.scheme != "https"
        or not parts.hostname
        or parts.username
        or parts.password
        or parts.query
        or parts.fragment
        or not token
        or len(token) < 32
    ):
        raise ValueError("CLOUD_REQUIRES_HTTPS_URL_AND_32_CHAR_TOKEN")
    return url, token


class CloudRemoteVision:
    """Same vision interface as local inference, but images are processed remotely."""

    def __init__(self, cfg):
        if os.environ.get("SSS_CLOUD_BIOMETRIC_CONSENT") != "1":
            raise ValueError("CLOUD_BIOMETRIC_CONSENT_REQUIRED")
        self.url, token = cloud_credentials()
        self.client = httpx.Client(
            headers={"Authorization": f"Bearer {token}"},
            timeout=httpx.Timeout(cfg["cloud"]["inference_timeout_ms"] / 1000),
            trust_env=False,
            follow_redirects=False,
        )
        self.last_environment = None

    def _request(self, method: str, route: str, **kwargs):
        response = self.client.request(method, f"{self.url}{route}", **kwargs)
        response.raise_for_status()
        return response.json()

    def detections(self, _dummy):
        ready = self._request("GET", "/health/ready")
        if not ready.get("ready"):
            raise ValueError("CLOUD_INFERENCE_NOT_READY")
        return [], []

    def reset(self):
        self._request("POST", "/v1/reset")

    @staticmethod
    def _gallery(gallery):
        return {
            person_id: [
                base64.b64encode(np.asarray(vector, dtype="<f4").tobytes()).decode()
                for vector in vectors
            ]
            for person_id, vectors in gallery.items()
        }

    def analyze(self, jpeg: bytes, gallery: dict, now: int):
        result = self._request(
            "POST",
            "/v1/infer",
            json={
                "purpose": "monitoring",
                "jpeg": base64.b64encode(jpeg).decode(),
                "gallery": self._gallery(gallery),
            },
        )
        self.last_environment = result.get("environment")
        return [Observation.model_validate(item) for item in result["observations"]]

    def enrollment_sample(self, jpeg: bytes):
        result = self._request(
            "POST",
            "/v1/infer",
            json={
                "purpose": "enrollment",
                "jpeg": base64.b64encode(jpeg).decode(),
                "gallery": {},
            },
        )
        self.last_environment = result.get("environment")
        vector = (
            np.frombuffer(base64.b64decode(result["feature"], validate=True), dtype="<f4").copy()
            if result["feature"]
            else None
        )
        return vector, result["quality"]

    def close(self):
        self.client.close()
