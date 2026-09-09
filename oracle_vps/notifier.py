"""
Sistema di notifiche push proattive su Telegram per eventi critici
e Dead Man's Switch per monitoraggio caduta di linea/blackout.
"""

import asyncio
import logging
import time
from typing import Any
from telegram import Bot
from telegram.constants import ParseMode

from config import ALLOWED_USER_IDS, HEARTBEAT_TIMEOUT_MINUTES
from mqtt_manager import MQTTManager

logger = logging.getLogger("Notifier")


class Notifier:
    def __init__(self, bot: Bot, mqtt_mgr: MQTTManager):
        self.bot = bot
        self.mqtt = mqtt_mgr
        self.is_flagged_offline = False
        self._loop: asyncio.AbstractEventLoop | None = None

        # Registra i listener con il manager MQTT
        self.mqtt.add_info_listener(self._handle_info_event)
        self.mqtt.add_log_listener(self._handle_log_event)

    def set_event_loop(self, loop: asyncio.AbstractEventLoop) -> None:
        self._loop = loop

    async def broadcast(self, text: str) -> None:
        """Invia un messaggio push a tutti gli utenti Telegram autorizzati."""
        for user_id in ALLOWED_USER_IDS:
            try:
                await self.bot.send_message(
                    chat_id=user_id,
                    text=text,
                    parse_mode=ParseMode.MARKDOWN,
                )
            except Exception as e:
                logger.error(f"Impossibile inviare notifica all'utente {user_id}: {e}")

    def _safe_broadcast(self, text: str) -> None:
        """Helper thread-safe per inviare broadcast dall'event loop asincrono."""
        if self._loop and self._loop.is_running():
            asyncio.run_coroutine_threadsafe(self.broadcast(text), self._loop)
        else:
            logger.warning("Event loop non disponibile per inviare il messaggio push.")

    # -------------------------------------------------------------
    # Gestione Eventi MQTT (Info & Log)
    # -------------------------------------------------------------
    def _handle_info_event(self, data: dict[str, Any]) -> None:
        reason = data.get("reason", "")
        # Se era segnalato offline ed è arrivato un messaggio, torna online
        if self.is_flagged_offline:
            self.is_flagged_offline = False
            self._safe_broadcast("✅ *Centralina Irrigazione tornata ONLINE*\nComunicazione con l'ESP8266 ripristinata.")

        # Notifiche specifiche basate sugli eventi documentati in MQTT_COMMANDS.md
        if reason == "program_started":
            prog_id = data.get("runningProgramId", "N/D")
            self._safe_broadcast(f"🌱 *Irrigazione Avviata*\nIl programma `{prog_id}` è partito regolarmente.")

        elif reason == "program_test_started":
            prog_id = data.get("runningProgramId", "N/D")
            self._safe_broadcast(f"▶️ *Test Irrigazione Avviato*\nAvvio manuale del programma `{prog_id}`.")

        elif reason == "program_step":
            prog_id = data.get("runningProgramId", "N/D")
            step = data.get("runningStep", "?")
            self._safe_broadcast(f"💧 *Avanzamento Programma*\n`{prog_id}`: passaggio allo step/settore `{step}`.")

        elif reason == "program_completed":
            prog_id = data.get("runningProgramId", "N/D")
            self._safe_broadcast(f"✅ *Irrigazione Completata*\nIl programma `{prog_id}` è terminato con successo.")

        elif reason == "program_stopped":
            self._safe_broadcast("🛑 *Irrigazione Arrestata*\nLa sequenza attiva è stata interrotta.")

        elif reason == "rain_mode_updated":
            mode = data.get("rainMode", False)
            desc = "ATTIVATA 🔴 (Irrigazione sospesa)" if mode else "DISATTIVATA 🟢 (Impianto abilitato)"
            self._safe_broadcast(f"🌧️ *Modalità Pioggia:* {desc}")

        elif reason == "rain_mode_blocked":
            self._safe_broadcast("⚠️ *Avviso:* Tentativo di irrigazione respinto perché la *Modalità Pioggia* è attiva.")

        elif reason in ("connected", "reconnected"):
            self._safe_broadcast("🔌 *Centralina Riconnessa*\nL'ESP8266 si è riconnesso con successo ad HiveMQ Cloud.")

    def _handle_log_event(self, data: dict[str, Any]) -> None:
        if self.is_flagged_offline:
            self.is_flagged_offline = False
            self._safe_broadcast("✅ *Centralina Irrigazione tornata ONLINE*\nComunicazione ripristinata.")

        if "error" in data:
            err_msg = data["error"]
            self._safe_broadcast(f"⚠️ *Allarme Centralina (Errore)*\n`{err_msg}`")

    # -------------------------------------------------------------
    # Task Watchdog / Dead Man's Switch
    # -------------------------------------------------------------
    async def run_dead_man_switch(self) -> None:
        """Loop asincrono che verifica periodicamente se l'ESP8266 ha smesso di comunicare."""
        logger.info(f"Avvio Dead Man's Switch (timeout: {HEARTBEAT_TIMEOUT_MINUTES} minuti)...")
        timeout_seconds = HEARTBEAT_TIMEOUT_MINUTES * 60

        while True:
            await asyncio.sleep(60)  # Controlla ogni 60 secondi
            if self.mqtt.last_seen_timestamp == 0.0:
                # Nessun messaggio ancora ricevuto dall'avvio del bot
                continue

            elapsed = time.time() - self.mqtt.last_seen_timestamp
            if elapsed > timeout_seconds and not self.is_flagged_offline:
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
