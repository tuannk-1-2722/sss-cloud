# Đánh giá cấp độ IoT và thiết kế nâng cấp — bản staging 20/10/2026

**Cập nhật topology 01/11:** bản triển khai hiện tại dùng ESP32 → Oracle, backend,
AI và DB cùng trên VM; laptop chỉ mở web. Các kiến trúc laptop/gateway phía dưới
là thiết kế staging ban đầu. Xem [RECOGNITION_UPGRADE](RECOGNITION_UPGRADE.md) và
[DEMO_RUNBOOK](DEMO_RUNBOOK.md) cho hiện trạng và các gate chưa nghiệm thu.

**Địa vị:** đề xuất kỹ thuật + code staging, **không phải báo cáo nghiệm thu Level 3**, không phải kết quả HIL. Các ngưỡng môi trường và chất lượng AI còn phải hiệu chỉnh với ESP32-CAM/OV2640 thật. Nguyên tắc: giữ core v2.1 đang chạy, chỉ bật cloud khi đáp ứng điều kiện triển khai và đồng ý xử lý dữ liệu sinh trắc.

## 1. Nguồn phân cấp chính: PDF môn học

Nguồn ưu tiên là `Thiết kế hệ thống IoT-trang-2.pdf` do nhóm cung cấp, trang trình chiếu 41–53 (13 trang PDF). Đặc biệt cần **đọc sơ đồ**, không gán level chỉ bằng số lượng tính năng.

| Level | Nút đo/cảm nhận | Nơi lưu trữ | Nơi phân tích | Điều kiện phân biệt | Đối chiếu PDF |
|---|---|---|---|---|---|
| 1 | Một | Local | Local | Hệ khép kín, không có cloud cho dữ liệu/phân tích | trang trình chiếu 45 |
| 2 | Một | Cloud | Local | Cloud chủ yếu lưu trữ, phân tích tại nút/local | 46 |
| **3** | **Một** | **Cloud** | **Cloud** | **Ứng dụng và phân tích trên cloud**, có thể là tính toán lớn | **47** |
| 4 | Nhiều | Cloud | Cả local và cloud | Nhiều nút độc lập, phần analytics trên cloud | 48 |
| 5 | Nhiều nút đầu cuối | Cloud | Cloud/Local | Nút coordinator/gateway thu thập các endpoint | 49–50 |
| 6 | Nhiều nút độc lập | Cloud | Cloud | Bộ điều khiển tập trung biết trạng thái và điều khiển ngược | 51–52 |

Trang 53 tóm tắt toàn bộ 6 level; diễn giải ở trên cố ý giữ đúng tiêu chí **topology và nơi xử lý**. Các chức năng Telegram, AI, PWA, HTTPS hay camera ban đêm **không tự mang lại Level 3**. Máy laptop local không biến thành cloud khi dùng Tailscale hay khi kết nối Internet. Có hai server khác nhau (ESP32 + laptop) không nhất thiết tương đương hai *monitoring nodes*: ESP32 là sensor node; laptop là gateway/coordinator ứng dụng. Tuy nhiên giảng viên có thể yêu cầu chứng minh ranh giới này; nhóm cần đối chiếu với thầy.

## 2. Baseline thực tế đọc từ ZIP

- `firmware/security_node/security_node.ino`: AI-Thinker ESP32-CAM, OV2640, PIR GPIO13, còi GPIO14, JPEG VGA, `fb_count=1`, upload HTTP trong LAN. Có heartbeat/sync, ACK, watchdog cho còi. **Chưa điều khiển đèn đêm**, chưa có mã cloud.
- `backend/security_app/vision.py`: NanoDet → YuNet → quality khuôn mặt → SFace → matching/tracking; AI chạy trong worker trên laptop. `models/manifest.json` ghim weight SHA256, weights không kèm ZIP.
- `backend/security_app/service.py`, `policy.py`: state/mode/lease, tiếp cận vùng A→B, `KNOWN`/`UNKNOWN`/`UNCERTAIN`, xác nhận nhiều frame, lưu event và lệnh còi sau COMMIT.
- `storage.py`, `migrations/001_initial.sql`: SQLite local, vector khuôn mặt local, media và outbox.
- `notifications.py`: đã hỗ trợ ảnh Telegram và text, **mặc định tắt**. Nhận định trước đây trong bản phân tích cũ rằng hiện chỉ là text chưa hoàn toàn chính xác với mã hiện tại.
- `main.py`, `templates/`, `static/`: dashboard responsive, ảnh JPEG mới nhất, không có MJPEG live/PWA hoàn chỉnh trong bản nhận.
- `tests/`: baseline 84 test software pass trước thay đổi. Trong ZIP có runtime/simulation, token, credential, SQLite và `.git`/cache; bản bàn giao mới **không đóng gói** những tệp đó.

