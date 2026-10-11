# SPS v7 — Hệ thống đỗ xe chung cư

Mô hình đỗ xe dùng ESP32, thẻ RFID và barie servo. Máy tính chạy WebSocket, web quản lý Flask và bot Telegram, dùng chung SQLite. Camera IP chụp ảnh; hai model YOLO nhận diện biển số để lưu lịch sử và cảnh báo lệch biển. RFID quyết định quyền mở cổng, OCR xử lý sau đó.

## Trạng thái ngày 11/10/2026

- Đã thử cổng vào: quẹt thẻ → mở → cảm biến xác nhận xe qua → đóng → cập nhật xe trong bãi.
- Đã thử mở cổng ra từ web và tự đóng. RFID cổng ra chưa thử vì thiếu đầu đọc thứ hai.
- Web có xe/thẻ, cấp thẻ 60 giây, yêu cầu đăng ký, lịch sử/ảnh, cảnh báo, cư dân và cài đặt. Dashboard cập nhật mỗi 2 giây.
- Telegram đã liên kết cư dân, trả lời xe/lịch sử, nhận đăng ký và gửi thông báo. Điện thoại đã nhận cảnh báo OCR kèm ảnh từ sự kiện lưu trước đó.
- 120 kiểm thử đồng bộ đã qua ở lần kiểm chứng trước. Nhóm bất đồng bộ chưa xác nhận do môi trường chạy bị treo lúc tạo event loop.
- Còn thử RFID cổng ra, các lỗi phần cứng, lượt quẹt mới tới Telegram sau bản sửa ảnh và đo ít nhất 50 lượt xuyên suốt.

Xem [checklist](docs/E2E_CHECKLIST.md) và [bàn giao kỹ thuật](docs/HANDOFF.md). Dự án là mô hình demo, chưa nghiệm thu vận hành thực tế.

## Cấu trúc

| Thư mục | Nội dung |
|---|---|
| `backend/` | Nghiệp vụ, SQLite, WS, camera/OCR, Telegram, Flask |
| `firmware/sps_esp32/` | Sketch ESP32 và cấu hình chân nối |
| `simulator/` | Giả lập cổng qua WebSocket |
| `tests/` | Kiểm thử phần mềm |
| `tools/` | Tạo tài khoản, dữ liệu mẫu, xem DB, benchmark |
| `docs/` | Bàn giao, giao thức, mã quyết định, checklist |
| `data/` | DB và ảnh thực tế; không đưa vào Git |
| `models/` | Hai model .pt; không đưa vào Git |

## Cài đặt trên Windows

Dùng Python 3.12. Chạy từ thư mục dự án và dùng đúng Python trong `.venv` để tránh lỗi thiếu thư viện như `cv2`.

```powershell
cd D:\SPS
py -3.12 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
```

Nếu `.venv` đã có và chạy được, không cần tạo lại. Requirements khai báo phiên bản tối thiểu, không khóa toàn bộ môi trường. Thư viện chạy ứng dụng nằm trong `requirements.txt`; thư viện kiểm thử ở `requirements-dev.txt`.

Đặt model tại `models/license_plate.pt` và `models/character_detect.pt`. Thiếu model hoặc lỗi nạp OCR thì server vẫn chạy nhưng OCR bị vô hiệu hóa. Requirements dùng `opencv-python` phù hợp dependency của Ultralytics, không khai báo thêm bản headless dùng chung module `cv2`.

Chỉ khi tạo hệ thống mới, chạy lệnh sau để khởi tạo schema và nhập tên/mật khẩu quản lý:

```powershell
.\.venv\Scripts\python.exe -m tools.create_manager
```

Với DB hiện tại, dùng tài khoản đã có. `tools.seed_demo` tạo/reset dữ liệu thử, thay đổi trạng thái xe và thẻ; không dùng làm lệnh khởi động hằng ngày.

## Chạy hằng ngày

Mở ba cửa sổ PowerShell tại `D:\SPS`. Mỗi process giữ cửa sổ riêng; dừng bằng Ctrl+C. Biến môi trường chỉ áp dụng cho process khởi chạy từ cửa sổ đã đặt biến.

**1. WebSocket và camera/OCR:** nhập API key trùng với firmware.

```powershell
cd D:\SPS
$env:SPS_API_KEY = Read-Host "API key trung voi firmware"
$env:SPS_CAMERA_URL_IN = "http://192.168.1.127:8080/shot.jpg"
.\.venv\Scripts\python.exe -m backend.ws_server
```

Camera cần bật IP Webcam và cùng mạng. Khi không dùng camera, đặt `$env:SPS_CAMERA_URL_IN = ""` trước khi chạy.

**2. Web quản lý:** dùng cùng web secret qua các lần khởi động.

```powershell
cd D:\SPS
$env:SPS_WEB_SECRET = Read-Host "Web secret"
.\.venv\Scripts\python.exe -m backend.web_server
```

Truy cập http://127.0.0.1:5000 trên máy chạy server, hoặc `http://<IP-may-tinh>:5000` trên thiết bị cùng mạng. IP máy tính demo hiện tại là `192.168.1.75`.

**3. Bot Telegram:**

