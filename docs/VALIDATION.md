# Kiểm chứng, calibration và demo

Review demo 02/11 đã được operator chấp nhận: clear test 1/1 đúng; mask test chưa
xác nhận KNOWN, trả UNCERTAIN/không alarm được chấp nhận trong phạm vi demo; unknown
PASS theo operator. Test còi 1000 ms có ACK và operator xác nhận tự tắt. Profile
`doorstep-demo-v2-20261102-reviewed`, limit 1 người, đã áp dụng; không đồng nghĩa
toàn bộ mục tiêu G4/full HIL đã đạt. Xem [RECOGNITION_UPGRADE](RECOGNITION_UPGRADE.md).

Cập nhật02/11: `early-evidence-v2` thêm xác nhận known-only bằng hai ảnh mạnh;
xem [RECOGNITION_UPGRADE](RECOGNITION_UPGRADE.md). Local 158 test PASS, Linux 157 PASS/
1skip, HTTP smoke PASS; validation native clear/mask thu đủ đúng2/2, chưa là
holdout. Mục người lạ PASS theo xác nhận của người dùng; không biến các clip
known bị gắn nhãn nhầm thành số đo unknown. Cần freeze rồi đối chứng mới.

Nâng cấp lượt ngắn xem [RECOGNITION_UPGRADE](RECOGNITION_UPGRADE.md). Giữ model
baseline; bổ sung kiểm identity trước B, khe ROI, quay mặt khi vặn khóa, lượt
ngắn không đủ mặt, reentry, người lạ, completion idle và STOP/disarm race.
Thời điểm alarm/hiển thị sau khi rời ảnh phải đo riêng trên hardware; mục tiêu
người dùng cho phép khoảng 3–4 giây, không là kết quả đã nghiệm thu. Replay
scheduled không thay HIL hoặc holdout. Thay flow/ROI làm profile hash đổi.
Kiểm tra software ngày 01/11/2026: `tools.check --http` đạt 138 test, lint,
format và HTTP smoke. Linux biệt lập đạt 137 pass/1 skip (thiếu Node); 13 ca Node
đạt local. Flow mới đã triển khai Oracle 22:27 ngày 01/11; gallery rỗng và
`CAMERA_PROFILE_CHANGED` vẫn chặn giám sát. Chưa có holdout/người lạ độc lập
hoặc HIL của flow mới; không tự cập nhật calibration để vượt gate.

Các con số trong YAML là **seed**. Chỉ report từ camera/laptop/bố trí thực của nhóm mới cho biết phạm vi đã đạt. Simulator kiểm luồng; unit test kiểm quy tắc; ONNX smoke kiểm model load. Không phép nào thay độ chính xác, độ trễ đầu-cuối hoặc kiểm nguồn/còi thật.

**Topology hiện tại đã chọn:** ESP32 → backend/AI trên Oracle; laptop chỉ mở
web. Các hướng dẫn “laptop inference” phía dưới thuộc baseline cũ; đo capacity
và AI trên Oracle đang triển khai, đo hiển thị trên browser người xem.

## 1. Điều kiện và mục tiêu nghiệm thu

Camera cố định, VGA/JPEG quality 12, ánh sáng trong nhà, đường tiếp cận A → B, không đeo vật che mặt trong phần nhận diện chính. Cố ý test điều kiện không đủ mặt/chiều ở phần warning. Ghi model manifest/hash, camera profile/hash, threshold, quy mô gallery, OS/Python/dependency, CPU/RAM, Wi-Fi, nguồn/board/module.

