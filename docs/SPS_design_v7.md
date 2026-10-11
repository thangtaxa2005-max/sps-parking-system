> Tài liệu thiết kế gốc, giữ làm lịch sử. Trạng thái và thông số hiện hành: [HANDOFF](HANDOFF.md), [README](../README.md). Gap report ban đầu: [archive/GAP_REPORT.md](archive/GAP_REPORT.md).

# SPS v7: Hệ thống đỗ xe thông minh cho chung cư (bản demo mô hình thu nhỏ)

Tài liệu này tổng hợp mọi quyết định thiết kế đã chốt, sau đó chia việc thành các giai đoạn kèm lệnh dán sẵn cho AI trong VS Code. Nên lưu file này vào dự án (ví dụ `docs/SPS_design_v7.md`) để các lệnh ở Phần B tham chiếu tới.

---

# PHẦN A. THIẾT KẾ

## 1. Phạm vi

**Có trong bản này**
- Chung cư ~20 chỗ (ô tô và xe máy), dựng bằng mô hình thu nhỏ.
- Chỉ cư dân. Xe không đăng ký thì không được vào.
- Hai cổng riêng (vào, ra), một ESP32, một máy tính chạy toàn bộ phần mềm.
- Xác thực bằng thẻ RFID. Camera và AI chỉ để chụp ảnh và gắn biển số vào lịch sử.
- Web cho quản lý. Bot Telegram cho cư dân.

**Ngoài phạm vi (để phiên bản sau)**
- Khách vãng lai, thanh toán theo lượt, tính phí.
- Ứng dụng di động riêng cho cư dân.
- Nút mở khẩn cấp, UPS, mạng dự phòng, cảm biến từng chỗ đậu.

## 2. Kiến trúc

```
[RFID RC522 x2] [Siêu âm x2] [Servo x2] [OLED] [LED] [Còi]
                    |
                 [ESP32]  <-- WebSocket giữ sẵn (có API key) -->  [Máy tính: Flask + SQLite]
                                                                   |- Bộ quyết định (cache trong RAM)
[Camera IP x2 (đặt phía sau xe)] --- luồng hình ---------------->  |- Lấy ảnh, lưu ảnh, OCR nền
                                                                   |- Web quản lý
                                                                   |- Bot Telegram (cư dân + quản lý)
```

Thiết kế máy chủ theo kiểu API: bot Telegram và web chỉ là hai "khách hàng" của cùng một lớp lõi, để sau này thêm ứng dụng di động thì không phải đổi phần lõi.

## 3. Phần cứng và bản đồ chân ESP32

| Thiết bị | Chân | Ghi chú |
|---|---|---|
| RC522 (dùng chung) SCK / MISO / MOSI | GPIO18 / 19 / 23 | SPI mặc định, cấp 3.3V |
| RC522 RST (nối chung hai đầu đọc) | GPIO16 | |
| RC522 cổng vào, SS | GPIO5 | |
| RC522 cổng ra, SS | GPIO17 | |
| Siêu âm cổng vào | TRIG=GPIO27, ECHO=GPIO14 | |
| Siêu âm cổng ra | TRIG=GPIO32, ECHO=GPIO33 | |
| Servo cổng vào / ra | GPIO13 / GPIO25 | |
| OLED 128x64 | SDA=GPIO21, SCL=GPIO22, 0x3C | |
| LED | GPIO26 | |
| Còi | GPIO4 | |
| Còn trống | GPIO34, GPIO35 | Chỉ nhận. Dành cho nút khẩn cấp hoặc đầu đọc QR sau này |

Lưu ý đấu dây:
- ECHO của HC-SR04 xuất 5V: dùng cầu chia áp ECHO → 1 kΩ → GPIO, GPIO → 2 kΩ → GND.
- Servo dùng adapter 5V riêng, nối chung GND với ESP32, gắn tụ 470-1000 µF song song nguồn servo.
- Dây SPI giữ ngắn (dưới ~30 cm). Nếu chập chờn thì hạ tốc độ SPI xuống ~1 MHz.
- Cảm biến siêu âm đặt **ngang làn, trên đường quét của thanh chắn**, cao bằng hoặc thấp hơn thanh khi hạ xuống, và phải có **tấm chắn phẳng ở phía đối diện** để lúc trống vẫn có tiếng vọng. Vạch dừng nằm **trước** tia cảm biến.
- Camera đặt **phía sau xe**: cổng vào đặt phía ngoài nhìn về thanh chắn, cổng ra đặt phía trong nhìn về thanh chắn. Biển in cho xe mô hình nên to hơn tỷ lệ thật một chút, giấy mờ, đúng phông chữ biển số Việt Nam, có cả biển dài một dòng và biển vuông hai dòng.

