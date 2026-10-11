# SPS v7 — Bàn giao kỹ thuật

Cập nhật: 11/10/2026. Đọc tài liệu này trước khi sửa mã. Lệnh cài đặt/vận hành nằm trong [README](../README.md), bằng chứng kiểm thử nằm trong [E2E_CHECKLIST](E2E_CHECKLIST.md). Thiết kế gốc và gap report lưu lại lịch sử; trạng thái hiện tại được mô tả ở đây.

## 1. Mục tiêu và kiến trúc

Hệ thống đỗ xe chung cư, demo mô hình thu nhỏ, chỉ phục vụ cư dân đã đăng ký. ESP32 điều khiển hai cổng in/out: RC522, servo barie, HC-SR04; OLED, LED và còi theo pin map bên dưới. Hiện chỉ có một RC522, cổng ra chưa đọc thẻ; dự kiến mua thêm.

Máy tính chạy ba process Python: WebSocket server cổng 8765, Flask web cổng 5000 và bot Telegram; dùng chung SQLite với WAL. Camera IP dùng ứng dụng IP Webcam trên Android, poll `/shot.jpg` mỗi 200 ms bằng urllib. OCR dùng hai model YOLOv8: nhận diện biển và ký tự.

RFID quyết định mở cổng. Camera/OCR chỉ ghi lịch sử và cảnh báo, không quyết định quyền vào. Xe hết hạn gói không được vào nhưng vẫn được ra. Chỉ xác nhận xe đã qua mới đổi `in_lot`; timeout không qua không đổi trạng thái. Web quản lý và bot cư dân dùng chung lõi nghiệp vụ.

## 2. Nguyên tắc bắt buộc

- `decide()` thuần túy: không I/O, không DB, không mutate cache.
- Web/WS/Bot gọi `api_core` cho nghiệp vụ; không viết SQL trong route. Đây là quy tắc phát triển; không suy ra toàn bộ mã hiện có đã tuân thủ.
- Luồng tap thường gửi decision cho ESP32 trước khi ghi sự kiện DB; camera/OCR/thông báo chạy nền.
- Enroll là luồng gắn thẻ cần thao tác DB trước phản hồi thành công; không được mở cổng khi cấp thẻ.
- Firmware không dùng `delay()` chặn, ngoại trừ `delay(5)` cuối loop và `delay(300)` reset anten RC522 theo quyết định hiện tại.
- Ngày DB là ISO `YYYY-MM-DD`; web hiển thị `dd/mm/yyyy`.
- API key và web secret lấy từ env, không hard-code vào mã ứng dụng.

## 3. Cấu trúc

| Đường dẫn | Vai trò |
|---|---|
| `backend/api_core.py` | Lõi nghiệp vụ, tài khoản, yêu cầu, xe/thẻ, enroll |
| `backend/cache.py` | CacheState RAM, db_lock RLock |
| `backend/decision.py` | Bộ quyết định thuần túy |
| `backend/db.py`, `models.py` | Connection, schema, migration, WAL |
| `backend/events.py` | confirm_pass, record_no_entry |
| `backend/handlers.py`, `ws_session.py`, `ws_server.py` | WS handlers, SessionManager, entry point |
| `backend/camera.py`, `ocr.py` | Poll ảnh, lưu ảnh, OCR, validate_plate |
| `backend/config.py` | Cấu hình |
| `backend/test_server.py` | HTTP test, chỉ bật khi SPS_TEST_MODE=1 |
| `backend/web/`, `backend/web_server.py` | Flask, auth, routes, templates, entry point |
| `firmware/sps_esp32/` | Arduino sketch và các header |
| `simulator/` | Giả lập ESP32 và kịch bản |
| `tools/` | seed_demo, seed_requests, create_manager, check_db |
| `tests/` | Test phần mềm |
| `models/` | license_plate.pt, character_detect.pt; không commit |
| `data/` | DB và ảnh; không commit |
| `docs/decision_codes.md`, `docs/ws_protocol.md` | Chi tiết mã và giao thức; cần đồng bộ khi thay đổi |

## 4. Quyết định đã chốt