| Chỉ tiêu dự án | Mục tiêu G4 | Cách đo |
|---|---|---|
| Người đã đăng ký đúng tên | ≥90% lượt known holdout; không có tên sai | Số lượt info đúng / tất cả lượt known; báo thêm nhóm face-eligible |
| Người chưa đăng ký nhận nhầm thành quen | 0 trên holdout đã thu | Bất kỳ observation KNOWN sai trên lượt unknown; không chỉ event đã confirmed |
| Unknown incoming có alarm event | ≥90% nhóm face-eligible; báo cả tỷ lệ tất cả lượt | Alarm event recall; lượt thiếu mặt/direction vẫn nằm mẫu số tổng |
| Coverage chiều tiếp cận | ≥80% lượt known + unknown chính | Có track INCOMING / tất cả lượt tiếp cận |
| Known/ngang/xa/ra ngoài/empty/B trực tiếp | 0 alarm không hợp lệ | Báo số alarm / từng nhóm và toàn bộ nhóm negative |
| Unresolved hiện diện đủ lâu | Có warning, không còi | Đủ warning dwell theo config; tách chưa rõ chiều/mặt/geometry |
| Quy mô | Mục tiêu 20 người, hai người đồng thời | Limit đã đo theo report; chưa đủ người thì chưa ghi validated 20 |
| CPU inference | p95 ≤700 ms là mục tiêu ban đầu | Tool offline, máy demo; điều chỉnh scope có evidence nếu không đạt |
| Đầu-cuối còi | p95 ≤4 s là mục tiêu ban đầu | Bắt đầu lượt đứng B có mặt đủ điều kiện → buzzer thực ON; video đo thời gian |
| Safety | Mọi test deadline/STOP/reboot pass | HIL; còi không vượt hard cap, không phát lại duplicate |
| Chạy bền | 30 phút không reset nguồn/heap tăng liên tục | Board heap/reset log, server RSS/queue/frame age, ảnh/còi |

Đây là mục tiêu cho BTL, không kết quả đã đạt và không chuẩn chứng nhận sản phẩm. Không đổi mục tiêu sau khi thấy holdout để báo pass. Nếu phải giới hạn phạm vi, ghi quyết định nhóm + report mới.

## 2. Thu dữ liệu đúng cách

### 2.1. Enrollment và chia phiên

1. Có đồng ý của người tham gia; dùng alias P01/P02… trong dataset/report, không dùng tên trong tên file.
2. Preview, chốt vị trí camera, zones và ánh sáng. Đảm bảo body center đi qua A/B và khuôn mặt ở B đủ pixel. Giữ bố trí này trong enrollment, validation và test.
3. Thu enrollment hai lượt, mỗi lượt 5 mẫu chất lượng. Commit luôn draft; calibration/sanity check xong mới activate riêng. Không đưa ảnh enrollment vào dataset đánh giá.
4. Thu **validation** ở lượt/buổi khác; dùng để chọn accept/reject/margin/quality. Người known giữ cùng danh tính; ảnh, lượt và pose phải mới.
5. Thu **test/holdout** ở phiên khác và giữ chưa chạy trong lúc tune. Unknown là người không có trong gallery; nên dùng danh tính unknown khác giữa validation/test khi có thể. Không chỉ chia các frame liền nhau của cùng video ra hai split.

Pilot 3–5 người known + vài người unknown để sửa bố trí. Để claim mục tiêu 20: đủ 20 người đã enrollment, từng người có ít nhất 3 lượt validation và 3 lượt test. Thu tối thiểu 30 lượt unknown từ nhiều người; thêm tối thiểu 50 lượt negative tổng, có known/ngang/xa/ra ngoài/empty/B trực tiếp. Mỗi kiểu chính cần nhiều lần, hai người chéo/che nhau ít nhất 10 lượt riêng để review association và command thực. Bộ tối thiểu này còn nhỏ; **0/30 nhầm không bảo đảm tỷ lệ nhầm ngoài đời dưới 1%**.

Các lượt tiếp cận chính dù không thấy mặt vẫn giữ kind `known`/`unknown` và `face_eligible=false`; không đổi thành empty hay bỏ khỏi report. Kind `unidentified` dành cho bài thử chủ động che/quay mặt để kiểm warning. Eligible do người annotate trước inference, dựa ảnh rõ, đủ kích thước, không che và đi đúng vùng; lưu lý do/biên bản riêng. Báo cả tổng và eligible để thấy coverage.

### 2.2. Ghi một lượt từ board

Tool tạo viewer lease có xác thực và tự gia hạn, chỉ chấp nhận runtime hardware
+ board online. Ghi 5–300 giây; actor chờ video bắt đầu, đi từ A vào B rồi giữ
vài giây. Raw JPEG lưu riêng trong `data/` ignored, không phải enrollment storage.