## 4. Máy trạng thái của mỗi cổng (chạy trên ESP32, hai cổng độc lập, không dùng delay chặn)

1. **IDLE:** chờ quẹt thẻ. OLED hiện "Mời quẹt thẻ" và số chỗ trống từng loại xe.
2. Quẹt thẻ: ESP32 gửi UID lên máy tính, chờ kết quả. Từ chối thì OLED hiện lý do khoảng 2 giây rồi về IDLE.
3. **OPENING:** được phép thì servo mở 90°, còi kêu, LED nháy, máy tính chụp ảnh (song song, không chặn).
4. **OPEN_WAIT:** đồng hồ 2 giây bắt đầu khi servo **đạt 90°**. Hết 2 giây mà cảm biến chưa thấy gì thì sang CLOSING, ghi sự kiện "quẹt nhưng xe không vào", **không đổi trạng thái xe**.
5. **PASSING:** cảm biến thấy xe thì giữ mở. Khi không thấy gì liên tục **0,5 giây** thì sang CLOSING và báo máy tính "xe đã qua" (lúc đó mới cập nhật "ở trong bãi"/"đã ra" và số đếm). Giữ mở quá 30 giây thì cảnh báo quản lý, còi vẫn kêu.
6. **CLOSING:** nếu cảm biến thấy vật khi đang đóng thì dừng và mở lại ngay (về PASSING).
7. Quẹt thẻ khi cổng không ở IDLE thì bỏ qua. Quẹt lại ngay sau khi đóng là hợp lệ, không khóa chờ.

Lọc cảm biến: đo mỗi 50-60 ms, lấy trung vị của 3 lần đo, chỉ coi là "có vật" khi 2-3 lần đo liên tiếp nhỏ hơn ngưỡng. Lần đo hết thời gian (không có tiếng vọng) phải xử lý riêng, không được hiểu nhầm thành "trống" hay "có vật". Ngưỡng lấy từ đo thực tế (khoảng cách lúc trống trừ biên an toàn) và cấu hình được.

## 5. Quy tắc quyết định khi quẹt thẻ (chạy trên máy tính, theo đúng thứ tự)

**Cổng vào**
1. UID chưa đăng ký: từ chối, "Thẻ không hợp lệ".
2. Thẻ đang tạm khóa hoặc báo mất: từ chối, "Thẻ đã khóa" (kèm cảnh báo và ảnh người quẹt).
3. Gói tháng/năm hết hạn: từ chối, "Thẻ hết hạn".
4. Xe đã ở trong bãi: từ chối, "Xe đã ở trong bãi" (kèm cảnh báo).
5. Hết chỗ cho loại xe đó: từ chối, "Hết chỗ ô tô" hoặc "Hết chỗ xe máy".
6. Còn lại: cho mở.

**Cổng ra**
1. UID chưa đăng ký, hoặc thẻ khóa/báo mất: từ chối như trên.
2. Xe chưa ở trong bãi: từ chối, "Xe chưa vào bãi" (kèm cảnh báo).
3. Còn lại: cho mở. **Hết hạn gói không chặn cổng ra**, để xe không bị nhốt trong bãi.

**Số chỗ trống** luôn tính lại từ dữ liệu, không cộng trừ dần: `chỗ trống (theo loại) = sức chứa (theo loại) − số xe loại đó có trạng thái "ở trong bãi"`. Sức chứa ô tô và xe máy là hai cấu hình riêng.

**Trạng thái thẻ:** hoạt động; tạm khóa (chủ thẻ tự mở lại trong bot); báo mất (vĩnh viễn, chỉ quản lý xử lý, quản lý cấp thẻ mới gắn vào cùng hồ sơ xe). Thẻ chỉ là chìa khóa, trạng thái "ở trong bãi", gói và hạn mức đều nằm ở hồ sơ xe.

