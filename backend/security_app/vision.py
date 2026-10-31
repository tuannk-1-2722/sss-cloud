from __future__ import annotations

import hashlib
import importlib.util
import json
import time

import cv2
import numpy as np

from .config import ROOT
from .contracts import Observation
from .matching import match, normalize
from .spatial import zone_for
from .tracking import ConservativeTracker as ConservativeTracker


def perceptual_hash(image) -> str:
    small = cv2.resize(cv2.cvtColor(image, cv2.COLOR_BGR2GRAY), (9, 8))
    bits = (small[:, 1:] > small[:, :-1]).reshape(-1)
    return f"{sum(int(bit) << i for i, bit in enumerate(bits)):016x}"


def hash_distance(a: str, b: str) -> int:
    return (int(a, 16) ^ int(b, 16)).bit_count()


def environment_quality(image: np.ndarray, config: dict) -> dict:
    """Screen exposure problems conservatively; scores are not a weather classifier.

    Dynamic thresholds are placeholders until a labeled day/night validation is done.
    A dark scene does not mean the person is unknown.
    """
    gray = cv2.cvtColor(cv2.resize(image, (160, 120)), cv2.COLOR_BGR2GRAY)
    median = float(np.median(gray))
    p95 = float(np.percentile(gray, 95))
    dark = float((gray <= 10).mean())
    bright = float((gray >= 245).mean())
    q = config["environment"]
    reason = None
    if median < q["min_median_luma"] and p95 < q["low_light_p95"]:
        reason = "SCENE_TOO_DARK"
    elif dark >= q["max_clipped_ratio"] or bright >= q["max_bright_ratio"]:
        reason = "SCENE_EXPOSURE_FAILURE"
    return {
        "usable": reason is None,
        "reason": reason,
        "median_luma": round(median, 1),
        "p95_luma": round(p95, 1),
        "dark_fraction": round(dark, 3),
        "bright_fraction": round(bright, 3),
    }


