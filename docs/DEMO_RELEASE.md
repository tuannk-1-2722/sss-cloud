# Release demo 03/11/2026

## Quyết định chốt 02/11

Operator xác nhận phần AI cải tiến đã ổn và yêu cầu: ngay khi hệ thống kết luận
**Không khớp người đã đăng ký**, phát lệnh còi, không thêm điều kiện vùng, chiều
đi, body hay dwell. Nhánh phát hành: `release/demo-20261103`, ghép `origin/main`
7bb308c chứa PWA78e1ca7 vào AI đã test (snapshot880a281).

Nhận diện đóng khung tại `configs/recognition.freeze.json`: model/vision/matching/
tracking/enrollment và hàm bỏ phiếu identity giữ nguyên. Giám sát blur6/conf0.80,
enrollment60/0.90, matching0.45/0.30/margin0.08; UNKNOWN2ảnh/400ms, KNOWN thường
3ảnh/600ms, strong-known2ảnh/500ms. Test freeze chặn thay đổi ngoài quyết định.

Flow `immediate-unknown-v1` phát `unknown_approach` ngay khi UNKNOWN confirmed
(giữ tên event cũ để tương thích). Alarm chỉ một lần/lượt, vào B không phát lặp;
khách mới không bị cooldown khách trước chặn. STOP/silence chủ động, COMMIT,
freshness/revision/session, TTL/hard OFF vẫn bảo vệ điều khiển. “Ngay” là tại
quyết định server; còi vật lý còn phụ thuộc vòng control/sync và ACK của ESP32.
Một ảnh UNKNOWN chưa đủ2/400 không phải kết luận danh tính đã xác nhận.

PWA/Web Push tích hợp cùng Telegram, migration004 + ledger005 theo từng máy.
Người dùng tự cấp quyền trên cả Android/iPhone; xem [PUSH_SETUP](PUSH_SETUP.md).
Nút thử PWA độc lập, text-only, không phát còi/Telegram. Lỗi push không giữ lock
AI/control và không làm STOP chờ provider. Signed image hết hạn/tắt ảnh fail-closed.

## Chuẩn bị trước khi mang đi

1. Giữ góc camera/ánh sáng/nguồn, gallery hiện tại. Không đổi model/ngưỡng trước
   demo. Xem UI camera online, AI ready và trạng thái ARMED.
2. Trên mỗi điện thoại mở app, đăng nhập, cấp quyền và bấm Thử PWA một lần. Xác
   nhận OS hiển thị thực tế; đăng ký server thành công chưa là nghiệm thu push.
3. Một người quen đã đăng ký: đúng danh tính, không alarm. Một người lạ thật:
   khi confirmed UNKNOWN, kiểm event → command/ACK → còi tự tắt; không cần đi A→B.
4. Giữ cùng một người lạ trước camera: không phát lặp trong lượt; thử STOP.
   Thử PWA/Telegram khi điện thoại khóa màn hình. Không lưu ảnh vào tài liệu Git.
5. Mang nguồn/cáp và mạng dự phòng. Sau restart server mặc định DISARMED theo
   thiết kế; bật lại giám sát có chủ đích. Đăng nhập lại nếu phiên cũ bị thu hồi.

## Kiểm chứng và giới hạn

Đã triển khai source release `624e79b` lên `https://btliot05.duckdns.org` lúc
22:28 ngày 02/11/2026 (giờ Việt Nam). Kiểm tra sau triển khai lúc 22:33: ARMED,
camera online, AI ready, analysis enabled; không có lỗi worker/push hoặc traceback
trong journal kể từ rollout. Gallery giữ nguyên 1 người active/10 mẫu. Push đã cấu
hình, chưa có điện thoại đăng ký tại thời điểm kiểm tra. HTTPS ảnh ký hợp lệ trả
JPEG/no-store; chữ ký sai và hết hạn trả 404.

Software checks `python -m tools.check --http`: Windows 250 pass/2 skip POSIX;
Linux 251 pass/1 skip Node không có trên máy kiểm tra. Ruff, HTTP/auth/video smoke,
host C++ và Node UI/service worker đều pass ở môi trường tương ứng. AI freeze giữ
nguyên snapshot 880a281. Sau rollout, operator đã xác nhận thông báo và nhận diện
hoạt động ổn; server cũng đã ghi nhận đăng ký Android và iPhone. Đây là xác nhận
vận hành của operator, không phải báo cáo accuracy/HIL. Chưa có xác nhận riêng
về độ trễ còi vật lý của rule mới hoặc thử tại mạng/địa điểm trường.

Phải báo riêng software checks, native replay, operator live test và physical
HIL/nhận trên hai điện thoại. Không gọi fixture synthetic, provider accepted,
hoặc13JPEG replay là accuracy/HIL100%. Kết quả và manifest triển khai được ghi
trong thư mục diagnostics riêng trên Oracle; ảnh/vector/credentials không upload
Git. Backup source/config/profile/DB chỉ giữ riêng trên VM.

Luồng A/B cũ trong các biên bản trước là lịch sử; quyết định immediate UNKNOWN
này là hành vi hiện tại. Khả năng nhận khẩu trang/20 người vẫn theo giới hạn dữ
liệu đã đo, không được nâng claim chỉ vì operator cho phép vận hành gallery20.
