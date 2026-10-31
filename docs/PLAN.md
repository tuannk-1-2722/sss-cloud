# Kế hoạch nhóm — đến khoảng tháng 11/2026

Baseline: nhóm 3 người, một AI-Thinker ESP32-CAM, laptop CPU, tổng mua dưới 1 triệu. Ngày nộp chính thức cần xác nhận với giảng viên; **không phải năm 2027**. Tháng 10 là thời gian triển khai và đo, tháng 11 dành cho ổn định, báo cáo và demo. Gate là điều kiện hoàn thành; lịch chỉ là dự kiến.

## 1. Chia trách nhiệm và review

| Người | Sở hữu chính | Phải giao cho nhóm | Reviewer |
|---|---|---|---|
| A | Nguồn, đấu dây, firmware, capture/control/alarm | Ảnh lắp, build version, HIL và cadence/heap/reset log | C review protocol, B review ảnh |
| B | Camera placement, person/face pipeline, enrollment, dataset/calibration | Profile camera, manifest dataset, validation/holdout và giới hạn gallery đã đo | C review policy/metrics, A review bố trí |
| C | API, SQLite, dashboard, policy, tích hợp và release | Simulator, history/command ACK, tests, backup/restore, demo script | A review command, B review identity |

Mỗi người đều chạy simulator và hiểu bảng policy. Owner không làm việc một mình đến sát deadline: producer và consumer ghép sớm. Một người giữ máy server và runtime thật; không gửi DB/ảnh/token cho agent/cloud theo mặc định.

## 2. Gate và lịch dự kiến

| Gate | Khoảng thời gian | Điều kiện qua | Evidence tối thiểu |
|---|---|---|---|
| G0 — Baseline | 01–04/10 | Cả ba chạy setup/simulator; chốt owner, BOM, board, đường đi và ngày nộp | Setup từng máy, quyết định nhóm, issue backlog |
| G1 — Board + safety | 05–11/10 | Full sketch compile; VGA JPEG ổn; control/upload độc lập; còi tự OFF; nguồn không reset | CLI/core/lib version, HIL restart/offline/duplicate/STOP/hard cap, ảnh dây |
| G2 — Hình học | 12–18/10 | Camera cố định; bbox đi A → B qua khe; ngang/ra ngoài/B trực tiếp được phân biệt; đủ face size | Video/ảnh riêng của nhóm, zones và hash, lỗi association/tracker |
| G3 — Enrollment + AI | 19–25/10 | Hai lượt chất lượng, draft gallery; tune validation; chốt reject/accept/margin/quality/quy mô | Report có mẫu số, camera/model hash, thông số máy; chưa dùng holdout để tune |
| G4 — End-to-end | 26/10–01/11 | Holdout + HIL + tải hai người; false alarm và latency đạt gate; backup/restore thực hành | Report frozen, command ACK, p95 thực, checklist đạt/chưa đạt |
| G5 — Nộp/demo | Theo ngày nộp tháng 11 | Release freeze, chạy lại trên máy demo, đủ báo cáo/video/slides theo rubric | Tag, ZIP, hướng dẫn setup, demo script và limitations |

Nếu lịch ngắn hơn, ghép G0/G1 và G2/G3 nhưng giữ các điều kiện safety/data. Nếu G3/G4 không đạt, báo cáo phạm vi nhỏ hơn có bằng chứng: giảm quy mô gallery hoặc giới hạn ánh sáng/đường đi. Không giảm chuẩn an toàn còi hoặc gọi simulator là AI đạt chuẩn.

## 3. Backlog cụ thể

### 3.1 Nhận baseline v2.1 trước khi làm G1

Không coi những dòng dưới là G0–G4 đã PASS. Review software hiện tại có evidence
trong HANDOFF; board/dữ liệu/nhóm chưa được nghiệm thu.

