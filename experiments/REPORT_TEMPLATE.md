# Báo cáo một vòng thử — TEMPLATE, chưa có kết quả

**ID / ngày / owner / reviewer:** …

**Mục đích/gate:** G…; giả thuyết hoặc lỗi cần kiểm.

**Setup:** board/module/nguồn, vị trí camera/ánh sáng/vùng, máy CPU/RAM/OS, core/library/Python/dependency, commit source, camera/model hashes.

**Dữ liệu:** consent, số người/gallery/sample/lượt, alias, phiên enrollment/validation/test, nhãn eligible và tiêu chí, path manifest/hash. Dataset/ảnh/vector ở nơi private, không đưa vào repo.

**Thay đổi so với lần trước:** Một nhóm thông số hoặc một thay đổi code; ghi chính xác giá trị. Holdout có được mở chưa; nếu đã tune theo nó thì không gọi independent test.

**Lệnh và kết quả:** Tool report path/checksum; số đếm/mẫu số từng metric, p50/p95/scope timing. Không điền số từ seed hoặc simulator vào AI accuracy.

| Kiểu lượt / metric | N | Đúng / lỗi / missed / unresolved | Kết quả so gate |
|---|---:|---|---|
| Known | | | CHƯA ĐO |
| Unknown tổng / eligible | | | CHƯA ĐO |
| Negative từng nhóm | | | CHƯA ĐO |
| Hai người / association | | | CHƯA ĐO |
| CPU / E2E / safety | | | CHƯA ĐO |

**Phân tích lỗi:** Lượt thất bại, nguyên nhân quan sát được và điều còn chưa rõ; ảnh/log không có consent không gửi lên Git/cloud.

**Kết luận của reviewer:** PASS/FAIL/chưa đủ dữ liệu; giới hạn gallery/camera được duyệt; bước kế. Không tự ký thay owner/reviewer.