```bash
python -m tools.record_visit --directory data/camera-v1 --participant-alias P01 --split validation --kind known --expected-person-id UUID --face-eligible --seconds 15
python -m tools.record_visit --directory data/camera-v1 --participant-alias U01 --split validation --kind unknown --face-eligible --seconds 15
python -m tools.record_visit --directory data/camera-v1 --participant-alias U02 --split test --kind direct-b --seconds 15
```

Dùng Python trong venv; tool hỏi mật khẩu, không in mật khẩu. Oracle thêm
`--server https://YOUR_HOSTNAME`. Dừng giám sát trước nếu ghi lượt đánh giá;
hết lượt tool release viewer và logout, không can thiệp phiên enrollment khác.
Ngắt/lỗi mạng thì lease tự hết; bỏ folder dở chưa có manifest entry.

### 2.3. Video/AI độc lập — kiểm chứng bản 31/10

Phần mềm: `tests/test_continuous_video.py`, `test_worker_boundaries.py`,
`test_dashboard_stream.py`, `test_preview_boundaries.py`, Node lifecycle tests
và `tests/firmware/video_transport_test.cpp`. Test dùng ảnh/observation giả,
không báo thành HIL hoặc accuracy. `python -m tools.smoke_integration` dùng
server TCP thật, WebSocket binary fragmented, cửa sổ hai ACK, MJPEG, oversize
1009 và từ chối anonymous, rồi kiểm event/ACK giả lập.

HIL cần ghi core3.3.12, WebSockets2.7.2, nguồn và mạch đầy đủ; theo dõi Serial
`VIDEO_CAPTURE`, `FB_NULL`, `VIDEO_ACK_TIMEOUT`, `VIDEO_DISCONNECTED_OR_TLS_ERROR`,
`TLS_WAIT_FOR_VALID_CLOCK_AND_CA` và status.device_telemetry. Các phép thử bắt buộc:

Chẩn đoán mất ảnh khi nối PIR (`BOOT_DIAG build=pir-net-diag-1`): firmware ghi
`NET_DIAG`/`VIDEO_DIAG` mỗi 5 s, `SYNC_DIAG` sau response tối đa một lần/5 s.
`pir` là mức GPIO13 hiện tại; `pir_sent` là mức lúc gửi sync; `rssi_dbm=0` khi
Wi-Fi chưa kết nối, không phải phép đo sóng hợp lệ. `control_age_ms` và
`video_lease_ms` cho biết tuổi sync và thời gian lease còn lại. `sent` chỉ là
số frame đã ghi vào transport; `acked` mới đếm ACK hợp lệ. `VIDEO_SEND_FAIL`
ghi thời gian gửi. Nhãn `CONTROL_EXPIRED`, `VIDEO_LEASE_EXPIRED`, `WIFI_DOWN`,
`ACK_TIMEOUT`, `RX_UNEXPECTED_ACK`, `RX_INVALID_JSON`, `SEND_FAILED` chỉ ra
nhánh đóng kết nối; chúng chưa tự chứng minh lỗi nguồn hoặc nguyên nhân gốc.
Log mới không chứa token, SSID, JPEG hay nội dung response. Không đổi timeout,
chân, nhịp chụp, TLS hoặc policy còi. Serial có thể tác động timing: chưa dùng
bản chẩn đoán làm số đo hiệu năng nghiệm thu.

HIL bổ sung cho bản chẩn đoán (chưa chạy trên board trong môi trường agent):
giữ vị trí camera, nguồn, tab video và dây cố định; lấy log 2 phút với PIR nối
đầy đủ. Xác nhận marker boot đúng bản. Nếu vẫn lỗi, đối chiếu nguyên nhân đóng
với RSSI, mức PIR, sync RTT và ACK trước khi quyết định phép thử kế tiếp.
Trước khi nghiệm thu firmware vẫn phải chạy H01/H05/H08/H09, kiểm còi OFF theo
deadline khi mất control kể cả lúc log/video bận; host test không thay HIL.

