# Người lạ luôn chưa xác định — điều tra 02/11/2026

**Hiện trạng vận hành 21:46 ngày 02/11:** người dùng yêu cầu bật ngay và mở full
luồng; đã ARM qua API, AI/camera/Telegram ready. Profile mới là approval vận hành
của operator, không phải nghiệm thu accuracy/HIL; xem quyết định mới nhất trong
[RECOGNITION_UPGRADE](RECOGNITION_UPGRADE.md). Trạng thái bị chặn sau triển khai
21:29 dưới đây là lịch sử, không còn trạng thái hiện tại.

**Quyết định sau nghiên cứu:** người dùng đã duyệt phương án, sửa duy nhất yêu
cầu UNKNOWN thành **2 ảnh khác nhau trong ít nhất 400ms**. Bảng đề nghị 3/600ms
bên dưới là lịch sử nghiên cứu, được quyết định này thay thế. Phần mềm thực hiện
cặp tham số UNKNOWN riêng, giữ cả hai đường KNOWN và các gate còi như mô tả.
Approval không chứng nhận accuracy/HIL hoặc tự cho phép ký calibration mới.

**Implementation:** source đã triển khai Oracle 21:29 ngày 02/11; local
202 pass/2 skip, Linux 203 pass/1 skip, lint/format/HTTP smoke pass. Native replay
source thật khớp sensitivity đề nghị. Gallery và calibration giữ nguyên; source
mới khởi động DISARMED và chặn ARM bằng profile cũ (`CAMERA_PROFILE_CHANGED`).
Chi tiết hiện trạng ở [RECOGNITION_UPGRADE](RECOGNITION_UPGRADE.md); các phần còn
lại của tài liệu này là bằng chứng nghiên cứu trước implementation.

**Kết luận:** đã tái hiện một nguyên nhân trực tiếp trên ảnh sự kiện thật của đợt
20:05–20:17: quality gate `blur_min=60` loại nhiều mặt trước khi SFace được chạy.
Các mặt đó cho score dưới `reject=0.30` khi bỏ riêng gate độ nét trong phép chẩn
đoán. Nới ngưỡng so khớp người lạ hoặc thay toàn bộ model chưa phải cách sửa đúng
điểm nghẽn đã tìm thấy. Có thêm vấn đề phát hiện/ghép body, ảnh hưởng continuity,
chiều tiếp cận và còi; nới quality một mình chưa bảo đảm hết mọi lượt UNCERTAIN.

Phần điều tra dưới đây ghi trạng thái **trước implementation**. Không có lần
ARM, restart hoặc gửi thông báo thử nào do các probe nghiên cứu thực hiện.
Trạng thái implementation/kiểm chứng mới xem
[RECOGNITION_UPGRADE](RECOGNITION_UPGRADE.md).

## 1. Phạm vi và nguồn sự thật

- Người dùng xác nhận AI chạy trên cloud, người quen nhận tương đối ổn; đợt lỗi
  cần xét là **20:05–20:17 ngày 02/11, Asia/Saigon**, tức 13:05–13:17 UTC.
  Người lạ là **người thật đứng trước camera**, không phải ảnh/video trên màn hình.
- Source đang chạy trên Oracle khớp checkout `feat/doorstep-visit-recognition`,
  HEAD `b004fdd`, qua hash của vision/policy/worker/tracker/service/video/manifest.
- VM x86_64, 2 logical CPUs; backend, AI và SQLite cùng ở VM.
  `cloud.inference=local` có nghĩa local trong VM, không phải AI chạy trên laptop.
- NanoDet/YuNet/SFace 128d, gallery 1 người active / 10 mẫu committed.
  Trong audit từ đầu cửa sổ thử đến lúc đọc chỉ có `admin_arm`, không có thao tác
  thay gallery. Đây không phải chứng minh ground truth cho từng mặt trong ảnh.
- Đã fetch `origin/dev`; tip vẫn là `dd192ee`. Không checkout hoặc merge đè nhánh
  đang làm. Tệp lắp ráp phần cứng untracked của người dùng không thuộc task này.

