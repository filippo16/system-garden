# PROJECT.md

## Obiettivo del progetto

Questo firmware è progettato per un ESP8266 (NodeMCU v2) che controlla un sistema di irrigazione a giardino tramite 8 relay gestiti da un espansore I2C tipo PCF8574/PCF8574A all'indirizzo 0x20.

L'unità:
- legge e salva programmi di irrigazione su LittleFS;
- esegue programmi schedulati in base all'orario locale;
- può essere controllata da comandi MQTT;
- supporta una modalitá `rainMode` che blocca l'esecuzione automatica e manuale;
- pubblica stato e log su topic MQTT.

Il firmware è pensato per essere un controller indipendente, con un'interfaccia MQTT che permette:
- lettura dello stato;
- creazione/modifica/eliminazione programmi;
- attivazione manuale dei relay;
- stop di una sequenza in esecuzione;
- gestione nomi dei settori e modalitá pioggia.

---

## Struttura del progetto

### File principali

- `src/main.cpp` — firmware principale, tutta la logica applicativa.
- `platformio.ini` — configurazione di PlatformIO per ESP8266.
- `MQTT_COMMANDS.md` — documentazione dei comandi MQTT supportati.
- `data/index.html` — frontend browser che connette al broker MQTT e permette il controllo manuale.
- `/programs.json` — file JSON di LittleFS con i programmi salvati.
- `/settings.json` — file JSON di LittleFS con impostazioni globali (`rainMode`, nomi settori).

### Librerie usate

Nel file `platformio.ini` sono presenti:
- `knolleary/PubSubClient` — client MQTT
- `bblanchon/ArduinoJson` — parsing/serializzazione JSON

---

## Architettura hardware software

### Hardware presunto

- MCU: ESP8266
- Wi‑Fi: connessione a rete domestica
- Broker MQTT: HiveMQ Cloud su TLS 8883
- Relay: 8 canali, rappresentati come bit in un byte `relayState`
- PCF via I2C: indirizzo `0x20`

### Convenzione relay

Il firmware usa un byte `relayState` dove:
- bit 0..7 = relay 0..7
- `isRelayOn(relay)` restituisce `true` se il bit è spento, perché il driver del PCF usa logica inversa:
  - `relayState & (1 << relay) == 0` => relay ON
  - bit impostato a 1 => relay OFF

Questo è importante perché il firmware non usa una mappatura relay "naturale" ma un modello invertito per i driver hardware.

---

## MQTT: topic e comportamento

### Topic usati

- `command` — riceve comandi in input
- `info` — pubblica lo stato corrente
- `log` — pubblica messaggi di log e errori

### Broker configurato

- Host: `288ea69720d64b498ef73c73d5c405a5.s1.eu.hivemq.cloud`
- Porta: `8883`
- Username: `ESP-8266`
- Client ID: `esp8266-client`

### Sicurezza

Il client usa:
- `WiFiClientSecure`
- `secureClient.setInsecure();`

Questo disabilita la validazione del certificato TLS, quindi va considerato un setup semplificato per reti di test o ambiente domestico. Non è l’assetto più sicuro per produzione.

---

## Modello di dati

### `ProgramStep`

```cpp
struct ProgramStep {
  uint8_t relay;       // 0..7
  uint16_t durationMin;
};
```

Ogni step rappresenta:
- quale relay attivare;
- per quanti minuti mantenerlo acceso.

### `ProgramItem`

```cpp
struct ProgramItem {
  String id;
  uint8_t startHour;    // 0..23
  uint8_t startMinute;  // 0..59
  bool enabled;
  uint8_t stepCount;
  ProgramStep steps[MAX_STEPS_PER_PROGRAM];
  int lastRunDay;
};
```

Ogni programma include:
- ID univoco;
- orario di partenza;
- flag `enabled`;
- sequenza di step;
- ultimo giorno in cui è stato eseguito (per evitare doppia esecuzione nello stesso giorno).

### Limiti imposti

- `MAX_PROGRAMS = 12`
- `MAX_STEPS_PER_PROGRAM = 8`
- `STEP_TRANSITION_DELAY_SEC = 3`

---

## Persistenza su LittleFS

### `settings.json`

Contiene:
- `rainMode`
- `sectorNames` (array di 8 nomi)

### `programs.json`

