#pragma once

#include <Arduino.h>
#include <MFRC522.h>
#include <ESP32Servo.h>
#include <SPI.h>
#include "config.h"
#include "display.h"
#include "sensors.h"

typedef void (*GateSendJson)(uint8_t gateId, const char* json);
typedef bool (*GateIsWsConnected)(uint8_t gateId);

class Gate {
 public:
  enum State { IDLE, WAIT_DECISION, OPENING, OPEN_WAIT, PASSING, CLOSING, DENY_SHOW };

  static String asciiMessage(const String& code) {
    if (code == "ok") return "Moi vao";
    if (code == "card_unknown") return "The khong hop le";
    if (code == "card_locked") return "The da khoa";
    if (code == "card_lost") return "The da khoa";
    if (code == "plan_expired") return "The het han";
    if (code == "already_in_lot") return "Xe da o trong bai";
    if (code == "not_in_lot") return "Xe chua vao bai";
    if (code == "lot_full") return "Het cho";
    if (code == "enrolled") return "Da dang ky";
    return code;
  }

  Gate(uint8_t id, const char* name, uint8_t trig, uint8_t echo, uint8_t servoPin,
       uint8_t ssPin, int threshold, GateSendJson send, GateIsWsConnected isWsConnected,
       bool hasReader)
      : id_(id), name_(name), servoPin_(servoPin), ssPin_(ssPin),
        reader_(ssPin, PIN_RC522_RST), send_(send), isWsConnected_(isWsConnected),
        hasReader_(hasReader) {
    (void)trig; (void)echo; (void)threshold;
  }

  void begin() {
    pinMode(PIN_LED, OUTPUT);
    pinMode(PIN_BUZZER, OUTPUT);
    servo_.setPeriodHertz(50);
    servo_.attach(servoPin_, 500, 2400);
    servo_.write(0);
    if (hasReader_) {
      reader_.PCD_Init();
      reader_.PCD_SetAntennaGain(MFRC522::RxGain_max);
    }
    state_ = IDLE;
    stateEnteredAt_ = millis();
    showIdle();
  }

  void update() {
    updateIndicators();
    unsigned long now = millis();
    updateServoMotion(now);
    switch (state_) {
      case IDLE: updateIdle(); break;
      case WAIT_DECISION:
        if (now - stateEnteredAt_ >= WAIT_DECISION_MS) {
          Serial.printf("[%s] Decision timeout\n", name_);
          displayConnection(false);
          changeState(DENY_SHOW);
          stateEnteredAt_ = now;
        }
        break;
      case OPENING:
        if (servoAngle_ >= servoTarget_) {
          Serial.printf("[%s] Servo reached 90 at t=%lu\n", name_, now);
          changeState(OPEN_WAIT);
        }
        break;
      case OPEN_WAIT: {
          bool objectPresent = Ultrasonic::isObjectPresent(id_);
          bool sensorOk = !Ultrasonic::sensorError(id_);
          if (sensorOk && objectPresent) {
            lastSeenAt_ = now;
            changeState(PASSING);
          } else if (now - stateEnteredAt_ >= OPEN_WAIT_MS) {
            if (manualOpen_) {
              Serial.printf("[%s] Manual open timeout; closing (sensor_ok=%d)\n", name_, sensorOk);
              beginClosing();
            } else if (sensorOk) {
              Serial.printf("[%s] No entry after %lu ms -> sending no_entry_timeout\n",
                            name_, (unsigned long)OPEN_WAIT_MS);
              sendSimple("no_entry_timeout");
              beginClosing();
            }
          }
          if (!sensorOk && !manualOpen_ && !stuckSent_ &&
              now - stateEnteredAt_ >= STUCK_OPEN_MS) {
            Serial.printf("[%s] Sensor error; gate remains open\n", name_);
            sendStuck((float)(now - stateEnteredAt_) / 1000.0f);
            stuckSent_ = true;
          }
          break;
        }
      case PASSING:
        updatePassing(now);
        break;
      case CLOSING:
        if (!Ultrasonic::sensorError(id_) && Ultrasonic::isObjectPresent(id_)) {
          startServoMove(90, now);
          lastSeenAt_ = now;
          sendSimple("obstruction");
          Serial.printf("[%s] Obstruction: reopening\n", name_);
          changeState(PASSING);
        } else if (servoAngle_ <= servoTarget_) {
          setAlarm(false);
          changeState(IDLE);
          showIdle();
        }
        break;
      case DENY_SHOW:
        if (now - stateEnteredAt_ >= DENY_DISPLAY_MS) {
          changeState(IDLE);
          showIdle();
        }
        break;
    }
  }

  void onDecisionReceived(bool allow, const String& reason, const String& oled,
                          int freeTotal) {
    if (state_ != WAIT_DECISION) return;
    freeTotal_ = freeTotal;
    if (!allow) {
      Serial.printf("[%s] Decision received: allow=false reason=%s\n", name_, reason.c_str());
      (void)oled;
      displayMessage(asciiMessage(reason));
      changeState(DENY_SHOW);
      return;
    }
    Serial.printf("[%s] Decision received: allow=true reason=%s\n", name_, reason.c_str());
    manualOpen_ = false;
    stuckSent_ = false;
    startServoMove(90, millis());
    setAlarm(true);
    changeState(OPENING);
  }

  void onOpenCommand() {
    if (state_ != IDLE) return;
    manualOpen_ = true;
    stuckSent_ = false;
    startServoMove(90, millis());
    setAlarm(true);
    changeState(OPENING);
  }

  void onWsConnected() {
    if (state_ == IDLE) showIdle();
  }

  void onWsDisconnected() {
    if (state_ == WAIT_DECISION) {
      displayConnection(false);
      changeState(DENY_SHOW);
    }
  }

