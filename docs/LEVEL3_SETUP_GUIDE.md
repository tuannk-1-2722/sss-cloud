# Setup IoT Level 3: Oracle VM + DuckDNS + HTTPS + ESP32

Ngày: 30/10/2026. Hiện trạng mới nhất: Oracle instance E5 đã chạy, hostname đã đăng ký là `btliot05.duckdns.org`; đã có R2 và Workers Free. Các phần tạo A1/DuckDNS bên dưới là hướng dẫn ban đầu để tham khảo, không yêu cầu tạo lại tài nguyên đang dùng.

**Nâng cấp video 31/10:** dùng backend và firmware cùng phiên bản. Backend cài
requirements mới (có websockets16.0), chạy `python -m tools.serve` thay lệnh uvicorn
cũ để áp giới hạn WS. Caddy vẫn reverse_proxy cùng origin, không thêm CDN/cache
cho camera. Cập nhật ExecStart systemd tương ứng, restart backend rồi Ctrl+F5 web.
Firmware core3.3.12/WebSockets2.7.2 có TLS CA/time validation; `SERVER_URL` là
`https://btliot05.duckdns.org`, không đặt token trên URL. Giữ SSID/token trong
secrets.h cục bộ; không upload secrets/runtime/gallery bằng gói source.

Build bằng Arduino CLI: `arduino-cli compile --profile ai_thinker firmware/security_node`.
Nếu ổC thiếu dung lượng, đặt data/downloads trong thư mục build ignored trên ổD
bằng config CLI riêng, rồi thêm `--config-file build/arduino-cli.yaml` cho compile/
upload. Không cần Arduino IDE. Backend mới dùng video demand; chỉ nạp firmware
sau khi backend mới đã sẵn sàng để tránh producer/consumer lệch phiên bản.

**Cập nhật sau khi tạo máy:** đã kiểm tra Console: instance `sss` Running, Ubuntu 24.04, shape `VM.Standard.E5.Flex`, 1 OCPU/12 GB RAM, user SSH `ubuntu`. Máy này dùng x86_64, không phải ARM64. Phần tạo A1 bên dưới được giữ để tham khảo phương án Always Free; với máy hiện tại, bắt đầu từ bước 4 và dùng private key tương ứng instance E5 đã tạo.

**Cổng kiểm tra trước khi cài:** lần kiểm tra mới nhất SSH bị timeout. Console cho thấy rule SSH22 vẫn giới hạn public IP quản trị cũ, khác public IPv4 hiện tại của máy Windows. Chạy `curl.exe -4 https://api.ipify.org` trên Windows rồi sửa Source CIDR của chính rule TCP22 thành kết quả đó kèm `/32`; không mở mọi nguồn chỉ để vượt lỗi. Giữ nguyên 80/443 và các rule khác. Khi mạng nhà đổi IP, cập nhật lại rule này.

**DNS cần sửa trước HTTPS:** lần kiểm tra mới nhất `btliot05.duckdns.org` chưa trỏ đến public IPv4 của instance trong Console. Trong DuckDNS, đặt Current IP đúng IP của VM (không phải IP mạng nhà), cập nhật rồi kiểm `Resolve-DnsName btliot05.duckdns.org -Type A`. Không xin certificate trước khi A record đúng và không có AAAA trỏ sai. Trong các ví dụ `YOUR_HOSTNAME` bên dưới, dùng `btliot05.duckdns.org`.

Hai địa chỉ khác vai trò: Source CIDR của SSH là public IP mạng quản trị; Current IP của DuckDNS là public IP VM. Sửa DNS không giải quyết việc SSH theo IP bị chặn.

