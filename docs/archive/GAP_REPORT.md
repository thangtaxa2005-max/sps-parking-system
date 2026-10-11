# GAP Report — SPS v7

Ngày rà soát: 2026-10-06  
Phạm vi: `docs/SPS_design_v7.md` và toàn bộ file hiện có trong workspace.

## Tóm tắt

Workspace hiện là bộ khung chưa có phần mềm chạy được. Ngoài tài liệu thiết kế, README và `.gitignore`, các thư mục `backend/`, `data/`, `firmware/`, `simulator/` và `tests/` chưa có file. Không tìm thấy cơ sở dữ liệu, schema, firmware hay cấu hình ứng dụng hiện hữu. Vì vậy chưa có mã để đánh giá khả năng tái sử dụng hoặc xác nhận xung đột với thiết kế; cũng chưa có dữ liệu cũ để migrate.

Git đã được khởi tạo trên nhánh `master`; trạng thái working tree tại thời điểm rà soát sạch.

## 1. Phần khớp thiết kế và có thể tái sử dụng

| Hiện trạng | Đánh giá |
|---|---|
| `docs/SPS_design_v7.md` | Là đặc tả nguồn cho kiến trúc, phần cứng, quy tắc quyết định, dữ liệu và các giai đoạn triển khai. Tiếp tục dùng làm chuẩn nghiệm thu. |
| `README.md` | Có mô tả ngắn về dự án và liên kết tương đối tới tài liệu thiết kế. Có thể tiếp tục dùng, nhưng trạng thái “Đang ở Bước 2” cần cập nhật khi hoàn thành báo cáo/giai đoạn rà soát. |
| `.gitignore` | Đã loại trừ môi trường Python, `.env`, dữ liệu trong `data/`, cơ sở dữ liệu SQLite và artifact PlatformIO; phù hợp với cấu trúc dự kiến. Cần bảo đảm migration/schema và các file cấu hình mẫu không bị loại trừ. |
| Các thư mục `backend/`, `data/`, `firmware/`, `simulator/`, `tests/` | Mới là chỗ đặt thành phần tương lai, chưa có implementation hoặc quy ước nội bộ để tái sử dụng. |

Không có module nghiệp vụ, giao thức, giao diện, test hoặc schema hiện hữu nào có thể đánh giá là đã đáp ứng thiết kế.

## 2. Mâu thuẫn hoặc phần sẽ bị thay thế

Không phát hiện mâu thuẫn thực tế vì không có mã ứng dụng hiện hữu. Cụ thể, chưa thấy hệ thống dùng OCR/biển số làm điều kiện mở cổng, schema cũ, firmware cũ hoặc giao thức cũ để cần thay thế.

Các yêu cầu dưới đây là những điểm cần giữ làm tiêu chí khi triển khai, không phải mâu thuẫn đã quan sát được:

- RFID là căn cứ quyết định mở cổng; OCR chỉ gắn biển số vào lịch sử/cảnh báo, không được trở thành điều kiện cho phép xe qua.
- Quy tắc cổng vào và ra khác nhau: hết hạn chặn vào nhưng không chặn ra.
- Trạng thái `in_lot` chỉ đổi khi xác nhận xe đã qua hoặc quản lý sửa có ghi audit; timeout không vào không được đổi trạng thái.
- Web quản lý và bot Telegram phải gọi chung lớp API/nghiệp vụ, không chứa luật nghiệp vụ riêng hoặc truy cập DB trực tiếp.
- Chụp ảnh, OCR, ghi DB và gửi thông báo không được chặn phản hồi quyết định mở cổng.
- Firmware phải dùng máy trạng thái không chặn, hai cổng độc lập và giữ cổng đóng khi mất kết nối.

## 3. Lược đồ dữ liệu và phương án migration

### Lược đồ đích

Tạo schema mới theo mục 6 của thiết kế, tối thiểu gồm:

- `units`: mã căn hộ và hạn mức xe theo loại nếu có ghi đè mặc định.
- `users`: vai trò, căn hộ, Telegram chat đã liên kết và thông tin liên kết dùng một lần/hạn dùng.
- `vehicles`: biển số, loại xe, căn hộ, loại đối tượng, hạn gói và `in_lot`.
- `credentials`: loại chứng danh, UID, xe liên quan, trạng thái `active`/`locked`/`lost` và thời điểm cấp; cần giữ lịch sử thẻ cũ khi cấp lại.
- `registration_requests`: thông tin đăng ký, trạng thái duyệt và lý do.
- `events`: cổng/hướng, kết quả/lý do, liên kết thẻ/xe, ảnh, OCR/độ tin cậy/cờ lệch và các mốc thời gian cần đo.
- `notifications`: người nhận, mức, nội dung/ảnh, trạng thái gửi.
- `settings`: sức chứa, hạn mức mặc định, ngưỡng OCR và thời hạn lưu ảnh.

Khi thiết kế migration, bổ sung khóa ngoại, chỉ mục tra cứu UID/trạng thái xe/thời gian sự kiện, ràng buộc giá trị enum và các trường audit cần thiết. Trạng thái trong RAM phải được khởi tạo từ DB và cập nhật đồng bộ khi thao tác quản trị làm thay đổi thẻ hoặc `in_lot`.

### Cách xử lý dữ liệu cũ

Hiện không có DB hoặc dữ liệu cũ trong workspace, nên không thể lập ánh xạ cột cụ thể hay viết migration chuyển đổi có thể chạy và kiểm chứng. Ở trạng thái hiện tại, hướng phù hợp là migration khởi tạo schema rỗng, không giả định có dữ liệu legacy.