## 6. Mô hình dữ liệu (gợi ý)

- **units** (căn hộ): mã căn, hạn mức xe riêng (nếu khác mặc định).
- **users:** vai trò (quản lý hoặc cư dân), căn hộ, telegram_chat_id, mã liên kết dùng một lần và hạn dùng.
- **vehicles:** biển số, loại xe (ô tô/xe máy), căn hộ, loại đối tượng (mặc định "cư dân", chừa chỗ cho "khách" sau này), ngày hết hạn gói, in_lot.
- **credentials:** loại (RFID, chừa chỗ cho QR sau này), giá trị (UID), vehicle_id, trạng thái (active/locked/lost), thời điểm cấp.
- **registration_requests:** căn hộ, biển số, loại xe, gói, ảnh giấy tờ (tùy chọn), trạng thái (chờ duyệt / đã duyệt chờ cấp thẻ / đang hoạt động / từ chối), lý do.
- **events:** thời gian, cổng, hướng, credential, vehicle, kết quả, lý do, đường dẫn ảnh, biển số OCR đọc được, độ tin cậy, cờ lệch biển, các mốc thời gian (quẹt, gửi, nhận, ra lệnh servo).
- **notifications:** người nhận, mức (cảnh báo/thông tin), nội dung, ảnh, thời gian, đã gửi Telegram chưa.
- **settings:** sức chứa ô tô và xe máy, hạn mức mặc định (gợi ý 1 ô tô + 2 xe máy mỗi căn), ngưỡng OCR, số ngày lưu ảnh.

Thẻ đang hoạt động và trạng thái xe nên nằm sẵn trong bộ nhớ máy tính để tra trong vài mili giây, ghi xuống cơ sở dữ liệu sau.

## 7. Ảnh và OCR nền

- Ảnh chụp **ngay khi quẹt** (xe đang đứng yên), chạy song song, không chặn việc mở cổng. Luồng camera luôn mở sẵn, lấy khung hình mới nhất.
- OCR chạy nền sau khi cổng đã mở, ghi biển số và độ tin cậy vào lịch sử.
- **Cờ lệch biển** chỉ bật khi OCR đủ tin cậy và biển đọc được khác biển đăng ký của thẻ quá 1 ký tự (khoảng cách chỉnh sửa). Đọc không ra hoặc độ tin cậy thấp thì ghi "không đọc được", không cảnh báo.
- Lưu ảnh 30 ngày rồi tự xóa, ảnh gắn với cảnh báo giữ 90 ngày.

## 8. Thông báo và giao diện

**Bot Telegram (cư dân):**
- Gửi yêu cầu đăng ký xe (bot hỏi lần lượt biển số, loại xe, gói). Kiểm tra hạn mức ngay lúc gửi.
- Xem xe, thẻ, hạn gói. Khóa và mở lại thẻ bằng nút bấm.
- Xem vài lượt ra vào gần nhất kèm ảnh, xem thông báo gần đây.
- Nhận cảnh báo (luôn đẩy, không tắt được): lệch biển, bị chặn do sai trạng thái, thẻ khóa/mất bị quẹt. Kèm nút "Khóa thẻ ngay".
- Nhận thông tin (cư dân bật/tắt được): mỗi lần quẹt thẻ bình thường, nhắc hết hạn trước 7, 3 và 1 ngày.
- Liên kết tài khoản: quản lý tạo tài khoản cư dân theo căn hộ trên web, cư dân nhận mã dùng một lần (hết hạn sau ~15 phút) và gửi cho bot. Bot chỉ nhận lệnh từ tài khoản đã liên kết.

