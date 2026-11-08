// LoRa RECEIVER: LilyGO T3 LoRa32 V1.6.1 (plugged into the MacBook via USB)
// Library: "LoRa" by Sandeep Mistry (Arduino Library Manager)
//
// Arduino IDE board: "ESP32 Dev Module" (or "TTGO LoRa32-OLED").
// The LoRa chip is wired on the board at these pins:

#include <SPI.h>
#include <LoRa.h>

#define LORA_SCK    5
#define LORA_MISO  19
#define LORA_MOSI  27
#define LORA_CS    18
#define LORA_RST   23
#define LORA_DIO0  26

// Use the frequency your T3 was sold for (915E6 US, 868E6 EU).
// Must match the sender.
#define LORA_FREQ  915E6

void setup() {
  Serial.begin(115200);
  while (!Serial && millis() < 3000);

  SPI.begin(LORA_SCK, LORA_MISO, LORA_MOSI, LORA_CS);
  LoRa.setPins(LORA_CS, LORA_RST, LORA_DIO0);

  if (!LoRa.begin(LORA_FREQ)) {
    Serial.println("LoRa init failed. Check board/frequency.");
    while (true) delay(1000);
  }

  // These settings must match the sender
  LoRa.setSpreadingFactor(7);
  LoRa.setSignalBandwidth(125E3);
  LoRa.setCodingRate4(5);
  LoRa.setSyncWord(0x12);

  Serial.println("LoRa receiver ready");
}

void loop() {
  int packetSize = LoRa.parsePacket();
  if (packetSize) {
    String msg = "";
    while (LoRa.available()) {
      msg += (char)LoRa.read();
    }

    // Parse "count,raw,volts"
    int c1 = msg.indexOf(',');
    int c2 = msg.indexOf(',', c1 + 1);

    Serial.print("Received: ");
    Serial.print(msg);
    Serial.print("  | RSSI: ");
    Serial.print(LoRa.packetRssi());
    Serial.print(" dBm  SNR: ");
    Serial.println(LoRa.packetSnr());

    if (c1 > 0 && c2 > c1) {
      long count  = msg.substring(0, c1).toInt();
      int raw     = msg.substring(c1 + 1, c2).toInt();
      float volts = msg.substring(c2 + 1).toFloat();
      Serial.printf("  packet #%ld, raw=%d, volts=%.2f\n", count, raw, volts);
    }
  }
}
