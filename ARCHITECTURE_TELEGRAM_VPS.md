# Architettura Sistema di Irrigazione: Integrazione Telegram & VPS

Questo documento riassume l'analisi architetturale, le raccomandazioni e le linee guida per integrare un **Bot Telegram** e un **Server VPS** con il controller di irrigazione basato su **ESP8266**, garantendo al contempo la **completa autonomia di funzionamento offline (senza internet)**.

---

## 1. Visione Architetturale: *Local Autonomy, Cloud Supervision*

Per un sistema critico come l'irrigazione automatica da giardino, l'approccio raccomandato è separare nettamente le responsabilità tra **Edge (ESP8266)** e **Cloud (VPS)**:

```
                          ┌──────────────────────────┐
                          │     Utente (Telegram)    │
                          └─────────────┬────────────┘
                                        │ HTTPS
                                        ▼
 ┌────────────────────────────────────────────────────────────────────────┐
 │                              VPS / SERVER                              │
 │  • Bot Telegram (ricezione comandi, menu bottoni, invio notifiche)     │
 │  • Dead Man's Switch (Allarme se l'ESP si disconnette/spegne)          │
 │  • Smart Rain Mode (interrogazione API meteo es. Open-Meteo)           │
 │  • Storico & Statistiche (database persistente es. SQLite / InfluxDB)  │
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
 │  • Schedulazione locale dei programmi su LittleFS (`/programs.json`)   │
 │  • Macchina a stati sequenze (tempi, transizioni 3s tra settori)       │
 │  • Pilotaggio hardware 8 relay via I2C / PCF8574                       │
 │  • TOTALE AUTONOMIA: funziona anche a Internet spento o assente        │
 └────────────────────────────────────────────────────────────────────────┘
```

---

## 2. Perché conviene usare una VPS per il Bot Telegram?

1. **Criticità Memoria RAM sull'ESP8266:**
   - L'ESP8266 possiede risorse di RAM molto limitate (~30–40 KB liberi).
   - Il firmware attuale utilizza già `WiFiClientSecure` con cifratura TLS BearSSL per HiveMQ Cloud e un buffer MQTT di 4096 byte.
   - Gestire una seconda connessione HTTPS/TLS concorrente verso `api.telegram.org` (e relativo polling o webhook) rischierebbe crash per Out-Of-Memory (OOM) o Watchdog Reset (WDT).
2. **Disaccoppiamento Totale:**
   - L'ESP8266 non deve conoscere Telegram né implementare protocolli aggiuntivi: continua semplicemente a pubblicare sui topic `info` e `log` e a ricevere da `command`.
   - La VPS si comporta come un normale client MQTT connesso allo stesso broker.
3. **Dead Man's Switch (Monitoraggio Guasti & Blackout):**
   - Se l'ESP8266 perde alimentazione o il Wi-Fi di casa smette di funzionare, l'ESP non è in grado di inviare alcun messaggio.
   - La VPS, rilevando l'assenza di messaggi o tramite il meccanismo **MQTT LWT (Last Will and Testament)**, può notificare tempestivamente l'utente su Telegram (*"⚠️ Allarme: ESP8266 offline da oltre 5 minuti"*).

---

## 3. Suddivisione delle Responsabilità

### Cosa DEVE rimanere sull'ESP8266 (Edge)
* **Programmi e Schedulatore:** File `programs.json` e `settings.json` persistiti su LittleFS.
* **Macchina a stati dei Relay:** Avanzamento sequenze, rispetto della durata dei settori, ritardo di sicurezza (`STEP_TRANSITION_DELAY_SEC = 3s`), blocco di sicurezza se `rainMode` è attivo.
* **Protezioni Hardware:** Chiusura automatica dei relay al termine dei minuti impostati senza dipendere da comandi remoti.

### Cosa SPOSTARE sulla VPS (Cloud / Home Server)
* **Bot Telegram Interattivo:** Gestione comandi (`/stato`, `/irriga`, `/stop`, `/pioggia`), tastiere inline, autorizzazione utenti tramite whitelist di `chat_id`.
* **Notifiche Push & Allarmi:** Parsing dei messaggi su topic `log` (es. errori, blocchi rain mode) e inoltro immediato su Telegram con icone e formattazione chiara.
* **Smart Weather (Previsioni Meteo):** Script che controlla una volta al giorno le previsioni meteo locali (es. Open-Meteo) e imposta automaticamente il `rainMode` via MQTT se sono previste piogge significative.
* **Storico & Metriche:** Memorizzazione delle sessioni di irrigazione su database senza consumare i cicli di scrittura della memoria flash dell'ESP8266.

