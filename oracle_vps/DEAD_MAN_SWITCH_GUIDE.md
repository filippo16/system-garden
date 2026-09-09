# Guida: Come Reimplementare il Dead Man's Switch (Ping & Watchdog)

Questo documento contiene la guida tecnica e il codice pronto all'uso per riattivare il meccanismo di **Dead Man's Switch basato su polling/ping periodico** nel caso in cui in futuro si preferisca non fare affidamento esclusivo sull'MQTT Last Will and Testament (LWT).

---

## 1. Perché reimplementarlo in futuro?
Attualmente il sistema usa **MQTT LWT (Last Will and Testament)**: se l'ESP8266 si spegne (blackout, crash hardware o Wi-Fi spento), il broker HiveMQ rileva la chiusura della sessione TCP e invia subito l'allarme alla VPS.

Il Dead Man's Switch aggiunge una verifica software proattiva in più: serve nel caso rarissimo in cui l'ESP8266 rimanga connesso alla rete Wi-Fi, ma il software si sia bloccato in un loop/blocco interno senza rispondere ai comandi.

---

## 2. Codice da aggiungere in `oracle_vps/notifier.py`

All'interno della classe `Notifier` in `oracle_vps/notifier.py`, aggiungere il seguente metodo:

```python
    # -------------------------------------------------------------
    # Task Watchdog / Dead Man's Switch (Opzionale con Ping)
    # -------------------------------------------------------------
    async def run_dead_man_switch(self) -> None:
        """Loop asincrono che invia un ping orario e notifica se l'ESP non risponde."""
        logger.info("Avvio Dead Man's Switch con ping periodico...")
        
        # PARAMETRI CONFIGURABILI:
        PING_INTERVAL_SEC = 3600    # Ping ogni 60 minuti se l'impianto è a riposo
        OFFLINE_TIMEOUT_SEC = 3900  # Allarme se passano 65 minuti senza risposte (60 min + 5 min tolleranza)

        while True:
            await asyncio.sleep(60)  # Controllo ciclico leggero ogni minuto
            
            if self.mqtt.last_seen_timestamp == 0.0:
                # All'avvio del bot, invia la prima richiesta di stato
                self.mqtt.request_info()
                continue

            elapsed = time.time() - self.mqtt.last_seen_timestamp

            # Ping periodico leggero: se non ci sono messaggi da oltre 1 ora, chiediamo get_info
            if elapsed >= PING_INTERVAL_SEC:
                self.mqtt.request_info()

            # Se trascorsi oltre 65 minuti non ha risposto neppure al ping, scatta l'allarme
            if elapsed > OFFLINE_TIMEOUT_SEC and not self.is_flagged_offline:
                self.is_flagged_offline = True
                min_elapsed = int(elapsed // 60)
                logger.warning(f"Dead Man's Switch scattato: nessun messaggio da {min_elapsed} minuti!")
                await self.broadcast(
                    f"⚠️ *ALLARME: Centralina Irrigazione OFFLINE!*\n\n"
                    f"Nessun segnale ricevuto dall'ESP8266 da oltre *{min_elapsed} minuti*.\n"
                    "Possibili cause:\n"
                    "• Mancanza di alimentazione (blackout domestico)\n"
                    "• Wi-Fi o connessione internet assente\n"
                    "• Riavvio del router"
                )
```

---

## 3. Modifica in `oracle_vps/main.py`

In `oracle_vps/main.py`, dentro la funzione `on_startup`, riabilitare l'avvio del task in background aggiungendo questa riga:

```python
    async def on_startup(application: Application) -> None:
        loop = asyncio.get_running_loop()
        handlers.set_bot(application.bot)
        handlers.set_event_loop(loop)
        notifier.set_event_loop(loop)
        
        # AGGIUNGERE QUESTA RIGA PER RIATTIVARE IL TASK:
        asyncio.create_task(notifier.run_dead_man_switch())
        
        logger.info("Servizio Notifier, Handlers e Dead Man's Switch avviati.")
```

---

## 4. Riavviare il container
Dopo aver apportato le due modifiche sopra:

```bash
cd ~/oracle_vps
docker-compose restart
```
