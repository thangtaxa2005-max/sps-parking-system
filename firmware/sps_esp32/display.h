#pragma once

#include <Adafruit_GFX.h>
#include <Adafruit_SSD1306.h>
#include <Wire.h>
#include "config.h"

static Adafruit_SSD1306 spsDisplay(OLED_WIDTH, OLED_HEIGHT, &Wire, -1);

inline void displayInit() {
  Wire.begin(PIN_OLED_SDA, PIN_OLED_SCL);
  spsDisplay.begin(SSD1306_SWITCHCAPVCC, OLED_ADDR);
  spsDisplay.setTextColor(SSD1306_WHITE);
  spsDisplay.clearDisplay();
  spsDisplay.display();
}

inline void displayClear() {
  spsDisplay.clearDisplay();
  spsDisplay.display();
}

inline void displayLines(const String& first, const String& second = "",
                         const String& third = "") {
  // Font mặc định không có glyph tiếng Việt; dùng fallback không dấu để luôn đọc được.
  spsDisplay.clearDisplay();
  spsDisplay.setTextSize(1);
  spsDisplay.setCursor(0, 0); spsDisplay.println(first);
  if (second.length()) { spsDisplay.setCursor(0, 20); spsDisplay.println(second); }
  if (third.length()) { spsDisplay.setCursor(0, 40); spsDisplay.println(third); }
  spsDisplay.display();
}

inline void displayIdle(int freeTotal) {
  displayLines("Moi quet the", "Cho trong: " + String(freeTotal));
}

inline void displayMessage(const String& msg) {
  spsDisplay.clearDisplay();
  spsDisplay.setTextSize(1);
  spsDisplay.setCursor(0, 22);
  spsDisplay.println(msg);
  spsDisplay.display();
}

inline void displayConnection(bool connected) {
  if (!connected) displayMessage("Mat ket noi");
}