- DISARMED mở trang có video, không chạy AI giám sát; đóng trang thì nguồn dừng
  sau viewer TTL. ARMED đóng browser vẫn nhận diện/điều khiển; không phụ thuộc laptop.
- Video nhận khoảng8 FPS là mục tiêu thử, không bắt AI chạy8 FPS. Đo received_fps,
  FPS hiển thị và AI effective_fps riêng. Monitoring interval300ms cần khảo sát
  T95≤210ms theo ngân sách bận70%; đây là target mới, không kết quả đã đạt.
- Enrollment1Hz: mặt giữa ellipse ở vị trí ngoài B vẫn hợp lệ; nhiều mặt bị loại;
  hidden/cancel/đổi người/đổi lượt trong inference không thêm nhầm mẫu. Đủ lượt
  dừng extraction, video tiếp tục; kiểm diversity thật và identity consistency.
- Ngày/đêm/cảnh nhiều chi tiết, một/hai người, đi nhanh, Wi-Fi jitter/mất mạng,
  1–3 viewer/slow viewer; không phát lại backlog khi reconnect. Chạy bền30phút.
- Sai CA/hostname, clock không hợp lệ, token sai, mất ACK, upload kẹt: video fail
  rõ ràng; còi OFF theo STOP/control loss/hard cap độc lập truyền ảnh. Không dùng
  `setInsecure` để vượt lỗi HTTPS.

Tuổi ảnh ở UI là ước lượng server+timestamp cảm biến; sync clock anchor lấy offset
nhỏ nhất trong60sync để bổ sung delay có thể đo. Offset vẫn có sai số bằng độ trễ
request sync nhỏ nhất: không gọi đây là phép đo exposure→paint chính xác. Dùng
thử vật lý/clock được hiệu chỉnh khi nghiệm thu p95 đầu-cuối video dưới1giây.

Thời gian recorder là thời điểm nhận ảnh ở client, xấp xỉ cadence quan sát. Không dùng nó như timestamp exposure hoặc độ trễ còi vật lý. Quan sát quá ít/ảnh mất cần ghi vào biên bản; không chỉ giữ các lượt đẹp. Tool không tự xác nhận consent hoặc nhãn đúng; người nhóm review chúng.

Recorder CLI chờ luồng có bốn frame khác nhau trong hai giây trước khi đếm ngược
và tính thời gian lượt; warmup/đếm ngược vẫn gia hạn viewer lease. Nếu khởi động
quá 20 giây chưa có luồng ổn định thì chưa bắt đầu lượt. Mỗi clip đã bắt đầu có
`capture-health.json`; clip thiếu số ảnh hoặc chỉ phủ dưới 80% thời gian tại
client không được thêm manifest, ảnh lỗi vẫn giữ riêng để chẩn đoán.
Ảnh và timestamp của lượt thu thiếu được giữ trong `visit.partial.json`, không
được đưa vào manifest chính như một lượt thu đạt. Ghi cả số
lượt thu thất bại vào báo cáo reliability; không bỏ chúng để nâng accuracy hoặc
gọi những lượt còn lại là holdout thành công. Coverage của recorder không chứng
minh đủ ảnh mặt rõ, đúng geometry hay đáp ứng độ trễ còi.

`data/camera-v1/visits.jsonl`: mỗi dòng một object. Ví dụ schema (các path phải là ảnh JPEG thực, duy nhất; không copy ảnh để tăng N):

```json
{"visit_id":"visit-001","participant_alias":"P01","split":"validation","kind":"known","expected_person_id":"UUID-gallery","face_eligible":true,"frames":[{"path":"visit-001/0000.jpg","time_ms":0},{"path":"visit-001/0001.jpg","time_ms":500}]}
```

Có thể đổi kind thành `unknown`, `unidentified`, `passerby`, `far`, `outgoing`, `direct-b`, `empty`; chỉ known có expected_person_id, kind khác là null/không có. Mỗi lượt ít nhất 2 frame với thời gian tăng nghiêm ngặt; thực tế phải đủ chuỗi A/B/warning. Chỉ frame path dưới folder dataset, không path ngoài/URL. Tool đánh giá kiểm path được dùng một lần; **không tự phát hiện mọi ảnh gần trùng hoặc rò phiên**. Người nhóm chịu trách nhiệm tách phiên và kiểm nội dung.