Contiene array `programs` con ogni programma serializzato come:
- `id`
- `startHour`
- `startMinute`
- `enabled`
- `sequence[]` con `relay` e `duration`

Le funzioni principali sono:
- `saveSettings()`
- `loadSettings()`
- `savePrograms()`
- `loadPrograms()`

Il firmware crea i file se mancanti alla prima boot.

---

## Logica di scheduling

### Programmi automatici
Nella funzione `processPrograms()`:
- controlla ogni secondo (`lastProgramCheckMs`);
- se `time(nullptr)` non è sincronizzato, non esegue nulla;
- se `rainMode == true`, non parte alcun programma;
- se non c’è una sequenza in corso, guarda se esiste un programma con:
  - `enabled == true`
  - `startHour == ora locale`
  - `startMinute == minuto locale`
  - `lastRunDay != giorno corrente`

Se trova un match, invoca `startProgramSequence(index, now, false)` e lo esegue.

### Anti-duplica
Il campo `lastRunDay` evita che lo stesso programma venga eseguito due volte nello stesso giorno.

---

## Sequenze di irrigazione

### Stato della sequenza
I seguenti flag gestiscono l’esecuzione in corso:

```cpp
bool sequenceRunning = false;
int runningProgramIndex = -1;
uint8_t runningStepIndex = 0;
time_t runningStepEndTs = 0;
int activeRelayFromSequence = -1;
bool waitingBetweenSteps = false;
time_t transitionPauseUntilTs = 0;
```

### Comportamento

- al start, il primo relay della sequenza viene acceso;
- la durata del passo viene convertita in secondi:
  - `step.durationMin * 60`
- quando scade il tempo, il relay viene spento;
- si attende `STEP_TRANSITION_DELAY_SEC` = 3 secondi prima del prossimo step;
- la sequenza termina quando sono stati eseguiti tutti i passi.

### Funzioni chiave

- `startProgramSequence(int index, time_t now, bool forceStart = false)`
- `advanceSequence(time_t now)`
- `stopSequence()`

### Nota importante

Se `rainMode` è attivo, `startProgramSequence` non parte alcun programma a meno che non venga chiamato con `forceStart = true`.

---

## Comandi MQTT supportati

La documentazione dettagliata è in `MQTT_COMMANDS.md`.

### Azioni principali

- `get_info` — restituisce stato completo
- `set_program` — crea/aggiorna programma
- `enable_program` — abilita programma
- `disable_program` — disabilita programma
- `delete_program` — elimina programma
- `stop_program` — interrompe sequenza
- `start_program` — avvia manualmente un programma per test
- `set_rain_mode` — attiva/disattiva rain mode
- `set_sector_names` — aggiorna nomi settori

Inoltre, payload generici con:
- `relay`: numero relay 1..8
- `active`: `true`/`false`

possono attivare o disattivare manualmente un relay.

---

## Stato e pubblicazione MQTT

### `publishInfo(const char* reason)`
Genera un payload JSON con:
- motivo di aggiornamento (`reason`)
- ora locale ISO (`time`)
- `rainMode`
- stato di tutti i relay
- nomi dei settori
- lista programmi
- eventuale sequenza in corso

### `publishLog(const char* message)`
Pubblica messaggi su `log`.

### `onMqttMessage(...)`
Parser delle richieste MQTT. Fa il dispatch delle azioni e chiama le funzioni corrispondenti.

---

## NTP e orario locale

Il firmware usa:

```cpp
configTime(TZ_OFFSET_SECONDS, DST_OFFSET_SECONDS, NTP_SERVER_1, NTP_SERVER_2);
```

Con:
- NTP server 1: `pool.ntp.org`
- NTP server 2: `time.google.com`
- `TZ_OFFSET_SECONDS = 3600` (CET)
- `DST_OFFSET_SECONDS = 3600` (CEST)

Questo garantisce che i programmi vengano eseguiti in base all’ora locale, ma richiede che l’ESP sia connesso a Internet e sincronizzato con NTP.

---

## Startup e loop principali

### `setup()`
Sequence di avvio:
1. `Wire.begin();`
2. `writePCF();` per spegnere immediatamente tutti i relay all’avvio (sicurezza hardware)
3. `LittleFS.begin();`
4. `loadSettings();`
5. `loadPrograms();`
6. `WiFi.mode(WIFI_STA);` e `WiFi.begin(...)` con timeout iniziale non bloccante
7. `configTime(...)`
8. setup client MQTT (`setInsecure`, `setServer`, `setBufferSize`, `setCallback`)
9. tentativo di prima connessione MQTT se Wi-Fi già associato