E5 không thuộc danh sách Always Free compute; trial credit có thể chi trả khi còn hiệu lực và đủ điều kiện. Theo dõi credit/cost trong Console, không coi E5 là máy miễn phí lâu dài. [Thông tin Free Tier](https://docs.oracle.com/en-us/iaas/Content/FreeTier/freetier.htm).

Với key Oracle đã tải, mở PowerShell và đặt biến theo **đúng file private key của máy E5**, không dùng file `.pub`:

```powershell
Get-ChildItem "$env:USERPROFILE\Downloads" -Filter "ssh-key*.key"
$sshKey = "$env:USERPROFILE\Downloads\ssh-key-2026-10-30 (1).key"
ssh -i "$sshKey" ubuntu@VM_PUBLIC_IP
```

Tên key ở trên đã được xác minh trên máy Windows của bạn; file đã được sửa ACL để chỉ owner OS truy cập. Thay `VM_PUBLIC_IP` bằng địa chỉ hiện trong Console trước khi chạy. Nếu key được chuyển sang thư mục khác thì sửa đường dẫn tương ứng. Các ví dụ cũ dùng `sss_oracle` chỉ áp dụng nếu VM đã được tạo bằng public key của cặp key đó; máy tạo bằng key Oracle phải dùng key tương ứng. Không gửi private key vào chat.

Hướng dẫn này dựng **toàn ứng dụng hiện tại trên VM**, không dùng laptop làm server ở đích cuối. Các lệnh được đối chiếu source; chưa được chạy trên VM của bạn. Những đoạn cấu hình dưới đây là mẫu để bạn áp dụng, không phải tài nguyên đã được tạo.

## 1. Thứ tự và những gì cần chuẩn bị

| Chuẩn bị | Cần khi nào | Kết quả cần có |
|---|---|---|
| Oracle account hoạt động | Đang chờ | Đăng nhập được Console, thấy tenancy và home region |
| SSH key riêng cho project | Làm ngay trên Windows | Private key giữ trên máy, public key đưa vào VM |
| VM Ubuntu 24.04 ARM64 | Sau Oracle kích hoạt | A1, khoảng 2 OCPU và 6–12 GB RAM, boot disk trong quota miễn phí |
| Hostname miễn phí DuckDNS | Có thể đăng ký ngay | Ví dụ tên tự chọn `sss-nhom3-xxxx.duckdns.org` |
| Source sạch | Làm ngay | Archive không có secrets, runtime, DB, dataset, ảnh, embedding hoặc venv |
| Bộ phần cứng hiện có | Khi nối board | ESP32-CAM AI-Thinker/PSRAM, bộ nạp, nguồn ổn, PIR, còi, Wi-Fi 2,4 GHz |
| Người tham gia và quyền xử lý ảnh | Trước enrollment | Biết rằng gallery/evidence sẽ nằm trên cloud VM |
| Điện thoại 4G | Nghiệm thu | Truy cập dashboard ngoài Wi-Fi của board |

Chưa cần mua domain, nâng Workers Paid, thêm Firestore/GCS, Docker hoặc MQTT. R2 tích hợp sau khi camera→cloud chạy ổn.

Các ký hiệu phải thay: `VM_PUBLIC_IP` là public IPv4 của VM; `YOUR_HOSTNAME` là hostname DuckDNS thực; `COM_PORT` là cổng nạp board. Không copy nguyên placeholder vào cấu hình thật.

## 2. Làm ngay trong lúc chờ Oracle

### 2.1 Tạo SSH key trên Windows

Mở PowerShell. Máy hiện tại đã có `ssh`, `scp`, `ssh-keygen` và `tar`.

```powershell
New-Item -ItemType Directory -Force "$env:USERPROFILE\.ssh" | Out-Null
ssh-keygen -t ed25519 -f "$env:USERPROFILE\.ssh\sss_oracle" -C "smart-security-cloud"
```

Đặt passphrase khi được hỏi. Nếu file cùng tên đã tồn tại, dùng lại key đúng project hoặc đổi tên file; không ghi đè key đang dùng.

- `sss_oracle`: private key, giữ riêng trên máy.
- `sss_oracle.pub`: public key, được đưa vào ô SSH public key khi tạo VM.

Đọc public key để copy vào Oracle:

```powershell
Get-Content "$env:USERPROFILE\.ssh\sss_oracle.pub"
```

Oracle hỗ trợ Ed25519 cho platform images theo [quản lý SSH keys](https://docs.oracle.com/en-us/iaas/Content/Compute/Tasks/managingkeypairs.htm).

### 2.2 Đăng ký hostname miễn phí

Vào [DuckDNS](https://www.duckdns.org/), đăng nhập bằng phương thức trang hỗ trợ, tạo tên riêng của nhóm. Ví dụ trong tài liệu không phải tên đã đăng ký cho bạn.

Lưu hostname và token trong nơi riêng. Sau khi VM có public IPv4, điền **IP của VM** vào mục IP của hostname rồi cập nhật. Không để IP tự nhận từ trình duyệt ở nhà. Nếu có IPv6 cũ không đúng VM, xóa nó để tránh HTTPS đi sai máy.

DuckDNS là dịch vụ DNS động miễn phí; [spec chính thức](https://www.duckdns.org/spec.jsp) mô tả cập nhật IPv4/IPv6. Giai đoạn đầu cập nhật IP thủ công trong dashboard là đủ. Khi IP VM đổi phải cập nhật lại. Tên miền con DuckDNS không cần đưa vào Cloudflare DNS; tài khoản R2/Workers của bạn vẫn dùng bình thường.

### 2.3 Gói source sạch

Nếu nhận được gói `dist/level3-source-20261030.tar.gz` kèm hướng dẫn này, dùng trực tiếp gói đó. Nó chỉ chứa source và tài liệu; weights tải trên VM bằng tool của project. Không nén cả `D:\smart-security-upgraded` để gửi lên cloud.

Nếu tự đóng gói lại, mở PowerShell tại repo:

```powershell
Set-Location D:\smart-security-upgraded
New-Item -ItemType Directory -Force dist | Out-Null
tar -czf dist/level3-source-20261030.tar.gz --exclude=__pycache__ --exclude=*.pyc --exclude=*.egg-info --exclude=secrets.h --exclude=secrets.example.h backend tools tests firmware docs configs/default.yaml configs/hardware.example.yaml configs/calibration.seed.json models/manifest.json models/vendor models/licenses pyproject.toml requirements.txt requirements.in requirements-dev.txt requirements-dev.in README.md AGENTS.md THIRD_PARTY.md .gitignore
tar -tzf dist/level3-source-20261030.tar.gz
Get-FileHash dist/level3-source-20261030.tar.gz -Algorithm SHA256
```

Kiểm danh sách trước upload: không có `runtime/`, `data/`, `backups/`, `configs/local.yaml`, calibration thực, SQLite, ONNX, private key, `secrets.h` hoặc credential. Mẫu `secrets.example.h` hiện có giá trị cấu hình cụ thể nên cũng không đưa vào gói này. Firmware vẫn được build trên Windows với secrets local.

## 3. Tạo VM sau khi Oracle kích hoạt

Trong Console tìm **Compute → Instances → Create instance**; nhãn UI có thể khác nhẹ.

| Mục | Lựa chọn |
|---|---|
| Name | `smart-security-cloud` |
| Region | Home region của tài khoản; kiểm khả năng cấp A1 trước |
| Image | Canonical Ubuntu 24.04, kiến trúc ARM64 tương thích A1 |
| Shape | `VM.Standard.A1.Flex`, có nhãn/điều kiện Always Free phù hợp tài khoản |
| OCPU | 2 |
| RAM | Khởi đầu 6 GB, có thể 12 GB nếu tổng quota cho phép |
| Boot volume | Khoảng 50 GB, loại/mức performance mặc định đủ điều kiện miễn phí |
| Network | VCN + public subnet + Internet Gateway và default route |
| Public IPv4 | Gán cho VM |
| SSH keys | Paste nội dung `sss_oracle.pub` |
| Tùy chọn trả phí | Không thêm theo mặc định |

Kiểm toàn bộ tổng tài nguyên của tenancy và cost estimate trước Create. Đừng chọn tài nguyên chỉ được tài trợ bằng trial credit rồi coi là Always Free. [Tài liệu Oracle hiện tại](https://docs.oracle.com/en-us/iaas/Content/FreeTier/freetier_topic-Always_Free_Resources.htm) nêu A1 Always Free tương đương 2 OCPU/12 GB và 200 GB tổng boot/block storage; capacity không được bảo đảm. Home region [không đổi được sau khi tạo tenancy](https://docs.oracle.com/en-us/iaas/Content/Identity/regions/managingregions.htm).

Nếu `Out of host capacity`: thử availability domain khác trong home region nếu có, hoặc thử lại sau. Không tự chuyển sang shape trả phí hoặc đổi region với giả định vẫn miễn phí. Nếu chờ quá mốc nhóm đặt ra, dùng VM được trường/nhóm cấp hoặc đánh giá phương án dự phòng trong COST_PROPOSAL.

### 3.1 Rules mạng OCI

Trên NSG hoặc Security List thực sự gắn với subnet/VNIC của VM, cấu hình stateful ingress:

| Source IPv4 CIDR | Protocol / destination port | Mục đích |
|---|---|---|
| Public IP quản trị của bạn `/32` | TCP 22 | SSH; phải cập nhật khi IP quản trị đổi |
| `0.0.0.0/0` | TCP 80 | Cấp/gia hạn certificate và redirect HTTP→HTTPS |
| `0.0.0.0/0` | TCP 443 | Dashboard/board HTTPS |

Source port để any; không nhầm source port với destination port. Không mở public port 8000. Giữ egress cần cho apt, tải model, DNS và đồng bộ thời gian; cấu hình mặc định cho phép outbound phù hợp bước đầu. Tài liệu nền: [launch instance](https://docs.oracle.com/en-us/iaas/Content/Compute/tutorials/first-linux-instance/overview.htm).

Ghi riêng: instance name, home region, public IPv4, shape/RAM/disk. Cập nhật DuckDNS trỏ tới IP đó.

## 4. SSH và cài môi trường trên VM

**Windows PowerShell:** thay IP trong chuỗi:

```powershell
ssh -i "$env:USERPROFILE\.ssh\sss_oracle" ubuntu@VM_PUBLIC_IP
```

User mặc định cho Ubuntu image là `ubuntu`. Đối chiếu host fingerprint qua kênh Console khi có thể trước khi chấp nhận lần đầu. Giữ cửa sổ SSH đang hoạt động trong lúc chỉnh firewall.

**Các lệnh từ đây tới hết bước 9 chạy trong SSH trên Ubuntu**, trừ chỗ ghi Windows:

```bash
uname -m
lsb_release -ds
free -h
df -h /
timedatectl status
sudo apt update
sudo apt install -y python3.12 python3.12-venv python3-pip ca-certificates curl nano
python3.12 --version
```

Với **instance E5 hiện tại**, mong đợi `x86_64`, Ubuntu 24.04 và Python 3.12.x. Chỉ mong đợi `aarch64` khi dùng phương án A1. Nếu image khác, điều chỉnh theo OS thật; không đổi phiên bản Python/model/packages của project để vượt lỗi một cách tùy tiện.

### 4.1 Firewall trong Ubuntu

Mạng OCI và firewall hệ điều hành là hai lớp riêng. Kiểm trước:

```bash
sudo iptables -S INPUT
sudo ufw status
```

Nếu dùng image OCI với iptables/netfilter-persistent và **UFW không active**, cho phép web mà giữ rules hiện có:

```bash
sudo iptables -C INPUT -p tcp --dport 80 -m conntrack --ctstate NEW -j ACCEPT || sudo iptables -I INPUT 1 -p tcp --dport 80 -m conntrack --ctstate NEW -j ACCEPT
sudo iptables -C INPUT -p tcp --dport 443 -m conntrack --ctstate NEW -j ACCEPT || sudo iptables -I INPUT 1 -p tcp --dport 443 -m conntrack --ctstate NEW -j ACCEPT
sudo apt install -y iptables-persistent
sudo netfilter-persistent save
```

Nếu trình cài hỏi lưu rules IPv4/IPv6, lưu rules hiện tại. Nếu UFW đã active, dùng `sudo ufw allow 80/tcp` và `sudo ufw allow 443/tcp` thay nhánh iptables ở trên; không bật thêm một hệ quản lý firewall khác. Không flush rules hoặc tắt toàn firewall. Canonical ghi nhận images Oracle có cấu hình [iptables persistent](https://ubuntu.com/docs/oracle/oracle-how-to/upgrade-from-jammy-to-noble/).

## 5. Upload và cài source

Mở **PowerShell Windows thứ hai**:

```powershell
Set-Location D:\smart-security-upgraded
scp -i "$sshKey" dist/level3-source-20261030.tar.gz ubuntu@VM_PUBLIC_IP:/home/ubuntu/
```

Trở lại **SSH Ubuntu**, dùng thư mục mới chưa có deployment cũ:

```bash
sha256sum ~/level3-source-20261030.tar.gz
mkdir -p ~/smart-security
tar -xzf ~/level3-source-20261030.tar.gz -C ~/smart-security
cd ~/smart-security
python3.12 -m venv .venv
.venv/bin/python -m pip install --upgrade pip
.venv/bin/python -m pip install -r requirements-dev.txt
.venv/bin/python -m pip install -e .
.venv/bin/python -m tools.download_models
```

Đối chiếu SHA256 archive với Windows. Nếu đã có deployment trong thư mục, không extract đè khi service đang chạy; dừng và chuẩn bị thư mục release mới.

Giữ nguyên package pin trong repo. Nếu wheel cho kiến trúc máy thiếu hoặc package/model download lỗi, lưu tên package/lỗi để xử lý đúng; chưa tiếp tục đưa dữ liệu thật vào. Không tải model mới thay cho weights đã ghim.

## 6. Cấu hình runtime riêng trên VM

```bash
umask 077
mkdir -p ~/sss-private
cp configs/hardware.example.yaml ~/sss-private/hardware.yaml
nano ~/sss-private/hardware.yaml
```

File mẫu nên có nội dung sau; hai vùng là seed, cần thay bằng bố trí camera thật:

```yaml
profile: hardware
runtime_dir: /home/ubuntu/sss-private/runtime-hardware
device_id: camera-1
zones:
  approach: [0.05, 0.05, 0.40, 0.90]
  doorstep: [0.55, 0.05, 0.40, 0.90]
cloud:
  inference: local
  events_enabled: false
notifications:
  telegram_enabled: false
```

`local` ở đây nghĩa là chạy model ngay trong VM cloud. Không cần remote inference adapter hay `SSS_CLOUD_API_TOKEN` cho kiến trúc toàn app này. Không dùng `seed-simulation` trong runtime hardware.

Đặt env trong phiên SSH hiện tại, thay hostname thực:

```bash
export SSS_CONFIG=/home/ubuntu/sss-private/hardware.yaml
export SSS_CALIBRATION=/home/ubuntu/sss-private/calibration.local.json
export SSS_HTTPS=1
export SSS_DASHBOARD_URL=https://YOUR_HOSTNAME
.venv/bin/python -m tools.manage doctor
.venv/bin/python -m tools.manage bootstrap --username admin
.venv/bin/python -m tools.manage models-smoke
.venv/bin/python -m tools.manage calibration-check
```

Bootstrap hỏi mật khẩu admin tối thiểu 12 ký tự. Dùng mật khẩu riêng, lưu vào password manager. Lệnh tạo credential và token mới cho cloud; token nằm trong `~/sss-private/runtime-hardware/device-token.txt`, không in ra log.

`models-smoke` cần PASS, dimension 128. `calibration-check` ban đầu trả `CALIBRATION_REQUIRED` là đúng, vì chưa có hồ sơ thực. Chưa thể arm nhưng vẫn tiếp tục setup server, kết nối board, preview và enrollment draft.

Ghi nhớ: khi chạy full app trên cloud, biến consent của remote adapter không tự bảo vệ luồng này. Nhóm cần quyết định quyền xử lý/lưu gallery trước khi đăng ký người; không copy runtime/gallery từ laptop theo mặc định.

### 6.1 Kiểm phần mềm trên VM

```bash
SSS_HTTPS=0 .venv/bin/python -m pytest -q
.venv/bin/python -m ruff check backend tools tests
.venv/bin/python -m ruff format --check backend tools tests
```

`SSS_HTTPS=0` chỉ áp dụng cho đúng tiến trình pytest này, vì TestClient hiện dùng HTTP. Nếu giữ `SSS_HTTPS=1` từ cấu hình deploy khi chạy test, cookie login có cờ Secure sẽ không được gửi trên HTTP, gây ba lỗi API (401 thay vì 403/409 và thiếu mode_revision). Không sửa `secure` trong app hoặc tắt HTTPS của service để vượt test. Biến `SSS_HTTPS=1` trong shell/service vẫn giữ nguyên sau lệnh trên.

Trong phiên nghiên cứu trên Windows trước đó: 90 test pass; lint fail ba lỗi import, format fail bảy file có sẵn. Kết quả trên VM phải chạy và ghi riêng. Không sửa tràn lan hoặc ghi software gate PASS nếu vẫn còn lỗi.

## 7. Chạy backend thử rồi đặt systemd

Trong SSH tại `~/smart-security`, với env bước 6:

```bash
.venv/bin/python -m tools.serve --host 127.0.0.1 --port 8000
```

Mở SSH thứ hai và kiểm:

```bash
curl -i http://127.0.0.1:8000/health/live
curl -i http://127.0.0.1:8000/health/ready
```

Live cần 200/alive; ready có thể 503 vài giây đầu khi load model, sau đó cần 200/ready. Ready không chứng minh camera/calibration sẵn sàng. Không đăng nhập trực tiếp HTTP khi đang đặt Secure cookie; bước này chỉ kiểm health.

Sau khi kiểm, Ctrl+C tiến trình chạy tay. Không chạy đồng thời tiến trình tay và systemd.

Tạo env file:

```bash
nano ~/sss-private/service.env
```

Nội dung, thay hostname:

```ini
SSS_CONFIG=/home/ubuntu/sss-private/hardware.yaml
SSS_CALIBRATION=/home/ubuntu/sss-private/calibration.local.json
SSS_HTTPS=1
SSS_DASHBOARD_URL=https://YOUR_HOSTNAME
```

```bash
chmod 600 ~/sss-private/service.env
sudo nano /etc/systemd/system/smart-security.service
```

Nội dung unit:

```ini
[Unit]
Description=Smart Security cloud backend
Wants=network-online.target
After=network-online.target

[Service]
Type=simple
User=ubuntu
Group=ubuntu
WorkingDirectory=/home/ubuntu/smart-security
EnvironmentFile=/home/ubuntu/sss-private/service.env
Environment=PYTHONUNBUFFERED=1
UMask=0077
ExecStart=/home/ubuntu/smart-security/.venv/bin/python -m tools.serve --host 127.0.0.1 --port 8000
Restart=on-failure
RestartSec=5
TimeoutStopSec=15
NoNewPrivileges=true

[Install]
WantedBy=multi-user.target
```

```bash
sudo systemd-analyze verify /etc/systemd/system/smart-security.service
sudo systemctl daemon-reload
sudo systemctl enable --now smart-security
sudo systemctl status smart-security --no-pager
curl -i http://127.0.0.1:8000/health/ready
```

Chẩn đoán: `sudo journalctl -u smart-security -n 80 --no-pager`. Một process/một worker; không thêm `--reload` hoặc tăng `--workers`.

## 8. HTTPS tự động bằng Caddy

Trước bước này, DuckDNS phải trỏ đúng public IPv4 của VM, không có AAAA sai; port 80/443 thông qua cả OCI lẫn OS firewall.

Cài theo [repository chính thức của Caddy](https://caddyserver.com/docs/install):

```bash
sudo apt install -y debian-keyring debian-archive-keyring apt-transport-https curl gnupg
curl -1sLf https://dl.cloudsmith.io/public/caddy/stable/gpg.key | sudo gpg --dearmor -o /usr/share/keyrings/caddy-stable-archive-keyring.gpg
curl -1sLf https://dl.cloudsmith.io/public/caddy/stable/debian.deb.txt | sudo tee /etc/apt/sources.list.d/caddy-stable.list
sudo chmod o+r /usr/share/keyrings/caddy-stable-archive-keyring.gpg /etc/apt/sources.list.d/caddy-stable.list
sudo apt update
sudo apt install -y caddy
sudo nano /etc/caddy/Caddyfile
```

Cấu hình, thay hostname thực; áp dụng cho VM mới không có website Caddy khác:

```caddyfile
YOUR_HOSTNAME {
    reverse_proxy 127.0.0.1:8000
}
```

```bash
sudo caddy validate --config /etc/caddy/Caddyfile
sudo systemctl reload caddy
sudo systemctl status caddy --no-pager
curl -i https://YOUR_HOSTNAME/health/live
curl -i https://YOUR_HOSTNAME/health/ready
```

Caddy tự cấp/gia hạn certificate khi DNS và cổng public đúng, theo [hướng dẫn reverse proxy](https://caddyserver.com/docs/quick-starts/reverse-proxy). Không dùng `curl -k` để kết luận TLS đạt.

**Windows**, kiểm từ mạng ngoài VM:

```powershell
Resolve-DnsName YOUR_HOSTNAME
Test-NetConnection YOUR_HOSTNAME -Port 443
curl.exe -i https://YOUR_HOSTNAME/health/live
curl.exe -i https://YOUR_HOSTNAME/api/v2/status
```

Health cần 200. Status chưa login cần 401. Trình duyệt mở `https://YOUR_HOSTNAME`, đăng nhập admin đã bootstrap. Điện thoại 4G cũng phải vào được; lúc này camera chưa online vẫn là trạng thái bình thường.

Nếu sau này dùng domain riêng trong Cloudflare: A record trỏ VM, bắt đầu DNS-only để giữ đường HTTPS giống hướng dẫn này. [DNS-only](https://developers.cloudflare.com/dns/proxy-status/) trả IP origin, không gọi Worker script. Nếu bật proxy Cloudflare sau đó thì cần kiểm lại chuỗi certificate ESP, client IP và cache; không phải bước bắt buộc hiện tại.

## 9. ESP32: phần phải bổ sung trước khi nối cloud trực tiếp

**Firmware hiện chưa có triển khai TLS rõ ràng để nghiệm thu. Không chỉ đổi URL rồi coi là đã xong.** Bước 1–8 dựng được backend; bước 9 cần một issue firmware riêng trước khi dùng board với cloud.

Yêu cầu của issue:

- Dùng `NetworkClientSecure` với CA root đúng chuỗi certificate của hostname thực, kiểm hostname/certificate; không dùng `setInsecure()`.
- Đồng bộ giờ có timeout hữu hạn trước TLS; lỗi giờ/certificate phải từ chối kết nối và giữ còi OFF.
- Control và capture dùng client TLS riêng; giữ vòng đời client đến khi `HTTPClient` kết thúc, không dùng chung đối tượng giữa hai task.
- Giữ command lease/session/revision/generation, ACK, hard cap và STOP độc lập upload. Không tự nới control timeout/freshness để che RTT chậm.
- Đo RAM/stack/heap, handshake và reconnect trên AI-Thinker thật. Certificate root lấy từ CA chính thức sau khi xác định issuer thực; không copy leaf certificate tạm hoặc certificate ví dụ.
- Bổ sung test fail-closed và chạy HIL cho TLS lỗi, DNS lỗi, upload chậm, mất Wi-Fi/cloud, STOP và command cũ.

Espressif có [ví dụ TLS đúng core 3.3.0](https://github.com/espressif/arduino-esp32/blob/3.3.0/libraries/NetworkClientSecure/examples/WiFiClientSecure/WiFiClientSecure.ino); phải ghép theo hai task hiện tại của project.

Chuẩn bị `secrets.h` **chỉ trên Windows**, sau khi issue TLS có implementation:

```cpp
#pragma once
#define WIFI_SSID "YOUR_2_4_GHZ_WIFI"
#define WIFI_PASSWORD "YOUR_WIFI_PASSWORD"
#define SERVER_URL "https://YOUR_HOSTNAME"
#define DEVICE_ID "camera-1"
#define DEVICE_TOKEN "TOKEN_PROVISIONED_ON_CLOUD_VM"
#define ALARM_ACTIVE_HIGH true
```

Active level phải theo module thực. CA/TLS settings theo implementation mới, không đoán tên macro chưa tồn tại.

Lấy token từ VM qua SSH/SCP vào file private ngoài repo, không cần in token vào chat. Ví dụ **Windows**:

```powershell
New-Item -ItemType Directory -Force "$env:USERPROFILE\.sss-private" | Out-Null
scp -i "$sshKey" ubuntu@VM_PUBLIC_IP:/home/ubuntu/sss-private/runtime-hardware/device-token.txt "$env:USERPROFILE\.sss-private\cloud-device-token.txt"
```

Mở file bằng editor riêng, điền vào secrets local. Token này phải khớp credential cloud; token runtime laptop cũ sẽ không khớp. Không chuyển `credentials.json` cloud vào firmware.

**Windows**, sau khi TLS implementation sẵn sàng:

```powershell
Set-Location D:\smart-security-upgraded
arduino-cli version
arduino-cli board list
arduino-cli compile --profile ai_thinker firmware/security_node
arduino-cli upload --profile ai_thinker --port COM_PORT firmware/security_node
arduino-cli monitor --port COM_PORT --config baudrate=115200
```

Profile pin ESP32 core 3.3.0/ArduinoJson 7.4.1; hướng dẫn repo dùng Arduino CLI 1.2.2. Compile pass không thay HIL. Dùng nguồn/đấu dây đã kiểm, còi OFF khi boot.

Đích kiểm: board hello thành công, sync có session, dashboard camera online; khi DISARMED chưa gửi ảnh giám sát là đúng. Bấm Preview để kiểm ảnh, không cố arm lúc calibration chưa đạt.

## 10. Preview → enrollment → calibration → arm

Trước ảnh/người thật, hoàn tất quyết định xử lý/lưu biometric trên VM. Dùng runtime cloud mới thay vì upload DB/gallery laptop theo mặc định.

1. Gắn cố định camera, đủ sáng; preview và chỉnh zones trên `~/sss-private/hardware.yaml`. Restart service khi đổi config. Không giữ server preview mở để thao tác runtime trực tiếp.
2. Đăng ký người với consent; thu hai lượt đủ mẫu chất lượng; commit thành draft. Chưa activate/arm khi calibration bị chặn.
3. Thu validation và holdout theo `docs/VALIDATION.md`, giữ tách phiên. Tool `record_visit` hỗ trợ `--server https://YOUR_HOSTNAME` và hỏi mật khẩu; dataset lưu ở máy chạy tool, nên chọn nơi lưu có chủ đích.
4. Evaluation phải chạy với đúng draft gallery, config và model; tool lấy runtime lock, nên dừng service trước khi evaluate trên VM. Không chạy evaluate với gallery trống trên laptop rồi dùng report đó cho cloud.
5. Reviewer lập calibration profile bằng hash/report thật, đặt ở `~/sss-private/calibration.local.json`; `validation_report` trỏ file thực có trên VM. Không tự sửa seed thành validated.
6. Restart service, chạy calibration-check với đúng env, activate người trong phạm vi đã đo, rồi mới arm chủ động.

Các lệnh quản trị trong SSH mới cần đặt lại env bước 6. File systemd EnvironmentFile không tự export cho shell đang mở. Ví dụ kiểm calibration:

```bash
cd ~/smart-security
export SSS_CONFIG=/home/ubuntu/sss-private/hardware.yaml
export SSS_CALIBRATION=/home/ubuntu/sss-private/calibration.local.json
.venv/bin/python -m tools.manage calibration-check
```

Kịch bản nghiệm thu: known; unknown A→B; đi ngang/đi ra/B trực tiếp; mặt mờ/che; hai người; STOP khi upload; mất Internet; reboot board/server. Log ghép frame/event/command/ACK và đo còi thật. Tắt laptop, điện thoại 4G và board vẫn phải hoạt động với VM.

## 11. Chuẩn bị R2 sau khi core hoạt động

Trong Cloudflare Dashboard → R2:

1. Tạo bucket riêng, ví dụ `sss-evidence-demo`, Standard storage.
2. Để bucket private: không bật public development URL/custom domain cho ảnh.
3. Đặt lifecycle expire ảnh sau 7 ngày; đây không thay cơ chế xóa ngay khi xóa người.
4. Chưa cần tạo key khi chưa có adapter. Khi triển khai adapter Python, tạo S3 credential **Object Read & Write**, scope đúng bucket này, lưu riêng trên VM.
5. Chuẩn bị Account ID, bucket name, S3 endpoint, Access Key ID, Secret Access Key. Không đưa key vào `secrets.h` hay JS trình duyệt.

Theo [R2 S3 setup](https://developers.cloudflare.com/r2/get-started/s3/), credential và endpoint dùng cho S3 API. **Project hiện chưa có R2 adapter**, nên tạo bucket chưa làm ảnh tự upload. Cần issue riêng: media outbox sau COMMIT, retry/idempotency, authenticated read, retention và xóa người; R2 lỗi không chặn STOP.

Workers Free/D1 để sau; chưa cần deploy Worker hoặc bật thêm binding cho bước 1–10.

## 12. Vận hành và khôi phục

| Việc | Lệnh / yêu cầu |
|---|---|
| Trạng thái backend | `sudo systemctl status smart-security --no-pager` |
| Log backend | `sudo journalctl -u smart-security -n 80 --no-pager` |
| Log HTTPS | `sudo journalctl -u caddy -n 80 --no-pager` |
| Dung lượng/RAM | `df -h` và `free -h` |
| Restart backend | `sudo systemctl restart smart-security`; sau đó đăng nhập/arm lại |
| Dừng backend | `sudo systemctl stop smart-security`; kiểm còi OFF theo HIL |
| Billing | Theo dõi Compute/storage/network và usage thực trong Oracle/R2 |

Backup cần vị trí private riêng có quyền lưu; không tự tải lên R2. Tool backup cần runtime lock, vì vậy dừng service và dùng đường backup mới:

```bash
sudo systemctl stop smart-security
cd ~/smart-security
export SSS_CONFIG=/home/ubuntu/sss-private/hardware.yaml
umask 077
mkdir -p ~/sss-private/backups
.venv/bin/python -m tools.manage backup --path ~/sss-private/backups/before-demo-20261030
sudo systemctl start smart-security
```

Đổi tên backup mỗi lần; thư mục đích phải chưa tồn tại. Backup tool không thay việc giữ riêng SSH key, config, calibration/report và kế hoạch cấp lại credential. Không chép SQLite live bằng `scp` khi app còn ghi. Thực hành restore vào runtime mới theo tool/repo trước khi coi backup hữu dụng.

## 13. Lỗi hay gặp

| Triệu chứng | Kiểm tra |
|---|---|
| Oracle account chưa vào được tenancy | Chờ trạng thái provisioning/email của tài khoản; chưa thể chạy bước tạo VM |
| SSH timeout | Public IPv4, route/Internet Gateway, source `/32` của rule 22, OS firewall |
| SSH Permission denied | User `ubuntu`, đúng private key tương ứng public key lúc tạo máy |
| Hostname trỏ IP nhà | Sửa DuckDNS thành public IPv4 VM; kiểm AAAA |
| HTTPS timeout | OCI + OS rules 80/443 và Caddy đang listen |
| Caddy không lấy được cert | DNS chưa đúng/chưa propagate, AAAA sai, port bị chặn, CA rate limit; đọc log rồi sửa, không retry mù |
| HTTPS 502 | Backend chết hoặc chưa listen 127.0.0.1:8000; xem journal |
| ready 503 | Model/package/hash, worker startup; đọc mã lỗi, không bỏ gate |
| FileLock khi start/bootstrap/evaluate | Còn process app khác; chỉ một process trên runtime |
| Login HTTP không giữ session | `SSS_HTTPS=1`: đăng nhập bằng URL HTTPS thật |
| Board 401 | Token trong firmware không khớp token cloud |
| Board không TLS được | Firmware TLS chưa hoàn thiện, giờ/CA/hostname sai, heap hoặc timeout |
| Camera online nhưng chưa có ảnh | DISARMED: bấm Preview; kiểm lease/capture/Serial |
| CALIBRATION_REQUIRED | Cần validation/reviewer/profile thật, chưa được arm |
| R2 trống | Chưa có adapter/outbox; bucket mới không tự nhận ảnh |
| Restart xong DISARMED | Hành vi an toàn đúng; không tự phục hồi START cũ |

## 14. Mốc tiến độ của bạn ngay bây giờ

- Làm ngay: SSH key, đăng ký DuckDNS, giữ gói source sạch, kiểm phần cứng LAN và lưu thông tin tài khoản riêng.
- Oracle kích hoạt: tạo VM đúng quota, SSH, gửi source, cài packages/model, bootstrap runtime mới, systemd và HTTPS.
- Khi backend HTTPS chạy: hoàn thiện issue TLS firmware rồi nối board; sau đó calibration/HIL.
- Sau core: R2 ảnh; Workers/D1 chỉ khi cần.

Hướng dẫn không tự tạo VM, mở cổng, truyền biometric, thay firmware hay tạo R2 credential. Phần còn phải triển khai trong code là TLS firmware và adapter R2; không diễn giải các bước cấu hình như bằng chứng hai phần đó đã hoạt động.