**Kết luận baseline:** gần **Level 1**, có thể là giải pháp IoT local mạnh nhưng chưa đáp ứng "lưu trữ và phân tích trên cloud" của Level 3. Nếu chỉ bật Telegram/Tailscale/PWA/night, vẫn gần Level 1. Nếu mirror event vào cloud nhưng phân tích nhận diện trên laptop, chỉ có đặc điểm Level 2/hybrid. Nếu cloud trực tiếp phân tích JPEG, lưu event và cung cấp app analytics độc lập, có cơ sở để bảo vệ Level 3 — **sau khi triển khai thật, đo và trình diễn**.

## 3. Hai kiến trúc và nguyên tắc lựa chọn

**A — Local demo đáng tin cậy (mặc định):**

```text
ESP32-CAM + PIR → LAN → Laptop FastAPI → local NanoDet/YuNet/SFace
                                    → Policy + SQLite local + buzzer command
                                    → Telegram photo + HTTPS View Live link
                                    → PWA + MJPEG via Tailscale Serve (private)
```

Ưu: chịu được mất Internet (ngoại trừ Telegram/remote); phần cứng ít đổi; dữ liệu mặt ở local; dễ đạt demo cơ bản. Nhược: **không phải Level 3** theo PDF, live thực tế chỉ ~1–2 FPS theo thông số sampling cấu hình, inference chậm sẽ kéo giảm.

**B — Cloud inference + analytics, dùng khi bảo vệ Level 3:**

```text
ESP32-CAM (1 monitoring node)
      ↓ JPEG + PIR/control telemetry (LAN)
Laptop gateway (local auth + buffer + safety/command + SQLite)
      ↓ HTTPS authenticated, explicit consent
Cloud FastAPI + OpenCV models (NanoDet/YuNet/SFace)
      ↓ observations/quality ← return to gateway policy
      ├── cloud SQLite event metadata (MOUNT persistent volume)
      └── cloud web dashboard: count by severity/reason/day
Gateway → Telegram photo/link & local PWA MJPEG
```

Trong chế độ B, inference JPEG và nhận diện thuộc cloud. Gateway giữ **actuation và fail-safe**; cloud không điều khiển GPIO trực tiếp. Điều này tránh tình trạng mạng chậm khiến còi giữ ON vô hạn. Độ trễ internet/inference phải thỏa `max_result_age_ms=3000`; quá thời gian thì **bỏ kết quả**, không diễn giải là `UNKNOWN`. Cloud bị lỗi thì hệ thống báo lỗi, fail closed chứ không âm thầm chuyển sang chế độ khác.

**Chi phí/rủi ro chính:** triển khai cloud có OpenCV và weights đủ bộ, đủ CPU/RAM và filesystem bền vững; đảm bảo HTTPS, kiểm thử upload ảnh/biometric consent, sự ổn định kết nối, dữ liệu cá nhân. Free web service có thể sleep và ổ đĩa tạm, không tự động là demo 24/7 hoặc lưu trữ bền vững. Ưu tiên thử cloud trên server có persistent disk phù hợp hoặc VM/VPS được nhóm kiểm soát, không tự công khai camera.

### Vì sao không nhắm Level 4/5/6 ngay?

- Level 4 yêu cầu **nhiều monitoring node thực**, không phải thêm "app node" hay giả lập data.
- Level 5 cần **nhiều endpoint + coordinator** và topology hoạt động được; thêm một ESP32 giả lên diagram không đủ.
- Level 6 là mô hình nhiều node độc lập, controller tập trung và phân tích cloud, cách xa quy mô nhóm 3 người/1 camera/deadline tháng 11.
- Level 3, triển khai thật và có log, có khả năng giải thích tốt hơn lựa chọn "Level 5 cho đẹp".

## 4. Các lựa chọn sản phẩm đã chốt

