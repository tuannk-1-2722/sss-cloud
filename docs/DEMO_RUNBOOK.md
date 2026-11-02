# Chuẩn bị và chạy demo tại cửa

**Phạm vi đã duyệt 02/11:** một hồ sơ 10 mẫu đã active trên Oracle, calibration
`doorstep-demo-v2-20261102-reviewed`. Clear nhận đúng; khẩu trang có thể chưa
xác định và không còi. User chấp nhận mục người lạ PASS; test còi 1 giây cơ bản đã
ACK/tự tắt. Trước buổi demo tải lại web, đăng nhập, kiểm device online/video rồi
chủ động bật giám sát. Các bước đăng ký/review bên dưới dùng khi thay đổi setup
hoặc chuẩn bị người mới; không cần đăng ký lại hồ sơ hiện tại.

Topology đang dùng: ESP32-CAM gửi ảnh và telemetry tới Oracle. Backend, AI,
gallery và lịch sử cùng ở VM; laptop/điện thoại mở dashboard. `inference=local`
nghĩa là AI chạy trong VM. Model giữ NanoDet/YuNet/SFace; không dùng engine thử
của nhánh dev. Video và đăng ký không tự bật giám sát hoặc còi.

## Trước buổi demo

1. Cấp nguồn ổn định, kiểm tra dây PIR/còi, lens và Wi-Fi/Internet tại trường.
   Giữ góc camera và ánh sáng đã kiểm chứng. Không đổi góc sau calibration mà
   tiếp tục dùng hồ sơ cũ.
2. Mở dashboard, đăng nhập, kiểm tra thiết bị online và video trên điện thoại
   lẫn máy trình chiếu. AI sẵn sàng không đồng nghĩa gallery/calibration đã đủ.
3. Nếu gallery rỗng: xin đồng ý của người tham gia, tạo hồ sơ ở **Người quen**,
   thu hai lượt mặt rõ, đổi nhẹ góc theo hướng dẫn và **Lưu mẫu**. Mẫu được lưu
   thành draft; chưa tự kích hoạt. Không dùng khẩu trang làm mẫu enrollment chính.
4. Chốt vùng A trên đường tiếp cận, B tại chỗ vặn khóa, recognition ROI bao phủ
   đoạn thấy mặt. Kiểm bằng bbox thực, không coi các hình chữ nhật seed là số đo.
   Thu validation theo [VALIDATION](VALIDATION.md), gồm clear/mask/hand/glasses,
   người lạ độc lập và các lượt negative. Không dùng ảnh validation làm gallery.
5. Freeze cấu hình/model/ngưỡng/gallery, kiểm holdout và HIL, review hồ sơ theo
   mục calibration của VALIDATION. Không sửa hash hoặc ghi `validated` chỉ để
   vượt lỗi `CAMERA_PROFILE_CHANGED`.
6. Sau review/restart, kích hoạt đúng hồ sơ trong giới hạn đã đo. Chủ động bấm
   **Bật giám sát**; kiểm một lượt known, một lượt unknown và STOP trước buổi demo.
   Kiểm lại sau khi đổi Wi-Fi hoặc khởi động lại board/server.

## Kịch bản trình diễn

| Lượt | Hành động | Kết quả cần đối chiếu |
|---|---|---|
| Người quen | Tiếp cận 4–5 giây, dừng vặn khóa 1–2 giây rồi đi | Đúng hồ sơ, lịch sử có lượt; không còi |
| Che mặt | Khẩu trang/tay che/kính, vẫn nhìn được mắt | Đúng hồ sơ khi đủ bằng chứng; nếu thiếu phải chưa xác định, không nhận nhầm |
| Người lạ | Người chưa đăng ký đi từ A tới B, thao tác khóa | Unknown/cảnh báo khi đủ mặt, chiều và dwell; đo ACK và còi thật |
| Negative | Đi ngang, đứng xa hoặc xuất hiện trực tiếp ở B | Không có alarm sai vì thiếu chiều tiếp cận hợp lệ |
| Rời camera | Kết thúc lượt ngắn, không còn quan sát | Có kết quả hoàn tất lượt; đo thời gian hiển thị sau khi rời ảnh |
| Dừng | Tắt còi lượt này hoặc tắt giám sát | Còi dừng theo ACK/deadline; không phát lại lệnh cũ |

Cho phép kết quả/cảnh báo hoàn tất khoảng 3–4 giây sau khi người rời ảnh theo
yêu cầu demo. Đây là mục tiêu cần đo, không phải cam kết suy ra từ test software.
Cooldown có thể chủ động chặn còi ở lượt liên tiếp; xem suppression trong lịch sử
và chờ hết cooldown cấu hình trước lượt cần minh họa còi. Không tắt cooldown.

## Khi có vấn đề

- **Không bật được giám sát:** đọc hướng dẫn trên banner; gallery rỗng cần đăng
  ký/kích hoạt, calibration đổi cần kiểm chứng/review. Video vẫn mở được.
- **Chưa xác định:** kiểm mặt đủ lớn/rõ, ánh sáng, góc quay và bố trí ROI; có thể
  ghi chẩn đoán hai phút để xem lý do. Không đổi ngưỡng tùy ý để ép nhận quen.
- **Mất mạng/khởi động lại:** chờ thiết bị online, đăng nhập lại nếu cần, xác nhận
  trạng thái DISARMED và chủ động bật lại khi đã sẵn sàng. Không giả định tự resume.
- **Cần khôi phục source:** dùng manifest/backup của lần triển khai trên Oracle;
  dừng service, khôi phục đúng file rồi restart/kiểm readiness. Không restore DB
  hoặc người đã xóa để giải quyết lỗi source.

Số đo/test software và giới hạn accuracy nằm trong
[RECOGNITION_UPGRADE](RECOGNITION_UPGRADE.md). Chỉ ghi PASS cho lượt thực đã chạy;
phần chưa thử tại trường phải giữ trạng thái chưa kiểm chứng.
