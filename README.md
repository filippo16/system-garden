# Irrigation ESP8266 Controller

Firmware PlatformIO per un ESP8266 che gestisce un sistema di irrigazione domestico tramite MQTT.

## Documentazione principale

- [PROJECT.md](PROJECT.md) — contesto completo per agent AI e manutenzione del progetto
- [MQTT_COMMANDS.md](MQTT_COMMANDS.md) — lista dei comandi MQTT supportati

## Panoramica rapida

- ESP8266 + Wi‑Fi
- broker MQTT TLS
- 8 relay gestiti via I2C/PCF
- programmazione automatica dei programmi di irrigazione
- persistenza JSON su LittleFS
- supporto `rainMode` e nomi dei settori

## File chiave

- [src/main.cpp](src/main.cpp) — firmware principale
- [platformio.ini](platformio.ini) — configurazione PlatformIO
- [data/index.html](data/index.html) — dashboard web MQTT di controllo

## Build

```bash
pio run
pio run --target upload
```

## Usa il progetto come contesto per un agente AI

Leggi prima [PROJECT.md](PROJECT.md), perché contiene la definizione del dominio applicativo, gli schemi MQTT, le regole di irrigazione e i punti critici del firmware.