Sức chứa `CAPACITY_TOTAL=20`, không chia theo loại xe. Hạn mức mặc định mỗi căn: một ô tô và một xe máy. Ban đầu khởi tạo DB mới, không chuyển dữ liệu legacy; hiện có migration bổ sung tài khoản, liên kết sự kiện thông báo và trạng thái xử lý cảnh báo.

### Pin map

```text
RC522: SCK=18, MISO=19, MOSI=23, RST=16, SS_IN=5, SS_OUT=17
HC-SR04: TRIG_IN=27, ECHO_IN=14, TRIG_OUT=32, ECHO_OUT=33
Servo: IN=13, OUT=25
OLED: SDA=21, SCL=22, addr=0x3C
LED=26, Buzzer=4
HAS_RC522_OUT=0 khi chỉ có một đầu đọc
```

### Mã quyết định

| Mã | Cổng | OLED | Alert |
|---|---|---|---|
| ok | cả hai | Mời vào | false |
| card_unknown | cả hai | Thẻ không hợp lệ | false |
| card_locked | cả hai | Thẻ đã khóa | true |
| card_lost | cả hai | Thẻ đã khóa | true |
| plan_expired | in | Thẻ hết hạn | false |
| already_in_lot | in | Xe đã ở trong bãi | true |
| not_in_lot | out | Xe chưa vào bãi | true |
| lot_full | in | Hết chỗ | false |

`no_entry`, `manual_adjust` là mã sự kiện phụ. `enrolled` không thuộc tám kết quả của `decide()`, nhưng được dùng trong message decision cấp thẻ với `allow=false` và ghi events. Không hiểu mô tả cũ “chỉ ghi events” là cấm trả mã enrolled qua WS.

ESP32 gửi: tap, pass_confirmed, no_entry_timeout, obstruction, stuck_open. Server gửi: decision, open_command, ping. Message mẫu theo bàn giao:

```json
{"type":"decision","gate":"in","allow":true,"reason_code":"ok","oled_message":"Mời vào","free_total":15}
```

Đây là mẫu bàn giao; cần kiểm tra message thực gửi ở cả handler và SessionManager trước khi sửa giao thức.

```text
OPEN_WAIT_MS=5000; PASS_CLEAR_MS=500; STUCK_OPEN_MS=30000
WS_PING_INTERVAL_MS=25000; WS_RECONNECT_MS=3000
OCR_CONF_PLATE=0.65; OCR_CONF_CHAR=0.35; OCR_THRESHOLD=0.8
ENROLL_DURATION_S=60
```

Địa chỉ demo do chủ dự án cung cấp, có thể đổi theo mạng:

- Máy tính: 192.168.1.75; WS: ws://192.168.1.75:8765/ws.
- Camera: http://192.168.1.127:8080/shot.jpg.
- HTTP test: http://192.168.1.75:8766, chỉ khi SPS_TEST_MODE=1.
- Flask: http://192.168.1.75:5000; tạo tài khoản quản lý bằng `tools.create_manager` khi khởi tạo DB mới.
- `SPS_API_KEY` bắt buộc khi khởi chạy WS và phải khớp `firmware/sps_esp32/secrets.h`. `SPS_WEB_SECRET` đặt trong môi trường chạy web.

## 5. Chức năng hiện có

- Lõi quyết định, cache, schema/migration; WS, HTTP test server và 11 kịch bản simulator.
- Firmware hai phiên WS, chỉ bật đầu đọc IN. Cổng vào đã thử đầy đủ mở/xe qua/đóng; cổng ra đã thử lệnh web và tự đóng.
- Camera chụp ảnh, OCR hai model, ghi biển/confidence/mismatch. Ảnh và kết quả OCR gắn bằng ID sự kiện, không tìm lượt quẹt gần nhất.
- Web: đăng nhập, dashboard polling 2 giây, yêu cầu duyệt/từ chối, xe/thẻ, gia hạn, lịch sử/ảnh, cảnh báo, căn hộ/cư dân, cài đặt.
- Cảnh báo tách `sent_telegram` khỏi `resolved_at`/`resolved_by_user_id`. Thao tác sửa xe và mở cổng ghi audit, cập nhật trạng thái, xếp lệnh và xử lý cảnh báo trong một giao dịch.
- Enroll 60 giây từ trang xe hoặc yêu cầu đã duyệt. UID mới gắn vào xe, phản hồi `allow=false`, `reason_code=enrolled`, không mở cổng. Quẹt sau đó đi qua `decide()` bình thường.
- Bot Telegram: mã liên kết cư dân một lần, xe/thẻ, lịch sử, yêu cầu đăng ký, khóa/mở thẻ, worker gửi thông báo kèm ảnh. Telegram đã nhận ảnh cảnh báo OCR của sự kiện cũ #45; chưa xác nhận lại lượt quẹt mới tới điện thoại sau bản sửa.
- Worker WS dọn ảnh thường quá 30 ngày, ảnh lệch biển quá 90 ngày; công cụ benchmark hàm quyết định.

