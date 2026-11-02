# Nhận diện lượt đến cửa ngắn — quyết định 01/11/2026

## Review demo lúc 15:47 ngày 02/11/2026

Đã chạy hai lượt test mới sau freeze với cùng model/profile/gallery. Mặt rõ có
`known_visit` đúng hồ sơ; khẩu trang kết thúc `UNCERTAIN`. Cả hai có chiều
INCOMING, không tên sai và không alarm. Độ phủ nhận đúng danh tính là **1/2**
(clear 1/1, mask 0/1), không được báo thành accuracy 100%. Ảnh tạm Oracle đã xóa.

Người dùng đã duyệt phạm vi demo: một người đã đăng ký, clear nhận đúng, mask
được phép KNOWN hoặc UNCERTAIN và không còi; mục người lạ PASS theo xác nhận
của người dùng. Người dùng cũng xác nhận test còi 1 giây kêu rồi tự tắt; server
ghi TEST_ALARM 1000 ms đã ACK. Đây là kiểm tra còi cơ bản, chưa phải full fault HIL.

Đã ghi profile `doorstep-demo-v2-20261102-reviewed` theo review này và kích hoạt
hồ sơ 10 mẫu qua workflow hiện có. Calibration-check không lỗi, model/service
ready, auth giữ nguyên; có backup profile cũ và report review trên Oracle.
Limit gallery **1 người**. Không đổi policy/model/threshold sau khi xem holdout.
Full fault HIL, hand/glasses riêng và latency vật lý chưa được đo trong review.

Server restart về DISARMED; operator tải lại/đăng nhập và chủ động bật giám sát.
Đổi góc camera/cấu hình/model hoặc thêm người vượt limit cần review lại.

---

## Cập nhật 02/11/2026 — xác nhận người quen bằng cặp ảnh mạnh

Người dùng yêu cầu tiếp tục tối ưu trên dữ liệu đã thu và chấp nhận mục người lạ
**PASS theo xác nhận của người dùng**. Các clip mang nhãn U01 thực tế là cùng
người đã đăng ký; đã sửa nhãn trong manifest riêng, giữ bản gốc. PASS này là
quyết định nghiệm thu của người dùng, không phải phép đo người lạ từ các clip.

Đã triển khai `early-evidence-v2` lúc **00:52 ngày 02/11** (giờ Việt Nam). Giữ
NanoDet/YuNet/SFace, bộ lọc mặt/blur/exposure, matching accept/reject/margin.
Thêm đường xác nhận KNOWN bằng hai ảnh có score cao hơn (seed 0.60), margin
seed 0.16 nếu có người thứ hai, span ít nhất500ms. Cả hai vẫn phải qua quality;
thiếu score, danh tính xung đột, UNKNOWN trong cửa sổ hoặc hết freshness không
được dùng đường rút gọn. Nhánh UNKNOWN vẫn cần3phiếu/600ms. Có thể tắt
`strong_known_enabled` để dùng đường xác nhận thường; thay đổi cần review profile.

So sánh native trên Oracle cùng model/gallery draft10mẫu, cùng8clip chẩn đoán:
khi tắt nhánh mới, hai lượt thu đủ clear/mask đều chưa xác nhận; khi bật, cả hai
đúng hồ sơ, không alarm. Sáu clip thu thiếu vẫn không xác nhận. Đây là validation
của một người, không phải holdout hoặc chứng nhận accuracy. Ảnh tạm Oracle đã xóa.

Kiểm tra software: local 158 test PASS; Linux 157 PASS/1 skip do thiếu Node, lint/format
và HTTP smoke PASS. Có regression cho score/margin/span, duplicate, thiếu evidence,
xung đột, UNKNOWN xen kẽ, hết hạn/reentry; worker STOP/revision tests vẫn đạt.

Hiện có một draft đã đăng ký,10mẫu committed; chưa active. Source cloud khớp bản
đã thử; model/service ready. Calibration giữ nguyên và vẫn chặn ARM/activate
bằng `CAMERA_PROFILE_CHANGED`. Cần lượt đối chứng mới sau freeze và review thực
tế trước ký calibration. Không tự khôi phục profile cũ hoặc coi validation là HIL.

