"""
Handler dei comandi e delle interfacce interattive Telegram.
Supporta navigazione in-place (nessun messaggio duplicato), feedback nativi
e aggiornamento automatico live del messaggio quando arrivano dati MQTT.
"""

import asyncio
import functools
import logging
from typing import Any, Callable
from telegram import Bot, InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.constants import ParseMode
from telegram.error import BadRequest
from telegram.ext import ContextTypes

from config import is_user_allowed, ALLOWED_USER_IDS
from mqtt_manager import MQTTManager

logger = logging.getLogger("BotHandlers")


def authorized_only(func: Callable):
    """Decoratore per consentire l'esecuzione solo agli ID autorizzati."""
    @functools.wraps(func)
    async def wrapper(*args, **kwargs):
        update: Update | None = None
        for arg in args:
            if isinstance(arg, Update):
                update = arg
                break
        if update is None:
            update = kwargs.get("update")

        if not update:
            return await func(*args, **kwargs)

        user = update.effective_user
        if not user or not is_user_allowed(user.id):
            uid = user.id if user else "Sconosciuto"
            uname = user.username if user and user.username else "N/A"
            logger.warning(f"Tentativo di accesso NON autorizzato: ID={uid}, username=@{uname}")

            msg_text = (
                "⛔ *Accesso Negato*\n\n"
                f"Il tuo account Telegram (ID: `{uid}`) non è autorizzato ad utilizzare questa centralina.\n"
                "Aggiungi questo ID a `ALLOWED_USER_IDS` nel file `.env` della VPS per abilitarlo."
            )
            if update.callback_query:
                await update.callback_query.answer("⛔ Accesso non autorizzato.", show_alert=True)
                try:
                    await update.callback_query.edit_message_text(msg_text, parse_mode=ParseMode.MARKDOWN)
                except Exception:
                    pass
            elif update.message:
                await update.message.reply_text(msg_text, parse_mode=ParseMode.MARKDOWN)
            return

        return await func(*args, **kwargs)
    return wrapper


