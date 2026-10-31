"""One model owner and latest-only processing loop; no HTTP handlers here."""

from __future__ import annotations

import logging

import numpy as np

from .runtime import DomainError

logger = logging.getLogger(__name__)


class VisionWorker:
    def __init__(self, service):
        self.service = service

    def run(self):
        s = self.service
        vision = None
        if s.cfg["profile"] == "hardware" or s.vision_factory is not None:
            try:
                from .vision import OpenCVVision

                if s.vision_factory:
                    vision = s.vision_factory(s.cfg)
                elif s.cfg["cloud"]["inference"] == "cloud":
                    from .cloud_remote import CloudRemoteVision

                    vision = CloudRemoteVision(s.cfg)
                else:
                    vision = OpenCVVision(s.cfg)
                # Exercise model loading and both detection paths before reporting ready.
                vision.detections(np.zeros((480, 640, 3), dtype=np.uint8))
                with s.lock:
                    s.vision_ready = True
            except Exception:
                logger.exception("Vision initialization failed")
                with s.lock:
                    s.vision_ready, s.vision_error = False, "VISION_INIT_FAILED_CHECK_MODELS"
        vision_revision = -1
        gallery_revision, gallery = -1, {}
        cadence_revision, next_start, last_observed = -1, 0, -1
        last_cleanup = s.now()
        while True:
            packet = None
            try:
                with s.condition:
                    s.video.expire()
                    if cadence_revision != s.mode_revision:
                        cadence_revision, next_start, last_observed = s.mode_revision, 0, -1
                        s.analysis_times.clear()
                        s.analysis_durations.clear()
                    interval = s.cfg["analysis"][
                        "enrollment_interval_ms"
                        if s.mode == "ENROLLMENT"
                        else "monitoring_interval_ms"
                    ]
                    remaining = max(0, next_start - s.now())
                    if not s.stopping and (not s.pending or remaining):
                        s.condition.wait(timeout=min(1, remaining / 1000) if remaining else 1)
                    if s.stopping:
                        if vision and hasattr(vision, "close"):
                            vision.close()
                        return
                    s._expire_capture()
                    s.video.expire()
                    s._check_health(s.now())
                    if s.now() - last_cleanup > 3600000:
                        try:
                            s.store.cleanup(s.cfg["retention"])
                        except Exception:
                            s.worker_error = "CLEANUP_FAILED"
                        last_cleanup = s.now()
                    if cadence_revision != s.mode_revision:
                        cadence_revision, next_start, last_observed = s.mode_revision, 0, -1
                    if s.now() < next_start:
                        continue
                    if (
                        s.pending
                        and s.pending.meta.purpose == "enrollment"
                        and not s.video.sampling()
                    ):
                        s._cancel_pending()
                    packet, s.pending = s.pending, None
                    if packet is None:
                        continue
                    if packet.meta.mode_revision != s.mode_revision:
                        s.receipt(packet, "cancelled", "REVISION_CHANGED")
                        continue
                    s.receipt(packet, "processing")
                    if (
                        vision
                        and packet.meta.purpose == "monitoring"
                        and gallery_revision != s.gallery_revision
                    ):
                        gallery = s.store.gallery()
                        gallery_revision = s.gallery_revision
                    revision = (s.mode_revision, s.gallery_revision)
                    reset_vision = bool(vision and vision_revision != revision)
                    observed = (
                        packet.observed_ms
                        if packet.observed_ms is not None
                        else packet.received_ms - packet.meta.capture_age_ms
                    )
                    if s.now() - observed > s.cfg["limits"]["max_result_age_ms"]:
                        s.receipt(packet, "stale", "RESULT_TOO_OLD")
                        continue
                    if observed <= last_observed:
                        s.receipt(packet, "stale", "OBSERVATION_CLOCK_REGRESSED")
                        continue
                    last_observed = observed
                    started = s.now()
                    next_start = started + interval
                    s.last_ai_start_ms = started
                    s.last_ai_queue_ms = max(0, started - packet.received_ms)
                    s.processing = True
                if reset_vision:
                    vision.reset()
                    vision_revision = revision
                quality, vector, observations = None, None, []
                if packet.meta.purpose == "monitoring":
                    if vision:
                        observations = vision.analyze(packet.jpeg, gallery, observed)
                    elif packet.observations is not None:
                        observations = packet.observations
                    else:
                        raise DomainError("REAL_VISION_REQUIRED")
                elif packet.meta.purpose == "enrollment":
                    if vision is None:
                        raise DomainError("REAL_VISION_REQUIRED")
                    vector, quality = vision.enrollment_sample(packet.jpeg)
                with s.lock:
                    s.analysis_durations.append(max(0, s.now() - started))
                    s.analysis_times.append(started)
                    s.last_stage_ms = getattr(vision, "last_timings", None)
                    # Expire leases even if inference held the worker beyond the deadline.
                    s._expire_capture()
                    s.video.expire()
                    # Recheck after native inference to close races with STOP/gallery deletion.
                    if (
                        packet.meta.mode_revision != s.mode_revision
                        or packet.gallery_revision != s.gallery_revision
                        or packet.meta.session_id != s.session_id
                        or (packet.meta.purpose == "enrollment" and not s.video.sampling())
                    ):
                        s.receipt(packet, "cancelled", "REVISION_CHANGED")
                        vision_revision = -1
                    elif s.now() - observed > s.cfg["limits"]["max_result_age_ms"]:
                        s.receipt(packet, "stale", "RESULT_TOO_OLD")
                        vision_revision = -1
                    else:
                        s.environment_quality = getattr(vision, "last_environment", None)
                        if packet.meta.purpose == "monitoring":
                            try:
                                s._publish(packet, observations)
                            except Exception:
                                s.fail_closed("FRAME_PROCESSING_FAILED")
                                raise
                        elif packet.meta.purpose == "enrollment":
                            s._enrollment_result(packet, vector, quality)
                            s.last_analysis_ms = s.now()
                        s.receipt(packet, "processed")
            except Exception as exc:
                logger.exception("Worker failed: %s", packet.frame_id if packet else "housekeeping")
                with s.lock:
                    pending = s.fail_closed("FRAME_PROCESSING_FAILED")
                    vision_revision = -1
                    try:
                        if packet:
                            s.receipt(packet, "failed", type(exc).__name__)
                        if pending:
                            s.receipt(pending, "cancelled", "WORKER_FAILED")
                    except Exception:
                        s.mode, s.mode_revision = "DISARMED", s.mode_revision + 1
                        s.command = None
                        s.worker_error = "STORAGE_FAULT"
            finally:
                with s.lock:
                    s.processing = False
