# Bằng chứng bàn giao v2.1 — review 02/10/2026

Đây là **baseline software đã sửa/review**, không phải hệ thống camera/còi đã
nghiệm thu. Không có dữ liệu người thật, phần cứng hoặc thông báo Telegram thật.

| Kiểm tra thực sự chạy | Kết quả | Phạm vi |
|---|---|---|
| Cài requirements-dev đã ghim + editable package, Python3.12/Linux | PASS | Không đổi dependency pins |
| Suite gốc trước sửa | 49 PASS | Có deprecation warning TestClient/httpx |
| Bộ tái hiện ban đầu trên source gốc | 19 FAIL, 2 PASS | Chứng minh test cũ chưa đủ; không phải 19 lỗ hổng độc lập |
| `python -m tools.check --http` sau sửa | PASS | Gate gộp dưới đây |
| Pytest hiện tại | **84 PASS** | Policy, config, API/auth, worker races, enrollment/gallery, transaction/migration, notifier recovery |
| Ruff lint và format | PASS | Backend/tools/tests |
| Node syntax `static/app.js` | PASS | Không thay kiểm GUI/browser |
| g++17 AlarmLogic + FrameFreshness host tests | PASS | Không thay ESP32/FreeRTOS/GPIO |
| HTTP server + simulator | PASS | Unknown event/ACK/OFF; direct-B warning; known info |
| Exact model download/checksum | PASS | NanoDet, YuNet, SFace + wrapper/license |
| `tools.manage models-smoke` | PASS | Embedding128; blank persons0/faces0; không accuracy |
| Full Arduino CLI1.2.2 build | **BLOCKED** | CLI tải index/core lỗi DNS; không tới compiler |
| GitHub Actions, Windows, browser/mobile UI | NOT RUN | Nhóm chạy BASE-01/02/05 |
| HIL, gallery20/two-person camera, holdout, latency/soak | NOT RUN | G1–G4 cần thiết bị/dữ liệu của nhóm |
| Telegram provider thật | NOT RUN | Extension tắt mặc định |

Bản code cuối giữ wire protocol v2; application version v2.1.0. Full build chưa
được chứng minh ngay cả khi host C++ pass. Firmware đã sửa queue/session/revision
recheck và timestamp barrier nhưng cần BASE-02 và H06/H09/H10 trên board.

`review-evidence/software-checks.txt` là output gate gộp; `http-smoke.json` là
report simulator. Log baseline chỉ để tái hiện trước sửa, có thể tham chiếu dòng
source/test cũ; không dùng line number đó như vị trí code v2.1. `handoff-checks.json`
ghi source/config hashes và inventory từng file input. Evidence v2 cũ nằm riêng
trong `archive/v2/`, không đại diện cho code hiện tại.

Không có ONNX weights, secrets.h, runtime, credentials, DB hoặc ảnh người trong ZIP.
Các weights đã kiểm được tải riêng bằng `tools.download_models` khi nhóm setup.

Bước tiếp theo: README → REVIEW → ARCHITECTURE → BASE-01…05 trong PLAN. Hãy ghi
PASS/FAIL/NOT RUN theo bằng chứng thật, không chuyển seed calibration thành validated
để bỏ gate.
