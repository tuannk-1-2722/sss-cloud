# Bằng chứng bàn giao v2 — 01/10/2026

Đây là **spec viết lại + code nền runnable**, chưa phải nghiệm thu hệ thống an ninh trên phần cứng. Nguồn đầu vào v1 chỉ có tài liệu; v2 bổ sung implementation, simulator và tools. Ngày nộp đã sửa thành khoảng 11/2026, chờ ngày chính thức.

| Kiểm tra đã thực hiện | Kết quả | Ý nghĩa / giới hạn |
|---|---|---|
| Python3.12/Linux: cài exact requirements-dev + editable package | PASS | Chưa kiểm Windows/3.11; dùng hướng dẫn README |
| `python -m pytest -q` | **49 passed** | Policy, matching/tracker math, API/auth/bounds, idempotency/revision, enrollment và lỗi transaction/worker; fixtures synthetic |
| Ruff lint + format | PASS | Backend/tools/tests; không gồm third-party wrapper |
| `node --check backend/security_app/static/app.js` | PASS | Syntax JS; chưa test tương tác GUI trên trình duyệt/điện thoại thật |
| g++17 `tests/firmware/alarm_logic_test.cpp` | PASS | Logic header được dùng trong sketch: duplicate/expiry/STOP/cap/wrap; không compile full ESP32 sketch |
| g++17 `tests/firmware/frame_freshness_test.cpp` | PASS | Tuổi framebuffer từ timestamp driver, boundary/invalid/future/long idle/overflow; không kiểm capture task trên board |
| Download exact ONNX/wrapper/license + SHA256 | PASS | Đúng artifacts trong manifest; không đánh giá accuracy |
| `python -m tools.manage models-smoke` | PASS | Embedding128, blank persons0/faces0; không ảnh người thật |
| Offline evaluation method, 2 blank JPEG VGA | PASS | Decode/analyze/policy/report path trên blank synthetic; CLI dataset hardware thật chưa chạy |
| `python -m tools.smoke_integration` | PASS | Uvicorn thực, bootstrap/seed, auth, hello/sync/synthetic upload, 3 scenarios qua HTTP; runtime tạm riêng |
| Các path liên kết tài liệu | PASS | File đích có trong cây project; model weights được tải riêng |
| Full `arduino-cli compile --profile ai_thinker` | **BLOCKED** | CLI1.2.2 không tải được indexes/platform/library do DNS/network; chưa có bằng chứng sketch compile |
| GitHub workflow remote | CHƯA CHẠY | Có cấu hình CI, nhóm chạy sau khi tạo repo |

HTTP smoke kiểm unknown → `unknown_approach` + command ACK + còi giả ON rồi OFF; direct-b → warning không audible; known → info không audible. Đây là simulated device/predictions; không có còi vật lý hoặc recognition của người thật.

Pytest có một cảnh báo deprecation từ Starlette TestClient về httpx. Suite vẫn pass; đây không phải lỗi chức năng đã quan sát. Giữ toolchain lock hiện tại cho checkpoint, đánh giá nâng version trong issue riêng khi cần.

Report máy đọc ở `handoff-checks.json`: kết quả, môi trường, scenario report và hashes source/config để đối chiếu bản đã kiểm. Logs runtime chứa credentials/ảnh/vector không nằm trong gói; model weights không đóng ZIP, tool tải có hash/license.

**Chưa kiểm chứng:** full firmware, GPIO/logic level/nguồn/PSRAM thật, task scheduling và control latency, HIL, enrollment/recognition camera thật, accuracy/false accept, gallery20/hai người, p95 đầu-cuối, chạy bền30 phút, Windows, UI trên điện thoại và Telegram thật. Nhóm làm G1–G4 trong PLAN/VALIDATION trước gọi bản demo đạt.

Không đánh dấu calibration seed validated trong bàn giao. Không đổi baseline/hạ gate để biến các mục chưa đo thành PASS.