| Issue | Owner / file chính | Đầu vào và việc cần làm | Done / reviewer |
|---|---|---|---|
| BASE-01 | C, README + tools/check | Tạo repo chung, cả ba cài Python 3.12; chạy check/simulator trên máy nhóm | Mỗi máy có kết quả; A review; không commit runtime |
| BASE-02 | A, firmware/security_node | Full compile core3.3.0; nếu fail sửa đúng lỗi build; nạp sau khi kiểm dây | Log compile + build versions, H01–H10; C review; host test không thay compile |
| BASE-03 | B, vision/tracking/policy | Review TEAM_GUIDE, SPEC FR06–12 và ports.py; chạy từng tình huống pure test | B giải thích input/output và ambiguous reset; C review |
| BASE-04 | C + B, enrollment/worker | Walkthrough hai lượt, cancel, timeout, stale inference, commit draft/activate | Software tests + operator flow trên camera; B review |
| BASE-05 | C, static/templates | Test browser laptop/điện thoại: A/B overlay, tên known, preview/cancel, enrollment image, logout, network error | Checklist thực, không chỉ node --check; A review |

BASE-01 trước; BASE-02 và BASE-03 có thể làm đồng thời trên các máy/branch riêng;
BASE-04 cần ảnh thật từ A và adapter của B; BASE-05 ghép operator flow. Không cần
nhờ agent viết lại mọi file đã có. HIL/holdout trong backlog dưới vẫn bắt buộc.

### 3.2 Tích hợp và đo trên thiết bị

| Issue | Owner | Dependency | Done khi |
|---|---|---|---|
| HW-01 mua/kiểm nguồn và pin | A | G0 | Đúng board, đo tín hiệu/cấp nguồn, BOM thực không vượt trần |
| FW-01 full build + bring-up | A | HW-01 | Compile/upload pass, JPEG VGA đúng, PSRAM/heap/reset được ghi |
| FW-02 control/ACK safety | A + C | FW-01 | HIL trong VALIDATION đạt, không repeat START sau reconnect cùng session |
| CV-01 bố trí/vùng/tracker | B + A | FW-01 | Ba chiều đi + mất track + người che nhau có evidence |
| CV-02 thu enrollment + dữ liệu | B | CV-01 | Consent, hai lượt và split validation/test theo phiên riêng |
| CV-03 hiệu chỉnh/holdout | B + C | CV-02 | Report và reviewer; hash phù hợp, limit đã đo |
| BE-01 ghép board thật | C + A | FW-01 | Hello/sync/JPEG đúng contract, server/board reboot an toàn |
| BE-02 dashboard/enrollment | C + B | BE-01 | Operator hoàn tất preview, hai lượt, draft/activate/disable/delete |
| INT-01 đo tải/latency | C + A + B | CV-03 | Hai người, Wi-Fi kém, p95 CPU/E2E và frames replaced được ghi |
| DATA-01 backup/delete/restore | C | BE-02 | Restore runtime mới, sessions revoked, DISARMED, consent kiểm lại |
| DEMO-01 release và báo cáo | C điều phối | G4 | Fresh setup + script 5–7 phút, có phương án simulator ghi rõ |

Khung code đã có nền cho các issue trên. Từng issue tiếp tục từ hiện trạng, không viết lại repo để “chuẩn hóa kiến trúc”. Chưa có board, fixture nguồn thật hoặc dataset thật trong gói bàn giao.

## 4. Quy trình Git

1. Tạo repo chung từ gói này. Commit baseline trước sửa; private nếu có tài liệu lớp. Không commit runtime/secrets/dataset. `.gitignore` có sẵn nhưng vẫn xem diff trước push.
2. Mỗi issue có branch `feat/fw-sync`, `fix/enrollment-cancel` hoặc tên tương ứng. Một branch/PR tập trung một kết quả; pull main trước bắt đầu.
3. Ghi FR/API/config liên quan và acceptance trong issue. Contract thay thì A/C ghép cùng PR; schema/firmware/simulator/docs/tests không được lệch phiên bản.
4. Giữ ownership theo ARCHITECTURE: B sửa vision/tracking, B+C sửa enrollment, C sửa service/worker/API; A sửa firmware. Nếu cùng sửa contracts/service phải chốt người ghép trước.
5. PR dùng template, có reviewer ở cột trên; CI xanh chưa thay HIL hoặc AI report. Thay threshold chỉ merge với report validation, rồi test trên holdout mới theo VALIDATION.
6. Merge sau review; tag checkpoint `v2-g1`, `v2-g3`, `v2-demo`. Tag phần mềm không tự chứng nhận hardware/accuracy.

Workflow GitHub được cung cấp nhưng **chưa chạy trên GitHub** khi bàn giao. Nhóm kiểm quyền Actions và kích hoạt branch protection/review phù hợp. Job firmware dùng secrets giả, chỉ compile.

## 5. Dùng agent để coding có kiểm soát

