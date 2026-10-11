# Giao thức WebSocket giữa ESP32 và máy tính

File này là **nguồn chân lý** cho giao thức WebSocket dùng ở Giai đoạn 2 (simulator),
Giai đoạn 3 (firmware ESP32), và Giai đoạn 5 (web quản lý khi mở cổng thủ công).
Khi có xung đột với mục 2 của `SPS_design_v7.md`, file này thắng.

## Kết nối

- Một kết nối WebSocket giữ sẵn cho mỗi ESP32, mở lúc ESP32 khởi động, không đóng khi rảnh.
- Địa chỉ mặc định: `ws://<ip-may-tinh>:8765/ws`
- Xác thực: header `Authorization: Bearer <API_KEY>` khi bắt tay (handshake).
  - Server kiểm tra ngay khi nhận kết nối. Sai key → đóng kết nối với mã 4001.
  - API key đọc từ biến môi trường `SPS_API_KEY`, không hard-code.
- Mỗi ESP32 gửi kèm header `X-Gate-Id: in` hoặc `X-Gate-Id: out` để server biết
  đó là cổng nào. Server từ chối nếu thiếu hoặc giá trị không hợp lệ (đóng mã 4002).

## Định dạng tin nhắn

- Tất cả tin nhắn là **JSON**, một object mỗi dòng (không xuống dòng trong object).
- Trường `type` bắt buộc trong mọi tin nhắn, giá trị là chuỗi.
- Server bỏ qua tin nhắn không hiểu (ghi log, không ngắt kết nối).

## Tin nhắn ESP32 → Máy tính

### 1. `tap` — cư dân quẹt thẻ

```json
{"type": "tap", "gate": "in", "uid": "A1B2C3D4", "t_tap": 1234567890}