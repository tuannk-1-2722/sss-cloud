# Cơ sở kỹ thuật và quyết định baseline

Đối chiếu ngày 01/10/2026. Nguồn dưới đây là tài liệu/code chính thức; cấu hình camera, threshold, cadence và budget trong dự án là quyết định cần đo, không phải số đảm bảo do nguồn công bố. Gói v1 lịch sử chỉ có tài liệu; gói v2 được review đã có implementation. Bản v2.1 giữ protocol v2 và model/toolchain đã ghim.

## 1. Nguồn và cách áp dụng

| ID | Nguồn chính | Điều nguồn hỗ trợ | Quyết định của dự án / phần cần đo |
|---|---|---|---|
| R01 | [Espressif esp32-camera](https://github.com/espressif/esp32-camera) | JPEG phù hợp khi có Wi-Fi; PSRAM quan trọng khi tăng kích thước ảnh; nhiều framebuffer đổi cách capture | AI-Thinker có PSRAM, VGA JPEG, một framebuffer để đơn giản ownership; đo age/heap/cadence thực |
| R02 | [Pin map CameraWebServer, core 3.3.0](https://github.com/espressif/arduino-esp32/blob/3.3.0/libraries/ESP32/examples/Camera/CameraWebServer/camera_pins.h), [ESP32 GPIO](https://docs.espressif.com/projects/esp-idf/en/stable/esp32/api-reference/peripherals/gpio.html) | Camera pin map phụ thuộc board; strapping/input-only/flash-PSRAM có hạn chế | GPIO13 PIR, GPIO14 driver buzzer; không SD; xác minh board thật và logic level trước đấu |
| R03 | [Adafruit PIR overview](https://learn.adafruit.com/pir-passive-infrared-proximity-motion-sensor/overview) | PIR phát hiện thay đổi hồng ngoại do chuyển động | Chỉ kích burst capture; không suy khoảng cách, danh tính hoặc chiều từ PIR |
| R04 | [OpenCV 4.13 face detection/recognition tutorial](https://docs.opencv.org/4.13.0/d0/dd4/tutorial_dnn_face.html) | YuNet/SFace API: landmarks → alignCrop → feature → cosine comparison; có benchmark/ngưỡng theo benchmark | Dùng API CPU có sẵn; L2 normalize, gallery cosine; không áp threshold benchmark như bảo đảm cho ESP32-CAM |
| R05 | [OpenCV Zoo tại commit ghim](https://github.com/opencv/opencv_zoo/tree/47534e27c9851bb1128ccc0102f1145e27f23f98), [NanoDet wrapper](https://github.com/opencv/opencv_zoo/blob/47534e27c9851bb1128ccc0102f1145e27f23f98/models/object_detection_nanodet/nanodet.py) | Model artifacts và tiền xử lý/postprocess NanoDet chính thức | Ghim wrapper + weight + hash; letterbox416 trả bbox về ảnh gốc; chỉ class person |
| R06 | [SFace tác giả](https://github.com/zhongyy/SFace), [Zoo SFace artifact](https://github.com/opencv/opencv_zoo/tree/47534e27c9851bb1128ccc0102f1145e27f23f98/models/face_recognition_sface) | Nhận diện bằng pretrained features; bản Zoo đóng gói ONNX | Enrollment thêm gallery, không train lại model; score không là xác suất; validation riêng mới chọn accept/reject/margin |
| R07 | [Arduino-ESP32 installation](https://docs.espressif.com/projects/arduino-esp32/en/latest/installing.html), [core releases](https://github.com/espressif/arduino-esp32/releases) | Nền tảng ESP32 cài qua package index; phiên bản ảnh hưởng build/API | Core ghim 3.3.0 làm baseline đã chọn, không tuyên bố là latest |
| R08 | [Arduino CLI sketch profiles](https://docs.arduino.cc/arduino-cli/sketch-project-file/), [ArduinoJson v7](https://arduinojson.org/v7/) | Profile ghim FQBN/platform/library; JsonDocument v7 có API tương ứng | CLI1.2.2 + core3.3.0 + ArduinoJson7.4.1 trong sketch.yaml; full compile phải được nhóm kiểm |
| R09 | [FastAPI async/concurrency](https://fastapi.tiangolo.com/async/) | Async phù hợp I/O; việc CPU cần cách chạy phù hợp | Một worker vision riêng, API/control không chờ inference; native inference có thể kẹt nên health/reset và process restart có giới hạn |
| R10 | [SQLite WAL](https://www.sqlite.org/wal.html), [SQLite backup API](https://www.sqlite.org/backup.html) | WAL dùng shared-memory/local host; backup API tạo snapshot nhất quán | SQLite local trên máy server, không DB đặt network drive; backup khi stopped + media/checksums |
| R11 | [Telegram Bot API](https://core.telegram.org/bots/api) | sendMessage/error responses và retry_after khi giới hạn | Extension text outbox tắt mặc định; retries có trần, không hứa exactly-once sau timeout |

Quy tắc A → B, tâm body bbox, stability/votes/freshness, warning không đủ mặt/chiều và suppression per-visit là **thiết kế của dự án**, không kết luận trực tiếp từ các nguồn trên. Nó giúp demo đường đi kiểm soát nhưng có thể bỏ/nhầm chiều khi tracking mất, góc camera xấu hoặc người che nhau; cần evidence G2/G4.

R01 còn có [`camera_fb_t.timestamp`](https://github.com/espressif/esp32-camera/blob/master/driver/include/esp_camera.h) và [driver cam_hal](https://github.com/espressif/esp32-camera/blob/master/driver/cam_hal.c): timestamp first DMA tính từ esp_timer kể từ boot. Firmware dùng nó để kiểm tuổi ảnh; thời điểm lấy buffer không bảo đảm ảnh vừa chụp. Full build/HIL vẫn cần xác minh trên core ghim của nhóm.

## 2. Vì sao giữ kiến trúc này

- **Không chạy face recognition trên ESP32-CAM:** board capture/sensor/actuator, laptop CPU xử lý; phù hợp máy không NVIDIA và giảm tải RAM firmware. Thông lượng thực vẫn cần đo trên máy nhóm.
- **Thêm person detector:** face-only không quan sát tốt người quay mặt và không đủ hình học chiều. NanoDet CPU là baseline gọn; face-only fallback chỉ warning/identity không có quyền suy incoming để hú.
- **Không lấy PIR làm trigger duy nhất:** PIR có warm-up, vùng rộng và không giữ presence khi người đứng yên. Sentinel cadence giúp thấy người có thể bỏ qua PIR; presence ở camera giữ burst ngắn.
- **Không ép mọi mismatch là stranger:** empty/poor face là uncertain/no-face; chỉ ảnh đủ chất lượng với score thấp mới UNKNOWN. Hướng chưa rõ thì warning.
- **Một server/worker/pending slot:** giảm triển khai/xung đột nhóm, bỏ frame cũ thay backlog dài. Revision sau inference ngăn result cũ sau disarm/delete. Chưa có nhu cầu broker/microservices.
- **Dashboard cùng origin:** cùng API/auth/media, không cần framework/build chain. Responsive local web phục vụ laptop/điện thoại trên LAN; không native app ở baseline.
- **SQLite và SQL migration đơn giản:** dữ liệu nhỏ một server; chưa cần ORM/Alembic. Khi schema thực thay đổi, thêm migration đánh số và fixture nâng cấp; không sửa SQL đã áp dụng để giả migrate.
- **Không mua thêm sensor/relay ở vòng đầu:** demo A → B có kiểm soát đủ để kiểm giả thuyết camera. Buzzer có driver nhận3,3V logic; nguồn/đế/cáp/giá đã trong khoản phân bổ. Nếu hardware thực không đạt, thay đổi có issue/BOM/report.

## 3. Artifacts và license đã ghim

`models/manifest.json` sở hữu URL/commit/size/SHA256; tool tải kiểm cả weight, wrapper và license. Total weights khoảng 42,7 MB theo byte manifest; không kèm ONNX trong ZIP. Không tự đổi sang file cùng tên từ mirror khác.

| Thành phần | Artifact | License tại commit ghim |
|---|---|---|
| NanoDet | `object_detection_nanodet_2022nov.onnx` + official wrapper | Apache-2.0 |
| YuNet | `face_detection_yunet_2023mar.onnx` | MIT |
| SFace | `face_recognition_sface_2021dec.onnx` | Apache-2.0 |

Giữ license texts trong `models/licenses/`, notices trong `THIRD_PARTY.md`, và nguồn trong manifest khi chia sẻ model/wrapper. License của dependency Python/Arduino theo package upstream, không được ghi tất cả là một license của ứng dụng. Nhóm chọn license cho phần code của mình khi tạo repo, phù hợp yêu cầu lớp.

Exact download/hash và blank smoke đã được thực hiện trong phiên xây gói; đó chỉ xác minh artifact load/dimension, không chứng minh benchmark/face quality. Windows, board, gallery20 và dataset thật chưa kiểm. Xem HANDOFF/VALIDATION thay vì suy từ tên model.


## 4. Đối chiếu bổ sung khi review 02/10/2026

- [Pydantic strict mode](https://docs.pydantic.dev/latest/concepts/strict_mode/):
  cấm extra field không đồng nghĩa strict type. Bản sửa dùng StrictInt/StrictBool
  cho wire fields và validator cho quan hệ KNOWN/person_id/track unique.
- [Arduino sketch project file](https://docs.arduino.cc/arduino-cli/sketch-project-file/):
  profile ghim platform/library; CLI cần tải các thành phần còn thiếu. Có profile
  đúng không chứng minh sketch compile thành công.
- [OpenCV face tutorial](https://docs.opencv.org/4.13.0/d0/dd4/tutorial_dnn_face.html):
  giữ alignCrop → feature → so khớp; không thay model/threshold theo suy đoán review.
- [esp32-camera timestamp](https://github.com/espressif/esp32-camera/blob/master/driver/include/esp_camera.h):
  timestamp frame khác thời điểm lấy buffer. Bản sửa kiểm thêm barrier đổi mode;
  vẫn phải HIL với driver/core ghim, không suy từ header master rằng board đã đúng.

Các thay đổi concurrency/DB/geometry là kết luận từ đọc code và regression trong
repo, không phải kết quả benchmark do các nguồn trên chứng nhận.

## Nguồn cho extension Level 3 (đọc ngày 20/10/2026)

- ESP32 Camera README và OV2640 source: https://github.com/espressif/esp32-camera và https://github.com/espressif/esp32-camera/blob/master/sensors/ov2640.c — AEC/AWB/AGC; PSRAM/JPEG/fb_count.
- Telegram Bot API: https://core.telegram.org/bots/api — sendPhoto/inline keyboard link.
- Tailscale private Serve: https://tailscale.com/docs/features/tailscale-serve — HTTPS vào tailnet, không public Funnel.
- PWA HTTPS/manifest và SW caching: https://developer.mozilla.org/en-US/docs/Web/Progressive_web_apps/Guides/Making_PWAs_installable và https://developer.mozilla.org/en-US/docs/Web/Progressive_web_apps/Tutorials/CycleTracker/Service_workers.
- Cloud persistence caveats: https://render.com/docs/free và https://render.com/docs/disks.
- **Nguồn định cấp duy nhất cho báo cáo môn học là PDF do người dùng tải lên**. Các nguồn ngoài xác nhận feasibility, không chứng nhận điểm level.
