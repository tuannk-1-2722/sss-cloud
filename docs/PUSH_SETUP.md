# Hướng dẫn bật Web Push trên PWA (Oracle `btliot05.duckdns.org` hoặc Tailscale)

**Mục đích:** Điện thoại nhận thông báo đẩy (kèm ảnh bằng chứng trên Android) ngay cả khi khoá màn hình, hoàn toàn trên PWA của nhóm — không cần Telegram.

Vì hệ thống chính của nhóm (backend, AI, DB và ảnh từ ESP32-CAM) đã chạy trực tiếp trên máy chủ Oracle tại **`https://btliot05.duckdns.org`**, việc bật Web Push chỉ cần thực hiện trên máy chủ Oracle đó, sau đó mở web trên điện thoại bấm **Bật thông báo**.

> Đây là hướng dẫn cấu hình và kiểm tra, chưa phải báo cáo nghiệm thu HIL. Không đưa khoá `vapid_private.pem`, `push_media.key` hay file `service.env` vào Git hoặc ảnh chụp màn hình.

---

## 1. Cập nhật trên máy chủ Oracle (`btliot05.duckdns.org`)

SSH vào máy chủ Oracle (user `ubuntu`, thư mục project như trong `docs/LEVEL3_SETUP_GUIDE.md` mục 6–7):

### Bước 1.1: Pull code mới và cài thư viện `pywebpush`
```bash
cd ~/smart-security
git fetch origin
git checkout feat/doorstep-visit-recognition
git pull --ff-only origin feat/doorstep-visit-recognition
./.venv/bin/python -m pip install -r requirements.txt
```

### Bước 1.2: Sinh khoá VAPID (chỉ chạy 1 lần)
```bash
SSS_CONFIG=~/sss-private/local.yaml ./.venv/bin/python -m tools.manage push-keys
```
Lệnh này tự tạo `vapid_private.pem` trong thư mục `runtime_dir` của server và in ra `vapid_public_key`.

### Bước 1.3: Thêm biến môi trường vào `~/sss-private/service.env`
Mở `nano ~/sss-private/service.env` và đảm bảo có 3 dòng sau:
```ini
SSS_HTTPS=1
SSS_DASHBOARD_URL=https://btliot05.duckdns.org
SSS_VAPID_SUBJECT=mailto:huyquang2k5@gmail.com
```

### Bước 1.4: Bật `push_enabled` trong `~/sss-private/local.yaml`
Mở `nano ~/sss-private/local.yaml`, trong mục `notifications:` thêm `push_enabled: true`:
```yaml
notifications:
  push_enabled: true
  push_include_image: true
  push_image_ttl_s: 600
```
*(Nếu muốn nhận luôn thông báo kèm ảnh cho cả người quen ghé cửa và lượt chưa xác định khi kết thúc lượt, giữ `notify_known_visits: true` và `notify_unresolved_visits: true` như hiện tại).*

### Bước 1.5: Khởi động lại service
```bash
sudo systemctl restart smart-security
sudo systemctl status smart-security --no-pager
```
*(Lưu ý: Caddy trên Oracle đang `reverse_proxy 127.0.0.1:8000` cho toàn bộ domain nên tự động chuyển tiếp cả `/api/v2/push/*` và `/push-media/*`, không cần sửa Caddyfile).*

---

## 2. Bật thông báo trên điện thoại

1. Mở **`https://btliot05.duckdns.org`** bằng **Chrome** (Android) hoặc **Safari** (iPhone).
   - **Nếu dùng iPhone (iOS ≥ 16.4):** bấm nút **Chia sẻ** → **Thêm vào Màn hình chính** (Add to Home Screen), sau đó mở ứng dụng từ icon ngoài màn hình chính.
   - **Nếu dùng Android Chrome:** có thể bấm menu `⋮` → **Cài đặt ứng dụng** (Install app) để cài như app thật, hoặc dùng trực tiếp trên Chrome.
2. Đăng nhập tài khoản quản trị (`admin`).
3. Ở góc trên cùng bên phải (cạnh nút **Đăng xuất**), bấm nút **`Bật thông báo`** → chọn **Cho phép** (Allow) khi trình duyệt hỏi quyền.
4. Khi thành công, nút sẽ đổi thành **`Tắt thông báo`**.

---

## 3. Kiểm tra nghiệm thu

1. **Tạo sự kiện:** Bật giám sát (`ARMED`) và đi vào vùng cửa trước camera (hoặc chờ hết lượt để tạo sự kiện `Ket qua luot vua ket thuc`).
2. **Trên điện thoại (đang khoá màn hình):**
   - Nhận được thông báo đẩy của **Smart Security**.
   - **Android:** hiện luôn ảnh chụp từ camera ngay trong thông báo.
   - **iPhone:** hiện tiêu đề và thời gian (iOS Web Push chưa hỗ trợ trường `image`), bấm vào thông báo sẽ mở thẳng ứng dụng để xem ảnh trong mục **Lịch sử**.
3. **Kiểm tra an toàn link ảnh (`/push-media/{media_id}?exp=...&sig=...`):**
   - Link ảnh không cần cookie để hệ điều hành tải được ảnh thông báo, nhưng được ký HMAC-SHA256 và tự hết hạn sau `push_image_ttl_s` (mặc định 10 phút).
   - Sau khi hết hạn hoặc nếu sửa 1 ký tự trong `sig`/`exp`, server trả về `404 MEDIA_NOT_FOUND`.
   - Khi đặt `push_enabled: false`, toàn bộ route `/push-media/*` và `/api/v2/push/subscribe` tự động đóng (fail-closed).

---

## 4. Xử lý lỗi thường gặp

| Hiện tượng | Cách kiểm tra & khắc phục |
|---|---|
| Không thấy nút **Bật thông báo** sau khi đăng nhập | 1) Bấm `Ctrl+F5` hoặc xoá cache trình duyệt để tải `app.js` mới.<br>2) Kiểm tra `GET /api/v2/push/config`: nếu `enabled: false` là chưa sửa `local.yaml`; nếu `configured: false` là thiếu `SSS_DASHBOARD_URL`, `SSS_VAPID_SUBJECT` hoặc chưa chạy `push-keys`.<br>3) Trên iPhone bắt buộc phải mở từ icon ngoài Màn hình chính. |
| Có thông báo chữ nhưng không hiện ảnh | Trên iPhone là giới hạn của iOS. Trên Android: kiểm tra `push_include_image: true` và sự kiện trong tab **Lịch sử** có ảnh bằng chứng hay không. |
| Đổi tên miền hoặc tạo lại khoá VAPID | Trên điện thoại bấm `Tắt thông báo` rồi bấm lại `Bật thông báo` để đăng ký lại với khoá mới. |