| Nhu cầu | Hướng áp dụng | Giới hạn trung thực |
|---|---|---|
| Ban ngày, thiếu sáng, ban đêm | OV2640 tự điều chỉnh exposure/gain/AWB + **illumination hỗ trợ** + lấy thêm ảnh đăng ký trong ánh sáng thực nghiệm | Firmware không tạo thêm ánh sáng; **chưa có đèn để HIL**; cần chọn đèn trắng hoặc IR tương thích camera |
| Mưa lớn, sương mù, nước che lens | Cảnh báo `SCENE_TOO_DARK`/`SCENE_EXPOSURE_FAILURE`/blur/face quality thấp; chuyển `UNCERTAIN`, không dán nhãn người lạ nếu không đủ chứng cứ | Thuật toán thống kê pixel **không phát hiện được tất cả mưa/sương/lens ướt**; không cam kết nhận diện dưới mọi thời tiết |
| Telegram | Văn bản chứa lý do/thời điểm/event id + ảnh JPEG nếu có; nút "Xem camera trực tiếp / lịch sử" | Cần bot token/chat id, public user không thể mở private link nếu chưa tham gia tailnet, cần đăng nhập PWA |
| Truy cập điện thoại | PWA dùng HTTPS qua **Tailscale Serve**, không dùng Funnel/public exposure | Smartphone cần cài và đăng nhập Tailscale vào tailnet đúng quyền; laptop phải online |
| Xem trực tiếp | MJPEG latest processed-frame dùng chung source, tối đa 3 người xem; stream được auth mỗi 30s | 1–2 FPS cấu hình, **không phải video mượt**; stream không cung cấp lưu video |
| Level 3 | Mode cloud inference + cloud metadata analytics + cloud web app | Cần máy chủ thật, HTTPS, quyền xử lý khuôn mặt, đo SLA và demo |

### Kiểm tra chất lượng ảnh

- `environment_quality()` đo median luma, p95 luma, tỉ lệ clipped shadow/highlight trên ảnh downsample. Nếu scene không khả dụng, nhận diện ở vùng B chuyển **`UNCERTAIN`**. Cùng lúc, tiêu chí face blur/size/exposure hiện có vẫn hoạt động.
- Các giá trị `configs/default.yaml/environment` là **ngưỡng dự thảo**, không phải chứng nhận ngày/đêm. Khi không phát hiện được người vì tối, hệ thống có thể không sinh incident; người dùng phải nhìn **health status** ảnh suy giảm. Muốn tự tạo event `camera_degraded` cần thêm quy tắc debounce/retention, chưa triển khai.
- Không dùng đồng hồ 18:00/6:00 làm nhận biết ban đêm. Khi lắp đèn trắng nên ưu tiên ánh sáng đều, không chiếu chói thẳng vào lens; thử ngược sáng. IR chỉ sau khi xác minh sensor/cấu hình lens phù hợp.
- Tránh thao tác GPIO điều khiển đèn công suất bằng cách đoán chân từ link sản phẩm; phải xác minh board thật, nguồn, driver/MOSFET/relay, GPIO boot strapping, rồi mới tạo driver firmware riêng.

### Quyền riêng tư và an toàn

- Dữ liệu biometric JPEG/vectors gửi tới cloud **chỉ khi** `SSS_CLOUD_BIOMETRIC_CONSENT=1` do operator bật sau khi người đăng ký hiểu nơi xử lý; cấm bật mặc định. HTTPS phải kiểm chứng certificate, token >=32 ký tự và cấp riêng cho cloud.
- Cloud demo **không lưu ảnh hay vector khuôn mặt**, chỉ xử lý trong request và mirror `event_id`, loại sự kiện, severity, thời gian, `device_id`, `source`. Trên local ảnh evidence vẫn theo retention hiện tại.
- Không sử dụng Telegram group công khai. Không đặt credential vào Git, `.env` mẫu phải dùng placeholder. Không mở `/api/v2/live.mjpg` công khai Internet; cookie session và Tailscale cùng bảo vệ.
- PWA service worker cache chỉ manifest/icon/JS/CSS; **không cache API, trang nội bộ, JPEG, embedding, cookie**.

## 5. Những gì đã implement ở bản staging

| Mảng | File | Mô tả |
|---|---|---|
| Sáng/tối | `vision.py`, `worker.py`, `service.py`, `config.py`, `configs/default.yaml` | Scene quality, auto-gating, status, calibration hash đổi để buộc revalidate hardware |
| Auto exposure | `firmware/security_node/security_node.ino` | Bật explicit AEC/AGC/AWB, không thêm wiring đèn khi chưa xác minh chân |
| Live | `main.py`, `templates/dashboard.html`, `static/app.js`, `app.css` | MJPEG authenticated, 3 viewer max, TTL 30s, stop khi logout/ẩn tab |
| Telegram | `notifications.py` | Event reason/time/id, photo + optional inline URL HTTPS |
| PWA | `manifest.webmanifest`, `sw.js`, `icon-*.png`, `main.py`, `dashboard.html` | Installable shell; chỉ cache static shell |
| Cloud inference | `cloud_remote.py`, `cloud_service.py`, `worker.py` | HTTPS bearer, API remote inference và enrollment, không lưu media/embedding |
| Cloud event analytics | `cloud_bridge.py`, `migrations/002_cloud_outbox.sql`, `cloud_service.py`, `service.py` | Durable local queue riêng biệt, upload event metadata idempotent, cloud severity/reason/day app |
| Validation | `tests/test_upgrade.py` | API/PWA/scene-quality/Telegram/cloud authentication và storage smoke (fake model) |

