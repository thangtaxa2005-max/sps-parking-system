# SPS v7 — Checklist kiểm thử end-to-end

## Kết quả đã xác nhận ngày 11/10/2026

- [x] ESP32 kết nối Wi-Fi và cả hai phiên WebSocket (`ws_in=1`, `ws_out=1`).
- [x] Thẻ `353610E0` tại cổng vào được cho phép; servo mở, cảm biến nhận xe qua, gửi `pass_confirmed`, servo đóng và trạng thái `in_lot` cập nhật.
- [x] Lệnh mở cổng ra từ web đã hoạt động và barie tự đóng sau bản sửa firmware.
- [x] Camera trả ảnh qua `/shot.jpg`; OCR đọc được `18A41083` từ ảnh thử trên màn hình máy tính, ghi `plate_mismatch=1` và tạo cảnh báo khi khác biển đăng ký.
- [x] Sau sửa trang Cảnh báo và bổ sung trang Cư dân, 119 kiểm thử đồng bộ qua, gồm kiểm thử từ chối yêu cầu qua Flask với hai kết nối SQLite trên ổ đĩa. Trạng thái đã gửi Telegram không còn bị hiểu là đã xử lý. Quản lý có thể chọn xe, trạng thái và cổng để sửa trạng thái, ghi audit, xếp lệnh mở cổng và xử lý cảnh báo trong một thao tác.
- [x] Bot Telegram thật đã trả lời `/start`, liên kết tài khoản và hiển thị `/vehicles`, `/history`. `/vehicles` nay gộp xe có nhiều thẻ; `/history` định dạng thời gian dễ đọc sau khi khởi động lại bot.
- [x] Nút “Gửi thông báo thử” tạo notification `info`; điện thoại đã nhận và DB ghi `sent_telegram=1` cho notification #17.
- [x] Bot nhận `/register 30A88888 car month` và tạo yêu cầu #4 `pending`. Yêu cầu thử sau đó được chuyển sang `rejected` bằng `api_core`; không tạo xe. Người dùng báo web từ chối thành công nhưng lần đọc DB ngay sau đó vẫn thấy `pending`, nên thao tác từ chối qua web trên DB thật chưa được xác nhận trong lượt thử này.
- [ ] Cổng ra bằng quẹt thẻ: chưa có RC522 thứ hai. Không suy từ lệnh mở cổng từ xa ra rằng luồng RFID cổng ra đã được thử.
- [ ] Thẻ khóa/báo mất trên phần cứng: chủ dự án chọn bỏ qua bài test này ở thời điểm hiện tại; thẻ thật vẫn active.
- [x] Telegram thật đã nhận tin nhắn thử và cảnh báo OCR có ảnh trên chat cư dân. Cảnh báo ảnh được gửi lại từ sự kiện đã lưu #45; luồng chụp ảnh từ một lượt quẹt mới chưa được xác nhận lại sau bản sửa.
- [ ] Đo 50 lượt E2E và các kịch bản vật cản/mất mạng: chưa có kết quả đo phần cứng đầy đủ.

Các nhóm test dùng `asyncio` (Telegram, HTTP test server, WebSocket) bị treo khi tạo event loop trong môi trường Windows hiện tại; ngay cả `asyncio.run(asyncio.sleep(0))` cũng không kết thúc. Đây chưa phải kết quả pass/fail của nghiệp vụ. Không đánh dấu hoàn tất các mục chưa chạy được.

Các ô bên dưới là checklist cho lần nghiệm thu tiếp theo; không phải tất cả đều là chức năng chưa triển khai. Xem kết quả đã xác nhận ở trên.

## Chuẩn bị

- [ ] Cài dependencies trong `.venv` và chạy `.\.venv\Scripts\python.exe -m pytest tests/test_enroll.py -q`.
- [ ] Đặt `SPS_API_KEY` và `SPS_WEB_SECRET` trong hai terminal chạy WS/Flask.
- [ ] Kiểm tra DB bằng `.\.venv\Scripts\python.exe -m tools.check_db`.
- [ ] Cấu hình Wi-Fi, IP máy tính và camera trong firmware/config.
- [ ] Nếu thử Telegram, đặt `SPS_TELEGRAM_BOT_TOKEN` rồi chạy `.\.venv\Scripts\python.exe -m backend.telegram_bot`.

## Phần mềm không cần phần cứng

- [ ] Chạy `.\.venv\Scripts\python.exe -m backend.ws_server`.
- [ ] Chạy `.\.venv\Scripts\python.exe -m backend.web_server`.
- [ ] Chạy simulator auto khi test server được bật: `.\.venv\Scripts\python.exe -m simulator.gate_sim --gate in --auto` trên DB riêng theo README.
- [ ] Đo ít nhất 50 lượt tap và ghi median/p95/max từ log `DECISION latency_ms`.
- [ ] Kiểm tra lệnh mở cổng: dashboard → gate command → WS queue → `open_command`.
- [ ] Kiểm tra enroll: bật cấp thẻ → quẹt UID mới → `enrolled` không mở cổng → quẹt lại.

## ESP32 và barie

- [x] Quẹt thẻ hợp lệ tại cổng vào: servo mở, cảm biến thấy xe, xe qua, servo đóng.
- [ ] Quẹt thẻ không hợp lệ/khóa/hết hạn: servo không mở, OLED hiện đúng mã.
- [ ] Quẹt thẻ nhưng không có xe: sau 5 giây bắt đầu đóng, `in_lot` không đổi.
- [ ] Vật cản khi đóng: barie mở lại và giữ trạng thái PASSING.
- [ ] Giữ cổng mở quá 30 giây: notification cảnh báo được tạo.
- [ ] Mất WebSocket: barie giữ đóng; khi nối lại có thể quẹt lại.
- [ ] Thử hai cổng độc lập bằng RFID; cổng ra chưa có RC522.

## Camera/OCR/Telegram

- [ ] Ảnh được lưu sau tap, không làm chậm decision.
- [ ] OCR đúng biển: ghi `ocr_plate` và confidence.
- [ ] OCR khác biển trên ngưỡng: `plate_mismatch=1`, tạo alert.
- [ ] OCR không đọc được: ghi null, không tạo cảnh báo lệch biển.
- [x] Telegram nhận thông báo chủ động và đánh dấu notification đã gửi (đã xác nhận bằng thông báo thử `info`).
- [x] Cảnh báo thật kèm ảnh tới Telegram: đã sửa thiếu `notifications.image_path` và gắn ảnh/OCR theo ID sự kiện. Cư dân xác nhận điện thoại nhận được ảnh và nội dung cảnh báo từ sự kiện OCR #45 (notification #19). Đây là gửi lại sự kiện đã lưu, chưa thay thế thử nghiệm quẹt mới trọn luồng.
- [ ] Chạy cleanup: ảnh thường quá 30 ngày bị xóa; ảnh cảnh báo giữ đến 90 ngày.

## Đối soát sau test

- [ ] Đối chiếu số lượt allow/deny với log tap.
- [ ] Đối chiếu `vehicles.in_lot` với trạng thái thực tế.
- [ ] Kiểm tra events có `t_tap`, `t_received`, `t_sent`.
- [ ] Lưu median/p95/max và danh sách lỗi vào báo cáo nghiệm thu.