class OpenCVVision:
    """Construct and use only inside the dedicated worker thread."""

    def __init__(self, config: dict):
        self.cfg = config
        manifest = json.loads((ROOT / "models/manifest.json").read_text())
        for artifact in [*manifest["artifacts"], manifest["wrapper"]]:
            path = ROOT / "models" / artifact["file"]
            if (
                not path.is_file()
                or hashlib.sha256(path.read_bytes()).hexdigest() != artifact["sha256"]
            ):
                raise ValueError("MODEL_MISSING_OR_HASH_MISMATCH")
        files = {a["name"]: str(ROOT / "models" / a["file"]) for a in manifest["artifacts"]}
        spec = importlib.util.spec_from_file_location(
            "sss_nanodet", ROOT / "models/vendor/nanodet.py"
        )
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        v = config["vision"]
        cv2.setNumThreads(v["opencv_threads"])
        self.person_detector = module.NanoDet(
            files["nanodet"], v["person_confidence"], v["person_nms"]
        )
        self.face_detector = cv2.FaceDetectorYN.create(
            files["yunet"], "", (640, 480), v["face_confidence"], v["face_nms"]
        )
        self.recognizer = cv2.FaceRecognizerSF.create(files["sface"], "")
        self.dimension = manifest["embedding_dimension"]
        self.tracker = ConservativeTracker(config)
        self.last_environment = None

    def reset(self):
        self.tracker.reset()

    def decode(self, jpeg: bytes):
        started = time.perf_counter()
        image = cv2.imdecode(np.frombuffer(jpeg, np.uint8), cv2.IMREAD_COLOR)
        if image is None:
            raise ValueError("JPEG_DECODE_FAILED")
        self.last_timings = {"decode_ms": round((time.perf_counter() - started) * 1000, 2)}
        return image

    def detections(self, image):
        started = time.perf_counter()
        h, w = image.shape[:2]
        scale = min(416 / w, 416 / h)
        rw, rh = round(w * scale), round(h * scale)
        left, top = (416 - rw) // 2, (416 - rh) // 2
        canvas = np.zeros((416, 416, 3), np.uint8)
        canvas[top : top + rh, left : left + rw] = cv2.resize(image, (rw, rh))
        persons = []
        for row in self.person_detector.infer(canvas):
            if int(row[5]) != 0:
                continue
            x1, y1 = max(0, (row[0] - left) / scale), max(0, (row[1] - top) / scale)
            x2, y2 = min(w, (row[2] - left) / scale), min(h, (row[3] - top) / scale)
            if x2 > x1 and y2 > y1:
                persons.append((x1 / w, y1 / h, (x2 - x1) / w, (y2 - y1) / h))
        if hasattr(self, "last_timings"):
            self.last_timings["person_detection_ms"] = round(
                (time.perf_counter() - started) * 1000, 2
            )
        return persons, self.faces(image)

    def faces(self, image):
        started = time.perf_counter()
        h, w = image.shape[:2]
        self.face_detector.setInputSize((w, h))
        _, faces = self.face_detector.detect(image)
        if hasattr(self, "last_timings"):
            self.last_timings["face_detection_ms"] = round(
                (time.perf_counter() - started) * 1000, 2
            )
        return [] if faces is None else list(faces)

    def quality(self, image, face, enrollment: bool = False):
        v = self.cfg["vision"]
        x, y, w, h = map(float, face[:4])
        required = v["enrollment_face_min_px"] if enrollment else v["face_min_px"]
        if min(w, h) < required:
            return None, {"reason": "FACE_TOO_SMALL", "min_side_px": min(w, h)}
        landmarks = np.asarray(face[4:14]).reshape(5, 2)
        if not np.isfinite(landmarks).all():
            return None, {"reason": "LANDMARKS_INVALID"}
        if np.any(landmarks[:, 0] < x - 0.15 * w) or np.any(landmarks[:, 0] > x + 1.15 * w):
            return None, {"reason": "LANDMARKS_INVALID"}
        if np.any(landmarks[:, 1] < y - 0.15 * h) or np.any(landmarks[:, 1] > y + 1.15 * h):
            return None, {"reason": "LANDMARKS_INVALID"}
        crop = image[
            max(0, int(y)) : min(image.shape[0], int(y + h)),
            max(0, int(x)) : min(image.shape[1], int(x + w)),
        ]
        if crop.size == 0:
            return None, {"reason": "FACE_CROP_EMPTY"}
        gray = cv2.resize(cv2.cvtColor(crop, cv2.COLOR_BGR2GRAY), (112, 112))
        blur = float(cv2.Laplacian(gray, cv2.CV_64F).var())
        clipped = max(float((gray <= 10).mean()), float((gray >= 245).mean()))
        if blur < v["blur_min"]:
            return None, {"reason": "FACE_BLURRED", "blur": blur}
        if clipped > v["clipped_fraction_max"]:
            return None, {"reason": "FACE_EXPOSURE", "clipped": clipped}
        aligned = self.recognizer.alignCrop(image, np.asarray(face, np.float32))
        return aligned, {
            "reason": None,
            "blur": blur,
            "clipped": clipped,
            "min_side_px": min(w, h),
            "perceptual_hash": perceptual_hash(aligned),
            "pose_signature": [
                float(
                    (landmarks[2, 0] - np.mean(landmarks[:2, 0]))
                    / max(1.0, abs(landmarks[1, 0] - landmarks[0, 0]))
                ),
                w / image.shape[1],
                (x + w / 2) / image.shape[1],
                (y + h / 2) / image.shape[0],
            ],
        }

    def feature(self, aligned) -> np.ndarray:
        started = time.perf_counter()
        result = normalize(self.recognizer.feature(aligned).copy())
        if result.size != self.dimension:
            raise ValueError("EMBEDDING_DIMENSION_CHANGED")
        if hasattr(self, "last_timings"):
            self.last_timings["embedding_ms"] = round(
                self.last_timings.get("embedding_ms", 0) + (time.perf_counter() - started) * 1000, 2
            )
        return result

    def enrollment_sample(self, jpeg: bytes):
        image = self.decode(jpeg)
        self.last_environment = environment_quality(image, self.cfg)
        faces = self.faces(image)
        if len(faces) != 1:
            return None, {"reason": "EXACTLY_ONE_FACE_REQUIRED"}
        face = faces[0]
        h, w = image.shape[:2]
        fx, fy, fw, fh = map(float, face[:4])
        x, y, rw, rh = self.cfg["enrollment"]["face_region"]
        cx, cy = (fx + fw / 2) / w, (fy + fh / 2) / h
        if ((cx - x - rw / 2) / (rw / 2)) ** 2 + ((cy - y - rh / 2) / (rh / 2)) ** 2 > 1:
            return None, {"reason": "FACE_OUTSIDE_GUIDE"}
        if not self.last_environment["usable"]:
            return None, {"reason": self.last_environment["reason"]}
        aligned, quality = self.quality(image, face, enrollment=True)
        return (self.feature(aligned) if aligned is not None else None), quality

    def analyze(self, jpeg: bytes, gallery: dict, now: int) -> list[Observation]:
        image = self.decode(jpeg)
        height, width = image.shape[:2]
        self.last_environment = environment_quality(image, self.cfg)
        persons, faces = self.detections(image)
        boxes, face_for_box, valid = list(persons), [[] for _ in persons], [True for _ in persons]
        ambiguous = set()
        for face in faces:
            x, y, w, h = map(float, face[:4])
            cx, cy = (x + w / 2) / width, (y + h / 2) / height
            candidates = [
                i
                for i, box in enumerate(persons)
                if box[0] <= cx <= box[0] + box[2] and box[1] <= cy <= box[1] + box[3] * 0.65
            ]
            if len(candidates) == 1:
                face_for_box[candidates[0]].append(face)
            elif candidates:
                ambiguous.update(candidates)
            else:
                x1, y1 = max(0, x) / width, max(0, y) / height
                x2, y2 = min(width, x + w) / width, min(height, y + h) / height
                if x2 > x1 and y2 > y1:
                    boxes.append((x1, y1, x2 - x1, y2 - y1))
                    face_for_box.append([face])
                    valid.append(False)  # Face-only identity cannot establish approach direction.
        ambiguous.update(i for i, associated in enumerate(face_for_box) if len(associated) > 1)
        for i in ambiguous:
            valid[i] = False
        ids = self.tracker.assign(boxes, now, valid)
        results, embedded = [], 0
        for index, (key, box, associated, geometry_valid) in enumerate(
            zip(ids, boxes, face_for_box, valid, strict=True)
        ):
            decision, person_id, reason = "NOT_CHECKED", None, None
            if zone_for(box, self.cfg["zones"]) == "B":
                decision = "NO_FACE"
                if not self.last_environment["usable"]:
                    decision, reason = "UNCERTAIN", self.last_environment["reason"]
                elif index in ambiguous:
                    decision, reason = "UNCERTAIN", "FACE_ASSOCIATION_AMBIGUOUS"
                elif associated and embedded < self.cfg["policy"]["max_simultaneous"]:
                    aligned, quality = self.quality(image, associated[0])
                    if aligned is None:
                        decision, reason = "UNCERTAIN", quality["reason"]
                    else:
                        result = match(self.feature(aligned), gallery, self.cfg["vision"])
                        decision, person_id = result["decision"], result["person_id"]
                        reason = result.get("reason")
                        embedded += 1
                elif associated:
                    decision, reason = "UNCERTAIN", "CAPACITY_EXCEEDED"
            results.append(
                Observation(
                    track_id=key,
                    bbox=box,
                    decision=decision,
                    person_id=person_id,
                    geometry_valid=geometry_valid,
                    reason=reason,
                )
            )
        return results
