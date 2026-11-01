# Chỉ mục tài liệu — Smart Security System v2.1

Nâng cấp đang thực hiện: [`RECOGNITION_UPGRADE.md`](RECOGNITION_UPGRADE.md) —
quyết định đã duyệt cho lượt đến cửa ngắn, trace/pilot và gate nhận diện che mặt.
Chuẩn bị và thao tác tại trường: [`DEMO_RUNBOOK.md`](DEMO_RUNBOOK.md).

**Đọc file này trước**, sau đó mở đúng tài liệu cần thiết theo task. Không cần nạp
toàn bộ `docs/` mỗi lần; thứ tự ưu tiên dưới đây cho biết khi nào cần đọc gì.

---

## Thứ tự ưu tiên đọc

| Ưu tiên | File | Khi nào đọc |
|---|---|---|
| **1 — Luôn đọc trước** | `AGENTS.md` (root) | Mọi task — quy tắc làm việc, lệnh chuẩn |
| **2 — Định hướng nhanh** | `docs/INDEX.md` (file này) | Bắt đầu task, không chắc đọc gì |
| **3 — Hiểu sản phẩm** | `docs/TEAM_GUIDE.md` | Cần hiểu luồng sản phẩm, trigger, demo |
| **4 — Hành vi cần tuân thủ** | `docs/SPEC.md` | Coding mọi module nghiệp vụ |
| **5 — Giao tiếp wire** | `docs/CONTRACTS.md` | Firmware, backend, simulator, dashboard |
| **6 — Ranh giới code** | `docs/ARCHITECTURE.md` | Biết sửa ở đâu, ghép với ai, test bằng gì |
| **7 — Gate và backlog** | `docs/PLAN.md` | Xem việc tiếp theo, owner, lịch gate |
| **8 — Kiểm chứng** | `docs/VALIDATION.md` | HIL, đo accuracy, demo script, giới hạn kỹ thuật |
| **9 — Đọc khi cần** | `docs/RESEARCH.md` | Kiểm tra quyết định model/nguồn/dependency |

---

## Tóm tắt từng file

### `docs/TEAM_GUIDE.md` — Hiểu sản phẩm (cho người)
Giải thích bài toán, hai vùng A/B, trigger còi, bố trí camera, enrollment, linh kiện
và cách nhóm phối hợp. Không phải bản spec kỹ thuật; không là nguồn truth cho coding.
**Đọc một lần** khi mới vào dự án hoặc cần giải thích sản phẩm bằng ngôn ngữ thường.

### `docs/SPEC.md` — Hành vi (nguồn truth cho code)
FR01–FR24 + EX01: sampling/freshness/revision, geometry/direction/visit, identity/
enrollment/gallery, event/suppression/command, storage/auth/backup, health/config.
Mục 9 có các quy tắc bắt buộc bổ sung sau review v2.1.
**Đọc mục liên quan** khi implement hoặc sửa bất kỳ module nghiệp vụ nào.

### `docs/CONTRACTS.md` — Wire semantics (nguồn truth cho protocol)
Auth/limits/lỗi, device hello/sync/ACK, upload/retry, Observation boundary, Admin
API, compatibility/fixtures. Mục "Làm rõ v2.1" có các clarification quan trọng.
**Bắt buộc đọc** khi thay đổi liên quan firmware ↔ backend hoặc dashboard ↔ backend.

### `docs/ARCHITECTURE.md` — Ranh giới module (bản đồ code)
Quyết định giữ code, bản đồ thư mục với owner, dòng dữ liệu (flowchart), bất biến
khi thêm tính năng, ownership theo người (A/B/C), thứ tự đọc code để tự review,
cách dùng agent với Codex.
**Đọc mục 2/6** trước khi mở code; đọc mục 5 khi cần biết ai review PR.

### `docs/PLAN.md` — Kế hoạch và backlog
Chia trách nhiệm (A/B/C), gate G0–G5 với điều kiện PASS, backlog BASE/FW/CV/BE/INT/
DEMO, quy trình Git, cách dùng agent có kiểm soát, đầu ra cần nộp.
**Đọc trước khi nhận task mới** để chắc issue nào đang open và gate nào đang cần.

### `docs/VALIDATION.md` — Kiểm chứng và demo
Chỉ tiêu nghiệm thu (bảng G4), cách thu dữ liệu đúng cách, calibration/freeze
profile, HIL H01–H15, script demo 5–7 phút, bằng chứng gói bàn giao (mục 6),
regression mới phải giữ (6.1), giới hạn công cụ (6.2), giới hạn kỹ thuật đã biết (7).
**Bắt buộc đọc** khi làm G2/G3/G4, HIL, calibration hoặc chuẩn bị demo.

### `docs/RESEARCH.md` — Nguồn kỹ thuật
Các nguồn R01–R11 (esp32-camera, GPIO, PIR, OpenCV Zoo, SFace, Arduino CLI, FastAPI,
SQLite, Telegram Bot), lý do giữ kiến trúc, artifacts/license đã ghim, đối chiếu
bổ sung khi review.
**Chỉ đọc** khi cần xác minh một quyết định kỹ thuật, model hay dependency cụ thể.

---

## Bốn luồng chính cần hiểu trước khi chia việc

Nếu thời gian ít, ưu tiên hiểu bốn luồng này — đủ để bắt đầu chia task và review PR:

1. **Frame vào worker** — `service.accept` → `worker.run` → `service._publish`;
   theo một packet và chỉ ra các điểm kiểm stale/revision/lease/transaction.
2. **A → B tạo event** — `contracts.Observation` → `spatial.zone_for` → `policy.ingest`;
   giải thích được vì sao A → B mới có incoming, và khi nào tạo alarm vs warning.
3. **Event → command → ACK** — `service._issue` → sync response → `AlarmLogic.h`;
   giải thích event đỏ chưa chứng minh còi kêu, duplicate không kéo deadline.
4. **Enrollment draft → activate** — `enrollment.py`: draft → round1 → round2 →
   commit draft → activate; thử cancel/timeout/đổi round, đọc `test_worker_boundaries.py`.

---

## Không có trong docs/ — tìm ở đâu

| Thứ cần tìm | Vị trí |
|---|---|
| Default values (threshold, cadence, timeout…) | `configs/default.yaml` |
| Schema DB, bảng, FK | `backend/security_app/migrations/001_initial.sql` |
| Observation/Packet type | `backend/security_app/contracts.py`, `runtime.py` |
| Evidence software v2.1 | `experiments/HANDOFF.md`, `handoff-checks.json` |
| Calibration profile template | `configs/calibration.seed.json` |
| Model artifacts và hash | `models/manifest.json` |
| Lệnh linter/test/compile | `AGENTS.md` mục "Lệnh chuẩn" |
| Checkpoint task đang làm | `.codex/TASK.md` (tạo tại chỗ, ignored Git) |

## Mở rộng staging Level 3 (20/10/2026)

- **`docs/LEVEL3_UPGRADE.md`**: tài liệu bổ sung về level theo PDF môn học, kiến trúc cloud, PWA, Telegram/cloud.
- **`docs/LEVEL3_SETUP_GUIDE.md`**: tài liệu hướng dẫn triển khai, cài đặt và vận hành hệ thống thực tế trên Oracle Cloud VM và ESP32.
- **`docs/UPGRADE_AUDIT.md`**: mã đã thay, invariants được giữ, blocker và lệnh test.
- Các tệp này **không thay thế** baseline SPEC/CONTRACTS; phần tính năng mới là opt-in cho đến nghiệm thu.
