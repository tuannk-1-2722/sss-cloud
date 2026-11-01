from __future__ import annotations

import hashlib
import importlib.util
import json
import time
from dataclasses import dataclass

import cv2
import numpy as np

from .config import ROOT
from .contracts import Observation
from .matching import match, normalize
from .spatial import zone_for
from .tracking import ConservativeTracker as ConservativeTracker


@dataclass(slots=True)
class FaceRecord:
    values: np.ndarray
    source: object
    model_image: np.ndarray | None = None
    embedding: np.ndarray | None = None
    aligned: np.ndarray | None = None

    def __getitem__(self, key):
        return self.values[key]


def preprocess_image(image: np.ndarray, aggressive: bool = False) -> tuple[np.ndarray, float]:
    height, width = image.shape[:2]
    scale = max(1.0, 360.0 / min(height, width))
    if scale > 1:
        image = cv2.resize(
            image,
            (int(width * scale), int(height * scale)),
            interpolation=cv2.INTER_LANCZOS4,
        )
    blue, green, red = cv2.split(image)
    means = [float(channel.mean()) for channel in (blue, green, red)]
    mean_gray = sum(means) / 3
    balanced = cv2.merge(
        [
            cv2.convertScaleAbs(
                channel, alpha=float(np.clip(mean_gray / (mean + 1e-4), 0.75, 1.35))
            )
            for channel, mean in zip((blue, green, red), means, strict=True)
        ]
    )
    lab = cv2.cvtColor(balanced, cv2.COLOR_BGR2LAB)
    light, a, b = cv2.split(lab)
    mean_light = float(light.mean())
    height, width = light.shape
    reduced = cv2.resize(
        light, (max(16, width // 8), max(16, height // 8)), interpolation=cv2.INTER_AREA
    )
    illumination = cv2.addWeighted(
        cv2.GaussianBlur(reduced, (0, 0), 5),
        0.5,
        cv2.GaussianBlur(reduced, (0, 0), 15),
        0.5,
        0,
    )
    illumination = cv2.resize(illumination, (width, height), interpolation=cv2.INTER_LINEAR)
    target = 140.0 if aggressive else 120.0
    max_gain = 3.2 if aggressive else 2.6
    gain = np.clip(target / (illumination.astype(np.float32) + 20), 0.7, max_gain)
    exponent = 0.75 if aggressive else 0.65
    adjusted = np.clip(light.astype(np.float32) * gain**exponent, 0, 255).astype(np.uint8)
    if aggressive:
        gamma = 0.45
    elif mean_light < 75:
        gamma = 0.55 + 0.35 * (mean_light / 75)
    elif mean_light > 165:
        gamma = 1.0 + 0.8 * ((mean_light - 165) / 90)
    else:
        gamma = 1.0
    if gamma != 1.0:
        lut = np.array([((i / 255) ** gamma) * 255 for i in range(256)], dtype=np.uint8)
        adjusted = cv2.LUT(adjusted, lut)
    if mean_light < 60 or aggressive:
        adjusted = cv2.medianBlur(adjusted, 3)
    clip_limit = 2.8 if aggressive or mean_light < 75 else 2.0
    adjusted = cv2.createCLAHE(clipLimit=clip_limit, tileGridSize=(8, 8)).apply(adjusted)
    return cv2.cvtColor(cv2.merge([adjusted, a, b]), cv2.COLOR_LAB2BGR), scale


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
    """NanoDet and InsightFace adapter, owned by the dedicated worker thread."""

    def __init__(self, config: dict):
        self.cfg = config
        manifest = json.loads((ROOT / "models/manifest.json").read_text())
        for artifact in [*manifest["artifacts"], manifest["wrapper"]]:
            path = ROOT / "models" / artifact["file"]
            if (
                not path.is_file()
                or (
                    "size" in artifact
                    and path.stat().st_size != artifact["size"]
                )
                or hashlib.sha256(path.read_bytes()).hexdigest() != artifact["sha256"]
            ):
                raise ValueError("MODEL_MISSING_OR_HASH_MISMATCH")
        try:
            from insightface.app import FaceAnalysis
            from insightface.app.common import Face
            import onnxruntime
        except ImportError as exc:
            raise RuntimeError("INSIGHTFACE_DEPENDENCIES_MISSING") from exc

        providers = (
            ["CUDAExecutionProvider", "CPUExecutionProvider"]
            if "CUDAExecutionProvider" in onnxruntime.get_available_providers()
            else ["CPUExecutionProvider"]
        )
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
        self.face_analysis = FaceAnalysis(
            name="buffalo_l",
            root=str(ROOT),
            allowed_modules=["detection", "recognition"],
            providers=providers,
        )
        self.face_analysis.prepare(
            ctx_id=0 if providers[0] == "CUDAExecutionProvider" else -1, det_size=(640, 640)
        )
        self.face_analysis.models["detection"].det_thresh = v["face_confidence"]
        self.face_analysis.models["detection"].nms_thresh = v["face_nms"]
        self.face_type = Face
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
        prepared, scale = preprocess_image(image)
        detector = self.face_analysis.models["detection"]
        boxes, landmarks = detector.detect(prepared, max_num=0, metric="default")
        if boxes is None or len(boxes) == 0:
            brightness = float(cv2.cvtColor(image, cv2.COLOR_BGR2GRAY).mean())
            if brightness < 85 or brightness > 165:
                prepared, scale = preprocess_image(image, aggressive=True)
                boxes, landmarks = detector.detect(prepared, max_num=0, metric="default")
        records = []
        if boxes is None:
            boxes, landmarks = [], []
        elif landmarks is None:
            landmarks = [None] * len(boxes)
        for model_box, model_points in zip(boxes, landmarks, strict=True):
            model_box = np.asarray(model_box, dtype=np.float32).copy()
            box = model_box.copy()
            box[:4] /= scale
            if model_points is not None:
                model_points = np.asarray(model_points, dtype=np.float32).reshape(5, 2)
                points = (model_points / scale).reshape(-1)
            else:
                model_points = None
                points = np.full(10, np.nan, dtype=np.float32)
            values = np.concatenate(
                ([box[0], box[1], box[2] - box[0], box[3] - box[1]], points)
            ).astype(np.float32)
            source = self.face_type(
                bbox=model_box,
                kps=model_points,
            )
            records.append(FaceRecord(values, source, prepared))
        if hasattr(self, "last_timings"):
            self.last_timings["face_detection_ms"] = round(
                (time.perf_counter() - started) * 1000, 2
            )
        return records

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
        if face.model_image is None:
            return None, {"reason": "FACE_IMAGE_UNAVAILABLE"}
        started = time.perf_counter()
        self.face_analysis.models["recognition"].get(face.model_image, face.source)
        face.embedding = np.asarray(
            face.source.normed_embedding, dtype=np.float32
        ).reshape(-1)
        if hasattr(self, "last_timings"):
            self.last_timings["embedding_ms"] = round(
                self.last_timings.get("embedding_ms", 0)
                + (time.perf_counter() - started) * 1000,
                2,
            )
        if (
            face.embedding.size != self.dimension
            or not np.isfinite(face.embedding).all()
            or np.linalg.norm(face.embedding) < 1e-8
        ):
            return None, {"reason": "EMBEDDING_INVALID"}
        from insightface.utils import face_align

        face.aligned = face_align.norm_crop(
            face.model_image,
            landmark=np.asarray(face.source.kps, dtype=np.float32),
            image_size=112,
            mode="arcface",
        )
        return face, {
            "reason": None,
            "blur": blur,
            "clipped": clipped,
            "min_side_px": min(w, h),
            "perceptual_hash": perceptual_hash(face.aligned),
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

    def feature(self, face: FaceRecord) -> np.ndarray:
        started = time.perf_counter()
        if face.embedding is None:
            raise ValueError("EMBEDDING_NOT_COMPUTED")
        result = normalize(face.embedding.copy())
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