---

## 4. Punti Critici da Sistemare nel Firmware per il Funzionamento Offline

Nel firmware attuale ([src/main.cpp](src/main.cpp)) sono presenti due punti di attenzione essenziali per garantire la resilienza senza internet:

### ⚠️ Criticità 1: Loop bloccante durante la riconnessione MQTT
Attualmente in `connectMqtt()` è presente un ciclo `while` con `delay(2000)`:
```cpp
// ATTUALE (Bloccante)
void connectMqtt(bool isReconnect) {
  while (!mqttClient.connected()) {
    if (mqttClient.connect(...)) { ... }
    else { delay(2000); }
  }
}
```
* **Problema:** Se il Wi-Fi domestico è attivo ma la connessione internet esterna cade, `connectMqtt()` blocca l'esecuzione di `loop()`, impedendo a `processPrograms()` di far partire o avanzare l'irrigazione.
* **Soluzione Raccomandata:** Rendere la riconnessione **non bloccante** tramite controllo a intervalli basato su `millis()`:
```cpp
// SOLUZIONE NON BLOCCANTE (Esempio)
unsigned long lastMqttAttempt = 0;
void handleMqttConnection() {
  if (mqttClient.connected()) return;
  if (millis() - lastMqttAttempt > 10000) { // Tenta ogni 10 secondi
    lastMqttAttempt = millis();
    mqttClient.connect(CLIENT_ID, MQTT_USER, MQTT_PASS);
  }
}
```

### ⚠️ Criticità 2: Sincronizzazione Oraria (NTP) al Boot
Attualmente il controllo dei programmi richiede che l'orologio sia sincronizzato con i server NTP:
```cpp
time_t now = time(nullptr);
if (now < 100000) return; // Se non sincronizzato, l'irrigazione non parte
```
* **Problema:** Se l'ESP8266 si riavvia in assenza di internet (es. dopo un blackout), non può sincronizzare l'ora via NTP e i programmi non partiranno finché non torna la linea.
* **Soluzione Raccomandata:**
  1. Aggiungere un modulo hardware **RTC DS3231 I2C** (alimentato a batteria tampone).
  2. Il modulo si collega direttamente agli stessi pin I2C (`SDA`/`SCL`) già utilizzati per l'espansore PCF8574 a costo zero di pin aggiuntivi.
  3. All'avvio, l'ESP8266 legge l'ora dall'RTC anche se internet è assente, e aggiorna l'RTC via NTP solo quando la rete è disponibile.

---

## 5. Esempio Architettura Servizio VPS (Python Bridge)

Un tipico servizio su VPS può essere realizzato con un semplice container Docker o script Python (`paho-mqtt` + `python-telegram-bot`):

```python
# Schema logico del servizio Python su VPS
import paho.mqtt.client as mqtt
import telebot # o python-telegram-bot

# 1. Quando arriva un log di errore dall'ESP8266
def on_message(client, userdata, msg):
    if msg.topic == "log":
        payload = json.loads(msg.payload.decode())
        if "error" in payload:
            bot.send_message(CHAT_ID, f"🚨 *Allarme Irrigazione*: {payload['error']}", parse_mode="Markdown")
    elif msg.topic == "info":
        # Aggiorna stato locale o database storico
        pass

# 2. Quando l'utente preme un pulsante su Telegram
@bot.callback_query_handler(func=lambda call: True)
def callback_handler(call):
    if call.data == "stop_all":
        mqtt_client.publish("command", json.dumps({"action": "stop_program"}))
        bot.answer_callback_query(call.id, "Comando STOP inviato!")
    elif call.data == "toggle_rain":
        mqtt_client.publish("command", json.dumps({"action": "set_rain_mode", "enabled": True}))
        bot.answer_callback_query(call.id, "Modalità pioggia attivata!")
```

---

## 6. Riepilogo Roadmap Consigliata

1. **Fase 1 (Senza modifiche hardware):**
   - Configurare lo script/bot Telegram su VPS o mini-server collegato allo stesso HiveMQ Cloud.
   - Ricevere allarmi da `log` e inviare comandi su `command`.
2. **Fase 2 (Resilienza Firmware):**
   - Modificare `connectWiFi()` e `connectMqtt()` in [src/main.cpp](src/main.cpp) per renderle completamente non bloccanti con `millis()`.
3. **Fase 3 (Affidabilità Totale Offline):**
   - Integrare il modulo RTC DS3231 sul bus I2C per avere data e ora esatte anche dopo un riavvio a internet spento.
