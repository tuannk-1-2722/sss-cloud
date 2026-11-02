# PWA cho demo 03/11 — Android và iPhone

Bản PWA/Web Push từ `main` được ghép với AI đã được operator test ổn. Cùng một
sự kiện có thể gửi tới cả hai điện thoại; mỗi thiết bị phải đăng ký riêng.
Không cần đăng ký lại khuôn mặt. Telegram tiếp tục hoạt động độc lập.

## 1. Cài và cấp quyền trên từng điện thoại

**Android (Chrome)**

1. Mở `https://btliot05.duckdns.org`, chọn menu Chrome → Cài đặt ứng dụng /
   Thêm vào Màn hình chính nếu trình duyệt cung cấp.
2. Mở app, đăng nhập. Bấm **Bật thông báo** → **Cho phép**.
3. Khi nút đổi thành **Tắt thông báo**, bấm **Thử thông báo PWA**.
4. Kiểm có thông báo Smart Security. Khóa màn hình và thực hiện một lượt camera
   thật để kiểm nhận tự động. Android có thể hiển thị ảnh bằng chứng.

**iPhone (Safari)**

1. Dùng Safari mở cùng URL → Chia sẻ → **Thêm vào Màn hình chính**.
2. Đóng tab Safari và **mở bằng biểu tượng vừa tạo**, rồi đăng nhập.
3. Bấm **Bật thông báo** trong app → **Cho phép**, sau đó **Thử thông báo PWA**.
4. Kiểm Cài đặt iPhone → Thông báo → Smart Security: cho phép trên màn hình khóa;
   tắt Focus/Không làm phiền trong buổi demo nếu cần.
5. Thông báo trên iPhone có thể chỉ hiện chữ; mở app → Lịch sử để xem ảnh.

Web Push trên iPhone cần Home Screen web app và quyền được xin từ thao tác của
người dùng, theo [WebKit](https://webkit.org/blog/13878/). Không có cách cấu hình
server thay cho thao tác Cho phép trên từng điện thoại. Khi quyền đã bị từ chối,
cần đổi trong cài đặt thiết bị/trình duyệt rồi mở lại app.

## 2. Kiểm server và thông báo

- `GET /api/v2/push/config` sau đăng nhập cho biết enabled/configured, số thiết
  bị và số outbox theo trạng thái; không trả khóa riêng hoặc endpoint thuê bao.
- Nút **Thử thông báo PWA** gọi admin+CSRF API, idempotent và giới hạn một lần/30s.
  Đây là thông báo chữ được đánh dấu `system_test`, không gọi còi hoặc Telegram.
- Các sự kiện camera thật giữ ảnh opt-in và URL ảnh được ký, TTL mặc định10phút.
  Sai/hết hạn chữ ký, ảnh đã xóa hoặc tắt ảnh/Push trả404; không cache ảnh/API.
- Ledger migration005 theo dõi từng thiết bị: máy đã nhận không bị gửi lại khi
  máy kia cần retry. `partial` nghĩa là chưa xác nhận đủ các máy. `sent` là push
  service chấp nhận, không chứng minh người đã đọc hoặc OS đã hiển thị.
- Crash giữa lúc gửi được đánh dấu `unknown`, không âm thầm gửi lại. VAPID key
  giữ ổn định qua restart; không tự xoay khóa trước demo.

## 3. Cấu hình vận hành đã chuẩn bị cho Oracle

Server dùng Python venv và source release đồng nhất; dependencies từ lockfile,
migrations004/005, VAPID private key và HMAC key chỉ nằm trong runtime/private.
Private hardware config bật push và ảnh; `SSS_VAPID_SUBJECT` là HTTPS URL của hệ
thống. Auth/CSRF của dashboard vẫn bắt buộc, ảnh thông báo chỉ có capability
link ngắn hạn. Không đưa key, DB, subscription endpoint hoặc ảnh thật vào Git.

Không `git pull` đè một runtime đang có source sửa riêng. Việc nâng cấp dùng
staging/backup/source hashes, kiểm software + native rồi mới rollout. Nếu cần
rollback binary sau migration005, giữ file migrations004/005 để runner nhận
ledger hiện có; không restore DB cũ đè gallery hoặc các sự kiện mới.

## 4. Khi chưa nhận được thông báo

1. Mở app đúng biểu tượng, đăng nhập lại nếu server vừa restart, tải lại app để
   nhận script/SW mới. Kiểm có nút **Tắt thông báo** trên chính máy đó.
2. Kiểm quyền thông báo, Focus/tiết kiệm pin, Internet điện thoại và số thiết bị
   đã đăng ký. Không dùng số đếm subscriber để kết luận điện thoại đã nhận.
3. Bấm Thử, xem thông báo thật và outbox; `partial/unknown/failed` phải kiểm riêng
   từng máy. Nếu chỉ một máy nhận, không xóa/đăng ký lại cả hai máy cùng lúc.
4. Telegram là kênh độc lập; PWA lỗi không được chặn nhận diện/STOP/còi.