**Web (chỉ quản lý):**
- Tổng quan: chỗ trống từng loại, số xe trong bãi, cảnh báo đang mở.
- Yêu cầu chờ duyệt: duyệt hoặc từ chối kèm lý do.
- Cấp thẻ: bật "chế độ đăng ký thẻ" cho một cổng (hết hạn sau ~60 giây), lần quẹt kế tiếp lấy UID gắn vào yêu cầu thay vì mở cổng.
- Xe và thẻ: khóa, báo mất, cấp thẻ mới, gia hạn (quản lý bấm sau khi cư dân đóng phí ngoài hệ thống).
- Xe đang trong bãi: nút sửa trạng thái (đánh dấu đã ra hoặc đã vào), có ghi ai sửa, lúc nào.
- Lịch sử ra vào kèm ảnh. Cảnh báo, kèm hành động "Sửa trạng thái và mở cổng".
- Mở cổng thủ công từ xa (vẫn theo các quy tắc đóng cổng ở mục 4).
- Căn hộ và tài khoản: tạo tài khoản, tạo mã liên kết Telegram, hủy liên kết, chỉnh hạn mức.
- Cài đặt: sức chứa, hạn mức mặc định, ngưỡng OCR, số ngày lưu ảnh.

## 9. Bảo mật

- ESP32 gửi API key trong mỗi kết nối và yêu cầu, kể cả trong mạng nội bộ.
- Web quản lý có đăng nhập. Bot chỉ phục vụ chat_id đã liên kết, quản lý có thể hủy liên kết.
- Đồng bộ giờ (NTP) cho ESP32 và máy tính vì lịch sử và hạn gói dựa vào thời gian.

## 10. Mục tiêu tốc độ

Từ lúc quẹt thẻ đến lúc servo **bắt đầu mở** dưới ~0,5 giây. Ngân sách điển hình: đọc thẻ 50-100 ms, WebSocket đi và về 10-60 ms, tra cứu dưới 10 ms, ra lệnh servo dưới 5 ms. Điều kiện bắt buộc:
- WebSocket giữ sẵn, không mở kết nối mới mỗi lần quẹt.
- Tắt chế độ tiết kiệm điện WiFi của ESP32.
- Ghi cơ sở dữ liệu, chụp ảnh, OCR, gửi Telegram luôn chạy sau hoặc song song, không đứng trước lệnh mở cổng.
- Ghi mốc thời gian mỗi bước của mỗi lần quẹt để đo thật.

## 11. OLED

Rảnh: "Mời quẹt thẻ" kèm "Ô tô trống: x, Xe máy trống: y". Kết quả (hiện ~2 giây): "Mời vào", "Thẻ hết hạn", "Thẻ đã khóa", "Thẻ không hợp lệ", "Xe đã ở trong bãi", "Xe chưa vào bãi", "Hết chỗ ô tô", "Hết chỗ xe máy", "Mất kết nối". Mất kết nối với máy tính thì barrier giữ đóng, ESP32 tự thử kết nối lại.

## 12. Giới hạn đã biết (ghi vào đề tài)

- Thẻ có thể cho mượn hoặc nhặt được; hệ thống chỉ phát hiện sau bằng ảnh, OCR nền và thông báo cho chủ thẻ.
- Hai xe bám đuôi có thể qua chỉ với một lần quẹt (cảm biến vẫn thấy liên tục nên cổng giữ mở). Sau đó trạng thái "ở trong bãi" có thể lệch và quản lý phải sửa.
- Không có nút mở khẩn cấp: mất kết nối thì barrier giữ đóng.
- Khách của cư dân phải đậu ngoài bãi.
- Bot Telegram cần Internet và chưa có ứng dụng di động riêng.
- Khoảng đợi 2 giây có thể ngắn với người lái chậm (là hằng số cấu hình được).

---

# PHẦN B. CÁC GIAI ĐOẠN VÀ LỆNH DÁN SẴN CHO AI TRONG VS CODE

Cách dùng: lưu Phần A vào `docs/SPS_design_v7.md`, làm tuần tự từng giai đoạn, dán nguyên lệnh trong khung. Xong mỗi giai đoạn nên chạy test và commit trước khi sang giai đoạn kế tiếp.

## Giai đoạn 0. Rà soát mã hiện có so với thiết kế

