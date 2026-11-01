# Smart Security System — giám sát vùng cửa

**Hiện trạng 01/11/2026:** ESP32-CAM kết nối Oracle; backend, NanoDet/YuNet/SFace,
gallery và lịch sử chạy trên VM. Laptop/điện thoại mở dashboard. Luồng lượt đến
cửa ngắn đã triển khai lên Oracle lúc 22:27 (giờ Việt Nam), giữ model/ngưỡng cũ.
Gallery đang rỗng; calibration cũ cần duyệt lại nên chưa bật giám sát. Xem
[chuẩn bị demo](docs/DEMO_RUNBOOK.md) và [kết quả kiểm chứng](docs/RECOGNITION_UPGRADE.md).
Software pass không thay nghiệm thu accuracy, độ trễ hoặc còi thật.

**Ngày review:** 02/10/2026 · **Nhóm:** 3 người · **Deadline:** khoảng **tháng 11/2026**, chờ ngày nộp chính thức.

Giám sát **người tiến từ vùng ngoài vào vùng gần cửa** bằng ESP32-CAM + PIR.
Cấu hình mặc định repository là simulation; cấu hình triển khai hardware nằm
ngoài Git. Trên Oracle, `inference=local` nghĩa là chạy AI ngay trong VM; adapter
cloud từ xa vẫn là tùy chọn riêng. Server quyết định lệnh, ESP32 giữ ACK/STOP và
hard timeout của còi. Người đi ngang hoặc đứng xa không tự tạo cảnh báo danh tính.

Đã có camera lắp thực tế và pilot riêng được đồng ý; các dữ liệu đó không nằm
trong Git. Holdout độc lập và HIL của luồng mới còn thiếu. Các tài liệu staging
ngày 20/10 lưu lại thiết kế ban đầu; không dùng phần mô tả laptop inference trong
đó để giải thích topology Oracle hiện tại.

**Bắt đầu review bản này:** đọc [UPGRADE_AUDIT](docs/UPGRADE_AUDIT.md) để biết thay đổi/rủi ro,
rồi [ARCHITECTURE](docs/ARCHITECTURE.md) để nắm bộ khung và cách chia việc.
Giữ baseline runnable; không xóa toàn bộ. Hardware/AI thực vẫn là việc của nhóm.

## Đọc đúng phần cần dùng

| Bạn muốn | Đọc |
|---|---|
| Hiểu hệ thống, vị trí camera, đăng ký, còi và linh kiện | [TEAM_GUIDE.md](docs/TEAM_GUIDE.md) — bản cho cả nhóm |
| Coding đúng hành vi | [SPEC.md](docs/SPEC.md) — quy tắc triển khai |
| Ghép firmware/backend hoặc thay API | [CONTRACTS.md](docs/CONTRACTS.md) + `contracts.py` |
| Hiểu ranh giới code và cách thay một module | [ARCHITECTURE.md](docs/ARCHITECTURE.md) |
| Chia việc, dùng Git/agent, lịch đến tháng 11 | [PLAN.md](docs/PLAN.md) |
| Thu dữ liệu, hiệu chỉnh, kiểm thử, demo | [VALIDATION.md](docs/VALIDATION.md) |
| Chuẩn bị demo tại trường, vận hành và xử lý lỗi | [DEMO_RUNBOOK.md](docs/DEMO_RUNBOOK.md) |
| Kiểm tra lý do và nguồn kỹ thuật | [RESEARCH.md](docs/RESEARCH.md) |

**Agent đọc `AGENTS.md` trước, rồi chỉ đọc mục liên quan.** Không cần nạp cả bộ tài liệu ở mỗi task. `TEAM_GUIDE` giải thích cho người; `SPEC` sở hữu hành vi; `CONTRACTS` sở hữu protocol; `configs/default.yaml` sở hữu số mặc định. Khi đổi yêu cầu, sửa phần sở hữu và phần giải thích bị ảnh hưởng trong cùng PR.

## Chạy thử không cần board

Mở terminal **tại thư mục có README này**. Internet cần cho lần cài package/model đầu; core chạy trong LAN sau đó.

