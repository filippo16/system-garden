# Irrigation ESP8266 Controller

Firmware PlatformIO per un ESP8266 che gestisce un sistema di irrigazione domestico tramite MQTT.

## Documentazione principale

- [PROJECT.md](PROJECT.md) — contesto completo per manutenzione del progetto
- [MQTT_COMMANDS.md](MQTT_COMMANDS.md) — lista dei comandi MQTT supportati
- [ARCHITECTURE_TELEGRAM_VPS.md](ARCHITECTURE_TELEGRAM_VPS.md) — architettura VPS, integrazione Telegram e resilienza offline

## Panoramica rapida

- ESP8266 + Wi‑Fi
- broker MQTT TLS
- 8 relay gestiti via I2C/PCF
- programmazione automatica dei programmi di irrigazione
- persistenza JSON su LittleFS
- supporto `rainMode` e nomi dei settori

## File chiave

- [src/main.cpp](src/main.cpp) — firmware principale ESP8266
- [platformio.ini](platformio.ini) — configurazione PlatformIO
- [data/index.html](data/index.html) — dashboard web MQTT di controllo
- [oracle_vps/](oracle_vps/) — servizio supervisore VPS con Bot Telegram, Whitelist ID e Dead Man's Switch

## Build

```bash
pio run
pio run --target upload
```