```
Đọc docs/SPS_design_v7.md (thiết kế mới của hệ thống). Sau đó rà soát toàn bộ mã hiện có trong workspace (backend, firmware, cơ sở dữ liệu, cấu hình).
KHÔNG sửa mã ở bước này. Hãy tạo file docs/GAP_REPORT.md gồm:
1. Phần nào của mã hiện có khớp thiết kế và có thể tái sử dụng.
2. Phần nào mâu thuẫn hoặc sẽ bị thay thế (ví dụ: nhận diện biển số hiện là điều kiện mở cổng, trong khi thiết kế mới dùng RFID làm phương thức chính và biển số chỉ để ghi lịch sử).
3. Các thay đổi lược đồ cơ sở dữ liệu cần làm, kèm cách migrate dữ liệu cũ.
4. Thứ tự làm đề xuất và rủi ro chính.
Nếu workspace chưa có git thì chỉ ra điều đó trong báo cáo. Trả lời ngắn gọn các điểm cần tôi quyết định.
```

## Giai đoạn 1. Dữ liệu và bộ quyết định (máy tính)

```
Theo docs/SPS_design_v7.md, mục 5 và 6: triển khai lược đồ dữ liệu mới (kèm migration) và bộ quyết định khi quẹt thẻ.
Yêu cầu:
- Hàm quyết định thuần túy: decide(gate, uid, now) trả về {allow: bool, reason_code, oled_message, alert: bool}, thực hiện đúng thứ tự quy tắc cổng vào và cổng ra ở mục 5 (hết hạn gói chỉ chặn cổng vào).
- Số chỗ trống luôn tính lại từ trạng thái in_lot (theo loại xe), không cộng trừ dần.
- Cache thẻ đang hoạt động và trạng thái xe trong RAM, ghi DB sau, cache phải cập nhật ngay khi khóa/mở/báo mất thẻ hoặc sửa trạng thái xe.
- Hàm xác nhận "xe đã qua" (confirm_pass) cập nhật in_lot và ghi sự kiện; hàm ghi sự kiện "quẹt nhưng xe không vào" không đổi trạng thái.
- Chừa cột loại đối tượng (cư dân/khách) và loại chứng danh (RFID), nhưng chưa làm logic cho khách và QR.
Viết unit test phủ mọi nhánh quy tắc (kể cả hết hạn ở cổng ra, thẻ khóa/mất, hết chỗ, sai trạng thái in_lot). Chạy toàn bộ test và báo kết quả. Không đụng đến firmware, web, bot ở bước này.
```

## Giai đoạn 2. Giao thức WebSocket và giả lập cổng

```
Theo docs/SPS_design_v7.md, mục 2, 4, 9, 10: làm kênh WebSocket giữa máy tính và ESP32, và một chương trình giả lập cổng để kiểm thử không cần phần cứng.
Yêu cầu:
- Một kết nối WebSocket giữ sẵn cho mỗi ESP32, xác thực bằng API key, tự phục hồi khi rớt.
- Thông điệp ESP32 gửi lên: tap(gate, uid, t_tap), pass_confirmed(gate), no_entry_timeout(gate), obstruction/stuck_open(gate). Máy tính trả: decision(allow, oled_message, free_car, free_moto), và lệnh mở cổng thủ công từ web.
- Khi nhận tap: gọi decide() ở Giai đoạn 1, trả quyết định NGAY, rồi mới ghi DB, chụp ảnh, gửi thông báo (chạy sau hoặc luồng riêng, không chặn).
- Ghi các mốc thời gian (nhận tap, trả quyết định) vào bảng events.
- Viết chương trình giả lập (Python) đóng vai ESP32: gửi tap, đọc quyết định, mô phỏng chuỗi xe qua và đóng cổng; kèm kịch bản test tự động (hợp lệ, hết hạn, đã trong bãi, hết chỗ, xe không vào, mất kết nối rồi nối lại).
Chạy test, báo thời gian từ tap đến decision đo được (mục tiêu đo ở máy tính dưới 20 ms).
```

## Giai đoạn 3. Firmware ESP32