  State state() const { return state_; }

  const char* stateName() const {
    static const char* names[] = {"IDLE", "WAIT", "OPENING", "OPEN_WAIT", "PASSING", "CLOSING", "DENY"};
    return names[state_];
  }

 private:
  uint8_t id_;
  const char* name_;
  uint8_t servoPin_;
  uint8_t ssPin_;
  GateSendJson send_;
  GateIsWsConnected isWsConnected_;
  bool hasReader_ = true;
  MFRC522 reader_;
  Servo servo_;
  State state_ = IDLE;
  unsigned long stateEnteredAt_ = 0;
  unsigned long lastSeenAt_ = 0;
  bool stuckSent_ = false;
  bool alarm_ = false;
  bool manualOpen_ = false;
  int freeTotal_ = 0;
  int servoAngle_ = 0;
  int servoTarget_ = 0;
  unsigned long lastServoStepAt_ = 0;
  static inline uint8_t alarmMask_ = 0;

  void changeState(State next) {
    if (state_ != next) {
      Serial.printf("[%s] State: %s -> %s\n", name_, stateName(), stateText(next));
    }
    if (next == PASSING) stuckSent_ = false;
    if (next == IDLE) manualOpen_ = false;
    state_ = next;
    stateEnteredAt_ = millis();
  }

  const char* stateText(State value) const {
    static const char* names[] = {
      "IDLE", "WAIT_DECISION", "OPENING", "OPEN_WAIT",
      "PASSING", "CLOSING", "DENY_SHOW"
    };
    return names[value];
  }

  void setAlarm(bool on) {
    alarm_ = on;
    if (on) alarmMask_ |= (1 << id_);
    else alarmMask_ &= ~(1 << id_);
  }

  void startServoMove(int target, unsigned long now) {
    servoTarget_ = target;
    lastServoStepAt_ = now;
  }

  void updateServoMotion(unsigned long now) {
    if (servoAngle_ == servoTarget_) return;
    unsigned long interval = OPENING_DURATION_MS / 90UL;
    if (interval < 1UL || now - lastServoStepAt_ < interval) return;
    lastServoStepAt_ = now;
    if (servoAngle_ < servoTarget_) ++servoAngle_;
    else --servoAngle_;
    servo_.write(servoAngle_);
  }

  void updateIndicators() {
    bool active = alarmMask_ != 0;
    unsigned long phase = millis() % 500UL;
    digitalWrite(PIN_LED, active && phase < 100 ? HIGH : LOW);
    digitalWrite(PIN_BUZZER, active && phase < 200 ? HIGH : LOW);
  }

  void showIdle() {
    displayIdle(freeTotal_);
  }

  void updateIdle() {
    // Bỏ qua hoàn toàn nếu không có module RC522.
    if (!hasReader_) return;
    if (!reader_.PICC_IsNewCardPresent() || !reader_.PICC_ReadCardSerial()) return;

    String uid;
    for (byte i = 0; i < reader_.uid.size; ++i) {
      if (reader_.uid.uidByte[i] < 16) uid += "0";
      uid += String(reader_.uid.uidByte[i], HEX);
    }
    uid.toUpperCase();
    Serial.printf("[%s] Card detected: %s\n", name_, uid.c_str());

    if (!isWsConnected_(id_)) {
      displayConnection(false);
      changeState(DENY_SHOW);
      return;
    }

    char json[256];
    snprintf(json, sizeof(json),
             "{\"type\":\"tap\",\"gate\":\"%s\",\"uid\":\"%s\",\"t_tap\":%lu}",
             name_, uid.c_str(), millis());
    send_(id_, json);
    Serial.printf("[%s] Tap sent at t_tap=%lu\n", name_, millis());

    // Kết thúc phiên đọc thẻ
    reader_.PICC_HaltA();
    reader_.PCD_StopCrypto1();

    // FIX: tắt anten 300ms + re-init để thẻ mất điện và reset trạng thái HALT
    // → cho phép quẹt lại cùng thẻ mà không cần nhấc ra xa.
    reader_.PCD_AntennaOff();
    delay(300);
    reader_.PCD_AntennaOn();
    delay(50);
    reader_.PCD_Init();
    reader_.PCD_SetAntennaGain(MFRC522::RxGain_max);

    changeState(WAIT_DECISION);
  }

  void updatePassing(unsigned long now) {
    if (!stuckSent_ && now - stateEnteredAt_ >= STUCK_OPEN_MS) {
      Serial.printf("[%s] Stuck open after 30s\n", name_);
      sendStuck((float)(now - stateEnteredAt_) / 1000.0f);
      stuckSent_ = true;
    }
    bool object = Ultrasonic::isObjectPresent(id_);
    if (Ultrasonic::sensorError(id_)) return;
    if (object) {
      lastSeenAt_ = now;
    } else if (now - lastSeenAt_ >= PASS_CLEAR_MS) {
      Serial.printf("[%s] Pass confirmed\n", name_);
      if (!manualOpen_) sendSimple("pass_confirmed");
      beginClosing();
      return;
    }
  }

  void beginClosing() {
    startServoMove(0, millis());
    changeState(CLOSING);
  }

  void sendSimple(const char* type) {
    char json[160];
    snprintf(json, sizeof(json), "{\"type\":\"%s\",\"gate\":\"%s\"}", type, name_);
    send_(id_, json);
  }

  void sendStuck(float seconds) {
    char json[160];
    snprintf(json, sizeof(json),
             "{\"type\":\"stuck_open\",\"gate\":\"%s\",\"duration_s\":%.1f}",
             name_, seconds);
    send_(id_, json);
  }
};