---

## Quyết định và triển khai 01/11 (lịch sử)

Người dùng đã duyệt phương án nghiên cứu ngày 01/11/2026: cả lượt 4–5 giây,
dừng vặn khóa 1–2 giây; cho phép kết quả/còi sau khi rời ảnh khoảng 3–4 giây.
Giữ ESP32-CAM/Oracle, được chỉnh góc và ánh sáng. Demo 1–2 known, 2–3 unknown;
khẩu trang y tế, tay che một phần hoặc kính, mắt còn rõ. Ưu tiên tránh nhận nhầm,
cho phép UNCERTAIN. Đây là phạm vi demo, không chứng nhận gallery 20 người.

**Điều chỉnh đã chốt trong phiên:** người dùng xác nhận giữ model hiện tại và
chỉ hoàn thiện/kiểm thử luồng mới. Không tích hợp InsightFace vào source/dependency
của bản này; định dạng gallery SFace128 được giữ nguyên. Native candidate đã đo
trong thư mục thử riêng, không là backend AI trên web.

**Trạng thái triển khai:** theo yêu cầu tiếp theo, đã triển khai luồng mới từ
nhánh `feat/doorstep-visit-recognition` lên Oracle lúc **22:27 ngày 01/11/2026**
(giờ Việt Nam), sau kiểm tra local và Linux biệt lập. Source khớp bản kiểm tra,
model/service ready, admin API vẫn từ chối truy cập không đăng nhập; ESP32 đã
gửi sync trở lại. Có backup source để rollback.

Người dùng chưa thể chạy lại phần cứng trong phiên triển khai và chọn hoàn tất
software trước. Gallery vẫn rỗng sau khi người dùng tự xóa hồ sơ; không khôi phục
hoặc tạo mẫu. Calibration được giữ nguyên byte và báo `CAMERA_PROFILE_CHANGED`
với flow mới; server ở DISARMED, chưa cho bật giám sát. Video và thu draft enrollment
vẫn dùng được. Xem [DEMO_RUNBOOK](DEMO_RUNBOOK.md) cho các bước đưa vào demo thật.

## Thứ tự và gate

1. Trace metadata, đối chiếu server/board; pilot đúng hành động tại cửa.
2. Thu bằng chứng từ lúc tiếp cận; tách identity evidence khỏi việc vào B;
   kết thúc cả lượt ngắn với kết quả rõ ràng, giữ giới hạn an toàn và revision.
3. Giữ NanoDet/YuNet/SFace và ngưỡng/gallery hiện tại. Phần đổi engine dev tạm hoãn.
4. Kiểm ROI và validation riêng clear/mask/hand/glasses; chỉ reenroll nếu sau này
   thật sự đổi model/preprocessing hoặc bố trí cần làm lại mẫu.
5. Freeze model/preprocess/threshold/gallery; holdout và HIL trước kích hoạt
   giám sát hardware với hành vi mới. Chưa có số đo không được ghi profile validated.

## Trace hiện có

Sau đăng nhập, bấm **Ghi chẩn đoán 2 phút** trên trang giám sát; thao tác này chỉ
bật ghi số đo trong khoảng thời gian hữu hạn, không tự arm hoặc phát còi.
Sau đó bật giám sát chủ động và thực hiện từng lượt. Bấm lại sẽ bắt đầu cửa sổ
đo mới. Để kết thúc sớm dùng DELETE API chẩn đoán. Video/status vẫn hoạt động.

Trace có RAM ring hữu hạn và journal opt-in với tiền tố `RECOGNITION_TRACE`.
Gồm tuổi ảnh nguồn/server, queue và inference latency, quality/score số học,
vùng, số phiếu và track/candidate alias chỉ ổn định trong cửa sổ đo.
Không chứa JPEG, embedding, tên người, person_id, token, boot/session ID hoặc
reason tùy ý do client gửi. Điểm cosine không là xác suất. Alias không phải
định danh cá nhân dùng xuyên phiên. Tệp export/journal phải giữ ngoài Git.

