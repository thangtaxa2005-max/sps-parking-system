#pragma once

#include <Arduino.h>
#include "config.h"

namespace Ultrasonic {
static const uint8_t trigPins[2] = {PIN_TRIG_IN, PIN_TRIG_OUT};
static const uint8_t echoPins[2] = {PIN_ECHO_IN, PIN_ECHO_OUT};
static const int thresholds[2] = {SENSOR_THRESHOLD_CM_IN, SENSOR_THRESHOLD_CM_OUT};
static int distances[2] = {-1, -1};
static bool present[2] = {false, false};
static bool errors[2] = {false, false};
static unsigned long lastRead[2] = {0, 0};
static uint8_t candidateCount[2] = {0, 0};
static bool candidate[2] = {false, false};

inline int lastDistanceCm(uint8_t gateId) { return distances[gateId < 2 ? gateId : 0]; }
inline bool sensorError(uint8_t gateId) { return errors[gateId < 2 ? gateId : 0]; }

inline void ultrasonicInit() {
  for (uint8_t i = 0; i < 2; ++i) {
    pinMode(trigPins[i], OUTPUT);
    pinMode(echoPins[i], INPUT);
    digitalWrite(trigPins[i], LOW);
  }
}

inline bool isObjectPresent(uint8_t gateId) {
  if (gateId > 1) gateId = 0;
  unsigned long now = millis();
  if (now - lastRead[gateId] < SENSOR_INTERVAL_MS) return present[gateId];
  lastRead[gateId] = now;
  int samples[SENSOR_SAMPLES];
  uint8_t count = 0;
  for (uint8_t i = 0; i < SENSOR_SAMPLES; ++i) {
    digitalWrite(trigPins[gateId], LOW);
    delayMicroseconds(2);
    digitalWrite(trigPins[gateId], HIGH);
    delayMicroseconds(10);
    digitalWrite(trigPins[gateId], LOW);
    unsigned long pulse = pulseIn(echoPins[gateId], HIGH, 30000UL);
    if (pulse > 0) samples[count++] = (int)(pulse / 58UL);
    if (i + 1 < SENSOR_SAMPLES) delay(15);
  }
  if (count == 0) {
    errors[gateId] = true;
    return present[gateId];
  }
  errors[gateId] = false;
  for (uint8_t i = 0; i < count; ++i)
    for (uint8_t j = i + 1; j < count; ++j)
      if (samples[j] < samples[i]) { int t = samples[i]; samples[i] = samples[j]; samples[j] = t; }
  int median = samples[count / 2];
  distances[gateId] = median;
  bool next = median < thresholds[gateId];
  if (next == candidate[gateId]) {
    if (candidateCount[gateId] < SENSOR_DEBOUNCE) ++candidateCount[gateId];
  } else { candidate[gateId] = next; candidateCount[gateId] = 1; }
  if (candidateCount[gateId] >= SENSOR_DEBOUNCE) present[gateId] = candidate[gateId];
  return present[gateId];
}
}