Nếu sau này phát hiện DB cũ bên ngoài workspace:

1. Xác định định dạng/schema và lập bản sao lưu bất biến trước khi thay đổi.
2. Viết migration riêng có thể chạy lại an toàn; ánh xạ cư dân/căn hộ sang `units`/`users`, xe sang `vehicles`, và thẻ sang `credentials`. Không suy diễn UID, trạng thái thẻ hoặc xe đang trong bãi nếu dữ liệu nguồn không đủ.
3. Chuyển các lượt ra vào cũ sang `events` khi có thể giữ được thời gian và ý nghĩa; dữ liệu thiếu thì giữ nguyên nguồn hoặc đánh dấu không xác định, không tạo sự kiện giả.
4. Kiểm tra bản ghi mồ côi, UID trùng, biển số/căn hộ thiếu, trạng thái `in_lot` và số lượng xe; chạy thử trên bản sao DB trước khi áp dụng.
5. Chỉ chuyển sang DB mới sau khi kiểm tra đối soát đạt; giữ bản sao cũ và phương án quay lui cho đến khi nghiệm thu.

## 4. Thứ tự triển khai đề xuất và rủi ro

1. **Hoàn tất quyết định cấu hình và hợp đồng dữ liệu/giao thức:** chốt sức chứa từng loại xe, hạn mức mặc định, enum/mã lý do, timestamp và định dạng WebSocket.
2. **Schema, migration khởi tạo, lớp dữ liệu, cache và bộ quyết định:** triển khai luật mục 5–6 cùng unit test mọi nhánh; đặc biệt kiểm chứng hết hạn ở cổng ra và đồng bộ cache.
3. **WebSocket và simulator:** giữ kết nối, xác thực API key, phục hồi kết nối, trả quyết định trước tác vụ nền; đo tap-to-decision.
4. **Firmware ESP32:** máy trạng thái, lọc cảm biến, OLED và an toàn khi mất kết nối; biên dịch trước, sau đó kiểm tra phần cứng theo checklist.
5. **Camera, ảnh, OCR và retention:** xử lý nền, kiểm tra ngưỡng OCR/cờ lệch, chính sách lưu ảnh thường và ảnh cảnh báo.
6. **API và web quản lý:** xác thực quản lý, duyệt đăng ký, quản lý thẻ/xe, sửa trạng thái có audit, mở cổng từ xa và settings.
7. **Bot Telegram/thông báo:** liên kết một lần, phân quyền theo chat ID, mức thông báo và retry nền.
8. **Tích hợp và kiểm thử end-to-end:** chạy toàn bộ kịch bản mục 7, đo tối thiểu 50 lần và cập nhật README/giới hạn đã biết.

**Rủi ro chính**

- Sai lệch `in_lot` nếu sự kiện xe qua bị mất, xe bám đuôi, hoặc thao tác sửa thủ công thiếu audit; cần luồng đối soát và cảnh báo rõ ràng.
- Sai thứ tự xử lý tap có thể làm DB, OCR, Telegram hoặc camera tăng độ trễ mở cổng; cần bảo đảm quyết định được phản hồi trước các tác vụ nền.
- Mất WebSocket/mất điện hoặc cảm biến siêu âm nhiễu có thể khiến barrier hoạt động không an toàn; cần kiểm tra trạng thái lỗi và thử nghiệm phần cứng thực tế.
- UID thẻ, API key, Telegram chat ID, ảnh và thông tin căn hộ cần được bảo vệ; không commit token/secret, giới hạn truy cập ảnh và xác thực mọi lệnh quản trị.
- OCR biển số mô hình nhỏ có độ tin cậy không ổn định; phải giữ OCR ở vai trò ghi nhận, không dùng làm căn cứ từ chối/mở cổng.
- Chưa có dữ liệu/cấu hình và firmware để thử migration, hiệu năng hoặc tích hợp phần cứng; các kết quả đó chỉ xác nhận được ở những giai đoạn sau.

## Các điểm cần quyết định

1. Chốt sức chứa ô tô và xe máy trong tổng khoảng 20 chỗ; đồng thời xác nhận hạn mức mặc định mỗi căn (thiết kế đang gợi ý 1 ô tô + 2 xe máy).
2. Xác nhận có bắt đầu từ schema/DB rỗng hay cần cung cấp thêm DB/mã nguồn cũ nằm ngoài workspace để xây migration chuyển đổi cụ thể.
## Quyết định đã chốt

1. Sức chứa: 8 ô tô + 12 xe máy.
2. Hạn mức mặc định mỗi căn: 1 ô tô + 1 xe máy (có thể override theo căn).
3. Schema: khởi tạo mới hoàn toàn, không migrate.
## Quyết định đã chốt

1. **Sức chứa:** 8 ô tô + 12 xe máy (tổng 20 chỗ, đúng phạm vi ~20 chỗ của thiết kế).
2. **Hạn mức mặc định mỗi căn:** 1 ô tô + 1 xe máy, có thể override theo từng căn hộ qua trường `units`.
3. **Schema:** khởi tạo mới hoàn toàn, không migrate dữ liệu cũ.
4. **Lý do chọn sức chứa:** tỷ lệ 40/60 phản ánh thực tế chung cư Việt Nam (nhiều xe máy hơn ô tô); 8 chỗ ô tô đủ để demo "hết chỗ ô tô" với mô hình thu nhỏ.
5. **Lý do chọn hạn mức 1+1:** cho phép tối đa 8 căn có ô tô và 12 căn có xe máy, đủ dùng cho demo ~6-10 căn hộ; override theo căn nếu cần thêm.