```
Theo docs/SPS_design_v7.md, mục 3, 4, 10, 11: viết firmware ESP32 (hai cổng chạy độc lập, không dùng delay chặn).
Yêu cầu:
- Bản đồ chân đúng bảng ở mục 3; RC522 hai đầu đọc dùng chung SPI, mỗi cái một chân SS, chung chân RST.
- Máy trạng thái mỗi cổng đúng mục 4: IDLE, OPENING, OPEN_WAIT (2 giây đếm từ khi servo đạt 90°), PASSING (giữ mở khi cảm biến thấy xe; đóng khi không thấy liên tục 0,5 giây), CLOSING (nếu thấy vật thì dừng và mở lại). Cảnh báo khi giữ mở quá 30 giây.
- Lọc cảm biến: đo mỗi 50-60 ms, trung vị của 3 lần, cần 2-3 lần liên tiếp mới coi là có vật, xử lý riêng lần đo hết thời gian. Ngưỡng và các hằng số thời gian là cấu hình ở một chỗ.
- Còi kêu và LED nháy khi cổng mở. OLED theo mục 11, gồm số chỗ trống nhận từ máy tính.
- WebSocket giữ sẵn theo giao thức Giai đoạn 2, tắt WiFi power-save, đồng bộ giờ NTP, mất kết nối thì giữ cổng đóng, OLED "Mất kết nối" và tự nối lại.
- Ghi mốc thời gian (millis) lúc đọc thẻ, lúc gửi, lúc nhận quyết định, lúc ra lệnh servo.
Chưa cần nút khẩn cấp. Biên dịch được và kèm checklist kiểm thử phần cứng (đo khoảng cách lúc trống, ngưỡng, thử xe máy và ô tô mô hình). Chưa nạp lên board, hãy liệt kê điều tôi cần làm khi nạp và thử.
```

## Giai đoạn 4. Ảnh, OCR nền và lưu trữ

```
Theo docs/SPS_design_v7.md, mục 7: làm phần ảnh và OCR nền.
Yêu cầu:
- Mỗi camera luôn mở luồng sẵn, giữ khung hình mới nhất; khi có sự kiện quẹt thì lấy ngay khung hình đó, lưu ảnh, gắn vào sự kiện. Việc này chạy trong luồng riêng, không bao giờ làm chậm quyết định mở cổng.
- OCR chạy nền sau khi cổng đã mở, ghi biển số đọc được và độ tin cậy vào sự kiện. Dùng bộ đọc biển có trong dự án nếu có.
- Cờ lệch biển: chỉ bật khi độ tin cậy trên ngưỡng cấu hình và khoảng cách chỉnh sửa giữa biển đọc được và biển đăng ký của thẻ lớn hơn 1. Độ tin cậy thấp thì ghi "không đọc được" và không cảnh báo.
- Khi cờ lệch bật hoặc sự kiện bị từ chối do thẻ khóa/mất/sai trạng thái, tạo thông báo mức "cảnh báo" kèm ảnh.
- Tác vụ định kỳ xóa ảnh quá 30 ngày; ảnh gắn với cảnh báo giữ 90 ngày. Số ngày cấu hình trong settings.
Viết test cho cờ lệch (các ca sai 1 ký tự, 2 ký tự, độ tin cậy thấp) và cho tác vụ xóa ảnh. Không làm giao diện ở bước này.
```

## Giai đoạn 5. Web quản lý

```
Theo docs/SPS_design_v7.md, mục 8 (phần Web): làm giao diện web dành riêng cho quản lý (cư dân không dùng web).
Yêu cầu:
- Đăng nhập cho quản lý. Các trang: Tổng quan; Yêu cầu chờ duyệt; Xe và thẻ; Xe đang trong bãi; Lịch sử ra vào kèm ảnh; Cảnh báo; Căn hộ và tài khoản; Cài đặt.
- Chế độ đăng ký thẻ: bật cho một cổng, hết hạn sau khoảng 60 giây, lần quẹt kế tiếp lấy UID gắn vào yêu cầu đã duyệt thay vì mở cổng.
- Khóa, báo mất, cấp thẻ mới (thẻ cũ thành báo mất vĩnh viễn, thẻ mới gắn vào cùng hồ sơ xe), gia hạn gói (chọn thêm 1 tháng hoặc 1 năm), sửa trạng thái in_lot (ghi ai sửa, lúc nào), mở cổng thủ công từ xa.
- Tạo tài khoản cư dân theo căn hộ, tạo mã liên kết Telegram dùng một lần (hết hạn ~15 phút), hủy liên kết, chỉnh hạn mức xe theo căn hộ.
- Mọi thao tác đi qua lớp API lõi, không viết logic nghiệp vụ trong giao diện, để bot Telegram dùng lại.
Giao diện đơn giản, đủ dùng cho demo. Viết test API cho các thao tác chính và cập nhật cache khi khóa thẻ hoặc sửa trạng thái.
```

