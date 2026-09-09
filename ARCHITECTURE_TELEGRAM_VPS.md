# Architettura Sistema di Irrigazione: Integrazione Telegram & VPS

Questo documento descrive l'architettura implementata del sistema di irrigazione, l'integrazione del **Bot Telegram supervisore su VPS** e le garanzie di **completa autonomia offline** del controller **ESP8266**.

> [!NOTE]
> **Stato del Progetto:** **IMPLEMENTATO E OPERATIVO ✅**  
> Il servizio cloud è disponibile nella cartella [`oracle_vps/`](oracle_vps/) ed il firmware aggiornato è in [`src/main.cpp`](src/main.cpp).

---

## 1. Visione Architetturale: *Local Autonomy, Cloud Supervision*

Il sistema adotta un modello ibrido **Edge-Computing con Supervisione Cloud**:

```
                          ┌──────────────────────────┐
                          │     Utente (Telegram)    │
                          └─────────────┬────────────┘
                                        │ HTTPS (Pannello In-Place, Notifiche Push)
                                        ▼
 ┌────────────────────────────────────────────────────────────────────────┐
 │                   VPS / SERVER DOCKER (`oracle_vps/`)                  │
 │  • Whitelist Rigida: autorizza solo gli ID utente in .env              │
 │  • Pannello In-Place: aggiornamento real-time senza messaggi duplicati │
 │  • Popup Modali di Blocco: avvisi nativi se pioggia è attiva           │
 │  • Dead Man's Switch: allarme se l'ESP non risponde da oltre 15 min    │
 │  • Bridge MQTT over TLS 8883 (connessione in uscita verso HiveMQ)      │
 └──────────────────────────────────────┬─────────────────────────────────┘
                                        │ MQTT over TLS (8883)
                                        ▼
 ┌────────────────────────────────────────────────────────────────────────┐
 │                      BROKER MQTT (HiveMQ Cloud)                        │
 │           Topics: `command` (in)  |  `info` (out)  |  `log` (out)      │
 └──────────────────────────────────────┬─────────────────────────────────┘
                                        │ MQTT over TLS (8883)
                                        ▼
 ┌────────────────────────────────────────────────────────────────────────┐
 │                    ESP8266 (CONTROLLER DI BORDO)                       │
 │  • Schedulazione locale programmi su LittleFS (`/programs.json`)       │
 │  • Macchina a stati sequenze (tempi, transizioni 3s tra settori)       │
 │  • Pilotaggio hardware 8 relay via I2C / PCF8574                       │
 │  • Riconnessione non bloccante Wi-Fi/MQTT basata su millis()           │
 │  • Last Will and Testament (LWT) su topic log per allarmi immediati    │
 │  • TOTALE AUTONOMIA: irriga regolarmente anche a Internet/VPS spenti   │
 └────────────────────────────────────────────────────────────────────────┘
```

---

## 2. Perché la VPS alleggerisce l'ESP8266

1. **Protezione della memoria RAM dell'ESP8266:**
   - L'ESP8266 dispone di circa 30–40 KB di RAM libera.
   - Gestisce già la crittografia TLS (BearSSL) verso HiveMQ Cloud e un buffer MQTT di 4096 byte.
   - Gestire anche connessioni HTTPS verso `api.telegram.org` (polling, certificati SSL Telegram e parsing JSON) porterebbe a crash per Out-Of-Memory (OOM) o reset del Watchdog (WDT).
   - Con la VPS, l'ESP non sa nemmeno cosa sia Telegram e continua a usare solo il suo leggero canale MQTT.
2. **Disaccoppiamento e Concorrenza:**
   - L'ESP8266 risponde al broker con un solo listener. La VPS fa da proxy, consentendo a più utenti autorizzati di consultare lo stato senza appesantire il microcontrollore.
3. **Dead Man's Switch (Allarme Blackout/Guasto):**
   - Se salta la corrente a casa o cade il router, l'ESP si spegne e non può inviare allarmi.
   - La VPS rileva l'assenza di messaggi (o riceve il messaggio LWT da HiveMQ) e avverte tempestivamente l'utente: *"⚠️ Centralina Irrigazione OFFLINE!"*.

---

## 3. Suddivisione delle Responsabilità

### Assegnato all'ESP8266 (Edge)
* **Programmi e Schedulatore:** File `programs.json` e `settings.json` persistiti su LittleFS.
* **Macchina a stati dei Relè:** Avanzamento sequenze, rispetto della durata dei settori, ritardo di sicurezza (`STEP_TRANSITION_DELAY_SEC = 3s`), blocco di sicurezza se `rainMode` è attivo.
* **Autonomia totale:** Continua ad irrigare anche se la VPS è spenta o internet è assente.