`AGENTS.md` là chỉ dẫn ngắn ở root, không cần tạo thêm skill để lặp lại toàn spec. Agent tự đọc theo task; người nhóm không cần nhồi cả bộ docs vào prompt.

Prompt mẫu:

```text
Làm issue FW-02 trong repo hiện tại. Đọc AGENTS.md, CONTRACTS mục 2 và SPEC FR-18/19.
Giữ protocol v2 và board/model baseline. Kiểm tra firmware hiện có trước sửa.
Sửa reconnect cùng session để không reset generation/không phát lại START.
Thêm kiểm thử logic cần thiết, compile profile ai_thinker nếu toolchain có sẵn.
Không gửi command thật đến board. Báo diff, test, phần HIL còn cần nhóm đo.
```

Task B có thể yêu cầu `vision.py` + SPEC mục 4/5; task C yêu cầu endpoint/FR cụ thể. Đưa input/error đã bỏ secrets và ví dụ synthetic tái hiện. Nếu agent bị ngắt, checkpoint `.codex/TASK.md` nêu issue/branch/đã sửa/test/blocker/bước tiếp; file riêng mỗi checkout và ignored Git. Tiếp tục bằng yêu cầu đọc checkpoint, không “làm lại từ đầu”.

Người nhóm duyệt việc đổi model/board/policy, nâng dependency lớn hoặc giảm mục tiêu acceptance. Agent có thể sửa bug, refactor nhỏ và chạy tests trong issue mà không hỏi lại từng bước. Không giao agent giả số đo, đánh dấu calibration validated hoặc tune liên tục theo holdout.

## 6. Đầu ra cần nộp

Một repo chạy được, sơ đồ kết nối đúng linh kiện thật, BOM thực, mô tả trigger/enrollment/policy, hợp đồng API, source, tests, report AI/HIL, demo video và báo cáo theo rubric. Giữ số đo lỗi/limitation trong báo cáo: PIR không đo khoảng cách, nhận diện không chống spoof, CPU/Wi-Fi có giới hạn, B trực tiếp có thể chưa rõ chiều.

Code nền ưu tiên module nhỏ và thư viện có sẵn: FastAPI + OpenCV + SQLite + dashboard cùng server. Chưa thêm broker, cloud storage, vector DB, frontend build chain hoặc microservice. Mở rộng sau release bằng issue có dữ liệu/contract/gate, không để kéo trễ G4/G5.

## 7. Khi muốn thành viên tự triển khai lại

Chỉ thay module đã nhận ownership, giữ input/output trong ARCHITECTURE/ports.py và
acceptance hiện có. Trình tự: tests/fixture mô tả hành vi → implementation mới →
ghép service hoặc firmware → chạy gate của module → PR reviewer. Không xóa tests
để làm xanh, không để stub trả KNOWN/ACK/PASS giả. Muốn đổi contract phải cập nhật
các bên cùng PR. BASE/FW/CV/BE/INT là đơn vị giao Codex, không giao cả repo một lượt.

## Giai đoạn nâng cấp 20/10 — việc còn lại tới demo

| Issue ưu tiên | Nội dung | Acceptance cần thiết |
|---|---|---|
| UPG-01 | Verify board revision, programmer, PIR/buzzer/nguồn ổn định | Arduino compile+upload/HIL, log control-lost OFF |
| UPG-02 | Đèn trắng/IR, enclosure/nguồn, bật tắt illumination an toàn | GPIO pinout xác nhận, không brownout, thu khung hình day/night |
| UPG-03 | Labeled quality calibration + face enrollment theo ánh sáng | Validation/holdout cho known/unknown/unidentified mỗi điều kiện, quality/threshold report |
| UPG-04 | PWA & Telegram live qua Tailscale | 4G phone ngoài LAN, link HTTPS private, ẩn tab/logout, event photo thật |
| UPG-05 | Deploy cloud inference/API/SQLite persistent/analytics | Separate cloud host, bearer/TLS, ML weights + cloud report DB + log RTT |
| UPG-06 | Night/cloud negative tests | Cloud timeout fail closed, lens che/mờ không false unknown alarm, camera reboot |

Không khẳng định Level 3 trong slide trước khi `UPG-05` hoàn thành. Cùng lúc giữ local demo đáng tin cậy làm kế hoạch B; trình bày trung thực level thực tế.