## Giai đoạn 6. Bot Telegram và thông báo

```
Theo docs/SPS_design_v7.md, mục 8 (phần Bot Telegram) và mục 9: làm bot Telegram cho cư dân (và nhận cảnh báo cho quản lý).
Yêu cầu:
- Liên kết tài khoản bằng mã dùng một lần từ web quản lý; bot chỉ phục vụ chat_id đã liên kết.
- Menu cố định, dùng nút bấm thay vì gõ lệnh: Xe của tôi; Gửi yêu cầu đăng ký xe (hỏi lần lượt biển số, loại xe, gói, kiểm tra hạn mức ngay); Khóa/Mở thẻ; Lịch sử gần đây (kèm ảnh); Thông báo gần đây; Cài đặt thông báo.
- Hai mức thông báo: cảnh báo luôn đẩy và không tắt được (lệch biển, bị chặn do sai trạng thái, thẻ khóa/mất bị quẹt), kèm nút "Khóa thẻ ngay"; thông tin (quẹt thẻ bình thường, nhắc hết hạn 7, 3, 1 ngày trước) cư dân bật/tắt được.
- Quản lý nhận cảnh báo kèm ảnh và hành động nhanh "Sửa trạng thái và mở cổng", thông báo yêu cầu đăng ký mới, và cảnh báo giữ mở quá lâu hoặc mất kết nối ESP32.
- Mọi thông báo đều lưu vào bảng notifications; gửi Telegram chạy nền, lỗi mạng thì thử lại và không ảnh hưởng việc mở cổng.
- Bot chỉ gọi lớp API lõi, không truy cập DB trực tiếp.
Viết test với Telegram giả (mock) cho các luồng chính. Token bot đọc từ biến môi trường, không ghi vào mã.
```

## Giai đoạn 7. Tích hợp, đo tốc độ và kiểm thử kịch bản

```
Theo docs/SPS_design_v7.md, mục 10 và 12: tích hợp toàn hệ thống và kiểm thử.
Yêu cầu:
- Viết báo cáo đo thời gian: với mỗi lần quẹt, tách thời gian đọc thẻ, đi và về WebSocket, quyết định, ra lệnh servo; thống kê trung vị, p95, lớn nhất trên ít nhất 50 lần quẹt. So với mục tiêu dưới 0,5 giây (quẹt đến servo bắt đầu mở) và chỉ ra điểm nghẽn nếu vượt.
- Bộ kịch bản kiểm thử end-to-end (giả lập và, nếu có, phần cứng thật): thẻ hợp lệ vào và ra; thẻ hết hạn; thẻ tạm khóa rồi cư dân tự mở; thẻ báo mất bị quẹt; xe đã ở trong bãi quẹt cổng vào; xe chưa vào quẹt cổng ra; hết chỗ ô tô/xe máy; quẹt nhưng xe không vào; có vật khi đang đóng; mất rồi nối lại WebSocket; xe bám đuôi (ghi nhận hành vi và giới hạn); lệch biển OCR.
- Cập nhật README: cách chạy, cấu hình, cách đo, và mục "Giới hạn đã biết" đúng như mục 12 của thiết kế.
Báo kết quả từng kịch bản (đạt/không đạt) và danh sách việc còn lại.
```

---

## Ghi chú

- OCR cần dữ liệu huấn luyện riêng cho biển số Việt Nam (biển dài một dòng và biển vuông hai dòng). Đây là việc song song, không chặn các giai đoạn trên vì OCR chỉ ghi lịch sử.
- Khi muốn thêm khách sau này: thêm loại chứng danh QR và hai module đọc QR vào GPIO34 và GPIO35, thêm bảng lượt khách và luồng đăng ký khách trong bot. Các cột chừa sẵn ở Giai đoạn 1 giúp việc này không phải đập lại lược đồ.