### Assegnato alla VPS ([`oracle_vps/`](oracle_vps/))
* **Bot Telegram Interattivo:** Menu a bottoni inline per stato, settori manuali, programmi test, pioggia e stop d'emergenza.
* **Sicurezza:** Whitelist su `from_user.id` (accesso negato e log per utenti non autorizzati).
* **Esperienza Utente Single-Message:** Aggiornamento in-place del messaggio senza spam nella chat.
* **Notifiche Push & Watchdog:** Allarmi di centralina offline e notifiche di avvio/fine programmi schedulati.

---

## 4. Implementazioni di Resilienza nel Firmware ([src/main.cpp](src/main.cpp))

Tutte le criticità di connettività sono state risolte nel firmware:

### 1. Connessioni non bloccanti con `millis()` (IMPLEMENTATO ✅)
* In [src/main.cpp](src/main.cpp), `handleWiFi()` e `handleMqtt()` tentano la riconnessione a intervalli regolari (10s e 5s) senza mai usare cicli `while` o `delay()` bloccanti.
* `processPrograms()` continua a girare ad ogni ciclo di `loop()` garantendo l'irrigazione anche in assenza di rete.

### 2. MQTT Last Will and Testament - LWT (IMPLEMENTATO ✅)
* All'atto della connessione MQTT, l'ESP registra su HiveMQ il testamento:
  `{"error":"ESP8266 disconnected unexpectedly (LWT)"}` sul topic `log`.
* Se l'alimentazione viene a mancare improvvisamente, HiveMQ pubblica automaticamente il messaggio e la VPS avvisa l'utente all'istante.

### 3. Sincronizzazione Oraria (NTP & Opzione RTC)
* Il firmware sincronizza l'ora locale con i server NTP (`pool.ntp.org`). Una volta sincronizzato, l'ESP8266 mantiene autonomamente il tempo con il clock interno.
* *(Opzione Hardware Futura):* In caso di riavvio dell'ESP direttamente durante un blackout prolungato senza internet, è possibile aggiungere un modulo RTC DS3231 sugli stessi pin I2C (`SDA`/`SCL`) del PCF8574.

---

## 5. Struttura del Servizio VPS ([`oracle_vps/`](oracle_vps/))

Il servizio supervisore cloud è completamente implementato e include:

| File | Descrizione |
| ---- | ----------- |
| [`oracle_vps/config.py`](oracle_vps/config.py) | Configurazione centralizzata con caricamento `.env` e validazione whitelist utenti. |
| [`oracle_vps/mqtt_manager.py`](oracle_vps/mqtt_manager.py) | Client MQTT over TLS (porta 8883) verso HiveMQ Cloud con cache di stato e dispatcher comandi. |
| [`oracle_vps/bot_handlers.py`](oracle_vps/bot_handlers.py) | Gestione menu Telegram in-place, popup modali, navigazione e decoratore `@authorized_only`. |
| [`oracle_vps/notifier.py`](oracle_vps/notifier.py) | Notifiche push per eventi critici e loop del Dead Man's Switch (timeout 15 min). |
| [`oracle_vps/main.py`](oracle_vps/main.py) | Entrypoint principale con gestione lifecycle asincrono e log puliti. |
| [`oracle_vps/Dockerfile`](oracle_vps/Dockerfile) | Immagine container multi-architettura (ARM64 / AMD64) basata su Python 3.11 slim. |
| [`oracle_vps/docker-compose.yml`](oracle_vps/docker-compose.yml) | Orchestrazione con riavvio automatico continuo (`restart: unless-stopped`). |
| [`oracle_vps/DEPLOY.md`](oracle_vps/DEPLOY.md) | Guida passo-passo al deploy sia tramite Docker che con systemd Linux. |

---

## 6. Stato della Roadmap

1. **Fase 1: Sviluppo Servizio VPS Telegram & MQTT Bridge** ➔ **COMPLETATO ✅**
   - Bot operativo con pulsanti inline, whitelist ID, aggiornamento in-place e auto-refresh MQTT.
2. **Fase 2: Resilienza Firmware ESP8266 & LWT** ➔ **COMPLETATO ✅**
   - Riconnessione non bloccante `handleWiFi()` e `handleMqtt()` con `millis()`.
   - Integrazione Last Will and Testament su HiveMQ Cloud testata e compilata con successo.
3. **Fase 3: Deploy su VPS (Oracle Cloud Always Free / Linux)** ➔ **PRONTO PER IL DEPLOY ✅**
   - Testato in locale, configurato con variabili `.env` e pronto per `docker-compose up -d --build`.
4. **Fase 4 (Hardware Opzionale Futuro):**
   - Modulo RTC DS3231 I2C per mantenere l'ora anche dopo reboot isolati a internet spento.
