// LoRa SENDER: generic ESP32 dev board + Adafruit RFM95W breakout
// Library: "LoRa" by Sandeep Mistry (Arduino Library Manager)
//
// Wiring (RFM95W breakout -> ESP32):
//   VIN -> 3V3        GND -> GND
//   SCK -> GPIO18     MISO -> GPIO19    MOSI -> GPIO23
//   CS  -> GPIO5      RST  -> GPIO14    G0 (DIO0) -> GPIO26
//   Solder an antenna to the ANT pad (~8.2 cm wire for 915 MHz).
//   Sensor example: analog sensor on GPIO34 (input-only ADC pin).

#include <SPI.h>
#include <LoRa.h>

#define LORA_SCK   18
#define LORA_MISO  19
#define LORA_MOSI  23
#define LORA_CS     5
#define LORA_RST   14
#define LORA_DIO0  26

#define SENSOR_PIN 34

// 915E6 for the US. Must match the receiver.
#define LORA_FREQ  915E6

unsigned long packetCount = 0;

void setup() {
  Serial.begin(115200);
  while (!Serial && millis() < 3000);

  SPI.begin(LORA_SCK, LORA_MISO, LORA_MOSI, LORA_CS);
  LoRa.setPins(LORA_CS, LORA_RST, LORA_DIO0);

  if (!LoRa.begin(LORA_FREQ)) {
    Serial.println("LoRa init failed. Check wiring.");
    while (true) delay(1000);
  }

  // These settings must match the receiver
  LoRa.setSpreadingFactor(7);
  LoRa.setSignalBandwidth(125E3);
  LoRa.setCodingRate4(5);
  LoRa.setSyncWord(0x12);
  LoRa.setTxPower(17);   // dBm, up to 20 on the RFM95W

  Serial.println("LoRa sender ready");
}

void loop() {
  int raw = analogRead(SENSOR_PIN);              // 0-4095
  float volts = raw * 3.3f / 4095.0f;

  // Simple CSV payload: count,raw,volts
  String payload = String(packetCount) + "," + String(raw) + "," + String(volts, 2);

  LoRa.beginPacket();
  LoRa.print(payload);
  LoRa.endPacket();

  Serial.print("Sent: ");
  Serial.println(payload);

  packetCount++;
  delay(5000);   // send every 5 seconds
}