## 3. Hiệu chỉnh và freeze profile

Bản quality/UNKNOWN 02/11: giám sát blur6/confidence0.80, enrollment60/0.90,
UNKNOWN2ảnh/400ms, KNOWN và gate còi giữ nguyên. Trước review profile mới, kiểm
người quen/khách thật cùng đường A→B với trace metadata; báo riêng false UNKNOWN,
coverage khi thiếu body, số phiếu/span, event/command/ACK và còi. Test boundary ở
`test_unknown_upgrade.py` và replay ảnh đại diện không thay accuracy/HIL. Không
dùng calibration cũ hoặc thay hash của nó để ARM sau đổi quality/temporal.

1. Dừng server; giữ hardware config/runtime đúng. Download + smoke model. Draft gallery đã commit có thể dùng evaluation; không cần bỏ calibration gate để thu dữ liệu.
2. Chạy validation:

```bash
python -m tools.evaluate --manifest data/camera-v1/visits.jsonl --split validation --report experiments/private/validation-01.json
```

Tool acquire runtime lock, dùng committed gallery gồm drafts, reset tracker/policy mỗi lượt, chạy CPU trên JPEG thật và ghi report mới. Không điều khiển board, không đổi calibration. Không ghi đè report trước. Dùng tất cả drafts thuộc gallery muốn đo; dọn draft không liên quan hoặc dùng runtime evaluation riêng có consent.

3. Xem số đếm/mẫu số/null khi N=0; wrong identity, unknown accepted, alarm recall, direction coverage, warning, per-kind và p50/p95 CPU. Report gồm config/hash/máy/gallery, content hash ảnh đã đọc; không gồm raw face/vector. Kiểm ảnh lỗi/association thủ công. Matching score không phải xác suất.
4. Thay một nhóm tham số trong `configs/local.yaml` mỗi vòng; report tên mới. Thêm margin khi hai người dễ lẫn; kiểm cả UNKNOWN/UNCERTAIN/NO_FACE, đừng chỉ tối đa known recall. Hình học xấu thì sửa bố trí và thu lại, không hạ face gate để có nhiều phiếu.
5. Freeze config/model/camera/gallery và quyết định nhóm. Chạy test một lần trên bộ chưa dùng tune:

```bash
python -m tools.evaluate --manifest data/camera-v1/visits.jsonl --split test --report experiments/private/holdout-01.json
python -m tools.manage doctor
python -m tools.manage calibration-check
```

6. Nếu fail, giữ report fail, sửa theo validation và thu holdout mới. Không tune theo chính holdout rồi gọi nó independent test nữa.
7. Reviewer khác owner AI duyệt report + HIL. Copy `configs/calibration.seed.json` → `configs/calibration.local.json`, ghi `calibration_id`, state `validated`, **exact hashes từ tool**, model_set_id, path report tồn tại, reviewer, metrics và limit gallery thực đã đo. Seed values không đủ để làm hồ sơ validated.

Khung hồ sơ (placeholder phải thay bằng bằng chứng thật):

```json
{
  "calibration_id":"camera-v1-reviewed-YYYYMMDD",
  "state":"validated",
  "model_set_id":"opencv-zoo-cpu-v1",
  "camera_profile_hash":"FROM-DOCTOR",
  "model_manifest_sha256":"FROM-CALIBRATION-CHECK",
  "validated_gallery_limit":5,
  "validation_report":"experiments/private/holdout-01.json",
  "reviewer":"alias reviewer",
  "metrics":{"report_reviewed":true,"hil_report":"private path"}
}
```

Đây là hành động reviewer, tool không ký/chứng minh nội dung report. Backend chỉ kiểm structural gate, version/hash/limit/path, **không tự đọc report để chứng nhận mục tiêu đã đạt**. Restart server để load profile → `calibration-check` không lỗi → activate draft people đúng limit → arm chủ động. Khi camera/zone/quality/policy/model đổi, hash cũ bị chặn. Thêm người trong limit vẫn sanity check và kiểm nhầm với gallery; vượt limit cần đo lại.