Chỉ đọc DB ở chế độ `mode=ro`, cấu hình và log. Ảnh sự kiện vốn đã có trên Oracle
được đọc vào RAM và xử lý ngay trên Oracle; không tải ảnh/vector về máy, không
upload ảnh mới, không sao chép DB/biometric lên Git hoặc dịch vụ khác. Chỉ các số
đo và metadata cần thiết được ghi vào `runtime/recognition_research/`, ignored Git.

## 2. Bằng chứng đúng đợt lỗi

Trong cửa sổ đã sửa có 16 event, gồm 11 completion, 4 warning và 1 known_visit.
11 completion gồm 10 UNCERTAIN và 1 KNOWN; không có UNKNOWN.
Lý do cuối của 10 lượt UNCERTAIN là 6 NO_FACE và 4 FACE_BLURRED.
10/11 completion có direction UNRESOLVED; một lượt UNCERTAIN có INCOMING.
Không có event trước 20:14:46 trong chính cửa sổ này.

Đây là số event/track, **không phải 11 người hoặc 11 phép thử độc lập**.
Một người có thể tạo nhiều track/lượt. Không có trace từng ảnh được bật cho đợt
này; RAM ring đọc sau đó không còn observation của đợt thử. Vì vậy không suy ra
số phiếu, score mọi ảnh hoặc nguyên nhân toàn bộ lượt từ lý do cuối cùng.

14 event có media đọc được, nhưng 2 event dùng nội dung JPEG trùng nhau.
Sau khử trùng theo nội dung có **13 JPEG khác nhau** để replay:

| Replay ở cấu hình đang chạy | Số observation trên 13 ảnh |
|---|---:|
| UNCERTAIN / FACE_BLURRED | 9 |
| NO_FACE | 4 |
| KNOWN | 1 |

Số observation lớn hơn số ảnh vì một ảnh có thể có nhiều người.
Cả 13 ảnh qua environment gate. Không có cơ sở quy lỗi chính cho gate cảnh tối.
Một ảnh người quen có blur khoảng 249 và score 0.821.
Nhiều mặt khác rộng trên 100 px, detector confidence trên 0.90, nhưng blur dưới 60.

Ví dụ số đo trên các ảnh sự kiện, không phải metric accuracy:

| Thời điểm event, giờ Việt Nam | Cạnh mặt nhỏ nhất, px | Blur | Score khi bỏ riêng gate blur | Kết quả frame khi bỏ gate |
|---|---:|---:|---:|---|
| 20:14:46 | 110.4 | 8.24 | 0.141 | UNKNOWN |
| 20:14:57 | 163.7 | 17.57 | 0.213 | UNKNOWN |
| 20:15:01 | 142.3 | 9.18 | 0.167 | UNKNOWN |
| 20:15:44 | 124.4 | 52.12 | 0.227 | UNKNOWN |
| 20:16:04 | 116.6 | 11.38 | 0.272 | UNKNOWN |
| 20:16:18 | 117.9 | 12.31 | 0.224 | UNKNOWN |

Thời điểm trên là lúc ghi event; JPEG đại diện có thể được lấy trước đó trong
cùng lượt. Không dùng nó như timestamp exposure. Score không phải xác suất.
Ground truth từ người dùng là ở mức đợt thử; từng khuôn mặt chưa được người review
gán nhãn riêng. Không dùng kết quả tự phân loại để tính false-known/false-unknown.

## 3. Nguyên nhân và những thay đổi không giải quyết được nó

Luồng hiện tại là detector → association → size/landmarks/blur/exposure → SFace
→ matching → temporal policy → geometry/dwell → event/còi.
`quality()` trả về FACE_BLURRED ngay khi Laplacian variance trên crop 112×112
nhỏ hơn 60. Không có embedding/score cho frame đó trong luồng bình thường.

Vì vậy:

1. **Nới matching chưa giải quyết các frame bị loại quality.** Giữ score thật
   trong phép chẩn đoán, nhiều mặt đã thấp hơn 0.30; không cần tăng reject để
   chúng rơi vào UNKNOWN. Muốn mở rộng UNKNOWN theo score là *tăng* reject, không
   phải giảm reject; điều đó có thể tăng báo người quen thành người lạ.
2. **Chỉ giảm số phiếu không giúp khi không có phiếu UNKNOWN.** Giữ 3 phiếu và
   span 600 ms trước; đây không phải nút thắt trực tiếp của các JPEG đã tái hiện.
3. **Chỉ giảm face confidence vẫn bị gate blur chặn.** Ngưỡng detector 0.80 tìm
   thêm 2 mặt bị bỏ ở 0.90 trên bộ ảnh này; cả hai vẫn bị blur gate 60 loại.
   Hạ tiếp đến 0.70 không bổ sung mặt nào qua quality hiện tại.
4. **Chỉ đổi SFace sang model khác vẫn còn quality gate.** Không có bằng chứng
   rằng đổi recognizer sẽ sửa một frame bị chặn trước recognizer.

Một giá trị Laplacian không phải thước đo đầy đủ về khả năng nhận diện. Nó phụ
thuộc nội dung, crop, độ phân giải, resize, nhiễu và chi tiết trong ảnh. Chưa đủ
bằng chứng để gọi mọi ảnh blur 8–18 là mất thông tin danh tính; cũng chưa đủ để
coi chúng đều an toàn. NIST phân biệt nhiều yếu tố chất lượng và liên hệ chất
lượng với lỗi nhận diện, thay vì một ngưỡng Laplacian chung cho mọi camera.
Xem [NIST FATE Quality](https://pages.nist.gov/frvt/html/frvt_quality.html) và
[OpenCV Laplacian](https://docs.opencv.org/4.x/d5/db5/tutorial_laplace_operator.html).

Đối chứng nhỏ: lấy đúng một ảnh known đã lưu, làm mờ Gaussian trong RAM, giữ
landmark cố định để cô lập ảnh hưởng blur. Khi blur giảm từ 249 xuống 11.68,
score vẫn 0.802; xuống 6.63, score vẫn 0.777. Điều này chứng minh ngưỡng 60 loại
được cả ảnh còn hữu ích trong đối chứng này. Đây là **biến đổi nhân tạo của một
ảnh**, không phải validation người quen đa phiên, không chứng nhận false alarm.

Không kết luận lens mất nét hoặc chỉ do thiếu sáng từ các scalar này. Cần phân
biệt defocus, chuyển động, exposure và sự phụ thuộc texture của metric khi kiểm
camera tại vị trí dùng thật. Sharpen/CLAHE có thể làm thay đổi số đo mà không khôi
phục chi tiết; không đưa enhancement trước quality chỉ để vượt gate.

## 4. Body/geometry là điểm nghẽn thứ hai

Ở 5/13 JPEG, baseline thấy mặt nhưng không thấy body. Một số ảnh khác có body
nhưng mặt không ghép được vào phần trên body, tạo face-only observation.
Face-only có thể có bằng chứng danh tính nhưng không được tạo chiều A → B hoặc
cấp quyền còi. Đổi body ↔ face-only cũng làm tracker cắt continuity theo thiết kế.

Đã khảo sát riêng ngưỡng person trong process replay, giữ face threshold 0.80
và blur candidate 6:

| Person threshold | UNKNOWN có geometry hợp lệ | UNKNOWN face-only | Nhận xét |
|---|---:|---:|---|
| 0.50 | 5 | 5 | Còn thiếu body/association |
| 0.35 | 5 | 5 | Không tăng UNKNOWN được ghép body |
| 0.25 | 6 | 4 | Thêm nhiều bbox, sinh association mơ hồ trong ảnh known |

Đây là số observation trên tập JPEG chẩn đoán, không phải recall theo lượt.
Không chốt hạ person threshold hoặc bịa body bbox từ mặt để có còi. Nới xuống
0.25 làm ảnh có known trở thành association mơ hồ trong replay, là tác dụng phụ
đã quan sát được. Giữ person threshold 0.50 trong bản sửa đề nghị.

Cần hiển thị riêng “danh tính đã/ chưa xác nhận” và “chưa đủ chiều tiếp cận/body
để cảnh báo”. Không biến direction UNRESOLVED thành kết luận về danh tính.
Việc lưu kết quả UNKNOWN đã xác nhận theo lượt, độc lập với việc có phát alarm,
cần review tương tự cách KNOWN đã được giữ trong lịch sử; không giữ bằng chứng
qua track mới, reentry, conflict hoặc revision khác.

## 5. Nhánh dev có phù hợp cho bản sửa nhanh?

| Khía cạnh | Nhánh hiện tại | `origin/dev` dd192ee |
|---|---|---|
| Face engine | YuNet + SFace | SCRFD-10GF + ResNet50, InsightFace buffalo_l |
| Embedding | 128d | 512d |
| Blur gate | 60 | Vẫn dùng cơ chế/ngưỡng này |
| Matching accept/reject/margin | 0.45 / 0.30 / 0.08 | Giữ các số cũ, chưa chứng minh cho model mới |
| Luồng nhận diện sớm, trace, lượt ngắn, strong-known | Đã có | Thiếu các nâng cấp riêng của nhánh hiện tại |
| Gallery đang dùng | Dùng được | Cần reenroll với model/preprocess mới |

Không merge nguyên nhánh. Nó là ứng viên nâng recognizer/detector về sau, không
phải bản vá có bằng chứng cho blur gate hiện tại. Không dùng nguyên thresholds
của SFace cho embedding của engine khác. Ngay cả ví dụ OpenCV dùng cosine 0.363
cho một bài toán so cặp; đó không phải giá trị được chứng nhận cho hệ open-set
với max qua nhiều mẫu của repo này.
[OpenCV tutorial](https://docs.opencv.org/4.x/d0/dd4/tutorial_dnn_face.html).

Bằng chứng compute đã có từ lần nghiên cứu trước trên Oracle 2 CPUs: candidate
native có workload tổng hợp một embedding p50 khoảng 483 ms, hai embedding
khoảng 595 ms, 15 lượt đo. Đây là phép đo tensor/synthetic, không phải latency
camera hoặc accuracy. Không coi model lớn hơn là cách chắc chắn nhanh hơn hay
nhận đúng hơn trong lượt ngắn. Các package/weights có điều kiện license riêng;
[InsightFace v0.7 README](https://github.com/deepinsight/insightface/blob/v0.7/python-package/README.md)
phân biệt license code với weights cho nghiên cứu phi thương mại.

## 6. Một phương án nâng cấp đề nghị

Giữ model/gallery hiện tại. Làm một bản sửa tập trung vào **chất lượng ảnh giám
sát và giải thích kết quả**, không thay framework hoặc policy còi.

| Thành phần | Thay đổi đề nghị để review |
|---|---|
| Quality | Tách ngưỡng blur giám sát khỏi enrollment; không dùng chung ngưỡng 60 cho hai workflow |
| Giám sát | Candidate blur floor **6**, face confidence **0.80**; đây là candidate từ bộ chẩn đoán, chưa là baseline đã nghiệm thu |
| Enrollment | Giữ blur **60**, detector confidence **0.90**, mặt tối thiểu **100 px**; giữ gallery hiện có |
| Matching | Giữ accept **0.45**, reject **0.30**, margin **0.08** |
| Temporal | Giữ UNKNOWN **3 ảnh / tối thiểu 600 ms**; không cộng ảnh mờ nặng, NO_FACE hoặc điểm mơ hồ thành UNKNOWN |
| Body/actuation | Giữ person confidence **0.50**, A → B, dwell, cooldown, STOP/revision/freshness và hard cap |
| Kết quả/lý do | Ghi lý do và số phiếu trong cả lượt; phân biệt quality, score, thiếu phiếu và geometry, không chỉ lý do của ảnh cuối |

Trong replay riêng của 13 JPEG, face 0.80 + blur 6 làm 10 trong 11 mặt có score
thuộc nhánh UNKNOWN đi qua quality; 1 mặt blur 4.90 vẫn bị loại. Mặt KNOWN vẫn
KNOWN và còn 2 ảnh không tìm thấy mặt. Trong 10 observation UNKNOWN, 5 vẫn là
face-only. **Không diễn giải 10/11 thành tỷ lệ nhận đúng người lạ hoặc còi.**
Không có chuỗi frame liên tục để áp temporal/pending/freshness cho đợt này.

Floor 6 khá thấp so với baseline; nó chỉ được đưa ra vì có bằng chứng cụ thể ở
đúng camera, không phải tham số khuyến nghị chung. Nguy cơ cần kiểm là mặt người
quen chất lượng thấp có thể thành UNKNOWN. Đối chứng một ảnh ở trên không loại
trừ nguy cơ đó. Không hạ xuống 0 để “bắt hết”; không dùng ảnh quá mờ làm bằng
chứng người lạ; không tự ký calibration từ các JPEG chẩn đoán này.

Phạm vi implementation khi được chốt: `vision.py`/config/defaults/hash review,
test boundary cho monitoring so với enrollment và quality fail-closed; lý do
matching/temporal được allowlist trong diagnostics, UI thể hiện đúng nguyên nhân.
Nếu bổ sung giữ kết quả UNKNOWN theo lượt, phải test conflict, stale, reentry và
face-only không còi. Không đưa logic AI/state vào HTTP hoặc thêm worker thứ hai.

Trước triển khai: kiểm software, một staged native replay, review profile mới;
backup source/config/calibration để rollback. Không tự sửa hash của calibration
cũ hoặc ghi validated để vượt gate. Kích hoạt hardware vẫn theo workflow review
profile và operator ARM. Không cần chạy lại hàng loạt model hay yêu cầu thu đi
thu lại để xác định nút thắt đã tái hiện.

Khi người dùng có thể thử lại, một phiên xác nhận tập trung cần chứa cả người
quen và người lạ thật, cùng đường A → B, có trace metadata; đối chiếu cả nhận diện,
false UNKNOWN của người quen, continuity, event/ACK/còi. Đây là phép kiểm thực tế
còn thiếu, không được thay bằng test giả. Nếu body vẫn bị cắt/không ghép được,
cần xử lý khung hình/camera hoặc detector trong issue rõ ràng, không nới quyền
còi cho face-only để coi là xong.

## 7. Kiểm chứng và giới hạn

- Venv repository: 57 test hiện có về matching/vision math, policy, lượt ngắn,
  strong-known và diagnostics **PASS**. Đây là kiểm baseline, không phải test
  một bản nâng cấp đã implement. Một warning Starlette/httpx đã tồn tại.
- Ruff lint và format check `backend tools tests` **PASS**, 63 file đã đúng format.
- Remote native replay 13 JPEG sau khử trùng, detector ablation, bypass blur chỉ
  trong process chẩn đoán, person-threshold sensitivity và một synthetic known
  control chạy thành công. Không can thiệp process/model worker production.
- Chưa có ground truth từng mặt, chuỗi ảnh đủ temporal cho đúng đợt lỗi, accuracy
  nhiều người/đa phiên, kết quả candidate trên holdout, hoặc HIL candidate.
  Không tune bằng holdout cũ. Không claim “100% dứt điểm” từ ảnh đại diện.
- Lần nghiệm thu người lạ trước đó là xác nhận của người dùng; dữ liệu mang U01
  từng bị xác định là chính người đã đăng ký. Không dùng nó làm số đo người lạ.
- Log tổng 2 giờ có lỗi timeout/capture-age, nhưng chưa ghép đúng từng lượt của
  cửa sổ này. Không quy lỗi mạng cho riêng đợt thử chỉ từ số tổng đó.

Bằng chứng local (ignored): `unknown_deployment_inspection_20261102.json`,
`unknown_event_replay_2005_2017.json`, `unknown_blur_known_control.json` và các
probe tương ứng trong `runtime/recognition_research/`. Không đóng gói ảnh, DB,
credentials hoặc vector vào report/source.