### `loop()`
Sequence ciclica non bloccante:
1. `handleWiFi();` — riconnessione Wi-Fi non bloccante a intervalli regolari (10s)
2. `handleMqtt();` — elaborazione messaggi MQTT o riconnessione non bloccante a intervalli (5s) senza `delay()`
3. `processPrograms();` — elaborazione autonoma costante di programmi e sequenze di irrigazione

---

## Web UI fallback

Il file `data/index.html` contiene una dashboard web con:
- connessione MQTT via WebSocket
- status dei relay
- pulsanti di attivazione manuale
- form per gestione programmi
- gestione del `rainMode`
- lettura logs e stato

L’UI è utile per test e debug, ma il progetto è stato pensato principalmente come controller MQTT backend.

---

## Regole di business / logiche critiche

1. Se `rainMode` è attivo, non parte nessun programma automatico e non si possono attivare relay manuali se `active == true`.
2. Se c’è una sequenza in corso, i comandi manuali interrompono la sequenza con `stopSequence()`.
3. I programmi vengono salvati in JSON su LittleFS e ricaricati a boot.
4. L’ID del programma deve essere univoco.
5. Ogni step deve avere:
   - relay 1..8
   - durata > 0 minuti
6. I relay sono gestiti in logica inversa rispetto al bit del PCF, quindi non usare la semantica “bit 1 = ON” senza verificare il driver.

---

## Caveat importanti

### 1) `rainMode` blocca anche i comandi manuali attivi
Il firmware implementa:

```cpp
if (rainMode && active) {
  publishLog("{\"error\":\"Rain mode active: manual start blocked\"}");
  publishInfo("rain_mode_blocked");
  return;
}
```

Quindi, se il sistema è in modalitá pioggia, gli attivatori manuali di relay non hanno effetto.

### 2) `secureClient.setInsecure()`
Il TLS è attivato ma senza verifica certificati. È pratico, ma non robusto per un ambiente pubblico o critico.

### 3) Nessuna gestione avanzata di concorrenza o locking
Questo firmware assume un solo gestore del sistema e un solo client MQTT attivo; non è progettato per concorrere con molti writer paralleli.

### 4) `processPrograms()` contolla schedulazione ad ogni secondo
Il loop è semplice, ma non usa task multi-threading né scheduler RTOS. Se la logica cresce, può diventare difficile gestire più eventi simultanei.

---

## Comandi utili per sviluppo

### Build
```bash
pio run
```

### Upload via USB
```bash
pio run --target upload
```

### Monitor seriale
```bash
pio device monitor
```

### Build + upload + monitor
```bash
pio run --target upload && pio device monitor
```

---

## Quick reference per agent AI

Quando lavori su questo progetto, tieni presente:

- L’applicazione è un firmware ESP8266 che gestisce irrigazione tramite MQTT.
- Il cuore logico è in `src/main.cpp`.
- Rendersi la logica del scheduler e dei relay ècritico prima di modificarla.
- I dati persistenti sono in LittleFS, non in EEPROM.
- Tutte le azioni esterne arrivano via MQTT sul topic `command`.
- I programmi hanno una struttura seria: `relay` + `duration` con pause tra step.
- `rainMode` ha precedenza e blocca la sequenza automatica.

---

## Files da leggere in ordine consigliato

1. `src/main.cpp`
2. `MQTT_COMMANDS.md`
3. `platformio.ini`
4. `data/index.html`

Questo dipende dall’obiettivo: se stai modificando la logica dell’irrigazione, parte da `main.cpp`; se stai aggiungendo un'API o un nuovo comando MQTT, controlla prima `MQTT_COMMANDS.md` e poi `main.cpp`.

---

## Conclusione

Il progetto è un semplice ma completo controller di irrigazione ESP8266 basato su MQTT e schedulazione locale.

Il punto più importante da mantenere durante modifiche future è la coerenza tra:
- stato relay;
- scheduling programmi;
- `rainMode`;
- persistenza LittleFS;
- API MQTT.

Qualsiasi modifica alle regole di esecuzione deve essere testata in relazione a questi quattro flussi: comando esterno, scheduler automatico, sequenza in esecuzione e salvataggio su filesystem.
