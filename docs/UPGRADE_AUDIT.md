# Audit bản nâng cấp theo mã và risk

**Tính chất:** review ở mức source/module/contracts/tests; không tuyên bố đã hiểu "từng dòng" của `.git`, binary pretrained models hoặc dữ liệu private trong ZIP.

## Đã đọc/đối chiếu

- `AGENTS.md`, `docs/INDEX.md`, `README.md`, `docs/{SPEC,CONTRACTS,ARCHITECTURE,TEAM_GUIDE,VALIDATION,PLAN,RESEARCH,LEVEL3_UPGRADE}.md` dưới góc độ ràng buộc code/requirements.
- Firmware `security_node.ino`, `AlarmLogic.h`, `FrameFreshness.h`, `NodeState.h`; hiểu các đường camera→sync→command→buzzer.
- Backend routes/auth/contracts/service/policy/worker/vision/matching/tracking/spatial/storage/migrations/notifications; static dashboard và client JS.
- Config/default/hardware, tests, simulator, model manifest, tool quản lý và scripts.
- ZIP ban đầu chứa `.git`, cache/binary/SQLite và private runtime; **bỏ ra khỏi gói mới**. Không chuyển token, ảnh hoặc DB thật sang cloud.

## Invariants giữ lại

1. Tất cả lệnh còi đều đi qua session/revision/generation, STOP/hard cap, không cho cloud gọi GPIO.
2. Chỉ kết luận `UNKNOWN` khi có **quality hợp lệ + face embedding + policy vote + geometry**. Mất ảnh phải là `UNCERTAIN` hoặc không có quan sát.
3. Chỉ publish command sau transaction commit.
4. Chỉ một worker AI chịu trách nhiệm order và frame freshness; cloud inference không xử lý kết quả cũ.
5. Mọi HTML/API sự kiện, media, MJPEG dùng session auth; PWA không lưu dữ liệu private offline.
6. Env cloud/token, cloud biometric consent opt-in; giữ chế độ mặc định local để bộ test cũ chạy.
7. Không đổi giới hạn `max_capture_age_ms=1000`, `max_result_age_ms=3000`, timeout điều khiển, sampling ESP32 nếu chưa benchmark.

## Rủi ro/tồn đọng mức ưu tiên

- **BLOCKER Level 3**: chưa có cloud host thực, domain HTTPS, storage persistent, Cloud OpenCV weights, HIL đo RTT. Phần code chỉ là đường tích hợp có test unit.
- **BLOCKER Night**: chưa biết chính xác revision ESP32-CAM/IR filter; chưa chốt đèn và nguồn. Firmware mới chỉ set auto exposure/gain/AWB, không điều khiển đèn ngoài.
- **BLOCKER Accuracy**: validation/calibration seeds không phải đo thực. Chưa có bộ ảnh daylight/night/occlusion và báo cáo threshold/false alarm.
- **Risk Remote privacy**: gửi JPEG, embeddings qua cloud cần consent và kiểm soát hạ tầng. Nếu không đồng ý, giữ inference local; khi đó không nhận đầy đủ tiêu chí Level 3.
- **Risk Performance**: kết nối cloud roundtrip phải hoàn tất trong cửa sổ phân tích; JSON/base64 payload lớn; nếu không đạt thì phải thay transport/model/host chứ không tăng TTL vô căn cứ.
- **Risk Cloud retention**: không có admin purge/export cloud events; staging SQLite chỉ dùng cho demo, muốn triển khai dài hạn phải hoàn thiện retention và backup cloud.
- **Risk Stream load**: MJPEG từ processed latest frame; hữu hạn 3 clients và 30s connection. Chưa test với nhiều điện thoại/4G thực.
- **Risk Firmware**: Arduino CLI không khả dụng ở môi trường audit; firmware **chưa compile trên core pinned**.

## Lệnh kiểm chứng

```bash
python -m pytest -q
python -m compileall -q backend tools tests
node --check backend/security_app/static/app.js
node --check backend/security_app/static/sw.js
# Cần ruff trong virtualenv Python 3.12:
python -m ruff check backend tools tests
python -m ruff format --check backend tools tests
# Cần Arduino CLI + ESP32 core:
arduino-cli compile --profile ai_thinker firmware/security_node
```

`python -m tools.check` gọi ruff và các test; nếu thiếu ruff sẽ thất bại, **không diễn giải là test regression fail**. Camera/Cloud không thể nghiệm thu chỉ bằng pytest.