## 4. HIL và lỗi cần thử

Ghi setup/version/video/log đã loại token; mỗi test PASS/FAIL/chưa làm, actual timing/ACK và reviewer. Không test bằng cách cấp điện sai.

| ID | Thử | Kết quả bắt buộc |
|---|---|---|
| H01 | Boot/reboot board và server | Còi OFF, session mới, server DISARMED; không replay START cũ |
| H02 | START bình thường | Đúng command ID/generation, ACK; ON duration không vượt hard cap thực |
| H03 | Duplicate START/sync retry | Không kéo dài ON/deadline; TTL retry giảm, ACK không tạo thêm START |
| H04 | STOP rồi command cũ/ACK trễ | OFF; generation cũ không hồi sinh ON; command state đúng |
| H05 | Mất Wi-Fi, tắt laptop hoặc sync timeout | OFF theo deadline/control cap dù JPEG request kẹt; không buffer/replay ảnh |
| H06 | Disarm, enrollment, cancel hoặc gallery delete trong inference | OFF; ảnh/result revision cũ không có alert mới |
| H07 | TEST khi DISARMED/ARMED/enrollment | DISARMED test tối đa test cap; các trạng thái khác từ chối |
| H08 | Cấp nguồn/camera/PIR | VGA640×480 JPEG, PSRAM thật; warm-up PIR không gây alarm; không brownout/reboot |
| H09 | Upload lỗi/quá lớn/bad JPEG/seq cũ; ARM sau idle dài | Reject bounded; buffer cũ bị bỏ theo timestamp driver; còi/control không bị upload giữ vô hạn |
| H10 | Queue ACK/command đầy, control rehello cùng session | OFF nếu overflow; generation giữ cùng session; không phát lại START |
| H11 | Mất tracker/che nhau/hơn hai người | Reset/uncertain/warning phù hợp, không mượn known/direction người khác |
| H12 | Cạn media quota/không ghi DB | Quota dọn evidence; DB fail DISARMED; không START chưa persist |
| H13 | Một known đứng, unknown mới đến; hai khách sát cooldown | Từng lượt log riêng; suppression rõ, không còi muộn sau người đi |
| H14 | Auth và xóa dữ liệu | Device không vào admin/media; CSRF đúng; delete purge face/evidence/cache |
| H15 | Backup/restore vào runtime mới | Checksums pass, reprovision token/password, DISARMED, không replay command |

Kiểm UI streaming bổ sung 31/10: preview và enrollment phải cập nhật bằng một
MJPEG connection cho khung đang xem, không poll JPEG song song; A/B, oval và bbox
khớp frame ID. Thử cancel/đổi lượt, hết lease, mất ảnh, ẩn tab/logout và reconnect;
không được hiện lại ảnh revision cũ. `tests/test_dashboard_stream.py` chạy test
parser/lifecycle Node và metadata/slots backend; browser fixture sinh JPEG đã
kiểm render, chưa thay cho HIL camera thật hoặc benchmark 4G/nhiều viewer.

Mất control 5 s không cho còi kêu 5 s nếu hard cap 3 s ngắn hơn: cái đến trước đưa OFF. Độ trễ mạng/server có thể chặn arm hoặc làm warning; không sửa firmware bỏ deadline để “demo trơn”.

Backup khi server dừng:

```bash
python -m tools.manage backup --path backups/checkpoint-01
```

Restore vào **runtime mới** với `SSS_RUNTIME` đặt path riêng rồi chạy `restore --path backups/checkpoint-01`; sau đó bootstrap. Restore tự revoke session/command và đưa gallery về inactive; chỉ activate lại sau review consent. Trên PowerShell: `$env:SSS_RUNTIME="runtime/restored-hardware"`; Linux: `export SSS_RUNTIME=runtime/restored-hardware`. Kiểm profile/calibration/model/consent trước activate/arm. Backup không gồm credential, config hoặc weights; lưu các profile cần review riêng. Không restore người đã rút consent; xóa backup cũ sau full delete.

