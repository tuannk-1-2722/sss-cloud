# Contracts v2 — firmware, backend, AI và dashboard

Notification extension 02/11: authenticated status thêm `notifications` gồm
enabled/ready/reason, quyền ảnh, loại sự kiện, counts/latest; không lộ token/chat.
POST `/api/v2/notifications/test` admin+CSRF, body `{idempotency_key, include_photo:false}`,
202 trả `{event_id,state:"pending"}`. Cùng key/cùng body idempotent; đổi body409,
key mới trong30giây429. Ảnh thử cần consent và frame mới; không tạo command hoặc
AI observation, event mang `source=system_test`, reason `notification_test`.
History `notification` thêm next_attempt_at/delivered_at/media_kind/duplicate_possible.
`sent` là provider accepted, không là điện thoại đã đọc. Device contract không đổi.
Xem [NOTIFICATIONS](NOTIFICATIONS.md).

Base `/api/v2`. Wire identifiers tiếng Anh; JSON UTF-8, extra fields bị từ chối ở request Pydantic. UUID/session là opaque. Một board `device_id` slug theo config. Thời gian server UTC cho history; timeout dùng monotonic/uptime, không trừ UTC cho millis.

**Cập nhật 31/10/2026:** video liên tục dùng giao thức ở mục 7 bên dưới. Hello/
sync vẫn version 2, có thêm `video` demand và telemetry tùy chọn. Upload HTTP
monitoring/enrollment cũ còn hỗ trợ receipts; preview đã retire (410), không còn
lease 30 giây trong sản phẩm. Các mô tả stream 30 giây tại mục 10 là lịch sử.

## Mục lục

