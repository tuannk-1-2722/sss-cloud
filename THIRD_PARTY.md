# Third-party notices

Model provenance and exact checksums: `models/manifest.json`.

The application downloads these pinned OpenCV Zoo artifacts from upstream. ONNX weights are not distributed in this archive. The included NanoDet wrapper, when present, remains upstream code and must retain its license.

| Component | Source | License text |
|---|---|---|
| NanoDet model and wrapper | OpenCV Zoo, commit47534e27c9851bb1128ccc0102f1145e27f23f98, `models/object_detection_nanodet` | [Apache-2.0](models/licenses/nanodet.txt) |
| YuNet model | Same commit, `models/face_detection_yunet` | [MIT](models/licenses/yunet.txt) |
| SFace model | Same commit, `models/face_recognition_sface` | [Apache-2.0](models/licenses/sface.txt) |

Python dependencies are pinned in `requirements.txt` / `requirements-dev.txt`; their authors' license terms remain applicable. ESP32 Arduino core, esp32-camera and ArduinoJson follow their upstream licenses. Use the upstream projects' notices when redistributing those components.

This file does not assign a license to the team's own application source. The team should choose an appropriate application license before public distribution.