## 5. Script demo 5–7 phút

1. Cho thấy sơ đồ/camera thật, dashboard source hardware, calibration ID và trạng thái DISARMED; brief PIR chỉ tăng cadence.
2. Preview vùng A/B; người đi ngang/xa không gây alarm. Arm chủ động.
3. Known A → B: info đúng người, không còi. Người chưa đăng ký A → B: event, command ACK, còi tự hết. Giải thích một lần/lượt, cooldown.
4. Quay mặt/đứng B trực tiếp đủ lâu: warning cần kiểm tra, không gọi ngay người lạ. Known đứng, khách mới đến: lượt riêng.
5. Silence khác acknowledge, rồi disarm; demo enrollment hai lượt hoặc video đã ghi nếu thời gian ngắn.
6. Cho thấy một lỗi Wi-Fi/server bằng test đã kiểm trước, còi tự OFF; trình bày số đo/limitations.

Chuẩn bị nguồn/cáp dự phòng, laptop không sleep, IP LAN ổn định, máy đã cache model/package. Simulator là phương án trình bày luồng khi hardware hỏng, phải giữ banner và nói rõ chưa phải demo camera/AI thực.

## 6. Bằng chứng của gói bàn giao

`experiments/HANDOFF.md` và `handoff-checks.json` ghi kết quả **bản v2.1 hiện tại**.
Evidence v2 gốc chuyển vào `experiments/archive/v2/`; không dùng hash/kết quả cũ
để chứng nhận source đã sửa. Baseline cũ 49 test gốc pass; bộ tái hiện review ban
đầu có 19 case fail/2 case pass — đây là số case, không phải 19 lỗ hổng độc lập.
Các regression về sau bổ sung cho race inference, transaction, migration, auth và
notifier. Số test pass không phải chứng minh không còn bug.

Full Arduino CLI compile vẫn BLOCKED ở bước tải platform/index do DNS trong môi
trường review. Nhóm cần BASE-02 + HIL. C++ host tests không kiểm task scheduling,
GPIO, nguồn hoặc protocol parser trên ESP32. GitHub Actions và Windows chưa chạy.

### 6.1 Regression mới phải giữ

| Boundary | Test hoặc gate |
|---|---|
| Input config, duplicate tracks, direction/geometry | `test_review_regressions.py` |
| Disarm/gallery change sau khi inference đã bắt đầu | `test_worker_boundaries.py` với threading.Event |
| Capture timeout hoặc round change trong inference | `test_worker_boundaries.py`; không chỉ cancel pending |
| Command trước/sau transaction commit, migration rollback | `test_foundation.py`, `test_storage.py` |
| Same generation khác command, TEST trong preview | `test_foundation.py`, firmware host/HIL |
| Queue residence và frame trước mode barrier | Host AlarmLogic + H06/H09/H10 trên board |
| UI clear cache/logout, overlay/frame ID, tên known, mobile | BASE-05 manual; JS syntax không thay interaction test |

### 6.2 Giới hạn công cụ đánh giá hiện tại

`evaluate.py` là baseline single-actor recorded visits, không phải máy chấm đầy đủ
cho mixed/two-person scene. Các ca hai người/che nhau/capacity trong G4 phải có
annotation và review riêng theo H11/H13; muốn tự động hóa cần issue mở rộng schema
visit theo actor. Chưa đo throughput queue/replaced hay physical latency bằng tool
CPU offline. Dataset split theo buổi và consent vẫn cần người xác minh; path unique
không tự phát hiện các bản copy/ảnh gần trùng được đổi tên.

## 7. Giới hạn kỹ thuật đã biết — không giấu trong TODO

Đây là danh sách các giới hạn đã xác nhận tại thời điểm review v2.1. Không phải bug
mới hay điểm trừ; nhóm cần nắm để plan và báo cáo đúng phạm vi.

1. **Full ESP32 compile BLOCKED khi review:** đã thử CLI 1.2.2; tải package index/core
   bị DNS timeout. Đây không phải compile error đã xác nhận, cũng không phải PASS.
   BASE-02 là việc đầu tiên của A. Không nạp board trong phiên review.