## 6. Kiểm chứng và việc còn lại

120 kiểm thử đồng bộ đã qua trong lần kiểm chứng trước khi dọn thư mục. Các nhóm async (WS, HTTP test server, Telegram) từng treo khi tạo event loop trong môi trường công cụ Windows; chưa được coi là đã qua. Kiểm thử thật từ người dùng được ghi riêng trong checklist.

Còn lại:

1. Chạy hoàn chỉnh các nhóm async trong môi trường phù hợp và simulator trên DB riêng.
2. Khi có phần cứng: RFID cổng ra, vật cản, giữ mở lâu, lỗi cảm biến và mất mạng. Không suy từ cổng vào hoạt động rằng cổng ra đã được kiểm chứng.
3. Xác nhận một lượt quẹt mới → chụp ảnh → OCR lệch biển → Telegram sau bản sửa gắn ảnh theo ID.
4. Đo ít nhất 50 lượt xuyên suốt, ghi median/p95/max; benchmark pure decision không thay thế số đo này.
5. Kiểm tra thao tác từ chối yêu cầu trên DB thật: người dùng báo đã thao tác, nhưng lần đối soát trước chưa xác nhận được cập nhật. Kiểm thử Flask với hai kết nối SQLite đã qua.

Quản lý hiện chưa liên kết Telegram; cảnh báo phần cứng chỉ gửi tới quản lý nên chưa có bằng chứng chúng tới điện thoại. Bot hiện hỗ trợ liên kết cư dân. Cảnh báo lệch biển tới cư dân đã xác nhận bằng ảnh điện thoại.

## 7. Vận hành và dữ liệu

Dùng `.venv` Python 3.12 cho tất cả process. `requirements.txt` chứa runtime; `requirements-dev.txt` thêm pytest. Xem README cho các lệnh cụ thể.

Không chạy seed hoặc reset DB để khởi động thường ngày. `tools.seed_demo` sửa dữ liệu xe/thẻ và trạng thái; hiện dùng đường dẫn `data/sps.sqlite3` riêng trong script. Không dùng script này để seed một DB khác chỉ bằng biến `SPS_DB_PATH`.

Dữ liệu trong `data/`, model trong `models/`, cấu hình firmware và môi trường `.venv` cần được giữ khi dọn dự án. Không xóa WAL/SHM lúc server đang chạy. DB thật không phải dữ liệu dùng cho simulator tự động.

## 8. Lưu ý và giới hạn

- Middleware aiohttp cần `@web.middleware`.
- RC522 từng cần reset anten và `PCD_Init()` để đọc lượt sau. Chỉ một đầu đọc thì giữ `HAS_RC522_OUT=0`.
- IP Webcam cần hoạt động và giữ màn hình; camera dùng poll `/shot.jpg`, không dùng luồng MJPEG/FFmpeg.
- Gửi decision thường trước, sau đó ghi DB/chụp ảnh/OCR theo ID sự kiện.
- WS yêu cầu `SPS_API_KEY`; firmware giữ thông tin kết nối trong `secrets.h` cục bộ và dùng `secrets.example.h` làm mẫu.
- Thẻ cho mượn/nhặt được chỉ phát hiện sau qua ảnh/OCR; xe bám đuôi có thể làm lệch `in_lot`.
- Chưa có nút mở khẩn cấp; mất kết nối không cấp lượt mở mới. Bot cần Internet.
- Các giới hạn an toàn và độ bền cần kiểm chứng bằng phần cứng trước khi kết luận hoàn thiện.
