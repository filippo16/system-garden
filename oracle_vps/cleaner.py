"""
Gestore della pulizia automatica dei messaggi Telegram.
Mantiene una coda persistente su file JSON per garantire la cancellazione
delle notifiche dopo 24 ore (anche a seguito di riavvio del bot o del container).
"""

import asyncio
import json
import logging
import os
import time
from typing import Any
from telegram import Bot
from telegram.error import BadRequest, TelegramError

logger = logging.getLogger("MessageCleaner")

DELETIONS_FILE = os.path.join(os.path.dirname(__file__), "pending_deletions.json")


class MessageCleaner:
    def __init__(self, bot: Bot | None = None):
        self.bot = bot
        self._loop_task: asyncio.Task | None = None
        self._is_running = False
        self._queue: list[dict[str, Any]] = []
        self._load_queue()

    def set_bot(self, bot: Bot) -> None:
        self.bot = bot

    def _load_queue(self) -> None:
        """Carica da disco la lista dei messaggi in attesa di cancellazione."""
        if not os.path.exists(DELETIONS_FILE):
            self._queue = []
            return

        try:
            with open(DELETIONS_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
                if isinstance(data, list):
                    self._queue = data
                else:
                    self._queue = []
            logger.info(f"Caricati {len(self._queue)} messaggi in attesa di auto-eliminazione.")
        except Exception as e:
            logger.warning(f"Impossibile leggere {DELETIONS_FILE}: {e}")
            self._queue = []

    def _save_queue(self) -> None:
        """Salva su disco la coda aggiornata dei messaggi pendenti."""
        try:
            tmp_file = DELETIONS_FILE + ".tmp"
            with open(tmp_file, "w", encoding="utf-8") as f:
                json.dump(self._queue, f, indent=2)
            os.replace(tmp_file, DELETIONS_FILE)
        except Exception as e:
            logger.error(f"Errore durante il salvataggio di {DELETIONS_FILE}: {e}")

    def schedule_deletion(self, chat_id: int, message_id: int, delay_seconds: int = 86400) -> None:
        """Pianifica l'eliminazione di un messaggio (default: 24 ore = 86400s)."""
        delete_at = time.time() + max(1, delay_seconds)
        # Evita duplicati nella coda
        for item in self._queue:
            if item.get("chat_id") == chat_id and item.get("message_id") == message_id:
                item["delete_at"] = delete_at
                self._save_queue()
                return

        self._queue.append({
            "chat_id": chat_id,
            "message_id": message_id,
            "delete_at": delete_at,
        })
        self._save_queue()
        logger.debug(f"Pianificata eliminazione msg {message_id} (chat {chat_id}) tra {delay_seconds}s.")

    async def delete_now(self, chat_id: int, message_id: int) -> bool:
        """Elimina immediatamente un messaggio Telegram."""
        if not self.bot:
            return False
        try:
            await self.bot.delete_message(chat_id=chat_id, message_id=message_id)
            # Rimuove dalla coda se presente
            self._queue = [
                q for q in self._queue
                if not (q.get("chat_id") == chat_id and q.get("message_id") == message_id)
            ]
            self._save_queue()
            return True
        except BadRequest as e:
            # Messaggio già rimosso dall'utente o non trovato
            if "not found" in str(e).lower() or "can't be deleted" in str(e).lower():
                self._queue = [
                    q for q in self._queue
                    if not (q.get("chat_id") == chat_id and q.get("message_id") == message_id)
                ]
                self._save_queue()
            else:
                logger.debug(f"Delete fallita msg {message_id}: {e}")
            return False
        except Exception as e:
            logger.warning(f"Errore inatteso delete_message {message_id}: {e}")
            return False

    async def start(self) -> None:
        """Avvia il task asincrono in background per il controllo periodico."""
        if self._is_running:
            return
        self._is_running = True
        self._loop_task = asyncio.create_task(self._run_cleanup_loop())
        logger.info("Task MessageCleaner avviato.")

    async def stop(self) -> None:
        """Arresta il task di pulizia."""
        self._is_running = False
        if self._loop_task:
            self._loop_task.cancel()
            try:
                await self._loop_task
            except asyncio.CancelledError:
                pass
        self._save_queue()
        logger.info("Task MessageCleaner fermato.")

    async def _run_cleanup_loop(self) -> None:
        """Ciclo periodico che controlla ed elimina i messaggi scaduti."""
        while self._is_running:
            try:
                now = time.time()
                pending_to_delete = [item for item in self._queue if item.get("delete_at", 0) <= now]
                remaining = [item for item in self._queue if item.get("delete_at", 0) > now]

                if pending_to_delete:
                    modified = False
                    for item in pending_to_delete:
                        chat_id = item["chat_id"]
                        message_id = item["message_id"]
                        if self.bot:
                            try:
                                await self.bot.delete_message(chat_id=chat_id, message_id=message_id)
                                logger.info(f"Auto-eliminato messaggio {message_id} da chat {chat_id} (scadenza 24h).")
                            except BadRequest as e:
                                logger.debug(f"Messaggio {message_id} non eliminabile (già cancellato): {e}")
                            except TelegramError as e:
                                logger.warning(f"Errore Telegram eliminazione msg {message_id}: {e}")
                        modified = True

                    self._queue = remaining
                    if modified:
                        self._save_queue()

            except Exception as e:
                logger.error(f"Errore nel ciclo cleanup messaggi: {e}")

            await asyncio.sleep(20)  # Controlla ogni 20 secondi
