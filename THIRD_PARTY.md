# Third-party notices

Model provenance and exact checksums: `models/manifest.json`.

The application downloads the pinned NanoDet artifacts from OpenCV Zoo and InsightFace buffalo_l detector/recognizer weights from a pinned Hugging Face revision. Model weights are not redistributed here. The Hugging Face model card points to InsightFace model-use terms; verify and follow those terms before sharing or using beyond the permitted academic scope. The included NanoDet wrapper remains upstream code and must retain its license.

| Component | Source | License text |
|---|---|---|
| NanoDet model and wrapper | OpenCV Zoo, commit47534e27c9851bb1128ccc0102f1145e27f23f98, `models/object_detection_nanodet` | [Apache-2.0](models/licenses/nanodet.txt) |
| InsightFace buffalo_l detector and recognizer | [`immich-app/buffalo_l`](https://huggingface.co/immich-app/buffalo_l), revision `d09715916a0778919a770c343533641e250b8699`; exact artifact hashes in manifest | Model-use terms not verified; see note above |

Python dependencies are pinned in `requirements.txt` / `requirements-dev.txt`; their authors' license terms remain applicable. ESP32 Arduino core, esp32-camera and ArduinoJson follow their upstream licenses. Use the upstream projects' notices when redistributing those components.

This file does not assign a license to the team's own application source. The team should choose an appropriate application license before public distribution.