Đo source age và server age giúp phân biệt buffer nguồn với transit/clock;
chưa đủ để kết luận Wi-Fi hay clock hỏng. Timeout receive và ACK được ghi
tách ở trace, vẫn giữ deadline và cách đóng uplink hiện tại.

Phải báo riêng software tests, native model smoke, accuracy camera và HIL.
Trace hoặc synthetic fixture không chứng minh nhận diện khẩu trang đạt.

## Thu/replay bộ thử riêng

`tools.record_visit` và `tools.record_pilot` yêu cầu `--consent-recorded` trước
khi gọi mạng hoặc lưu ảnh. Mỗi người tham gia phải đồng ý; alias trong filename/
manifest thay tên. `record_pilot` không arm, không sửa gallery, dùng một tài khoản
đăng nhập để thu reference/clear/mask; có đếm ngược và `--reference-only` nếu cần.
File nằm trong `data/` ignored. Bản nâng cấp giữ SFace nên không bắt buộc thu lại
reference để sử dụng gallery hiện tại.

Evaluator hỗ trợ condition, exit_time_ms và timing_basis; báo mẫu số theo điều
kiện, quality reason và thời điểm quyết định. `--scheduled` mô phỏng một inference
+ một pending, cadence, measured CPU cost và stale rejection; không coi tất cả
frame ghi được đều đi qua worker. Đây vẫn là replay, không đo mạng/còi vật lý.
Tham chiếu enrollment phải là split/lượt riêng, không lấy validation/holdout làm
gallery. Evidence metric chỉ là scalar, không xuất vector.

New policy/ROI đổi camera_profile_hash. Giữ gate calibration, không tự đổi hash
hoặc đánh dấu validated để bật giám sát. Holdout và HIL còn thiếu được ghi rõ.

## Kết quả và giới hạn kiểm chứng 01/11/2026

- `python -m tools.check --http` bằng venv repository: **138 tests pass**,
  Ruff lint/format pass, HTTP simulation/video/auth smoke pass. Có một cảnh báo
  Starlette TestClient/httpx đã tồn tại; không có test fail.
- Cùng source trên Linux Oracle biệt lập: **137 pass, 1 skip** vì không có Node;
  lint/format và HTTP smoke pass. **13 ca Node** stream/dashboard đạt trên local.
  Native smoke bằng venv production nạp/chạy model trên ảnh trống đạt; không là
  bằng chứng nhận diện người. Không cài thêm dependency vào venv production.
- API probe biệt lập với model thật/runtime production: khởi động DISARMED,
  calibration cũ chặn ARM, vẫn cấp viewer lease, không tạo lệnh alarm. Probe dùng
  runtime tạm riêng; không chỉnh gallery hoặc session production, không là HIL.
- Test boundary gồm evidence trước B, khe ROI, evidence hết hạn, reentry,
  xung đột danh tính, dwell B bị gián đoạn, completion khi camera im lặng,
  disarm và không dùng lại calibration cũ. Đây là kiểm chứng software.
- Replay camera với gallery hợp lệ trước khi bị xóa: hai lượt known validation,
  một lượt mặt rõ có kết quả đúng, lượt mang nhãn khẩu trang chưa xác nhận KNOWN;
  không có alarm trên hai lượt này. Mẫu quá ít và chưa có nhãn thời điểm rời ảnh
  chính xác để kết luận accuracy hoặc đạt độ trễ demo.
- Đoạn mang alias U01 là cùng người sau khi xóa hồ sơ, không phải người lạ độc
  lập. Không dùng để chứng minh tỷ lệ nhận nhầm hoặc nghiệm thu người lạ/còi.
  Evaluator từ chối gallery rỗng hoặc thiếu người known khai báo trong dataset.
- Chưa nghiệm thu holdout clear/mask/hand/glasses, người lạ độc lập, bố trí ROI,
  độ trễ sau khi rời ảnh và còi thật của luồng mới. Không thay model/ngưỡng hoặc
  ghi calibration validated từ các kết quả software/replay này.