2. **Chưa có ảnh người thật/holdout:** không chứng minh quality seed, đường A/B,
   nhận diện 20 người, tốc độ đi, face/body association hoặc chống giả mạo.
3. **UI chưa chạy trên browser thật:** phần sửa có syntax/API evidence; nhóm phải
   kiểm laptop/mobile và thao tác enrollment. Không gọi `node --check` là UI test.
4. **Hiệu năng chưa đo:** vẫn là SQLite + media I/O trong một số critical section;
   latency cần đo thực tế. Một worker pending một slot hạn chế backlog nhưng không
   tạo bảo đảm realtime.
5. **Tracker đơn giản:** IoU/center matching bảo thủ, không ReID; overlap hoặc miss
   có thể mất incoming. Chưa đổi thuật toán khi chưa có failure dataset thực.
6. **Enrollment consistency dùng độ giống sample:** không phải xác minh danh
   tính/liveness; admin kiểm người trước camera. Cần đo cùng session và duplicate
   person trên gallery thật; không coi cosine là xác suất.
7. **Evaluate tool một actor/lượt:** mixed scenes, label/split theo buổi, gần trùng,
   calibration reviewer và physical latency vẫn có phần kiểm thủ công.
8. **Dependency warning:** Starlette TestClient/httpx deprecation; suite chạy được.
   Không nâng toàn stack chỉ để hết warning trong review nghiệp vụ.
9. **Runtime cài từ checkout:** chưa claim wheel distribution, multi-worker, nhiều
   camera hoặc deploy Internet. Những thứ này không thuộc baseline BTL.
10. **Không đảm bảo "100% hết bug":** phạm vi review/test và bằng chứng được công bố
    để nhóm biết cần kiểm gì tiếp. Không bịa số AI/HIL để đánh dấu gate hoàn thành.

## 7. Gate nâng cấp day/night, PWA, cloud Level 3

Tất cả là **chưa được đo** ở thời điểm bàn giao staging. Thu evidence tự tạo sau HIL, tuyệt đối không điền pass giả.

1. Gắn camera tại vị trí demo; 3 điều kiện sáng đều/ngược sáng/thiếu sáng và 2 điều kiện đêm có/không illumination. Mỗi điều kiện có known, unknown, face obscured, empty và người đi ngang. Ghi tỷ lệ false `UNKNOWN` và chưa xác định, frame quality score, RTT, FPS.
2. Đèn cần thời gian ổn định trước chụp; thử cấp nguồn/chạm tải bật đèn, brownout, nhiệt, bảo vệ nước và ánh sáng phản xạ chói camera.
3. Remote test 4G phone + Tailscale, trang PWA HTTPS, stream ảnh 30 giây rồi reconnect, 3 client, logout; ảnh phải dừng nếu thiết bị disarmed và frame cũ.
4. Telegram: có ảnh và sự kiện đúng, link mở private dashboard, không chứa bearer/session secret, không gửi video; đo retry khi Telegram timeout. Đối chiếu `sent` (provider chấp nhận) với tin thực trên điện thoại khi web đóng, thử nút Gửi thử và lượt known/chưa xác định ngắn; hướng dẫn và các giới hạn trong [NOTIFICATIONS](NOTIFICATIONS.md).
5. Level 3 cloud: capture trên ESP32, gateway upload JPEG qua HTTPS, process trên server cloud khác máy, đối chiếu observation frame/event ids, DB cloud metadata và app phân tích số lượng theo reason/severity/day. Cần báo cáo số frame stale khi mạng xấu, tỷ lệ quyết định bị fail-closed; **không được tắt freshness để đủ demo**.
6. Vận hành mất Internet/mất cloud, mất nguồn camera, server restart, revoke credential, cloud token sai; còi phải OFF khi control lost; cloud failure không biến `UNCERTAIN` thành `UNKNOWN`.

Kết quả synthetic unit tests và simulator ghi riêng khỏi ảnh/accuracy/hardware evidence.
