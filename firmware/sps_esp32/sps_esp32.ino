#include <Arduino.h>
#include <WiFi.h>
#include <time.h>
#include "config.h"
#include "display.h"
#include "sensors.h"
#include "ws_client.h"
#include "gate.h"

WsClient ws(SERVER_HOST, SERVER_PORT, SERVER_PATH, API_KEY);
bool isWsConnected(uint8_t id) { return ws.isConnected(id); }

Gate gateIn(0, "in", PIN_TRIG_IN, PIN_ECHO_IN, PIN_SERVO_IN, PIN_RC522_SS_IN,
            SENSOR_THRESHOLD_CM_IN, [](uint8_t id, const char* json) { ws.send(id, json); }, isWsConnected,
            HAS_RC522_IN);

Gate gateOut(1, "out", PIN_TRIG_OUT, PIN_ECHO_OUT, PIN_SERVO_OUT, PIN_RC522_SS_OUT,
             SENSOR_THRESHOLD_CM_OUT, [](uint8_t id, const char* json) { ws.send(id, json); }, isWsConnected,
             HAS_RC522_OUT);

void onDecision(uint8_t id, JsonDocument& doc) {
  bool allow = doc["allow"] | false;
  String reason = doc["reason_code"] | "unknown";
  String oled = doc["oled_message"] | reason;
  int freeTotal = doc["free_total"] | 0;
  if (id == 0) gateIn.onDecisionReceived(allow, reason, oled, freeTotal);
  else gateOut.onDecisionReceived(allow, reason, oled, freeTotal);
}

void onConnected(uint8_t id) {
  if (id == 0) gateIn.onWsConnected();
  else gateOut.onWsConnected();
}

void onOpenCommand(uint8_t id, JsonDocument& doc) {
  (void)doc;
  if (id == 0) gateIn.onOpenCommand();
  else gateOut.onOpenCommand();
}

void onDisconnected(uint8_t id) {
  if (id == 0) gateIn.onWsDisconnected();
  else gateOut.onWsDisconnected();
}

void setup() {
  Serial.begin(115200);
  delay(200);
  Serial.println("SPS v7 ESP32 starting...");

  displayInit();
  displayMessage("Dang ket noi WiFi...");

  SPI.begin(PIN_RC522_SCK, PIN_RC522_MISO, PIN_RC522_MOSI);
  Ultrasonic::ultrasonicInit();

  WiFi.mode(WIFI_STA);
  WiFi.setSleep(false);
  WiFi.begin(WIFI_SSID, WIFI_PASSWORD);

  while (WiFi.status() != WL_CONNECTED) {
    delay(50);
    Serial.print(".");
  }
  Serial.printf("\nWiFi connected IP=%s RSSI=%d\n",
                WiFi.localIP().toString().c_str(), WiFi.RSSI());

  configTime(GMT_OFFSET_SECONDS, 0, NTP_SERVER);

  ws.onDecision = onDecision;
  ws.onOpenCommand = onOpenCommand;
  ws.onConnect = onConnected;
  ws.onDisconnect = onDisconnected;
  ws.begin();

  gateIn.begin();
  gateOut.begin();
}

void loop() {
  gateIn.update();
  gateOut.update();
  ws.loop();

  static unsigned long lastStatus = 0;
  if (millis() - lastStatus >= STATUS_INTERVAL_MS) {
    lastStatus = millis();
    Serial.printf("[STATUS] in=%s out=%s ws_in=%d ws_out=%d heap=%u\n",
                  gateIn.stateName(), gateOut.stateName(),
                  ws.isConnected(0), ws.isConnected(1), ESP.getFreeHeap());
  }

  delay(5);
}
