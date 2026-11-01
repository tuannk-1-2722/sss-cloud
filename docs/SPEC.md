# Spec triển khai v2

**Baseline review:** 02/10/2026 (v2.1). Deadline khoảng **11/2026**. Tài liệu này sở hữu hành vi; [CONTRACTS](CONTRACTS.md) sở hữu wire contract; `configs/default.yaml` sở hữu default. [TEAM_GUIDE](TEAM_GUIDE.md) giải thích cho người, không là một bản yêu cầu cạnh tranh.

**Thay đổi 31/10:** người dùng chốt ESP32 → Oracle, laptop chỉ mở web và cho phép
refactor policy cũ. Video/AI tách riêng theo [wire protocol mới](CONTRACTS.md#7-continuous-video-protocol-1--31102026).

## Mục lục

1. [Phạm vi và ranh giới module](#1-phạm-vi-và-ranh-giới-module)
2. [Ảnh, chế độ và tính mới](#2-ảnh-chế-độ-và-tính-mới)
3. [Vùng, track và lượt tiếp cận](#3-vùng-track-và-lượt-tiếp-cận)
4. [Danh tính và xác nhận](#4-danh-tính-và-xác-nhận)
5. [Enrollment và gallery](#5-enrollment-và-gallery)
6. [Event, còi và thông báo](#6-event-còi-và-thông-báo)
7. [Dữ liệu và bảo vệ](#7-dữ-liệu-và-bảo-vệ)
8. [Lỗi, cấu hình và mở rộng](#8-lỗi-cấu-hình-và-mở-rộng)

## 1. Phạm vi và ranh giới module

Một camera AI-Thinker/OV2640 có PSRAM + PIR + buzzer logic 3,3 V, một laptop CPU RAM 8–16 GB, LAN tin cậy. Ngân sách phần cứng mục tiêu dưới 1 triệu VNĐ. Mục tiêu kiểm chứng cuối: 20 người active, hai người đồng thời, ánh sáng trong nhà, A/B cố định. Quy mô đã đo lấy từ calibration, không từ config limit.

Baseline v2.1 **chưa có**: mở khóa tự động, nhận diện trực tiếp ESP32, đếm chính xác mọi lượt vào/ra, liveness đáng tin cậy, outdoor/night, nhiều camera, public Internet/cloud sinh trắc, ứng dụng native. **Bản staging 20/10** mở rộng night hỗ trợ ánh sáng, Telegram ảnh/link, PWA/live và cloud inference khi có consent (xem mục 10). Vẫn không mở dịch vụ camera công khai Internet.

| Module | Nhận / trả | Không làm |
|---|---|---|
| Firmware | PIR, JPEG, sync response → upload, GPIO, ACK | Không quyết định danh tính/cảnh báo |
| `vision.py` / `tracking.py` / `matching.py` | JPEG + gallery snapshot → Observation[] | Không ghi event, gọi mạng hay GPIO |
| `spatial.py` / `policy.py` | Observation[] + monotonic time → decision[] | Không gọi model, DB hay network |
| `service.py` | Điều phối mode/revision, event/command; composition enrollment + worker | Không nhúng inference vào handler API |
| `main.py` / `auth.py` | HTTP → validation/auth → service | Không đổi domain rule theo UI |
| `storage.py` | Transaction, gallery/evidence/retention/backup | Không suy danh tính |
| `notifications.py` | Outbox event → text/ảnh + liên kết HTTPS tùy chọn | Không chặn AI/còi |

Baseline là modular monolith: Uvicorn **một process**, một thread sở hữu OpenCV models; notification thread riêng. SQLite WAL trên ổ local, connection riêng mỗi transaction. Một instance lock/runtime. Không cần SQLAlchemy/Alembic/broker để chạy baseline nhỏ; khi schema đổi thêm migration có số, không sửa file migration đã được áp dụng.

## 2. Ảnh, chế độ và tính mới

**FR-01 — Sampling:** video tự xem khi mở trang, kể cả DISARMED; không preview30s.
Có viewer dùng video.view_interval_ms; ARMED không viewer dùng armed_interval_ms;
DISARMED không viewer dừng ảnh, còn heartbeat. PIR không là điều kiện bắt buộc
để AI thấy người. AI dùng lịch analysis độc lập; DISARMED dừng AI giám sát/còi.
VGA/JPEG quality12, hai buffer PSRAM/GRAB_LATEST; nguồn ảnh không chờ inference.

**FR-02 — Bounded work:** mỗi device tối đa một frame đang xử lý + một pending. Frame mới thay pending, receipt cũ ghi `replaced`. Không đợi xử lý queue lịch sử để phát còi. Firmware không queue ảnh vào flash/SD và luôn trả framebuffer đúng một lần.

**FR-03 — Freshness:** kiểm size/header/dimensions/JPEG, capture age và request timeout. Định danh `(boot_id,seq)` chỉ được xử lý một lần; same key khác body/metadata → conflict. Watermark vẫn chặn replay khi receipt purge. Server loại frame quá processing age; receipt accepted không có nghĩa đã phân tích.

**FR-04 — Revision:** mode/enrollment round/pause-resume extraction tăng
`mode_revision`; gallery tăng `gallery_revision`. Video revision riêng, đổi
mode không ngắt video. Worker kiểm lại phiên/revision/freshness sau inference,
trước publish; enrollment còn phải có viewer hướng dẫn và còn cần mẫu.

**FR-05 — Startup:** server khởi động DISARMED; session admin cũ bị thu hồi, pending commands cancelled, staging samples xóa. Board boot còi OFF và boot ID mới. Hello cùng boot đang active idempotent; boot/session mới reset continuity/command. Không tự arm sau restart.

Ảnh latest chỉ RAM và có frame ID. Overlay chỉ vẽ khi frame ID ảnh và analysis khớp; UI luôn báo tuổi ảnh và online/fault riêng. Có JPEG mới không chứng minh AI đã xử lý ảnh đó.

## 3. Vùng, track và lượt tiếp cận

**FR-06 — Geometry:** A=`zones.approach`, B=`zones.doorstep`; normalized rectangles `[x,y,w,h]`, không chồng, nằm trọn ảnh. Anchor là tâm bbox **thân người**. Đây không phải ước lượng mét/homography/sàn. Face-only có bbox để hiển thị/so mặt nhưng `geometry_valid=false`, không tạo bằng chứng incoming.

Một raw zone phải đủ `stable_observations` liên tiếp trước thay stable zone. Đếm theo frame khác nhau. Nhận diện lấy bằng chứng trong `vision.recognition_region`, độc lập A/B, gồm cả đường tiếp cận và khe trung tính. `identity_eligible` cho biết observation nằm trong ROI này. Event tại cửa/còi vẫn cần raw và stable đều B; face-only không tạo incoming.

**FR-07 — Direction:** trong một track liên tục, đã stable A với geometry hợp lệ rồi stable B khi bằng chứng A còn trong `approach_history_ms` → INCOMING. Khoảng trung tính giữa hai vùng được phép nếu track liên tục. B → A → OUTGOING. B xuất hiện trước khi có A → UNRESOLVED. Mất track quá `track_gap_ms`, association mơ hồ hoặc đổi body/face-only → track mới, không kế thừa chiều/known.

**FR-08 — Visit:** stable entry B tạo `visit_id` UUID mới, reset emitted reasons, kết quả lượt và silence; giữ identity evidence còn mới đã thu trong ROI của cùng track trước khi đến cửa. Dwell tính từ raw B đầu tiên của chuỗi liên tục đã được xác nhận ổn định; mất observation trước stable entry bắt đầu lại chuỗi. Rời B ổn định thì đóng lượt và xóa evidence của lượt cũ; vào lại tạo lượt mới. Border bounce chưa stable không tạo lượt mới. Không có incident camera-wide để suppress mọi người mới khi còn người quen trong ảnh.

Khi rời B ổn định, đổi geometry hoặc mất track quá `track_gap_ms`, ghi `visit_completed` đúng một lần, kể cả lượt không đủ `unresolved_warning_ms`. Kết quả là KNOWN/UNKNOWN/UNCERTAIN, kèm completion/quality reason. Kết quả đã phát trước đó được giữ như lịch sử; conflicting known IDs làm kết quả cuối uncertain. Hoàn tất lượt khi worker nhàn rỗi không tạo phiếu hay alarm mới. Inference hợp lệ của ảnh chụp trước khi người rời đi có thể COMMIT sau lúc họ rời ảnh, trong freshness/revision gates hiện có. STOP/disarm/session/gallery change vẫn vô hiệu kết quả cũ.

Mỗi lượt có thể có info/warning/alarm riêng; warning có thể nâng thành alarm trong cùng lượt. Mỗi `(visit_id,reason)` được ghi tối đa một lần, unique DB constraint bảo vệ retry. Track ID chỉ tạm trong runtime; không là định danh người lạ xuyên buổi/camera.

**FR-09 — Multi-person:** conservative one-to-one bbox matching; gating IoU/center; ambiguous costs reset continuity. Body track và face-only track không đổi loại bằng một lần match bbox. Không dùng `faces[0]`, không coi người quen trong ảnh là trạng thái toàn ảnh. Vượt `max_simultaneous` đối tượng trong A/B tạo capacity warning; không counting người ngoài vùng để cảnh báo sai. Chỉ embed số mặt giới hạn để giữ latency, không hiển thị “tất cả quen” khi chưa xét hết.

Với vị trí camera mà center A/B không tách được hoặc thường mất body giữa vùng, không chốt calibration. Sửa bố trí/geometry trước; không giảm face threshold để bù direction sai.

## 4. Danh tính và xác nhận

**FR-10 — Model:** NanoDet ONNX person + YuNet face + SFace feature, CPU; artifact/wrapper/license ở exact commit và SHA256 trong manifest. BGR, person letterbox 416×416 rồi đưa bbox về ảnh gốc; YuNet input size đúng ảnh gốc; SFace `alignCrop` từ 5 landmark. Normalize L2 vector float32, output dimension đúng manifest.

YuNet chạy độc lập person detector. Gán mặt vào phần trên body khi có đúng một candidate hợp lý; nhiều candidates hoặc nhiều mặt trên body → uncertain. Không thấy mặt → NO_FACE, không UNKNOWN. Face confidence, min size, landmark, blur trên crop 112×112, clipped pixels là quality gate. Không ước lượng yaw chính xác bằng một heuristic chưa đo.

**FR-11 — Open set:** với query q và samples g của mỗi người i:

$$s_i=\max_k q^\top g_{i,k}$$

Xếp hạng theo người, không theo sample. `s1` best, `s2` best của **người khác**. Known khi s1 ≥ accept và đủ margin với s2 (gallery một người không cần s2). Unknown khi gallery không rỗng và s1 < reject. Còn lại uncertain. Phải reject < accept. Không gọi cosine là probability.

**FR-12 — Temporal:** mỗi frame khác nhau đóng góp tối đa một bằng chứng;
window theo vote_window_ms, tối đa128 entries. Confirmation cần confirm_votes
cùng loại/ID và trải trên confirmation_span_ms (mốc thử600ms). vote_window và
vote_spacing_ms cũ chỉ giữ để đọc config tương thích, không điều khiển vote.
Conflicting known IDs reset; unknown cần không có known ID trong cửa sổ.
Uncertain/no-face không thành unknown. Identity hết freshness thì uncertain.
Geometry/tracking/policy dùng cùng observed time, TTL lệnh dùng clock hiện tại.

Bằng chứng ROI trước B vẫn phải đủ số phiếu/span/freshness, không hạ ngưỡng matching vì hành động ngắn. Kết quả KNOWN của lượt đã ghi không bị xóa khỏi lịch sử khi ảnh sau mất mặt; identity hiện tại có thể hết freshness. `policy.flow_version` và ROI thuộc hash calibration, thay đổi đòi review profile. Model vẫn NanoDet/YuNet/SFace 128d trong bản nâng cấp này; ứng viên InsightFace tạm hoãn theo quyết định người dùng.

Gallery rỗng hoặc model/calibration lỗi chặn arm; không gọi mọi người là unknown. Threshold trong default là seed. Hardware arm cần validated profile đúng camera/config/model manifest và có report/reviewer.

## 5. Enrollment và gallery

**FR-13 — Workflow:** admin + consent → person draft → DISARMED → enrollment lease, STOP → hai lượt → commit → DISARMED. Người active phải disable trước reenroll; chỉ một capture session. Có thể thu draft trước calibration để tránh vòng phụ thuộc cần gallery mới hiệu chỉnh được.

Enrollment chạy YuNet/quality/SFace trên một JPEG mới nhất mỗi lịch (mốc thử1s),
không chạy NanoDet hay yêu cầu vùng B của giám sát. Cần đúng một face, tâm trong
ellipse face_region, quality cao hơn recognition. Không thêm ảnh nhiều mặt.
Hidden/pause/đủ lượt dừng extraction nhưng không ngắt video nếu còn quyền xem.

**FR-14 — Samples:** ít nhất per_round_min/lượt, accepted_min/phiên. Lịch AI
được xét trước extraction; spacing được kiểm theo thời điểm quan sát. dHash và
pose/scale/position signature giúp bỏ mẫu gần trùng; không chứng minh liveness
hay góc quay chính xác. Mẫu phải nhất quán identity, không khớp rõ active person
khác; sai thì feedback/reject. Người/admin vẫn kiểm đúng người trước camera.

Mã nền thu tối đa `per_round_min` mỗi lượt; giới hạn gallery phải phù hợp đủ hai lượt. Commit giữ 5–10 mẫu theo limit, xen kẽ hai lượt; xóa staging dư. Gallery commit atomic, revision tăng. Không train lại weights khi đăng ký.

**FR-15 — Activation:** commit luôn lưu draft; activate riêng sau sanity check. Active limit hardware = min(max_people, validated_gallery_limit). Không có calibration phù hợp thì không activate/arm. Đổi gallery trong limit vẫn phải sanity check người mới; gallery tăng vượt phạm vi cần evaluation lại. Disable/delete invalidate gallery ngay; packet cũ không publish. Xóa người cuối làm DISARMED.

Samples lưu embedding/hash/quality/version/consent, **không lưu raw enrollment image** ở baseline. Đổi model/preprocess cần reenroll. Runtime từ chối sample version không khớp manifest; không lặng lẽ dùng vector cũ dù dimension giống.

## 6. Event, còi và thông báo

**FR-16 — Policy table:**

| Điều kiện ở B khi ARMED | Event reason / severity | Audible |
|---|---|---|
| Known confirmed | `known_visit` / info | false |
| Incoming + geometry hợp lệ + dwell + unknown confirmed/fresh | `unknown_approach` / alarm | Một lần/lượt, tùy suppression |
| Hiện diện đủ warning interval/observations, direction/geometry/identity unresolved | `unresolved_presence` / warning, detail cụ thể | false |
| Capacity vượt giới hạn ở A/B | `capacity_exceeded` / warning | false |
| No observations, ngoài B, lỗi hoặc frame stale | Không alert danh tính | false |

Direction chưa rõ vẫn có thể có known info; warning geometry/direction nếu hiện diện kéo dài không được dịch thành “người lạ”. History giữ alarm cũ khi sau đó mặt được nhận ra; không xóa evidence để làm kết quả đẹp.

**FR-17 — Suppression:** cooldown toàn device và silence theo visit. Alert vẫn persist khi suppressed, có reason; không trì hoãn START để chờ cooldown. Pending STOP không bị alarm mới ghi đè: có event nhưng suppress `stop_pending` nếu STOP còn lease. Silence dừng âm vật lý hiện tại, không vô hiệu toàn bộ người mới; acknowledge không silence.

**FR-18 — Delivery:** event + START record cùng transaction. Command ID/generation duy nhất; state pending → acked/rejected/expired/superseded/cancelled. Mode/boot/session/revision phải khớp. START deadline, hard cap, duplicate, late ACK và STOP theo CONTRACTS. HTTP 200 không thay execution ACK. DB transaction fail không được để START chưa persist vẫn tồn tại trong RAM để gửi.

**FR-19 — Local alarm:** independent task kiểm deadline, không đợi upload/network. OFF khi boot/disarm/enrollment/mất control; test tối đa test cap khi DISARMED. GPIO owner duy nhất, active level theo module thực. Queue ACK bounded; overflow đưa OFF và phải có HIL report. Không cấp công suất buzzer từ GPIO.

**EX-01 — Telegram:** text-only outbox unique event, disabled mặc định. HTTP timeout/retry/backoff có hạn; HTTP 429 theo retry_after có trần chờ. Alert quá expiry skipped/expired. Delivery timeout không bảo đảm exactly-once; state unknown sau hết retry. Không gửi ảnh face/token qua log; không điều khiển arm từ bot. Extension lỗi không làm core chờ.

## 7. Dữ liệu và bảo vệ

**FR-20 — Storage:** bảng hiện tại do `migrations/001_initial.sql` sở hữu: people, samples, events, commands, receipts, web_sessions, action_keys, audit, outbox, schema_version. FK bật; unique `(visit_id,reason)` và `(boot_id,seq)`. Raw JPEG thường chỉ RAM; event evidence dùng UUID path do server tạo, không client path.

Ghi `.tmp` rồi atomic rename; nếu evidence thất bại, event vẫn có missing/evidence_error nếu DB khỏe. Dọn evidence theo tuổi/quota; không xóa active samples để lấy chỗ. Janitor áp dụng kể cả đang DISARMED; orphan `.tmp`/JPEG không có DB ref phải được dọn sau grace. Receipt retention không reset watermark.

**FR-21 — Retention:** exact defaults trong YAML. Metadata events/outbox/commands/audit purge theo retention; outbox cascade khi event purge. Samples giữ khi người tồn tại. Full person delete xóa vector/name, ẩn person ID trong event, **purge mọi evidence image + latest cache** vì ảnh có thể nhiều người. Backup cũ phải xóa riêng; không có quyền coi restore dữ liệu đã rút consent là hợp lệ.

**FR-22 — Auth:** một admin provision local, Argon2 hash, random cookie session 8h, hash lưu DB, HttpOnly/SameSite, Secure khi HTTPS; CSRF mutation. Device token riêng chỉ có device API. Media/history/people/status đều admin auth. Login/hello/upload có rate/bounds. Không echo password/raw body/vector khi validation lỗi. Không URL ảnh external hay arbitrary path read.

Core HTTP chỉ LAN private tin cậy; không tuyên bố chống nghe lén. Remote VPN/HTTPS là extension có task riêng, không port-forward camera/server. Secrets/data/runtime/backup ignored Git; CI chỉ fixture tổng hợp.

**FR-23 — Backup:** server stopped, acquire lock, SQLite backup API + media + checksum manifest; restore vào runtime mới, revoke sessions/pending commands và DISARMED. Secrets không trong backup; reprovision. Kiểm config/model/calibration và consent trước activate/arm; restore đưa mọi người về inactive và revoke session/command ngay. Không copy `.db` đang WAL rồi bỏ `.wal`.

## 8. Lỗi, cấu hình và mở rộng

**FR-24 — Health:** device sync age, frame age, inference age, vision init, storage và delivery tách biệt. Không có kết quả lâu → reset track continuity, `ANALYSIS_INTERRUPTED`, không tự ghi người đã ra. Frame processing exception đưa DISARMED, xóa START memory và cố persist STOP; storage fail vẫn DISARMED/command none, firmware tự OFF theo hard cap/control.

Config file không biết key/rectangle sai/ngưỡng sai/vote window sai/duration quá cap bị từ chối startup. `profile=simulation` cho synthetic observations, hardware từ chối chúng và runtime không dùng lẫn profile. Calibration seed không đánh dấu validated tự động bởi tool.

Threshold/config/camera geometry/model đổi phải DISARMED, có report và invalidate calibration hash. Chỉ display name đổi không buộc đổi camera hash. Firmware/backend protocol thay semantics bump version; v2 không tương thích payload v1.

**Mở rộng:** thêm module ở adapter phù hợp, giữ Observation → policy boundary. Thêm camera sẽ cần session/queue/policy per-device và migration; chưa đủ chỉ sửa device_count. Muốn sensor direction/liveness/new model/native UI tạo issue có contract và gate, giữ core release làm checkpoint. Chi tiết công việc trong PLAN, không để agent tự đổi baseline khi sửa bug khác.


## 9. Làm rõ sau review v2.1 — quy tắc bắt buộc khi triển khai

Các điểm dưới cụ thể hóa FR phía trên; giữ bài toán/board/model của TEAM_GUIDE.
Không đồng nghĩa camera/AI/hardware đã nghiệm thu. Evidence hiện tại ở `experiments/HANDOFF.md`.

- **FR-02/03:** kiểm JPEG hoàn chỉnh ngoài lock điều phối; sau đó lấy lock và kiểm lại
  session/revision/lease trước enqueue. Kết quả quá tuổi bị loại **trước và sau** inference.
  Capture lease phải còn hạn khi publish; đổi lượt enrollment tăng revision để kết quả
  chụp ở lượt 1 không được gán sang lượt 2. Tuổi packet dùng server monotonic; thời gian
  truyền trước nhận là giới hạn riêng, không gọi processing age là end-to-end latency.
- **FR-07/09:** một track chỉ có một observation mỗi frame. Geometry invalid hoặc
  association mặt–thân mơ hồ phải cắt continuity/phiếu, không dùng UNKNOWN cũ để hú
  trong lúc đang mơ hồ. Capacity đếm thân người + mặt thực sự không có thân, không đếm
  thêm cùng một mặt khi nó nằm trong hai bbox thân chồng nhau.
- **FR-12:** mâu thuẫn KNOWN khác ID phải xóa identity cũ kể cả phiếu cũ đã ra khỏi
  window nhưng confirmation chưa hết freshness. Không thừa hưởng incoming giữa body
  và face-only. Im lặng/identity freshness chỉ có nghĩa trên lượt còn hiện diện.
- **FR-13/14:** enrollment ở B theo body center nếu gán thân rõ; nếu không có thân,
  dùng face center ở B, vẫn phải đúng một mặt. Nhiều thân hoặc mặt–thân không gắn được
  thì loại. Commit luôn lưu **draft**; kích hoạt là thao tác riêng sau sanity check
  người mới và calibration hợp lệ. Không tự active chỉ vì còn chỗ trong limit.
- **FR-15:** ARM kiểm lại số người active không vượt calibrated limit; restore một
  runtime lớn hơn không vượt qua gate này. Mỗi người phải có số samples tối thiểu,
  đúng model/preprocess. Full delete hủy mọi capture và packet cũ trước purge cache.
- **FR-17/18:** tạo payload command trong transaction, chỉ công bố command trong RAM
  **sau COMMIT**. Lỗi DB trong API cũng fail closed như lỗi worker: DISARMED,
  revision tăng, xóa pending/START/cache/continuity; cố STOP khi DB phục hồi.
  Không dùng policy cooldown để coi một command đã phát nếu bị `stop_pending`.
- **FR-19:** command nhận qua HTTP vẫn phải kiểm session/revision/mode và TTL tại
  thời điểm alarm task thực thi. TTL phải trừ cả thời gian chờ queue. Overflow làm
  OFF và bỏ queue command cũ. Model Python simulator phải cùng semantics với header
  C++; host test không chứng minh FreeRTOS/GPIO chạy đúng.
- **FR-20:** migration runner áp dụng file SQL theo số, transaction từng migration,
  từ chối DB có schema mới hơn code. Không sửa `001_initial.sql` đã áp dụng.
- **FR-24:** lỗi config thiếu file được chỉ định, sai kiểu, bool thay int, NaN/Inf,
  ngưỡng/vote/interval sai phải fail startup. Các hằng safety/build đã khóa trên
  firmware phải được backend kiểm bằng đúng giá trị, không cho YAML hứa giá trị
  mà firmware bỏ qua. Capture/sampling config nằm trong calibration hash.
- **EX-01:** notifier không chết vĩnh viễn khi SQLite/provider trả lỗi; trạng thái
  fault riêng trên status. Retry hữu hạn, stop interruptible; không log token.

**Ý nghĩa của “hoàn thành”:** spec đã chốt semantics để coding; software PASS là
những kiểm thử đã thực sự chạy. Hardware, calibration, accuracy, latency và UI thực
chỉ PASS khi có bằng chứng tương ứng. Không có mục “spec đúng 100%” thay cho các gate.

## 10. Mở rộng đã chốt 20/10/2026 — staging Level 3

**FR-25 — Chất lượng môi trường:** Gắn `environment_quality` cho frame thực, đo độ sáng trung vị, percentile, vùng clipped. Khi scene suy giảm, identity ở B không được `UNKNOWN` hoặc `KNOWN` từ frame hiện tại; phải `UNCERTAIN`/lý do hoặc không có đối tượng detect; health hiển thị lý do. Không khẳng định thuật toán nhận ra được mọi sự kiện mưa/sương/lens ướt. Ngưỡng environment là seed phải validate day/night và đưa vào camera profile hash.

**FR-26 — Đèn và camera:** Exposure/gain/white balance auto là bước hỗ trợ, không thay thế nguồn sáng. Night illumination là yêu cầu phần cứng **chưa chốt mạch**; lắp đèn trắng/IR phải xác minh revision ESP32-CAM và nguồn, rồi cập nhật firmware/profile/validation cùng nhau. Tránh hardware assumptions từ ảnh shop.

**FR-27 — Telegram:** Cảnh báo metadata (loại/thời điểm/event id) kèm ảnh JPEG nếu evidence có. Nếu cấu hình HTTPS private dashboard URL thì đưa nút truy cập, tuyệt đối không nhúng session token hoặc link public raw JPEG. Không gửi video trong scope. Outbox không blocking worker, retry có thể gây duplicate nếu provider timeout.

**FR-28 — Live & PWA:** Luồng MJPEG từ **processed latest frame** server, không mở stream trực tiếp từ ESP32, session-auth, tối đa 3 client, 30 giây cần reconnect/auth lại, ngừng khi rời tab/logout. FPS không hứa vượt nhịp capture/inference. Manifest, icon, service worker chỉ cache static resources. HTTPS cần cho install PWA.

Dashboard tự dùng cùng luồng này cho khung preview và enrollment khi phiên hợp lệ,
chỉ một kết nối cho tab nội bộ đang xem; status/feedback vẫn cập nhật 2 giây.
ID/tuổi frame trong multipart giữ kiểm overlay/freshness. Hủy/đổi phiên, hết
lease hoặc mất ảnh mới phải xóa ảnh cũ. FPS hiển thị đếm frame đã tải/giải mã,
không được diễn giải thành FPS sensor hoặc tốc độ thu mẫu AI.

**FR-29 — Remote access:** Dùng private Tailscale Serve HTTPS với policy phù hợp; không Funnel hoặc public port-forward. Client cần vào tailnet và đăng nhập ứng dụng. Laptop/ngõ local phải online.

**FR-30 — Cloud inference tùy chọn:** `cloud.inference=local|cloud`, mặc định local. Cloud mode chỉ khi hardware, API URL HTTPS hợp lệ, API token >=32 ký tự, `SSS_CLOUD_BIOMETRIC_CONSENT=1`. JPEG/gallery được xử lý tạm thời trong request cloud, không lưu tại cloud. Worker vẫn recheck revision/freshness sau inference; network/model failure không phát quyết định danh tính thiếu căn cứ, mode fail-closed.

**FR-31 — Cloud analytics:** `cloud.events_enabled=false` mặc định. Khi bật, tạo `cloud_outbox` trong transaction event; thread riêng mirror **chỉ metadata** (`event_id`, `device_id`, reason, severity, occurred_at, source) và cloud lưu idempotently, analytics nhóm theo reason/severity/day. Ảnh/video/embedding/person-name không mirror qua event stream. Local alarm/policy không đợi cloud sync. Cloud DB phải gắn disk persistent khi triển khai thực.

**FR-32 — Đánh giá cấp IoT:** Baseline local gần Level 1. Có cloud storage nhưng infer local chưa đạt Level 3. Chỉ tuyên bố hướng Level 3 khi cloud inference + storage + cloud analytics web app chạy trên hạ tầng **khác** laptop và có evidence. Không nhầm Tailscale/Telegram với cloud analysis.

**FR-33 — Trạng thái nghiệm thu:** Chế độ cloud và night illumination chưa được HIL, chưa benchmark latency/accuracy/security. Test software không chứng nhận hoạt động trên ESP32 hoặc thực sự đạt Level 3.

Tham chiếu bổ sung: [`LEVEL3_UPGRADE.md`](LEVEL3_UPGRADE.md), [`UPGRADE_AUDIT.md`](UPGRADE_AUDIT.md).
