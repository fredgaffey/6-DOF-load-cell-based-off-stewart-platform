// 6-axis F/T sensor firmware: reads six HX711s (one per leg) in lockstep and
// streams binary frames over USB serial.
//
// HX711 sample rate is set by hardware, not software: RATE pin low = 10 Hz,
// RATE pin high = 80 Hz. The stream follows whatever the chips deliver.
//
// Frame (little endian, 34 bytes):
//   A5 5A | u16 seq | u32 micros | 6 x i32 raw | u16 CRC-16/CCITT-FALSE
// CRC covers the 30 bytes between the sync word and the CRC (poly 0x1021,
// init 0xFFFF, no reflection, no xorout).
#include <Arduino.h>

static const uint8_t DOUT[6] = {32, 33, 25, 26, 27, 14};
static const uint8_t SCK_[6] = {19, 18, 5, 17, 16, 4};
static const uint32_t BAUD = 921600;
static const uint32_t READY_TIMEOUT_MS = 500;

static uint16_t crc16(const uint8_t *p, size_t n) {
  uint16_t crc = 0xFFFF;
  while (n--) {
    crc ^= (uint16_t)(*p++) << 8;
    for (int b = 0; b < 8; b++)
      crc = (crc & 0x8000) ? (crc << 1) ^ 0x1021 : (crc << 1);
  }
  return crc;
}

static bool all_ready() {
  for (int i = 0; i < 6; i++)
    if (digitalRead(DOUT[i])) return false;
  return true;
}

// Clock 24 data bits out of all six chips together, then one extra pulse
// (25 total) to select gain 128 / channel A for the next conversion.
static void read_all(int32_t out[6]) {
  static portMUX_TYPE mux = portMUX_INITIALIZER_UNLOCKED;
  uint32_t v[6] = {0, 0, 0, 0, 0, 0};
  // SCK high for >60 us powers a chip down, so keep interrupts off.
  portENTER_CRITICAL(&mux);
  for (int bit = 0; bit < 24; bit++) {
    for (int i = 0; i < 6; i++) digitalWrite(SCK_[i], HIGH);
    delayMicroseconds(1);
    for (int i = 0; i < 6; i++) v[i] = (v[i] << 1) | (digitalRead(DOUT[i]) ? 1 : 0);
    for (int i = 0; i < 6; i++) digitalWrite(SCK_[i], LOW);
    delayMicroseconds(1);
  }
  for (int i = 0; i < 6; i++) digitalWrite(SCK_[i], HIGH);  // 25th pulse
  delayMicroseconds(1);
  for (int i = 0; i < 6; i++) digitalWrite(SCK_[i], LOW);
  portEXIT_CRITICAL(&mux);
  for (int i = 0; i < 6; i++) {
    if (v[i] & 0x800000) v[i] |= 0xFF000000;  // sign-extend 24 -> 32 bit
    out[i] = (int32_t)v[i];
  }
}

void setup() {
  Serial.begin(BAUD);
  for (int i = 0; i < 6; i++) {
    pinMode(DOUT[i], INPUT);
    pinMode(SCK_[i], OUTPUT);
    digitalWrite(SCK_[i], LOW);
  }
}

void loop() {
  static uint16_t seq = 0;
  uint32_t t0 = millis();
  while (!all_ready()) {
    if (millis() - t0 > READY_TIMEOUT_MS) return;  // chip missing/unpowered: retry
  }
  uint32_t t = micros();
  int32_t raw[6];
  read_all(raw);

  uint8_t f[34];
  f[0] = 0xA5;
  f[1] = 0x5A;
  memcpy(f + 2, &seq, 2);
  memcpy(f + 4, &t, 4);
  memcpy(f + 8, raw, 24);
  uint16_t c = crc16(f + 2, 30);
  memcpy(f + 32, &c, 2);
  Serial.write(f, sizeof f);
  seq++;
}
