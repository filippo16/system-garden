"""
Handler dei comandi e delle interfacce interattive Telegram.
Supporta navigazione in-place (nessun messaggio duplicato), feedback nativi
e aggiornamento automatico live del messaggio quando arrivano dati MQTT.
"""

import asyncio
import functools
import logging
from typing import Any, Callable, Optional
from telegram import Bot, InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.constants import ParseMode
from telegram.error import BadRequest
from telegram.ext import ContextTypes

from config import is_user_allowed, ALLOWED_USER_IDS
from mqtt_manager import MQTTManager
from cleaner import MessageCleaner

logger = logging.getLogger("BotHandlers")
DIVIDER = "───────────────────"  # 19 caratteri (esattamente 1 in meno per display mobile)


def authorized_only(func: Callable):
    """Decoratore per consentire l'esecuzione solo agli ID autorizzati."""
    @functools.wraps(func)
    async def wrapper(*args, **kwargs):
        update: Update | None = None
        for arg in args:
            if isinstance(arg, Update):
                update = arg
                break

        if not update:
            return await func(*args, **kwargs)

        user_id = update.effective_user.id if update.effective_user else None
        if user_id and is_user_allowed(user_id):
            return await func(*args, **kwargs)

        logger.warning(f"Accesso negato per utente non autorizzato ID: {user_id}")
        if update.callback_query:
            await update.callback_query.answer(
                "⛔ Non sei autorizzato a usare questa centralina.",
                show_alert=True,
            )
        elif update.message:
            msg_text = (
                "⛔ *Accesso Negato*\n"
                f"Il tuo Telegram ID (`{user_id}`) non è autorizzato a controllare questo impianto."
            )
            await update.message.reply_text(msg_text, parse_mode=ParseMode.MARKDOWN)
        return None

    return wrapper