1. [Auth, limits và lỗi](#1-auth-limits-và-lỗi)
2. [Device hello/sync](#2-device-hellosync)
3. [Upload và retry](#3-upload-và-retry)
4. [Observation boundary](#4-observation-boundary)
5. [Admin API](#5-admin-api)
6. [Compatibility và fixtures](#6-compatibility-và-fixtures)

## 1. Auth, limits và lỗi

Device API: `Authorization: Bearer <device token>` provisioned riêng. Không được dùng device token đọc người/ảnh/admin status. Admin: cookie `sss_session`; mutation có `X-CSRF-Token` từ login. Không bật CORS cross-origin mặc định.

Body JSON tối đa 32 KiB; JPEG ≤ `limits.max_frame_bytes`, header metadata ≤ 4 KiB; body read deadline 2 s. Image decoded dimensions bounded và hardware dimensions đúng camera profile. Upload token bucket theo config; hello tối đa 6/min; login tối đa 5 sai/min/IP. Receipt duplicate không tạo work/vote mới.

Lỗi gọn: `{"error":{"code":"MODE_REVISION_CONFLICT"}}`. 401 auth, 403 sai source/role/CSRF, 404 không có, 409 session/revision/idempotency/sequence, 408 upload quá chậm, 413 size, 415 content type, 422 schema/quality protocol, 429 rate limit, 503 model chưa ready. Không trả traceback/request body/password/embedding. Retry-After cho 429/503.

`GET /health/live` public chỉ alive. `GET /health/ready` public chỉ ready boolean/503, phản ánh worker/model; camera/calibration/gallery readiness chi tiết trong authenticated `/status`. Không dùng ready=true thay bằng chứng calibrated hardware.

## 2. Device hello/sync

### Hello

`POST /devices/{device_id}/hello`:

```json
{"protocol_version":2,"boot_id":"random-new-at-boot","firmware_version":"2.0.0","source":"hardware","psram_bytes":4194304}
```

`source=hardware|simulator` phải đúng runtime profile; hardware báo PSRAM=0 bị từ chối (422 PSRAM_REQUIRED); simulation nhận simulator, hardware nhận board. Boot ID 8–64 ký tự. Hello retry cùng boot/session đang active không reset; boot mới disarm/reset. Response:

```json
{"protocol_version":2,"session_id":"opaque-server-session","mode":"DISARMED","mode_revision":2,"max_frame_bytes":524288}
```

Firmware giữ command generation/last ID khi hello trả **cùng session** sau mất mạng ngắn; chỉ reset khi session mới. Sync sequence cũng không reset ở cùng session. Server restart cấp session mới, mặc định DISARMED; không phát lại command từ session cũ.

### Sync và ACK

`POST /devices/{device_id}/sync` độc lập upload:

```json
{
  "session_id":"opaque-server-session","boot_id":"random-new-at-boot",
  "sync_seq":12,"uptime_ms":18000,"buzzer_state":"OFF","pir_high":false,
  "acks":[{"command_id":"opaque-command","generation":4,"status":"applied","buzzer_state":"OFF","executed_uptime_ms":17500,"reason":null}]
}
```

ACK status `applied|duplicate|rejected`; acks tối đa 8. ACK được confirm bằng `acked_command_ids`; device giữ tới confirm. ACK của command superseded/session cũ không hồi sinh trạng thái command hiện tại. `buzzer_state` sync là trạng thái device báo, không tự suy từ event.

Response:

```json
{
  "sync_seq":12,"session_id":"opaque-server-session","mode":"ARMED","mode_revision":3,
  "generation":5,"acked_command_ids":["opaque-command"],
  "command":{"command_id":"next-command","type":"START_ALARM","generation":5,
    "session_id":"opaque-server-session","boot_id":"random-new-at-boot","mode_revision":3,
    "duration_ms":2000,"visit_id":"opaque-visit","remaining_ttl_ms":4200},
  "capture_purpose":null,"capture_session_id":null,"capture_lease_ms":0,"capture_hold_ms":3000,
  "camera":{"width":640,"height":480,"jpeg_quality":12,"sentinel_ms":1000,"active_ms":500,
    "pir_warmup_ms":60000,"pir_burst_ms":10000,"presence_hold_ms":3000}
}
```

`command=null` nếu không có; type `START_ALARM|STOP_ALARM|TEST_ALARM`. Duration/TTL lấy từ config; STOP duration 0, có ưu tiên. Preview/enrollment trả capture purpose/session/lease; giám sát dùng presence hold riêng. Camera width/quality hiện build-time, response mô tả profile; firmware áp dụng các interval, không tự reinitialize resolution từ response.

**Lease:** firmware ghi t0 trước request, t1 sau response, RTT=t1−t0. Chỉ nhận đúng sync_seq/session, revision không lùi, RTT ≤ control timeout. Cửa sổ START bảo thủ = remaining_ttl_ms−RTT; ≤0 thì reject. Duplicate ID không reset deadline; generation cũ hơn đã thấy reject. STOP mới đưa OFF, không cần START còn lease. Disarm/enrollment/đổi revision đưa OFF trước áp command. TEST chỉ DISARMED, test cap; ENROLLMENT không TEST.

Còi hard cap/deadline nằm ở alarm task, độc lập HTTP. `remaining_ttl_ms` là hạn **bắt đầu**, không thời lượng ON. Retry sync same seq nhận TTL tính lại, không response cũ với lease đầy. Seq nhỏ hơn watermark conflict. Mỗi lần poll mới dùng sync_seq mới, dù poll trước timeout; ACK chưa được confirm phải giữ lại. Nếu retry cùng seq thì body phải giống hệt (kể cả uptime/ACK), nếu khác trả SYNC_ID_REUSED. Không cache response TTL nguyên vẹn. Đây là sửa mô tả cũ để khớp cách firmware polling; không đổi wire version.

## 3. Upload và retry

`POST /devices/{device_id}/frames`: raw JPEG body, `Content-Type: image/jpeg`, metadata JSON trong `X-Frame-Meta`:

```json
{"session_id":"opaque-server-session","boot_id":"random-new-at-boot","seq":42,
 "mode_revision":3,"purpose":"monitoring","capture_age_ms":25,"capture_session_id":null}
```

`purpose=monitoring|preview|enrollment`. Preview/enrollment phải có đúng capture_session_id đang active và purpose/mode hợp lệ; không chỉ tin request. `seq` tăng mỗi **ảnh chụp**, không mỗi lần retry. Firmware bỏ ảnh cũ; không buffer khi mạng mất.

`capture_age_ms` tính từ timestamp first DMA của framebuffer đến trước upload, cùng clock esp_timer từ boot; không lấy thời điểm gọi `fb_get` làm thời điểm chụp. Bỏ buffer cũ từ trước lúc ARM/preview, lấy tối đa một replacement rồi vẫn kiểm tuổi. Upload/network và queue/inference có deadline riêng; field này không đo toàn bộ latency.

Mới: HTTP 202 `{"frame_id":"opaque-frame","state":"accepted","duplicate":false}`. Same key/cùng bytes+metadata: 200 cùng ID/state, duplicate=true, không queue. Same key khác fingerprint: 409 FRAME_ID_REUSED. Seq thấp không còn receipt: 409 STALE_FRAME_SEQUENCE. Receipt trạng thái `accepted|processing|processed|replaced|stale|failed|cancelled`; accepted chưa cam kết analyzed.

**v1 → v2:** v1 dự kiến multipart; v2 dùng raw JPEG/header để firmware ít cấp phát hơn. Không gửi multipart/base64/URL ảnh vào endpoint này. Chưa có source triển khai v1 cần migrate DB; nếu nhóm đã viết producer v1 riêng, đổi cả hai phía hoặc giữ adapter riêng có version, không trộn schema.

`POST /devices/{id}/synthetic-frames` **simulation only**, body:

```json
{"metadata":{"session_id":"opaque-session","boot_id":"random-boot","seq":1,"mode_revision":3,
 "purpose":"monitoring","capture_age_ms":0},
 "observations":[{"track_id":"sim-1","bbox":[0.1,0.2,0.2,0.5],"decision":"NOT_CHECKED",
 "person_id":null,"geometry_valid":true,"reason":null}]}
```

Hardware profile luôn từ chối synthetic observations. Dashboard giữ nhãn source; simulator không được gửi source=hardware để giả board. Offline evaluation dùng tool riêng.

## 4. Observation boundary

`contracts.Observation` là ranh giới B ↔ C, Pydantic: cấm extra/nonfinite và ép kiểu numeric/bool sai:

| Field | Kiểu / ý nghĩa |
|---|---|
| `track_id` | String 1–64, tạm trong chuỗi ảnh |
| `bbox` | 4 float hữu hạn `[x,y,w,h]` normalized ảnh gốc, trọn [0,1] |
| `decision` | `KNOWN|UNKNOWN|UNCERTAIN|NO_FACE|NOT_CHECKED` |
| `person_id` | UUID/null; KNOWN chỉ hợp lệ với active person |
| `geometry_valid` | Body/association đủ tin để xét chiều; face-only false |
| `reason` | Code chất lượng/association/matching/null |

Ảnh gốc là hệ tọa độ chuẩn, không trả letterbox coords. Embedding không đưa qua dashboard API. Policy nhận frame_id + monotonic time + Observation[], trả list decision có visit_id/track_id/direction/person_id/reason/severity/audible/suppression/detail. Mỗi đối tượng độc lập. Field/API thay phải có producer-consumer fixture.

## 5. Admin API

| Method/path (sau `/api/v2`) | Input / response chính |
|---|---|
| `POST /auth/login` | username/password → session cookie + csrf_token |
| `POST /auth/logout` | CSRF → 204, revoke |
| `GET /status` | mode/revision, source, age/health, tracks, latest_analysis, capture, gallery count, enrollment_requirements lấy từ config |
| `PUT /system/mode` | mode ARMED/DISARMED + expected_revision; arm cần model/gallery/calibration/device ready |
| `POST /system/test-alarm` | idempotency_key 8–64 → 202 command_id; DISARMED/không capture, device online |
| `POST /system/silence` | visit_id active + idempotency_key → 202 STOP; không disarm |
| `POST /capture/preview` | 410 `PREVIEW_RETIRED_USE_VIDEO_VIEW` |
| `POST /video/viewers` | tạo/gia hạn quyền xem theo cookie+CSRF; xem mục 7 |
| `DELETE /video/viewers/{lease_id}` | trả quyền xem của đúng web session; 204 |
| `GET /capture`, `DELETE /capture` | trạng thái / cancel về DISARMED + staging purge |
| `GET /people`, `POST /people` | list / display_name + consent_recorded=true → draft |
| `POST /people/{id}/enrollment` | inactive/draft, model thật ready → 201 hai lượt |
| `POST /enrollments/{id}/next-round` | round1 đủ → round2 |
| `POST /enrollments/{id}/commit` | `{mode:"DISARMED",expected_revision:n}` → gallery_revision, active=false (draft), next |
| `POST /people/{id}/activate`, `/disable` | activate có samples/calibration/capacity, DISARMED; disable invalidate ngay |
| `DELETE /people/{id}` | full delete + purge mọi evidence; 204 |
| `GET /events?limit=1..100` | newest-first, details/command/notification; bounded history |
| `POST /events/{id}/acknowledge` | idempotent đánh dấu đã xem, không silence |
| `GET /commands` | 50 records gần nhất; state/ACK riêng |
| `GET /latest-frame` | JPEG, X-Frame-Id, no-store; 404 chưa có |
| `GET /media/{id}` | authenticated JPEG evidence, 404 thiếu/purged |

Action key test/silence lưu DB 24h, cùng key khác fingerprint conflict; command + key persist atomically. Duplicate action trả kết quả cũ, không tạo lệnh mới. Thao tác khác dùng revision/resource state hoặc bản chất idempotent. Preview/enrollment đang active thì retry không tạo phiên thứ hai.

UI không diễn giải 202 là buzzer applied. History không xóa alarm khi known xuất hiện sau. User input chỉ `textContent`, không innerHTML tên người. Polling 2s, giảm/stop khi tab ẩn. Mutation có pending/error; ảnh overlay dùng cùng frame ID.

## 6. Compatibility và fixtures

OpenAPI từ server là schema máy đọc, docs này sở hữu semantics. PR cần cập nhật cả hai khi đổi. Device contract version 2; không chỉ thêm required field mà bỏ firmware/simulator chưa cập nhật.

Fixture/test phải cover same key same/different payload, watermark sau purge, boot/session/revision sai, stale inference sau disarm/gallery change, stream quá lớn, bad JPEG, CSRF/device-admin isolation, duplicate command, TTL sau retry, STOP/generation/ACK trễ, offline, enrollment cancel/timeout/multiple faces. Các seed numeric lấy YAML; đừng tạo bản default thứ hai trong doc/code UI.

## 7. Continuous video protocol 1 — 31/10/2026

Backend và firmware cần cập nhật cùng đợt. `hello` thêm
`video_protocol_version:1`. `sync` thêm:

```json
{"video":{"enabled":true,"interval_ms":125,"lease_ms":5000,"revision":2,
"protocol_version":1,"window_frames":2}}
```

Có viewer: nhịp nguồn mặc định 125 ms; ARMED không viewer: 250 ms; DISARMED
không viewer: disabled. Cửa sổ điều khiển bị trừ RTT ở firmware; mất control
quá 5 giây dừng truyền. Nhịp AI độc lập: monitoring 300 ms, enrollment 1000 ms,
đo từ lần bắt đầu trước; không chạy bù tick. Đây là cấu hình thử chưa nghiệm thu.

Device mở **WS `/api/v2/devices/{id}/video`**, WSS khi qua Internet; bearer header,
không cookie authority, không Origin browser, tối đa một uplink. Server chào
`{"protocol_version":1,"window_frames":2}`. Mỗi binary message:

```text
uint16 little-endian header_length | JSON UTF-8 header | JPEG bytes
```

Header 1–2048 byte, JPEG 1–524288 byte; tổng tối đa 526338 byte. JSON:

```json
{"protocol_version":1,"session_id":"opaque-session","boot_id":"random-boot",
"seq":42,"mode_revision":8,"capture_session_id":null,
"captured_uptime_ms":12000,"sent_uptime_ms":12020,"capture_age_ms":20}
```

Timestamp cùng clock monotonic từ boot; sent−captured phải khớp tuổi (sai số
làm tròn ≤2 ms), tuổi ≤1000 ms. Seq và capture timestamp tăng nghiêm ngặt theo
uplink/boot. Không replay sau reconnect. Server xác thực session, control còn
hiệu lực, camera có demand, size/dimensions/JPEG đầy đủ trước publish video RAM.
Ảnh mang analysis revision cũ vẫn được xem nhưng không được đưa vào AI. Firmware
tạo barrier chụp mới khi nhận analysis revision mới qua sync.

ACK `{"seq":42,"frame_id":"uuid","state":"video_received","ai_candidate":true}`
chỉ xác nhận video đã nhận; candidate có thể bị ảnh mới thay hoặc kết quả bị
loại. Không ghi receipt SQLite cho mỗi frame video. Tối đa hai frame chưa ACK,
một ảnh mới nhất chưa gửi; ACK quá 1,5 giây ngắt và bỏ dữ liệu cũ. Server nhận
message tối đa 2 giây, gửi ACK tối đa 1 giây; text/bad metadata/auth/replay đóng
1008, size đóng 1009. Transport fragment được thư viện WS ghép trước khi đọc
envelope; vẫn áp giới hạn tổng và deadline. Chạy `python -m tools.serve` để giới
hạn WS message 526338 byte, queue 2 và tắt nén permessage-deflate.

Viewer POST `/video/viewers` với `{lease_id:null,enrollment_session_id:null}`
→ `{lease_id:"uuid",remaining_ms:15000}`; renew cùng ID mỗi 5 giây, tối đa 3
lease; ID hết hạn/sai owner →404, vượt limit →429. Truyền enrollment session ID
chỉ khi khung hướng dẫn đang hiển thị. Hết/release viewer enrollment sẽ pause
extraction và đổi analysis revision; resume bắt buộc ảnh thuộc revision mới.
Đủ mẫu lượt cũng dừng extraction. Video không bị giới hạn tổng thời gian xem.

GET `/live.mjpg?lease_id=uuid` dùng cookie+lease; stream event-driven từ raw
video cache trước AI, mỗi part có `Content-Length`, `X-Frame-Id`,
`X-Video-Revision`, `X-Mode-Revision`, `X-Frame-Age-Ms`. Khi có sync, dùng clock
offset upper bound nhỏ nhất trong60sync để ước lượng tuổi cảm biến có tính delay
WAN quan sát được; chưa có anchor thì tuổi từ receive+device age. Đây không phải
đo end-to-end chính xác, sai số còn phụ thuộc độ trễ sync. Chỉ
phát ảnh mới và tuổi ≤video.stale_ms (mặc định3000). Video revision đổi khi boot/
xóa cache privacy; mode revision không ngắt video. Kiểm lại web session tối đa
mỗi giây, lease theo mỗi vòng; logout thu hồi mọi viewer của web session đó.
Không đóng/reconnect cưỡng bức sau 30 giây. Mỗi subscriber chỉ theo ảnh mới nhất.

Tương thích WebKit/iPhone: dashboard gửi `Accept: application/octet-stream` để
nhận cùng các byte envelope/metadata/JPEG với `Content-Type: application/octet-stream`.
JavaScript tự parse boundary `frame`; không giao multipart decoding cho browser.
Client không yêu cầu kiểu byte này vẫn nhận `multipart/x-mixed-replace; boundary=frame`
như trước. Cả hai dùng cùng auth, lease, freshness, viewer limit và `no-store`;
response có `Vary: Accept`. Không đổi protocol camera hoặc tạo source ảnh thứ hai.

Status thêm `video` (received_fps/viewers/demand/settings), `analysis` (enabled,
interval_ms/effective_fps/duration_p95_ms/queue_wait_ms/stages_ms/over_budget),
`device_telemetry` tùy chọn. Sync telemetry: control_rtt_ms, free_heap_bytes,
free_psram_bytes, frames_captured, frames_replaced, last_jpeg_bytes,
video_reconnects. Không in token, JPEG hoặc embedding trong log.

Dashboard tự xem ở DISARMED/ARMED, không có preview/live-toggle; mỗi trang một
luồng theo khung đang xem. Một decode đang chạy + một ảnh chờ mới nhất. Status
1 giây, enrollment500ms; lists/history3 giây. Bbox chỉ vẽ khi ID khớp ảnh đang
hiển thị. Hidden/logout giải phóng viewer, không dừng giám sát ARMED của hệ thống.


### Làm rõ v2.1

- Một synthetic frame không được lặp track_id; KNOWN bắt buộc có person_id,
  decision khác không mang person_id. Service kiểm person đó active trước policy.
- next-round tăng mode_revision; firmware lấy revision mới qua sync. Packet cũ
  phải cancelled. Commit không tự activate; API activate là bước chủ động riêng.
- Storage lỗi trả 503 `STORAGE_FAULT` sau khi xóa trạng thái nguy hiểm trong RAM.
- latest-frame có `X-Frame-Id`; logout/disarm/404 phải xóa ảnh/cache hiển thị cũ.
  Preview/enrollment hiển thị vùng A/B từ status, cùng hệ tọa độ với bbox.
- status bổ sung `zones`, `notification_error`, các giới hạn UI từ config; không
  hardcode 30s/1s/7 ngày ở UI nếu người vận hành đã đổi cấu hình hợp lệ.

## 10. Extension contracts — 20/10/2026 (protocol ESP32 vẫn là v2)

Các API dưới đây **không thay đổi** ESP32 device hello/sync/frames contract. Phiên bản firmware protocol vẫn 2. Backend authentication vẫn dùng cookie session + CSRF cho writes.

- **GET `/api/v2/live.mjpg`**: admin cookie required, HTTP 200 `multipart/x-mixed-replace; boundary=frame`. Mỗi part `Content-Type: image/jpeg`, `Content-Length`, `X-Frame-Id`, `X-Mode-Revision` và `X-Frame-Age-Ms` (tuổi từ lúc server nhận ảnh đến lúc tạo part, số nguyên không âm), payload JPEG latest đã xử lý; chỉ phát frame thuộc revision hiện tại và tuổi ảnh <=5000ms, stream đóng sau ~30s để trình duyệt reconnect; tối đa 3 client, quá tải trả 429. Không cho anonymous xem camera. Các header frame là bổ sung tương thích client cũ; device protocol không đổi.
- Dashboard dùng một stream cho ảnh preview/giám sát hoặc đăng ký đang xem; không poll JPEG song song. Status/tiến trình vẫn poll 2s. Client chỉ hiển thị frame cùng revision với status; clear ảnh khi hủy/đổi phiên, hết lease, logout, rời tab hoặc mất ảnh mới; reconnect không gia hạn capture. Bbox AI chỉ vẽ khi `X-Frame-Id` khớp analysis; ảnh JPEG từ `/latest-frame` vẫn phục vụ recorder/client cũ.
- **GET `/manifest.webmanifest`, `/sw.js`, `/static/icon-{192,512}.png`**: public static nhưng **không có dữ liệu người/camera**. SW chỉ cache manifest, JS, CSS, icon.
- **Status `/api/v2/status`** thêm `environment_quality: {usable,reason,median_luma,p95_luma,dark_fraction,bright_fraction}|null`, `cloud_error: string|null`. Client cũ bỏ qua field mới.
- **Cloud `GET /health/ready`**, bearer token required. Response `{ready,vision_error}`. Mặc định token 32+ ký tự do operator cấp, HTTPS reverse proxy.
- **Cloud `POST /v1/infer`**: bearer token required, JSON `{purpose:"monitoring"|"enrollment", jpeg:base64-JPEG, gallery:{person_id:[base64-little-endian-float32-normalized]}}`; payload bị giới hạn. Monitoring response `{observations:[Observation],environment}`, enrollment response `{feature:base64|null,quality,environment}`. Request ảnh/gallery chỉ phục vụ inference hiện tại, cloud không lưu. Cloud nhận một camera demo, chưa multi-node session HA.
- **Cloud `POST /v1/reset`**: bearer token; reset cloud tracking on mode/gallery revision change.
- **Cloud `POST /v1/events`**: bearer token, JSON `{event_id,device_id,reason,severity,occurred_at,source}`; event_id duy nhất, insert idempotent. Không gửi `person_id`, jpeg, embedding, raw details.
- **Cloud `GET /v1/summary`, GET `/`**: Basic auth riêng cho viewer, không dùng device bearer/edge admin cookie; trả analytics cloud. Cloud DB cần persistent disk.
- **Migrations**: `002_cloud_outbox.sql` thêm bảng outbox riêng, không sửa `001_initial.sql`. Thay đổi thêm cloud mode không làm đảo thứ tự command/ACK hoặc bỏ freshness/revision gates.

Các lỗi xác thực/validation không được echo `jpeg`, gallery, token. Không mở cloud service qua plain HTTP.

## Chẩn đoán nhận diện — 01/11/2026

- `Observation` có thêm `evidence` tùy chọn: min_side_px, eye_distance_px,
  blur/eye_blur, clipped, face_confidence, s1/s2; chỉ số hữu hạn, không vector.
  `tracking_reason` tùy chọn mô tả association. Producer/consumer Observation
  (gồm cloud service/adapter nếu dùng topology tách AI) phải nâng cùng phiên bản;
  firmware gửi JPEG không cần tạo các field này. Synthetic endpoint chỉ được dùng
  trong simulation như trước, không cho đưa evidence giả vào hardware.
  `identity_eligible` là boolean strict, mặc định false cho producer cũ; true cho
  phép tích evidence trong ROI dù anchor ở khe trung tính. Nó không cấp geometry
  hay incoming/alarm. Numeric NumPy từ detector được đổi thành Python bool trước
  khi tạo Observation; không nới strict validation của HTTP.
- GET `/api/v2/diagnostics/recognition?after=0`: admin session, no-store,
  ring metadata hữu hạn, sequence/counts và trạng thái trace. Không trả ảnh/vector
  hay danh tính thật. `after` chỉ lọc RAM, không đọc lịch sử DB.
- POST `/api/v2/diagnostics/recognition/start`: admin + CSRF, bắt đầu cửa sổ
  `diagnostics.trace_duration_ms`, clear ring/counts và đổi alias key; sequence
  tiếp tục tăng. Không thay mode/session/gallery hoặc quyền điều khiển còi.
- DELETE `/api/v2/diagnostics/recognition`: admin + CSRF, dừng journal trace,
  HTTP204. Ring vẫn hữu hạn; restart xóa toàn bộ RAM trace.
- Mặc định không ghi journal từng frame; chỉ log trong cửa sổ operator bật.
  Cấu hình history_limit tối đa1024, trace_duration_ms tối đa300000; mặc định
  trong YAML, không thuộc camera calibration hash vì không đổi quyết định AI.
- Status thêm `recent_visits` hữu hạn: visit_completed gồm identity/person_id,
  direction, completion_reason, last_observed_ms/completed_ms, không phải còi mới.
  Lịch sử DB có event info `visit_completed`; completion không lưu thêm ảnh.
- GET latest-frame thêm X-Observed-Ms và X-Received-Ms (clock monotonic server,
  observed là ước lượng nguồn từ sync), để recorder giữ riêng capture/arrival.
  Recorder cũ vẫn dùng được X-Frame-Id; timing client_poll phải ghi đúng giới hạn.
