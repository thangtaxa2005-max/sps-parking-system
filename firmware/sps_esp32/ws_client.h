#pragma once

#include <Arduino.h>
#include <ArduinoJson.h>
#include <ArduinoWebsockets.h>
#include <WiFi.h>
#include "config.h"

using namespace websockets;

class WsClient {
 public:
  WsClient(const char* host, uint16_t port, const char* path, const char* apiKey)
      : host_(host), port_(port), path_(path), apiKey_(apiKey) {}

  void (*onDecision)(uint8_t, JsonDocument&) = nullptr;
  void (*onOpenCommand)(uint8_t, JsonDocument&) = nullptr;
  void (*onConnect)(uint8_t) = nullptr;
  void (*onDisconnect)(uint8_t) = nullptr;

  void begin() {
    for (uint8_t i = 0; i < 2; ++i) {
      clients_[i].onMessage([this, i](WebsocketsMessage message) { handleMessage(i, message); });
      clients_[i].onEvent([this, i](WebsocketsEvent event, String data) {
        (void)data;
        if (event == WebsocketsEvent::ConnectionOpened) {
          connected_[i] = true; Serial.printf("[WS] Connected gate=%s\n", gateName(i));
          if (onConnect) onConnect(i);
        } else if (event == WebsocketsEvent::ConnectionClosed) {
          connected_[i] = false; Serial.printf("[WS] Disconnected gate=%s, reconnecting in 3s\n", gateName(i));
          if (onDisconnect) onDisconnect(i);
        }
      });
      clients_[i].addHeader("Authorization", String("Bearer ") + apiKey_);
      clients_[i].addHeader("X-Gate-Id", gateName(i));
    }
  }

  void loop() {
    if (WiFi.status() != WL_CONNECTED) return;
    unsigned long now = millis();
    for (uint8_t i = 0; i < 2; ++i) {
      if (connected_[i]) {
        clients_[i].poll();
        if (now - lastPing_[i] >= WS_PING_INTERVAL_MS) {
          lastPing_[i] = now;
          clients_[i].send("{\"type\":\"ping\"}");
        }
        continue;
      }
      if (now - lastAttempt_[i] < WS_RECONNECT_MS) continue;
      lastAttempt_[i] = now;
      clients_[i].connect(host_, port_, path_);
    }
  }

  bool isConnected(uint8_t gateId) const { return gateId < 2 && connected_[gateId]; }
  void send(uint8_t gateId, const char* json) { if (isConnected(gateId)) clients_[gateId].send(json); }
  void sendTap(uint8_t id, const char* gate, const char* uid, unsigned long t) {
    DynamicJsonDocument doc(256); doc["type"] = "tap"; doc["gate"] = gate; doc["uid"] = uid; doc["t_tap"] = t; sendDoc(id, doc);
  }
  void sendSimple(uint8_t id, const char* type) { DynamicJsonDocument doc(128); doc["type"] = type; sendDoc(id, doc); }
  void sendStuck(uint8_t id, const char* gate, float duration) {
    DynamicJsonDocument doc(192); doc["type"] = "stuck_open"; doc["gate"] = gate; doc["duration_s"] = duration; sendDoc(id, doc);
  }
  void wsSendTap(uint8_t id, const char* gate, const char* uid, unsigned long t) { sendTap(id, gate, uid, t); }
  void wsSendPassConfirmed(uint8_t id) { sendSimple(id, "pass_confirmed"); }
  void wsSendNoEntryTimeout(uint8_t id) { sendSimple(id, "no_entry_timeout"); }
  void wsSendObstruction(uint8_t id) { sendSimple(id, "obstruction"); }
  void wsSendStuckOpen(uint8_t id, const char* gate, float duration) { sendStuck(id, gate, duration); }

 private:
  WebsocketsClient clients_[2]; bool connected_[2] = {false, false};
  unsigned long lastAttempt_[2] = {0, 0}; unsigned long lastPing_[2] = {0, 0};
  const char* host_; uint16_t port_; const char* path_; const char* apiKey_;
  const char* gateName(uint8_t id) const { return id == 0 ? "in" : "out"; }
  void sendDoc(uint8_t id, JsonDocument& doc) { String output; serializeJson(doc, output); send(id, output.c_str()); }
  void handleMessage(uint8_t id, WebsocketsMessage& message) {
    DynamicJsonDocument doc(512);
    if (deserializeJson(doc, message.data())) return;
    String type = doc["type"] | "";
    if (type == "decision" && onDecision) onDecision(id, doc);
    else if (type == "open_command" && onOpenCommand) onOpenCommand(id, doc);
  }
};