class BotHandlers:
    def __init__(self, mqtt_mgr: MQTTManager, cleaner: MessageCleaner | None = None):
        self.mqtt = mqtt_mgr
        self.cleaner = cleaner
        self.bot: Bot | None = None
        self._loop: asyncio.AbstractEventLoop | None = None
        # Traccia le sessioni aperte: user_id -> {"chat_id": int, "message_id": int, "view": str}
        self.user_sessions: dict[int, dict[str, Any]] = {}

        # Iscrizione agli aggiornamenti MQTT info per auto-refresh
        self.mqtt.add_info_listener(self.on_mqtt_info)

    def set_bot(self, bot: Bot) -> None:
        self.bot = bot

    def set_cleaner(self, cleaner: MessageCleaner) -> None:
        self.cleaner = cleaner

    def set_event_loop(self, loop: asyncio.AbstractEventLoop) -> None:
        self._loop = loop

    def _schedule_auto_delete(self, chat_id: int, message_id: int, delay_seconds: int = 86400) -> None:
        if self.cleaner:
            self.cleaner.schedule_deletion(chat_id, message_id, delay_seconds)

    @staticmethod
    def _format_esp_time(raw_ts: str) -> str:
        if not raw_ts or raw_ts == "unsynced":
            return "Non sincronizzato"
        if "T" in raw_ts:
            try:
                date_part, time_part = raw_ts.split("T")
                y, m, d = date_part.split("-")
                return f"{time_part} ({d}/{m}/{y})"
            except Exception:
                return raw_ts
        return raw_ts


    # -------------------------------------------------------------
    # Auto-Refresh quando arriva un nuovo pacchetto INFO dall'ESP8266
    # -------------------------------------------------------------
    def on_mqtt_info(self, data: dict[str, Any]) -> None:
        """Callback eseguita dal thread MQTT quando l'ESP invia lo stato aggiornato."""
        if not self.bot or not self._loop or not self._loop.is_running():
            return
        # Schedula l'aggiornamento automatico su tutti i menu aperti
        asyncio.run_coroutine_threadsafe(self._refresh_all_active_views(), self._loop)

    async def _refresh_all_active_views(self) -> None:
        """Aggiorna in tempo reale i messaggi attivi degli utenti senza scriverne di nuovi."""
        if not self.bot:
            return

        for user_id, session in list(self.user_sessions.items()):
            chat_id = session.get("chat_id")
            message_id = session.get("message_id")
            view = session.get("view", "main")

            if not chat_id or not message_id:
                continue

            text, reply_markup = self._get_view_content(view)
            try:
                await self.bot.edit_message_text(
                    chat_id=chat_id,
                    message_id=message_id,
                    text=text,
                    parse_mode=ParseMode.MARKDOWN,
                    reply_markup=reply_markup,
                )
            except BadRequest as e:
                # Se il messaggio non è cambiato, Telegram genera questo errore innocuo
                if "Message is not modified" in str(e):
                    pass
                elif "Message to edit not found" in str(e):
                    # Il messaggio è stato cancellato dall'utente
                    self.user_sessions.pop(user_id, None)
                else:
                    logger.debug(f"Auto-refresh saltato per utente {user_id}: {e}")
            except Exception as e:
                logger.debug(f"Errore durante auto-refresh: {e}")

    # -------------------------------------------------------------
    # Generatori di Viste (Testo + Tastiera)
    # -------------------------------------------------------------
    def _get_view_content(self, view: str) -> tuple[str, InlineKeyboardMarkup]:
        if view == "sectors":
            return self._format_sectors_text(), self._build_sectors_keyboard()
        elif view == "status":
            return self._format_status_text(), self._build_status_keyboard()
        elif view == "programs":
            return self._format_programs_text(), self._build_programs_keyboard()
        else:
            return self._format_main_text(), self._build_main_keyboard()

    # --- Vista 1: MAIN MENU ---
    def _format_main_text(self) -> str:
        state = self.mqtt.get_state()
        if not state:
            return (
                "🌿 *CENTRALINA IRRIGAZIONE*\n"
                f"{DIVIDER}\n"
                "⏳ _In attesa dei dati dall'ESP8266..._\n"
                "Premi *Aggiorna* tra pochi istanti."
            )

        esp_time = self._format_esp_time(state.get("time", ""))
        rain_mode = state.get("rainMode", False)
        seq_running = state.get("sequenceRunning", False)
        relays = state.get("relays", [False] * 8)
        sector_names = state.get("sectorNames", [f"Settore {i+1}" for i in range(8)])

        # Banner stato
        if seq_running:
            prog_id = state.get("runningProgramId", "N/D")
            step = state.get("runningStep", "?")
            header_status = f"🚿 *IN CORSO:* `{prog_id}` (Step {step})"
        elif rain_mode:
            header_status = "🌧️ *MODALITÀ PIOGGIA ATTIVA 🔴*\n_Irrigazioni automatiche e manuali sospese._"
        else:
            header_status = "🟢 *STATO: STANDBY (Pronto)*"

        # Elenco valvole aperte
        active_sectors = []
        for i, is_on in enumerate(relays):
            if is_on:
                name = sector_names[i] if i < len(sector_names) else f"Settore {i+1}"
                active_sectors.append(f"• *S{i+1}:* {name}")

        if active_sectors:
            active_str = "\n".join(active_sectors)
        elif seq_running:
            active_str = "• _Pausa transizione tra settori (3s)..._"
        else:
            active_str = "• _Nessun settore attivo al momento._"

        return (
            "🌿 *PANNELLO IRRIGAZIONE*\n"
            f"{DIVIDER}\n"
            f"{header_status}\n"
            f"{DIVIDER}\n"
            f"💧 *Valvole Aperte:*\n{active_str}\n"
            f"{DIVIDER}\n"
            f"🕒 *Orario ESP:* `{esp_time}`"
        )

    def _build_main_keyboard(self) -> InlineKeyboardMarkup:
        state = self.mqtt.get_state()
        rain_mode = state.get("rainMode", False) if state else False
        rain_btn_text = "🌧️ Pioggia: ON 🔴" if rain_mode else "🌧️ Pioggia: OFF 🟢"

        keyboard = [
            [
                InlineKeyboardButton("🚰 Settori", callback_data="view_sectors"),
                InlineKeyboardButton("📊 Dettagli", callback_data="view_status"),
            ],
            [
                InlineKeyboardButton("▶️ Programmi", callback_data="view_programs"),
                InlineKeyboardButton(rain_btn_text, callback_data="act_toggle_rain"),
            ],
            [
                InlineKeyboardButton("⏹️ STOP", callback_data="act_stop"),
                InlineKeyboardButton("🔄 Aggiorna", callback_data="act_refresh"),
            ],
        ]
        return InlineKeyboardMarkup(keyboard)

    # --- Vista 2: CONTROLLO SETTORI ---
    def _format_sectors_text(self) -> str:
        state = self.mqtt.get_state()
        relays = state.get("relays", [False] * 8) if state else [False] * 8
        rain_mode = state.get("rainMode", False) if state else False
        any_on = any(relays)

        if rain_mode:
            state_summary = (
                "🌧️ *MODALITÀ PIOGGIA ATTIVA 🔴*\n"
                "_Comandi bloccati. Disattiva la pioggia per irrigare._"
            )
        elif any_on:
            state_summary = "🟢 *IRRIGAZIONE IN CORSO*"
        else:
            state_summary = "⚫ *Tutti i settori sono spenti*"

        return (
            "🚰 *CONTROLLO SETTORI*\n"
            f"{DIVIDER}\n"
            f"{state_summary}\n"
            f"{DIVIDER}\n"
            "Tocca per accendere (🟢) o spegnere (⚫):"
        )

    def _build_sectors_keyboard(self) -> InlineKeyboardMarkup:
        state = self.mqtt.get_state()
        relays = state.get("relays", [False] * 8) if state else [False] * 8
        rain_mode = state.get("rainMode", False) if state else False
        sector_names = state.get("sectorNames", [f"Settore {i+1}" for i in range(8)]) if state else [f"Settore {i+1}" for i in range(8)]

        keyboard = []
        for i in range(0, 8, 2):
            row = []
            for j in (i, i + 1):
                if j < 8:
                    name = sector_names[j]
                    is_on = relays[j]
                    short_name = name[:10] + ".." if len(name) > 12 else name
                    icon = "🟢" if is_on else "⚫"
                    label = f"{icon} S{j+1}: {short_name}"
                    row.append(InlineKeyboardButton(label, callback_data=f"act_toggle_relay_{j+1}"))
            keyboard.append(row)

        if rain_mode:
            keyboard.append([
                InlineKeyboardButton("🌧️ Disattiva Pioggia 🟢", callback_data="act_toggle_rain")
            ])

        keyboard.append([
            InlineKeyboardButton("⏹️ Spegni Tutto", callback_data="act_stop"),
            InlineKeyboardButton("🔙 Indietro", callback_data="view_main"),
        ])
        return InlineKeyboardMarkup(keyboard)


    # --- Vista 3: STATO COMPLETO ---
    def _format_status_text(self) -> str:
        state = self.mqtt.get_state()
        if not state:
            return "⚠️ *Nessun dato ancora disponibile.*"

        esp_time = self._format_esp_time(state.get("time", ""))
        rain_mode = state.get("rainMode", False)
        seq_running = state.get("sequenceRunning", False)
        relays = state.get("relays", [False] * 8)
        sector_names = state.get("sectorNames", [f"Settore {i+1}" for i in range(8)])

        rain_str = "Attiva 🔴" if rain_mode else "Disattiva 🟢"
        seq_str = "In corso 🚿" if seq_running else "Standby ⏸️"

        lines = [
            "📊 *DETTAGLI CENTRALE*",
            DIVIDER,
            f"🕒 *Orario:* `{esp_time}`",
            f"🌧️ *Pioggia:* {rain_str}",
            f"⚡ *Stato:* {seq_str}",
            DIVIDER,
            "📋 *Elettrovalvole:*",
        ]
        for i in range(8):
            name = sector_names[i] if i < len(sector_names) else f"Settore {i+1}"
            is_on = relays[i] if i < len(relays) else False
            icon = "🟢" if is_on else "⚫"
            lines.append(f"{icon} *S{i+1}:* {name}")

        programs = state.get("programs", [])
        if programs:
            lines.append(DIVIDER)
            lines.append("📅 *Programmi configurati:*")
            for p in programs:
                pid = p.get("id", "programma")
                h = p.get("startHour", 0)
                m = p.get("startMinute", 0)
                enabled = p.get("enabled", True)
                status_icon = "🟢" if enabled else "⏸️"
                days_label = self._format_program_days(p.get("days"))
                lines.append(f"• {status_icon} *{pid}* `{h:02d}:{m:02d}` ({days_label})")

        return "\n".join(lines)

    def _build_status_keyboard(self) -> InlineKeyboardMarkup:
        return InlineKeyboardMarkup([
            [
                InlineKeyboardButton("🔄 Aggiorna", callback_data="act_refresh"),
                InlineKeyboardButton("🔙 Indietro", callback_data="view_main"),
            ]
        ])

    @staticmethod
    def _format_program_days(days: Optional[list]) -> str:
        if not days:
            return "Tutti i giorni"
        # Supporta convenzione ISO (1=Lun .. 7=Dom) e C (0=Dom)
        normalized = sorted(set(7 if d == 0 else d for d in days if 0 <= d <= 7))
        if len(normalized) == 7:
            return "Tutti i giorni"
        if normalized == [1, 2, 3, 4, 5]:
            return "Lun-Ven (feriali)"
        if normalized == [6, 7]:
            return "Sab-Dom (weekend)"
        day_names = {1: "Lun", 2: "Mar", 3: "Mer", 4: "Gio", 5: "Ven", 6: "Sab", 7: "Dom"}
        return ", ".join(day_names[d] for d in normalized if d in day_names)

    # --- Vista 4: AVVIO PROGRAMMI DI TEST ---
    def _format_programs_text(self) -> str:
        state = self.mqtt.get_state()
        programs = state.get("programs", []) if state else []

        lines = [
            "▶️ *PROGRAMMI IRRIGAZIONE*",
            DIVIDER,
        ]

        if not programs:
            lines.append("_(Nessun programma configurato)_")
        else:
            lines.append("📋 *Programmi in memoria:*")
            for p in programs:
                pid = p.get("id", "programma")
                h = p.get("startHour", 0)
                m = p.get("startMinute", 0)
                enabled = p.get("enabled", True)
                status_icon = "🟢" if enabled else "⏸️"
                days_label = self._format_program_days(p.get("days"))
                lines.append(f"• {status_icon} *{pid}*: `{h:02d}:{m:02d}` | 📅 _{days_label}_")

            lines.append("")
            lines.append("Tocca per avviare il test manuale:")
            lines.append("_(Interrompe eventuali sequenze attive)_")

        return "\n".join(lines)

    def _build_programs_keyboard(self) -> InlineKeyboardMarkup:
        state = self.mqtt.get_state()
        programs = state.get("programs", []) if state else []

        keyboard = []
        if not programs:
            keyboard.append([InlineKeyboardButton("(Nessun programma configurato)", callback_data="noop")])
        else:
            for p in programs:
                pid = p.get("id", "programma")
                h = p.get("startHour", 0)
                m = p.get("startMinute", 0)
                enabled = p.get("enabled", True)
                icon = "▶️" if enabled else "⏸️"
                label = f"{icon} {pid} ({h:02d}:{m:02d})"
                keyboard.append([InlineKeyboardButton(label, callback_data=f"act_start_prog_{pid}")])

        keyboard.append([
            InlineKeyboardButton("🔙 Menu Principale", callback_data="view_main"),
        ])
        return InlineKeyboardMarkup(keyboard)

    # -------------------------------------------------------------
    # Helper di Aggiornamento Messaggio In-Place
    # -------------------------------------------------------------
    async def _render_view(self, user_id: int, chat_id: int, message_id: int, view: str) -> None:
        self.user_sessions[user_id] = {"chat_id": chat_id, "message_id": message_id, "view": view}
        text, reply_markup = self._get_view_content(view)
        if not self.bot:
            return

        try:
            await self.bot.edit_message_text(
                chat_id=chat_id,
                message_id=message_id,
                text=text,
                parse_mode=ParseMode.MARKDOWN,
                reply_markup=reply_markup,
            )
        except BadRequest as e:
            if "Message is not modified" in str(e):
                pass
            else:
                logger.warning(f"Errore render view: {e}")

    # -------------------------------------------------------------
    # Comandi Testuali (/start, /menu, /stato, /stop, /pioggia)
    # -------------------------------------------------------------
    @authorized_only
    async def cmd_start(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Invia un unico pannello di controllo interattivo, eliminando vecchie dashboard e comandi utente."""
        self.mqtt.request_info()
        user_id = update.effective_user.id
        chat_id = update.effective_chat.id

        # 1. Elimina il comando /start o /menu digitato dall'utente
        if update.message:
            try:
                await update.message.delete()
            except Exception:
                pass

        # 2. Elimina la vecchia dashboard per assicurare che la nuova sia l'ultimo messaggio assoluto
        if user_id in self.user_sessions:
            old_session = self.user_sessions.pop(user_id, None)
            if old_session and self.bot:
                try:
                    await self.bot.delete_message(chat_id=old_session["chat_id"], message_id=old_session["message_id"])
                except Exception:
                    pass

        # 3. Invia la nuova dashboard attiva
        text, reply_markup = self._get_view_content("main")
        if self.bot:
            msg = await self.bot.send_message(
                chat_id=chat_id,
                text=text,
                parse_mode=ParseMode.MARKDOWN,
                reply_markup=reply_markup,
            )
            self.user_sessions[user_id] = {"chat_id": chat_id, "message_id": msg.message_id, "view": "main"}

    @authorized_only
    async def cmd_stato(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Reindirizza al menu di stato aggiornando la dashboard in-place ed eliminando il comando."""
        self.mqtt.request_info()
        user_id = update.effective_user.id
        chat_id = update.effective_chat.id

        if update.message:
            try:
                await update.message.delete()
            except Exception:
                pass

        if user_id in self.user_sessions:
            s = self.user_sessions[user_id]
            await self._render_view(user_id, s["chat_id"], s["message_id"], "status")
        else:
            text, reply_markup = self._get_view_content("status")
            if self.bot:
                msg = await self.bot.send_message(chat_id=chat_id, text=text, parse_mode=ParseMode.MARKDOWN, reply_markup=reply_markup)
                self.user_sessions[user_id] = {"chat_id": chat_id, "message_id": msg.message_id, "view": "status"}

    @authorized_only
    async def cmd_stop(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Esegue stop, aggiorna il menu e auto-elimina l'eventuale messaggio di conferma."""
        self.mqtt.stop_program()
        if self.mqtt.last_info_data:
            self.mqtt.last_info_data["sequenceRunning"] = False
            self.mqtt.last_info_data["relays"] = [False] * 8

        user_id = update.effective_user.id
        chat_id = update.effective_chat.id

        if update.message:
            try:
                await update.message.delete()
            except Exception:
                pass

        if user_id in self.user_sessions:
            s = self.user_sessions[user_id]
            await self._render_view(user_id, s["chat_id"], s["message_id"], s.get("view", "main"))
            if self.bot:
                msg = await self.bot.send_message(chat_id=chat_id, text="🛑 *STOP eseguito!* Pannello aggiornato.", parse_mode=ParseMode.MARKDOWN)
                self._schedule_auto_delete(chat_id, msg.message_id, delay_seconds=10)
        else:
            if self.bot:
                msg = await self.bot.send_message(chat_id=chat_id, text="🛑 *STOP Emergenza inviato alla centralina!*", parse_mode=ParseMode.MARKDOWN)
                self._schedule_auto_delete(chat_id, msg.message_id, delay_seconds=10)

    @authorized_only
    async def cmd_pioggia(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Inverte la modalità pioggia, aggiorna la dashboard ed elimina il testo digitato."""
        state = self.mqtt.get_state()
        new_mode = not state.get("rainMode", False) if state else True
        self.mqtt.set_rain_mode(new_mode)
        if self.mqtt.last_info_data:
            self.mqtt.last_info_data["rainMode"] = new_mode

        user_id = update.effective_user.id
        chat_id = update.effective_chat.id

        if update.message:
            try:
                await update.message.delete()
            except Exception:
                pass

        desc = "ATTIVATA 🔴" if new_mode else "DISATTIVATA 🟢"
        if user_id in self.user_sessions:
            s = self.user_sessions[user_id]
            await self._render_view(user_id, s["chat_id"], s["message_id"], s.get("view", "main"))
            if self.bot:
                msg = await self.bot.send_message(chat_id=chat_id, text=f"🌧️ *Modalità Pioggia {desc}!*", parse_mode=ParseMode.MARKDOWN)
                self._schedule_auto_delete(chat_id, msg.message_id, delay_seconds=10)
        else:
            if self.bot:
                msg = await self.bot.send_message(chat_id=chat_id, text=f"🌧️ *Modalità Pioggia {desc}*", parse_mode=ParseMode.MARKDOWN)
                self._schedule_auto_delete(chat_id, msg.message_id, delay_seconds=10)

    @authorized_only
    async def cmd_help(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Invia la guida rapida e la programma per l'auto-eliminazione a 60 secondi."""
        chat_id = update.effective_chat.id
        if update.message:
            try:
                await update.message.delete()
            except Exception:
                pass

        help_text = (
            "📖 *Guida Rapida Centralina Irrigazione*\n\n"
            "• Invia `/menu` per aprire il pannello di controllo interattivo.\n"
            "• Tutti i comandi si gestiscono premendo i pulsanti direttamente nel messaggio.\n"
            "• Lo stato dei settori si aggiorna in tempo reale senza intasare la chat.\n"
            "• In caso di necessità puoi sempre inviare `/stop` per arrestare tutto all'istante.\n\n"
            "_(Questo messaggio di aiuto si auto-eliminerà a breve per mantenere la chat pulita)_"
        )
        if self.bot:
            msg = await self.bot.send_message(chat_id=chat_id, text=help_text, parse_mode=ParseMode.MARKDOWN)
            self._schedule_auto_delete(chat_id, msg.message_id, delay_seconds=60)

    # -------------------------------------------------------------
    # Gestione Callback Query (Click sui bottoni inline)
    # -------------------------------------------------------------
    @authorized_only
    async def handle_callback_query(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        query = update.callback_query
        if not query or not query.data:
            return

        data = query.data
        user_id = update.effective_user.id
        chat_id = query.message.chat_id
        message_id = query.message.message_id

        # Aggiorna sessione attiva
        session = self.user_sessions.get(user_id, {})
        current_view = session.get("view", "main")

        if data == "noop":
            await query.answer()
            return

        # --- CAMBIO VISTA (In-place) ---
        elif data.startswith("view_"):
            target_view = data.replace("view_", "")
            self.mqtt.request_info()
            await query.answer()  # Nessun popup fastidioso
            await self._render_view(user_id, chat_id, message_id, target_view)

        # --- AZIONE: REFRESH ---
        elif data == "act_refresh":
            self.mqtt.request_info()
            await query.answer()
            await self._render_view(user_id, chat_id, message_id, current_view)


        # --- AZIONE: TOGGLE RELÈ ---
        elif data.startswith("act_toggle_relay_"):
            relay_num = int(data.replace("act_toggle_relay_", ""))
            state = self.mqtt.get_state()
            rain_mode = state.get("rainMode", False) if state else False
            relays = list(state.get("relays", [False] * 8)) if state else [False] * 8
            current_is_on = relays[relay_num - 1] if 1 <= relay_num <= 8 else False
            new_state = not current_is_on

            # Se si cerca di accendere un settore mentre la pioggia è attiva -> POPUP MODALE!
            if new_state and rain_mode:
                await query.answer(
                    "🌧️ BLOCCO MODALITÀ PIOGGIA!\n\n"
                    "Non puoi accendere i settori mentre la Modalità Pioggia è attiva.\n"
                    "Disattivala dal pulsante sottostante per poter irrigare.",
                    show_alert=True,
                )
                return

            # Aggiornamento ottimistico immediato in memoria
            relays[relay_num - 1] = new_state
            if self.mqtt.last_info_data:
                self.mqtt.last_info_data["relays"] = relays

            # Invio comando all'ESP
            self.mqtt.set_relay(relay_num, new_state)

            # Feedback nativo elegante in alto allo schermo (toast)
            status_word = "ACCESO 🟢" if new_state else "SPENTO ⚫"
            await query.answer(f"Settore {relay_num} {status_word}", show_alert=False)

            # Ridisegna istantaneamente il menu settori con il nuovo stato del pulsante
            await self._render_view(user_id, chat_id, message_id, "sectors")

        # --- AZIONE: TOGGLE RAIN MODE ---
        elif data == "act_toggle_rain":
            state = self.mqtt.get_state()
            cur_mode = state.get("rainMode", False) if state else False
            new_mode = not cur_mode

            # Aggiornamento ottimistico
            if self.mqtt.last_info_data:
                self.mqtt.last_info_data["rainMode"] = new_mode

            self.mqtt.set_rain_mode(new_mode)
            desc = "ATTIVATA 🔴" if new_mode else "DISATTIVATA 🟢"
            await query.answer(f"Modalità Pioggia {desc}", show_alert=False)

            # Ridisegna la vista corrente (se era nei settori rimane nei settori, se era in main rimane in main)
            await self._render_view(user_id, chat_id, message_id, current_view)

        # --- AZIONE: STOP EMERGENZA ---
        elif data == "act_stop":
            self.mqtt.stop_program()

            # Aggiornamento ottimistico
            if self.mqtt.last_info_data:
                self.mqtt.last_info_data["sequenceRunning"] = False
                self.mqtt.last_info_data["relays"] = [False] * 8

            await query.answer("🛑 STOP inviato: impianto arrestato!", show_alert=False)
            await self._render_view(user_id, chat_id, message_id, current_view)

        # --- AZIONE: AVVIO PROGRAMMA TEST ---
        elif data.startswith("act_start_prog_"):
            state = self.mqtt.get_state()
            rain_mode = state.get("rainMode", False) if state else False
            if rain_mode:
                await query.answer(
                    "🌧️ BLOCCO MODALITÀ PIOGGIA!\n\n"
                    "Impossibile avviare programmi mentre la Modalità Pioggia è attiva.\n"
                    "Disattivala prima di procedere.",
                    show_alert=True,
                )
                return

            prog_id = data.replace("act_start_prog_", "")
            self.mqtt.start_program(prog_id)

            await query.answer(f"▶️ Avvio programma '{prog_id}' inviato!", show_alert=False)
            # Ritorna al menu principale dove vedrà lo stato di irrigazione in corso
            await self._render_view(user_id, chat_id, message_id, "main")

