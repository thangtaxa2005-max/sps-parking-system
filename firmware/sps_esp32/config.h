#pragma once

// ============================================================
// Thông tin mạng và API key nằm trong secrets.h (không đưa lên Git).
// Lưu ý:
//   - WIFI_SSID / WIFI_PASSWORD: tên và mật khẩu WiFi 2.4GHz.
//   - SERVER_HOST: IP máy tính đang chạy backend (KHÔNG có dấu cách).
//   - Tạo secrets.h từ secrets.example.h trước khi biên dịch.
// ============================================================
#include "secrets.h"

#define SERVER_PORT   8765
#define SERVER_PATH   "/ws"
// ============================================================
// Bật/tắt đầu đọc RC522 cho từng cổng.
// Đặt 0 nếu cổng đó CHƯA CÓ module RC522.
// QUAN TRỌNG: 2 module RC522 dùng chung chân RST=16. Nếu 1 module
// không tồn tại mà firmware vẫn gọi PCD_Init(), nó sẽ toggle RST
// và reset luôn module đang chạy → không đọc được thẻ nào cả.
// ============================================================
#define HAS_RC522_IN  1
#define HAS_RC522_OUT 0

// ============================================================
// Bản đồ chân ESP32 (theo mục 3 của docs/SPS_design_v7.md)
// ============================================================
// RC522 - dùng chung SPI, mỗi đầu đọc một chân SS, chung RST
#define PIN_RC522_SCK    18
#define PIN_RC522_MISO   19
#define PIN_RC522_MOSI   23
#define PIN_RC522_RST    16
#define PIN_RC522_SS_IN  5
#define PIN_RC522_SS_OUT 17

// Siêu âm HC-SR04
#define PIN_TRIG_IN   27
#define PIN_ECHO_IN   14
#define PIN_TRIG_OUT  32
#define PIN_ECHO_OUT  33

// Servo
#define PIN_SERVO_IN  13
#define PIN_SERVO_OUT 25

// OLED SSD1306 I2C
#define PIN_OLED_SDA 21
#define PIN_OLED_SCL 22
#define OLED_ADDR    0x3C
#define OLED_WIDTH   128
#define OLED_HEIGHT  64

// LED + còi
#define PIN_LED    26
#define PIN_BUZZER 4

// ============================================================
// Hằng số thời gian của state machine (mục 4 thiết kế)
// ============================================================
#define OPENING_DURATION_MS 1200UL
#define OPEN_WAIT_MS        5000UL
#define PASS_CLEAR_MS       500UL
#define STUCK_OPEN_MS       30000UL
#define DENY_DISPLAY_MS     2000UL
#define WAIT_DECISION_MS    5000UL

// ============================================================
// Cấu hình cảm biến siêu âm
// ============================================================
#define SENSOR_INTERVAL_MS      55UL
#define SENSOR_SAMPLES          3
#define SENSOR_DEBOUNCE         2
#define SENSOR_THRESHOLD_CM_IN  30
#define SENSOR_THRESHOLD_CM_OUT 30

// ============================================================
// Cấu hình WebSocket
// ============================================================
#define WS_PING_INTERVAL_MS 25000UL
#define WS_RECONNECT_MS     3000UL
#define STATUS_INTERVAL_MS  1000UL

// ============================================================
// NTP (đồng bộ giờ)
// ============================================================
#define NTP_SERVER "pool.ntp.org"
#define GMT_OFFSET_SECONDS (7 * 3600)