**Không làm giả:** hardware compile, IR/white-light effect, độ chính xác khuôn mặt ban đêm, reachability 4G, Telegram thật, cloud service on Internet, cloud capacity/latency, điện/nhiệt/độ bền enclosure. Các mục này cần nhóm thu minh chứng sau khi lắp.

## 6. Triển khai có kiểm soát

### 6.1 Chạy local trước (bắt buộc)

Theo README hiện tại, setup bằng Python 3.12. Sao lưu runtime cũ và dùng runtime profile riêng. Kiểm tra `pytest`, `ruff`, simulator, HIL board trước khi dùng dữ liệu thật. Lệnh mẫu:

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements-dev.txt
.\.venv\Scripts\python.exe -m pip install -e .
.\.venv\Scripts\python.exe -m pytest -q
.\.venv\Scripts\python.exe -m tools.manage bootstrap
.\.venv\Scripts\python.exe -m uvicorn security_app.main:app --host 127.0.0.1 --port 8000 --workers 1
```

Nếu đã có người/DB thật thì **không `seed-simulation` vào hardware**. Database local tự chạy migration `002_cloud_outbox.sql` khi server mở bằng backup phù hợp.

### 6.2 PWA và liên kết Telegram xem live

1. Cài Tailscale trên laptop và điện thoại, cho cùng tailnet. Đảm bảo laptop kết nối, mở dashboard FastAPI chỉ trên localhost.
2. Dùng `tailscale serve 8000` để tạo **private HTTPS tailnet URL**. Lệnh thực tế xem CLI help phiên bản hiện hành. Không sử dụng `tailscale funnel` cho camera riêng tư.
3. Đặt `SSS_HTTPS=1` trước khi khởi động backend để cookie được gắn Secure khi dùng HTTPS. Nếu kiểm thử **localhost bằng HTTP**, tắt biến này trong phiên local (Secure cookie có thể không gửi qua HTTP trên một số môi trường).
4. Đặt `SSS_DASHBOARD_URL=https://<ten-may>.<tailnet>.ts.net` (**chính xác URL HTTPS do Tailscale cấp**, không đoán). Kiểm tra nhấp từ Telegram trên điện thoại **4G** đã có Tailscale, đăng nhập PWA và kiểm tra luồng ảnh tự mở từ liên kết `?live=1` (nếu chưa có ảnh, cần xác nhận ESP32 đang gửi frame). URL không chứa admin credential/token.
5. Cấu hình `notifications.telegram_enabled: true` trong local YAML, export `SSS_TELEGRAM_TOKEN`, `SSS_TELEGRAM_CHAT`. Không đưa screenshot có token vào báo cáo.
6. Trên Chrome/Edge Android hoặc Safari iPhone, chọn Add to Home Screen sau khi mở URL HTTPS của tailnet. Test cài đặt và sau logout live tự dừng.

### 6.3 Cloud Level 3 (tùy chọn, yêu cầu cloud host thật)

Dùng **hai process/host độc lập**:

**Cloud server**: Cloud host Python 3.12 cài dependencies/model weights đã ghim trong manifest, đặt `SSS_CONFIG` trỏ profile camera **cùng vùng A/B, vision, environment** với gateway; đặt `SSS_CLOUD_API_TOKEN` ngẫu nhiên >=32 ký tự, `SSS_CLOUD_VIEW_USER`, `SSS_CLOUD_VIEW_PASSWORD`, `SSS_CLOUD_DB` trỏ tới filesystem persistent đã gắn. Chạy:

```bash
uvicorn security_app.cloud_service:app --host 0.0.0.0 --port 8000 --workers 1
```

Cloud reverse proxy HTTPS phía trước, **không** trưng API token hay mở cloud inference không xác thực. `GET /health/ready` với bearer phải trả `ready: true`. `GET /` phải yêu cầu Basic authentication và hiển thị dashboard cloud. Cloud cần cài weights bằng `python -m tools.download_models`.