Windows PowerShell, Python 3.12:

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements-dev.txt
.\.venv\Scripts\python.exe -m pip install -e .
.\.venv\Scripts\python.exe -m tools.manage bootstrap
.\.venv\Scripts\python.exe -m tools.manage seed-simulation
.\.venv\Scripts\python.exe -m uvicorn security_app.main:app --host 127.0.0.1 --port 8000 --workers 1
```

Linux:

```bash
python3.12 -m venv .venv
.venv/bin/python -m pip install -r requirements-dev.txt
.venv/bin/python -m pip install -e .
.venv/bin/python -m tools.manage bootstrap
.venv/bin/python -m tools.manage seed-simulation
.venv/bin/python -m uvicorn security_app.main:app --host 127.0.0.1 --port 8000 --workers 1
```

Bootstrap hỏi mật khẩu admin; không có mật khẩu mặc định. Token thiết bị được ghi trong file private dưới `runtime/simulation/`, không in token ra log. `seed-simulation` chỉ tạo vector giả để test luồng, không phải đăng ký mặt thật. Chỉ chạy một lần trên runtime simulation mới.

1. Mở [dashboard local](http://127.0.0.1:8000), đăng nhập bằng tài khoản vừa tạo.
2. Terminal thứ hai: chạy simulator trước, rồi bấm **Bật giám sát** trên dashboard:

```powershell
.\.venv\Scripts\python.exe -m tools.device_simulator --scenario unknown --seconds 120
```

Linux dùng `.venv/bin/python` thay executable Windows. Còi ở đây là trạng thái giả lập `ON/OFF`, máy không phát âm thanh. Có thể đổi scenario: `passerby`, `far`, `outgoing`, `direct-b`, `unidentified`, `empty`. Với `known` hoặc `mixed`, thêm `--person-id UUID` của người active trong tab Người quen.

Sau khi server restart, khởi động lại simulator rồi arm chủ động. Không dùng `--reload`/nhiều workers trong demo: mỗi restart làm mất phiên thiết bị và tắt giám sát.

## Kiểm tra code

Lệnh gộp: `python -m tools.check` (Python của venv); thêm `--http` để kiểm
server/simulator bằng runtime tạm. Node/g++ thiếu sẽ ghi NOT RUN, không giả PASS.
Full ESP32 compile và HIL vẫn là gate riêng.


```powershell
.\.venv\Scripts\python.exe -m pytest -q
.\.venv\Scripts\python.exe -m ruff check backend tools tests
.\.venv\Scripts\python.exe -m ruff format --check backend tools tests
```

Test Python không thay HIL hoặc holdout AI. Kiểm tra logic còi C++ nếu có compiler:

```bash
g++ -std=c++17 -Wall -Wextra -Werror tests/firmware/alarm_logic_test.cpp -o /tmp/sss-alarm-test
/tmp/sss-alarm-test
g++ -std=c++17 -Wall -Wextra -Werror tests/firmware/frame_freshness_test.cpp -o /tmp/sss-frame-age-test
/tmp/sss-frame-age-test
```

Kiểm tra HTTP qua server và simulator thật, dùng runtime simulation tạm riêng, không chạm board/dữ liệu đang dùng:

```bash
python -m tools.smoke_integration
```

Chạy bằng Python trong venv, khoảng 15–20 giây. Test ba ca unknown/direct-b/known, kiểm ACK và còi giả tự OFF.

## Chuyển sang camera thật

1. Đọc phần bố trí/lắp trong TEAM_GUIDE; lắp đúng **AI-Thinker ESP32-CAM**, không SD.
2. Copy `configs/hardware.example.yaml` → `configs/local.yaml`, chỉnh vùng A/B theo ảnh camera thực. Profile hardware dùng runtime riêng, không dùng người/vector giả.
3. Chạy bootstrap cho hardware và tải model:

```powershell
.\.venv\Scripts\python.exe -m tools.manage bootstrap
.\.venv\Scripts\python.exe -m tools.download_models
.\.venv\Scripts\python.exe -m tools.manage models-smoke
```

Weights không đóng trong ZIP; tool tải đúng commit và kiểm SHA256, kèm license. Không tự tải weight mỗi lần server chạy.

4. Copy `firmware/security_node/secrets.example.h` → `secrets.h`; điền Wi-Fi 2,4 GHz, IP LAN laptop, device token private, active level buzzer đúng module. Firmware chỉ nhận cấu hình board đúng loại này.
5. Cài Arduino CLI **1.2.2**, profile ghim core **3.3.0**, ArduinoJson **7.4.1**:

```bash
arduino-cli compile --profile ai_thinker firmware/security_node
arduino-cli upload --profile ai_thinker --port COM5 firmware/security_node
```

Thay `COM5` bằng cổng thực (`/dev/ttyUSB0` trên Linux nếu phù hợp). Profile hỗ trợ tải core/library; xem RESEARCH khi cần cài Boards Manager bằng IDE. Compile phải pass trước nạp; HIL phải pass trước demo. Không tắt brownout để che lỗi nguồn.

6. Chạy server với `--host 0.0.0.0 --workers 1` khi board/điện thoại cần truy cập LAN; mở đúng cổng 8000 trên private firewall. IP trong firmware là IP laptop, **không phải localhost**.
7. Preview → cố định camera/vùng → thu enrollment hai lượt → lưu mẫu draft → thu validation → reviewer chốt calibration → restart server → kiểm tra người mới → chủ động kích hoạt người → arm. **Seed calibration chặn arm hardware.** Quy trình và mẫu profile ở VALIDATION; không đổi `state` thành `validated` chỉ để bỏ lỗi.

## Bản đồ mã

| Vị trí | Công dụng / owner chính |
|---|---|
| `firmware/security_node/` | Camera, PIR, upload, sync và còi độc lập — A |
| `backend/security_app/vision.py`, `tracking.py`, `matching.py`, `spatial.py` | Model/quality/tracker/so khớp/vùng ảnh — B |
| `backend/security_app/policy.py` | Chiều tiếp cận, lượt ghé, xác nhận và quyết định cảnh báo — C + B review |
| `backend/security_app/service.py` | Điều phối state/revision/session, event/command — C |
| `worker.py`, `enrollment.py`, `ports.py`, `runtime.py` | Worker AI, capture/gallery, interface và packet — B/C |
| `main.py`, `auth.py`, `contracts.py` | API/auth/protocol — C + A review |
| `storage.py`, `migrations/`, `notifications.py` | SQLite, dữ liệu và outbox tùy chọn — C |
| `templates/`, `static/` | Dashboard cùng server, PWA shell và MJPEG xem ảnh gần trực tiếp — C |
| `cloud_remote.py`, `cloud_bridge.py`, `cloud_service.py` | Cloud inference tùy chọn, đồng bộ metadata, cloud analytics độc lập — B/C |
| `tools/` | Setup, simulator, model download, evaluation, backup/restore |
| `configs/`, `models/manifest.json` | Default, hồ sơ camera/calibration, exact weights |
| `tests/`, `experiments/` | Test tự động, HIL checklist, báo cáo có bằng chứng |

Ranh giới module và các invariant nằm trong ARCHITECTURE. Không đưa lại enrollment/inference vào service.py khi thêm tính năng. Chưa cần microservice, broker, vector DB hay framework frontend khác.

## Giới hạn vận hành

Mục tiêu tối đa 20 người active, hai người đồng thời, camera cố định. Có kiểm soát chất lượng ảnh quá tối/quá sáng và tự động cân bằng phơi sáng trên OV2640; **chưa chứng minh nhận diện ban đêm** trước khi thử thực tế với nguồn sáng bổ trợ phù hợp. Số đạt thật phải lấy từ calibration/report, không lấy từ giới hạn cấu hình. Laptop cần chạy và không sleep. Không có chống giả mạo mặt đáng tin cậy, mở khóa cửa hay nhận diện khi ống kính bị che nghiêm trọng. MJPEG chỉ phát khung hình backend vừa nhận, không phải video 25–30 FPS và chỉ có ảnh khi node đang gửi frame.

Ảnh/embedding/token/DB/backup không vào Git. Telegram có thể gửi text + ảnh và nút mở dashboard (địa chỉ HTTPS riêng), là extension tắt mặc định; nhóm cần tự cấp bot/chat ID và test thực tế. PWA cần localhost hoặc HTTPS. Cloud analytics chỉ lưu metadata sự kiện, và cloud inference đòi hỏi bật riêng sau khi có quyền xử lý dữ liệu khuôn mặt; sự cố Internet không được tạo ra kết luận nhận diện giả. Việc xóa người purge toàn ảnh evidence vì ảnh có thể chứa nhiều người; nhóm phải xóa các backup cũ đã tạo riêng.

## Bước nhóm cần làm trước

Chọn A/B/C và máy server → mỗi người chạy simulator → A bring-up board/nguồn → cả nhóm thử đường đi A/B với người thật → B đo chất lượng/hiệu chỉnh → C ghép và lưu evidence. Theo gate trong PLAN; không giao agent viết lại toàn bộ repo từ đầu.