class BotHandlers:
    def __init__(self, mqtt_mgr: MQTTManager):
        self.mqtt = mqtt_mgr
        self.bot: Bot | None = None
        self._loop: asyncio.AbstractEventLoop | None = None
        # Traccia le sessioni aperte: user_id -> {"chat_id": int, "message_id": int, "view": str}
        self.user_sessions: dict[int, dict[str, Any]] = {}

        # Iscrizione agli aggiornamenti MQTT info per auto-refresh
        self.mqtt.add_info_listener(self.on_mqtt_info)

    def set_bot(self, bot: Bot) -> None:
        self.bot = bot

    def set_event_loop(self, loop: asyncio.AbstractEventLoop) -> None:
        self._loop = loop

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
                "━━━━━━━━━━━━━━━━━━━━━━\n"
                "⏳ _In attesa dei dati dall'ESP8266..._\n"
                "Premi 'Aggiorna' se la centralina è appena stata accesa."
            )

        esp_time = state.get("time", "N/D")
        rain_mode = state.get("rainMode", False)
        seq_running = state.get("sequenceRunning", False)
        relays = state.get("relays", [False] * 8)
        sector_names = state.get("sectorNames", [f"Settore {i+1}" for i in range(8)])

        # Banner stato rapido
        if seq_running:
            prog_id = state.get("runningProgramId", "N/D")
            step = state.get("runningStep", "?")
            header_status = f"🚿 *IRRIGAZIONE IN CORSO*\n• Programma: `{prog_id}`\n• Step/Settore: `{step}`"
        elif rain_mode:
            header_status = "🌧️ *MODALITÀ PIOGGIA ATTIVA*\n_Tutte le irrigazioni sono temporaneamente sospese._"
        else:
            header_status = "🟢 *SISTEMA PRONTO / STANDBY*"

        # Elenco relè attualmente accesi
        active_sectors = []
        for i, is_on in enumerate(relays):
            if is_on:
                name = sector_names[i] if i < len(sector_names) else f"Settore {i+1}"
                active_sectors.append(f"• S{i+1}: *{name}*")

        active_str = "\n".join(active_sectors) if active_sectors else "• _Nessun settore attivo al momento._"

        return (
            "🌿 *PANNELLO DI CONTROLLO IRRIGAZIONE*\n"
            "━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
            f"{header_status}\n\n"
            f"💧 *Settori Attivi (Elettrovalvole Aperte):*\n{active_str}\n\n"
            f"🕒 *Orario ESP:* `{esp_time}`"
        )

    def _build_main_keyboard(self) -> InlineKeyboardMarkup:
        state = self.mqtt.get_state()
        rain_mode = state.get("rainMode", False) if state else False
        rain_btn_text = "🌧️ Pioggia: ATTIVA 🔴" if rain_mode else "🌧️ Pioggia: DISATTIVA 🟢"

        keyboard = [
            [
                InlineKeyboardButton("🚰 Controllo Settori", callback_data="view_sectors"),
                InlineKeyboardButton("📊 Dettagli Stato", callback_data="view_status"),
            ],
            [
                InlineKeyboardButton("▶️ Avvia Programma", callback_data="view_programs"),
                InlineKeyboardButton(rain_btn_text, callback_data="act_toggle_rain"),
            ],
            [
                InlineKeyboardButton("⏹️ STOP Emergenza", callback_data="act_stop"),
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
                "🌧️ *ATTENZIONE: MODALITÀ PIOGGIA ATTIVA! 🔴*\n"
                "_L'accensione dei settori è bloccata. Disattiva la pioggia per poter irrigare._"
            )
        elif any_on:
            state_summary = "🟢 _Ci sono settori attivi!_"
        else:
            state_summary = "⚫ _Tutti i settori sono spenti._"

        return (
            "🚰 *CONTROLLO MANUALE SETTORI*\n"
            "━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
            f"{state_summary}\n\n"
            "Tocca un pulsante per **accendere** (🟢) o **spegnere** (⚫) il settore corrispondente:\n"
            "_(I pulsanti si aggiornano istantaneamente)_"
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
                    short_name = name[:13] + ".." if len(name) > 15 else name
                    icon = "🟢 ON" if is_on else "⚫ OFF"
                    label = f"{icon} S{j+1}: {short_name}"
                    row.append(InlineKeyboardButton(label, callback_data=f"act_toggle_relay_{j+1}"))
            keyboard.append(row)

        bottom_row = []
        if rain_mode:
            keyboard.append([
                InlineKeyboardButton("🌧️ Disattiva Modalità Pioggia 🟢", callback_data="act_toggle_rain")
            ])

        keyboard.append([
            InlineKeyboardButton("⏹️ Spegni Tutto", callback_data="act_stop"),
            InlineKeyboardButton("🔙 Menu Principale", callback_data="view_main"),
        ])
        return InlineKeyboardMarkup(keyboard)


    # --- Vista 3: STATO COMPLETO ---
    def _format_status_text(self) -> str:
        state = self.mqtt.get_state()
        if not state:
            return "⚠️ *Nessun dato ancora disponibile.*"

        esp_time = state.get("time", "Non sincronizzato")
        rain_mode = state.get("rainMode", False)
        seq_running = state.get("sequenceRunning", False)
        relays = state.get("relays", [False] * 8)
        sector_names = state.get("sectorNames", [f"Settore {i+1}" for i in range(8)])

        rain_str = "🔴 *ATTIVO (Impianto Bloccato)*" if rain_mode else "🟢 Disattivo (Normale)"
        seq_str = "🚿 In corso" if seq_running else "⏸️ In standby"

        lines = [
            "📊 *DETTAGLI STATO CENTRALINA*",
            "━━━━━━━━━━━━━━━━━━━━━━━━━━",
            f"🕒 *Orario ESP:* `{esp_time}`",
            f"🌧️ *Modalità Pioggia:* {rain_str}",
            f"⚡ *Esecuzione:* {seq_str}",
            "",
            "📋 *Riepilogo 8 Settori:*",
        ]
        for i in range(8):
            name = sector_names[i] if i < len(sector_names) else f"Settore {i+1}"
            is_on = relays[i] if i < len(relays) else False
            icon = "🟢 ACCESO" if is_on else "⚫ Spento"
            lines.append(f"  {i+1}. *{name}*: {icon}")

        return "\n".join(lines)

    def _build_status_keyboard(self) -> InlineKeyboardMarkup:
        return InlineKeyboardMarkup([
            [
                InlineKeyboardButton("🔄 Aggiorna", callback_data="act_refresh"),
                InlineKeyboardButton("🔙 Menu Principale", callback_data="view_main"),
            ]
        ])

    # --- Vista 4: AVVIO PROGRAMMI DI TEST ---
    def _format_programs_text(self) -> str:
        return (
            "▶️ *AVVIO PROGRAMMI PER TEST*\n"
            "━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
            "Seleziona un programma per farlo partire immediatamente:\n"
            "_(Se un altro programma è attivo, verrà interrotto e sostituito)_"
        )

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
        """Invia un unico pannello di controllo interattivo."""
        self.mqtt.request_info()
        user_id = update.effective_user.id
        chat_id = update.effective_chat.id

        text, reply_markup = self._get_view_content("main")
        msg = await update.message.reply_text(
            text,
            parse_mode=ParseMode.MARKDOWN,
            reply_markup=reply_markup,
        )
        self.user_sessions[user_id] = {"chat_id": chat_id, "message_id": msg.message_id, "view": "main"}

    @authorized_only
    async def cmd_stato(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Reindirizza al menu di stato."""
        self.mqtt.request_info()
        user_id = update.effective_user.id
        chat_id = update.effective_chat.id

        text, reply_markup = self._get_view_content("status")
        msg = await update.message.reply_text(text, parse_mode=ParseMode.MARKDOWN, reply_markup=reply_markup)
        self.user_sessions[user_id] = {"chat_id": chat_id, "message_id": msg.message_id, "view": "status"}

    @authorized_only
    async def cmd_stop(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Esegue stop e aggiorna il menu dell'utente se presente, senza messaggi duplicati."""
        self.mqtt.stop_program()
        # Ottimizzazione locale immediata
        if self.mqtt.last_info_data:
            self.mqtt.last_info_data["sequenceRunning"] = False
            self.mqtt.last_info_data["relays"] = [False] * 8

        user_id = update.effective_user.id
        if user_id in self.user_sessions:
            s = self.user_sessions[user_id]
            await self._render_view(user_id, s["chat_id"], s["message_id"], s.get("view", "main"))
            await update.message.reply_text("🛑 *STOP eseguito!* Il menu sopra è stato aggiornato.", parse_mode=ParseMode.MARKDOWN)
        else:
            await update.message.reply_text("🛑 *STOP Emergenza inviato alla centralina!*", parse_mode=ParseMode.MARKDOWN)

    @authorized_only
    async def cmd_pioggia(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        state = self.mqtt.get_state()
        new_mode = not state.get("rainMode", False) if state else True
        self.mqtt.set_rain_mode(new_mode)
        if self.mqtt.last_info_data:
            self.mqtt.last_info_data["rainMode"] = new_mode

        user_id = update.effective_user.id
        desc = "ATTIVATA 🔴" if new_mode else "DISATTIVATA 🟢"
        if user_id in self.user_sessions:
            s = self.user_sessions[user_id]
            await self._render_view(user_id, s["chat_id"], s["message_id"], s.get("view", "main"))
            await update.message.reply_text(f"🌧️ *Modalità Pioggia {desc}!*\nPannello sopra aggiornato.", parse_mode=ParseMode.MARKDOWN)
        else:
            await update.message.reply_text(f"🌧️ *Modalità Pioggia {desc}*", parse_mode=ParseMode.MARKDOWN)

    @authorized_only
    async def cmd_help(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        help_text = (
            "📖 *Guida Rapida Centralina Irrigazione*\n\n"
            "• Invia `/menu` per aprire il pannello di controllo interattivo.\n"
            "• Tutti i comandi si gestiscono premendo i pulsanti direttamente nel messaggio.\n"
            "• Lo stato dei settori si aggiorna in tempo reale senza intasare la chat.\n"
            "• In caso di necessità puoi sempre inviare `/stop` per arrestare tutto all'istante."
        )
        await update.message.reply_text(help_text, parse_mode=ParseMode.MARKDOWN)

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
            await query.answer("Aggiornamento in corso...", show_alert=False)
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