```powershell
cd D:\SPS
$env:SPS_TELEGRAM_BOT_TOKEN = Read-Host "Token bot Telegram"
.\.venv\Scripts\python.exe -m backend.telegram_bot
```

Tạo cư dân và mã liên kết trên web, rồi gửi `/link MA` cho bot. Bot hỗ trợ `/vehicles`, `/history`, `/register BIEN_SO car|moto month|year`, `/lock UID`, `/unlock UID`. Cần Internet để giao tiếp Telegram. Không ghi token vào mã nguồn hoặc Git.

## Cấu hình và dữ liệu

| Biến môi trường | Ý nghĩa / mặc định |
|---|---|
| `SPS_API_KEY` | Key WS, phải khớp firmware |
| `SPS_WEB_SECRET` | Khóa phiên đăng nhập Flask |
| `SPS_TELEGRAM_BOT_TOKEN` | Token bắt buộc khi chạy bot |
| `SPS_CAMERA_URL_IN`, `SPS_CAMERA_URL_OUT` | URL `/shot.jpg`; cổng ra mặc định tắt |
| `SPS_DB_PATH` | Mặc định `data/sps.sqlite3`; các process dùng chung DB |
| `SPS_IMAGE_DIR` | Mặc định `data/images`; web/bot hiện phục vụ ảnh trong thư mục này |
| `SPS_TEST_MODE` | Đặt `1` để bật HTTP test server cổng 8766 |

Sức chứa mặc định 20 chỗ dùng chung. Enroll 60 giây, quẹt cấp thẻ không mở cổng. Firmware chờ xe 5 giây sau khi mở, xác nhận hết vật cản 500 ms, báo giữ mở quá 30 giây. Cấu hình ở `backend/config.py` và `firmware/sps_esp32/config.h`.

DB dùng WAL: không xóa file `.sqlite3`, `-wal`, `-shm` khi server chạy. Muốn sao lưu bằng cách chép file, dừng các process SPS trước. Worker WS dọn ảnh thường quá 30 ngày và ảnh lệch biển quá 90 ngày.

## Firmware

Mở `firmware/sps_esp32/sps_esp32.ino` bằng Arduino IDE với ESP32 board package. Thư viện dùng: MFRC522, ESP32Servo, Adafruit GFX, Adafruit SSD1306, ArduinoJson, ArduinoWebsockets.

Sao chép `firmware/sps_esp32/secrets.example.h` thành `firmware/sps_esp32/secrets.h` rồi điền Wi-Fi, IP máy tính và API key. File `secrets.h` chỉ lưu trên máy, không đưa lên Git. Máy hiện tại đã có file đó để giữ cấu hình phần cứng. Pin map và `HAS_RC522_OUT=0` nằm trong `config.h`; khi lắp đầu đọc thứ hai cần chỉnh cấu hình, nạp lại và thử cổng ra. Pin map ở [HANDOFF](docs/HANDOFF.md).

## Kiểm thử và công cụ

```powershell
.\.venv\Scripts\python.exe -m pip install -r requirements-dev.txt
.\.venv\Scripts\python.exe -m pytest tests/ -v
.\.venv\Scripts\python.exe -m tools.benchmark_decision
.\.venv\Scripts\python.exe -m tools.check_db
```

Benchmark chỉ đo hàm quyết định, không đo toàn bộ mạng/servo/cảm biến.

Simulator tự động thay đổi dữ liệu thử. Dùng DB riêng: trong terminal WS đặt `$env:SPS_DB_PATH = "D:\SPS\data\simulator.sqlite3"` và `$env:SPS_TEST_MODE = "1"`, rồi chạy WS. Trong terminal simulator đặt cùng `SPS_DB_PATH` và `SPS_API_KEY`, sau đó chạy:

```powershell
.\.venv\Scripts\python.exe -m simulator.gate_sim --gate in --auto
```

`simulator.test_client` là lớp hỗ trợ HTTP, không phải lệnh chạy kịch bản. Dừng server thử và bỏ `SPS_TEST_MODE` khi trở về vận hành thường. Nếu đã đặt `SPS_DB_PATH` cho simulator, bỏ biến đó hoặc đổi về DB thật trước khi chạy lại hệ thống.

## Tài liệu và giới hạn

- [Bàn giao](docs/HANDOFF.md): kiến trúc, nguyên tắc sửa mã và phần còn lại.
- [Checklist](docs/E2E_CHECKLIST.md): kết quả đã thử và các mục chưa xác nhận.
- [Giao thức WS](docs/ws_protocol.md), [mã quyết định](docs/decision_codes.md).
- [Thiết kế gốc](docs/SPS_design_v7.md), [gap report ban đầu](docs/archive/GAP_REPORT.md): tài liệu lịch sử; một số thông số đã thay đổi.

Thẻ cho mượn/nhặt được chỉ có thể phát hiện sau qua ảnh/OCR; hai xe bám đuôi có thể làm lệch số xe. Mất kết nối không cấp lượt mở mới và hiện chưa có nút mở khẩn cấp. Các tình huống vật cản, lỗi cảm biến, mất mạng và độ bền nhiều lượt vẫn cần thử trên phần cứng.
