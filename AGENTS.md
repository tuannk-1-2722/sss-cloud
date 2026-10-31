# Smart Security System — hướng dẫn agent

BTL IoT nhóm 3 người, deadline khoảng **11/2026**. Baseline v2.1 đã review software; hardware/AI thực chưa nghiệm thu. Kiểm tra hiện trạng trước sửa.

## Đọc có chọn lọc

- Ranh giới module/ownership: `docs/ARCHITECTURE.md`.
- Chỉ mục và định hướng tài liệu: `docs/INDEX.md`.
- Hành vi: `docs/SPEC.md`, chỉ các mục liên quan task.
- Giao tiếp: `docs/CONTRACTS.md` + `backend/security_app/contracts.py`.
- Số mặc định: `configs/default.yaml`; seed không phải kết quả đã đo.
- Việc tiếp theo, owner và gate: `docs/PLAN.md`.
- Kiểm chứng: `docs/VALIDATION.md`; giải thích cho người: `docs/TEAM_GUIDE.md`.
- Chỉ đọc `docs/RESEARCH.md` khi kiểm tra quyết định/model/nguồn. Không nạp cả repo docs mỗi lượt.

## Làm việc

1. Giao một issue/gate; xem code và test hiện có, giữ thay đổi trong scope.
2. Contract đổi: cập nhật schema, firmware, simulator, spec và test tương ứng cùng PR; không merge hai phía lệch.
3. Chạy kiểm tra đúng rủi ro. Không chạy toàn bộ HIL/AI holdout cho sửa văn bản/giao diện đơn giản.
4. Không tự đổi board/model/framework/policy còi, giảm mục tiêu test hoặc tune theo holdout. Ghi quyết định nhóm trước thay baseline.
5. Phân biệt simulation/hardware; không đưa vector giả vào profile hardware hoặc báo simulator là độ chính xác AI.
6. Không lưu/gửi ảnh thật, embedding, token, DB/backup lên Git/cloud theo mặc định.
7. Giữ một process/một AI worker, pending một slot; check revision/lease sau inference, command chỉ publish sau COMMIT; STOP và hard timeout không chờ upload.
8. Không tự mở Internet, gửi thông báo thật hoặc mua đồ. Setup/bug fix/config validation trong task được giao cứ thực hiện.
9. Khi ngắt phiên: ghi `.codex/TASK.md` ngắn với issue/branch, đã làm, test, blocker, bước kế; ignore file này trong Git.
10. Kết thúc: đã đổi gì, test pass/fail, phần chưa đo và bước tiếp theo. Không in lại spec; không lặp thông tin không đổi.

Lệnh chuẩn: `python -m pytest -q`, `python -m ruff check backend tools tests`, `python -m ruff format --check backend tools tests`. Firmware: `arduino-cli compile --profile ai_thinker firmware/security_node`. Dùng interpreter venv của repository.


Bộ khung: service sở hữu state/lock; enrollment sở hữu capture/gallery workflow;
worker sở hữu model thread; vision/tracking/matching/policy không HTTP/DB/GPIO.
Không nhập logic này lại vào main.py/service.py. Với bug, tạo ca tái hiện đúng race
hoặc boundary rồi sửa; fixture synthetic không được báo thành accuracy/HIL.
Lệnh gộp khi review nhiều module: `python -m tools.check --http`.

## Nhánh nâng cấp 20/10/2026

Trước khi sửa cloud/PWA, đọc `docs/LEVEL3_UPGRADE.md`, `docs/UPGRADE_AUDIT.md`.
Mode cloud mặc định tắt. Không bật cloud biometric hoặc gửi token/dữ liệu thật nếu không có quyền.
Mọi thay đổi cloud/firmware phải có test fail-closed và HIL; Level 3 đòi hỏi demo cloud triển khai thật.
