"""
Sistema di notifiche push proattive su Telegram per eventi critici.
Rilevamento disconnessioni basato al 100% su MQTT Last Will and Testament (LWT).
"""

import asyncio
import logging
from typing import Any
from telegram import Bot
from telegram.constants import ParseMode

from config import ALLOWED_USER_IDS
from mqtt_manager import MQTTManager
from cleaner import MessageCleaner

logger = logging.getLogger("Notifier")


class Notifier:
    def __init__(self, bot: Bot, mqtt_mgr: MQTTManager, cleaner: MessageCleaner | None = None):
        self.bot = bot
        self.mqtt = mqtt_mgr
        self.cleaner = cleaner
        self.is_flagged_offline = False
        self._loop: asyncio.AbstractEventLoop | None = None

        # Registra i listener con il manager MQTT
        self.mqtt.add_info_listener(self._handle_info_event)
        self.mqtt.add_log_listener(self._handle_log_event)

    def set_event_loop(self, loop: asyncio.AbstractEventLoop) -> None:
        self._loop = loop

    def set_cleaner(self, cleaner: MessageCleaner) -> None:
        self.cleaner = cleaner

    async def broadcast(self, text: str) -> None:
        """Invia un messaggio push a tutti gli utenti Telegram autorizzati con auto-cancellazione a 24h."""
        for user_id in ALLOWED_USER_IDS:
            try:
                msg = await self.bot.send_message(
                    chat_id=user_id,
                    text=text,
                    parse_mode=ParseMode.MARKDOWN,
                )
                if self.cleaner and msg:
                    self.cleaner.schedule_deletion(user_id, msg.message_id, delay_seconds=86400)
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

        # Se la centralina era segnalata offline ed è arrivato un messaggio, è tornata online
        if self.is_flagged_offline:
            self.is_flagged_offline = False
            self._safe_broadcast("✅ *Centralina Irrigazione tornata ONLINE*\nComunicazione con l'ESP8266 ripristinata.")

        # Notifiche push essenziali (avvio automatico schedulato, fine ciclo e blocchi)
        if reason == "program_started":
            prog_id = data.get("runningProgramId", "N/D")
            self._safe_broadcast(f"🌱 *Irrigazione Automatica Avviata*\nIl programma schedulato `{prog_id}` è partito.")

        elif reason == "program_completed":
            prog_id = data.get("runningProgramId", "N/D")
            self._safe_broadcast(f"✅ *Irrigazione Completata*\nIl programma `{prog_id}` è terminato con successo.")

        elif reason == "rain_mode_blocked":
            self._safe_broadcast("⚠️ *Avviso:* Comando irrigazione bloccato perché la *Modalità Pioggia* è attiva.")

    def _handle_log_event(self, data: dict[str, Any]) -> None:
        if "error" in data:
            err_msg = data["error"]

            # Ignora blocchi della modalità pioggia (già gestiti da popup ed eventi specifici)
            if "Rain mode active" in err_msg or "rain_mode" in err_msg.lower():
                return

            # Rilevamento immediato disconnessione via MQTT Last Will and Testament (LWT)
            if "LWT" in err_msg or "disconnected unexpectedly" in err_msg:
                self.is_flagged_offline = True
                logger.warning("Rilevato evento MQTT LWT: ESP8266 disconnesso!")
                self._safe_broadcast(
                    "⚠️ *ALLARME: Centralina Irrigazione Disconnessa!*\n\n"
                    "L'ESP8266 ha perso improvvisamente la connessione con il broker HiveMQ.\n"
                    "Possibili cause:\n"
                    "• Interruzione elettrica o blackout\n"
                    "• Wi-Fi domestico o internet assente\n"
                    "• Riavvio del router"
                )
                return

            # Altri errori generici dall'ESP8266
            self._safe_broadcast(f"⚠️ *Allarme Centralina (Errore)*\n`{err_msg}`")