**Gateway laptop**: giữ `SSS_CONFIG` hardware đã hiệu chỉnh; trong YAML:

```yaml
cloud:
  inference: cloud
  inference_timeout_ms: 2500
  events_enabled: true
```

Thiết lập env `SSS_CLOUD_API_URL=https://<cloud-domain-thuc>`, `SSS_CLOUD_API_TOKEN=<same-secret>`, `SSS_CLOUD_BIOMETRIC_CONSENT=1` sau khi có sự đồng ý. **Không dùng `localhost`/HTTP** và **không đổi `profile: simulation` thành hardware chỉ để qua validation**.

Edge chuyển ảnh JPEG và gallery vector sang cloud **trong request** để nhận kết quả; sự kiện sau đó mirror cloud bằng bảng outbox riêng. Khi cloud chậm hoặc mất kết nối, luồng kết quả vượt TTL bị loại và mode bị fail-closed. Đây là hy sinh có chủ đích cho tính trung thực của cảnh báo.

**Hạn chế bắt buộc của staging cloud**: cloud app dùng SQLite, cần volume persistent; chưa tích hợp managed Postgres, multi-tenant hoặc HA. Remote inference JSON/base64 gây overhead đáng kể; nếu RTT quá lâu phải tối ưu transport hoặc chọn cloud gần hơn, không tùy tiện nới các timeout nhận diện/còi. Token/api gateway single-client; thiết kế hiện phù hợp **một camera demo**, không phải SaaS multi-user.

## 7. Minh chứng nghiệm thu và gate đề xuất

| Gate | Điều kiện phải thể hiện trong demo | Trạng thái khi bàn giao |
|---|---|---|
| G0 Software regressions | Toàn bộ test baseline + test upgrade pass, không mất invariants mode/còi | Đạt trong môi trường Python của bản bàn giao |
| G1 Board & network | Firmware compile/nạp, nguồn ổn, PIR/còi, JPEG nhận qua LAN, STOP khi mất control | **Chưa HIL** |
| G2 Real AI | Enrollment người thật có consent, true known/unknown/unidentified theo từng góc/zone, đo precision/false alarms | **Chưa HIL** |
| G3 Day/night | Ánh sáng ngày/đêm + đèn hỗ trợ, thử ngược sáng và che lens; tối/blur không báo `UNKNOWN` thiếu căn cứ | **Chưa HIL**, cần linh kiện |
| G4 Telegram/PWA | Nhận ảnh sự kiện từ Telegram thật, click link, 4G/Tailscale, login/camera live, PWA install | **Chưa E2E bên ngoài** |
| **G5 Level 3** | **Cloud chạy trên host khác laptop; JPEG được phân tích từ cloud; cloud lưu event; web app cloud hiển thị thống kê**; ảnh/log cloud; đo RTT/phần trăm frame stale | **Chưa deploy/benchmark; chưa thể tự nhận Level 3** |
| G6 Fault injection | Cloud offline, camera offline, WiFi reconnect, DB lỗi, người khuất mặt; còi luôn tắt an toàn | Có test core mô phỏng, cần thêm HIL |

**Mức ưu tiên cho deadline tháng 11**: G0→G1→G2→G3→G4 trước; riêng G5 bắt đầu song song ngay sau G1, nhưng không đánh đổi demo ổn định. Không tăng level bằng cách lắp thêm thiết bị chưa được test.

## 8. Nguồn tham khảo bên ngoài (không thay PDF môn học)

- Espressif esp32-camera README: PSRAM, JPEG, một hay nhiều framebuffer, giới hạn với WiFi: https://github.com/espressif/esp32-camera
- Espressif OV2640 driver auto exposure/gain: https://github.com/espressif/esp32-camera/blob/master/sensors/ov2640.c
- Telegram Bot API (sendPhoto, inline keyboard): https://core.telegram.org/bots/api
- Tailscale Serve (private HTTPS): https://tailscale.com/docs/features/tailscale-serve
- PWA installability/HTTPS: https://developer.mozilla.org/en-US/docs/Web/Progressive_web_apps/Guides/Making_PWAs_installable
- Service workers caching: https://developer.mozilla.org/en-US/docs/Web/Progressive_web_apps/Tutorials/CycleTracker/Service_workers
- Render free service/storage limitations: https://render.com/docs/free
- Render persistent disks: https://render.com/docs/disks

Nghiên cứu những tài liệu này để xác định **khả năng kỹ thuật và giới hạn**, không diễn giải rằng chúng tự chứng minh hệ thống nhóm đã đạt mức IoT nào hoặc đã đạt accuracy nhất định.
