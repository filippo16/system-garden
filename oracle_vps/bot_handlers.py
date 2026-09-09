"""
Handler dei comandi e delle interfacce interattive Telegram,
con protezione whitelist e tastiere inline.
"""

import functools
import logging
from typing import Any, Callable
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.constants import ParseMode
from telegram.ext import ContextTypes

from config import is_user_allowed, ALLOWED_USER_IDS
from mqtt_manager import MQTTManager

logger = logging.getLogger("BotHandlers")


def authorized_only(func: Callable):
    """Decoratore per consentire l'esecuzione solo agli ID autorizzati."""
    @functools.wraps(func)
    async def wrapper(*args, **kwargs):
        # Cerca l'oggetto Update tra gli argomenti (es. args[1] se metodo con self, oppure args[0])
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

    # -------------------------------------------------------------
    # Helper di formattazione
    # -------------------------------------------------------------
    def _format_status_text(self) -> str:
        state = self.mqtt.get_state()
        if not state:
            return (
                "⚠️ *Nessun dato ancora ricevuto dall'ESP8266.*\n\n"
                "Verifica che l'ESP sia alimentato e connesso a internet.\n"
                "Ho inviato una richiesta di stato (`get_info`). Riprova tra pochi secondi."
            )

        esp_time = state.get("time", "Non sincronizzato")
        rain_mode = state.get("rainMode", False)
        seq_running = state.get("sequenceRunning", False)
        relays = state.get("relays", [False] * 8)
        sector_names = state.get("sectorNames", [f"Settore {i+1}" for i in range(8)])

        rain_str = "🔴 *ATTIVO (Impianto Bloccato)*" if rain_mode else "🟢 Disattivo"

        if seq_running:
            prog_id = state.get("runningProgramId", "N/D")
            step = state.get("runningStep", "?")
            status_seq = f"🚿 *IN CORSO*\n• Programma: `{prog_id}`\n• Settore (Step): `{step}`"
        else:
            status_seq = "⏸️ In standby (Nessuna irrigazione in corso)"

        lines = [
            "🌿 *STATO CENTRALINA IRRIGAZIONE*",
            "━━━━━━━━━━━━━━━━━━━━━━",
            f"🕒 *Orario ESP:* `{esp_time}`",
            f"🌧️ *Modalità Pioggia:* {rain_str}",
            f"⚡ *Stato Esecuzione:* {status_seq}",
            "",
            "📋 *Stato Settori / Elettrovalvole:*",
        ]

        for i in range(8):
            name = sector_names[i] if i < len(sector_names) else f"Settore {i+1}"
            is_on = relays[i] if i < len(relays) else False
            icon = "🟢 ON" if is_on else "⚫ OFF"
            lines.append(f"  {i+1}. *{name}*: {icon}")

        return "\n".join(lines)

    def _build_main_keyboard(self) -> InlineKeyboardMarkup:
        state = self.mqtt.get_state()
        rain_mode = state.get("rainMode", False) if state else False
        rain_btn_text = "🌧️ Pioggia: ATTIVA 🔴" if rain_mode else "🌧️ Pioggia: DISATTIVA 🟢"

        keyboard = [
            [
                InlineKeyboardButton("📊 Stato Completo", callback_data="menu_status"),
                InlineKeyboardButton("🚰 Controllo Settori", callback_data="menu_sectors"),
            ],
            [
                InlineKeyboardButton("▶️ Avvia Programma", callback_data="menu_programs"),
                InlineKeyboardButton(rain_btn_text, callback_data="toggle_rain"),
            ],
            [
                InlineKeyboardButton("⏹️ STOP Emergenza", callback_data="cmd_stop"),
                InlineKeyboardButton("🔄 Aggiorna", callback_data="refresh_main"),
            ],
        ]
        return InlineKeyboardMarkup(keyboard)

    def _build_sectors_keyboard(self) -> InlineKeyboardMarkup:
        state = self.mqtt.get_state()
        relays = state.get("relays", [False] * 8) if state else [False] * 8
        sector_names = state.get("sectorNames", [f"Settore {i+1}" for i in range(8)]) if state else [f"Settore {i+1}" for i in range(8)]

        keyboard = []
        # Righe da 2 bottoni ciascuna
        for i in range(0, 8, 2):
            row = []
            for j in (i, i + 1):
                if j < 8:
                    name = sector_names[j]
                    is_on = relays[j]
                    short_name = name[:14] + ".." if len(name) > 16 else name
                    label = f"{'🟢' if is_on else '⚫'} S{j+1}: {short_name}"
                    row.append(InlineKeyboardButton(label, callback_data=f"toggle_relay_{j+1}"))
            keyboard.append(row)

        keyboard.append([
            InlineKeyboardButton("🔄 Aggiorna", callback_data="menu_sectors"),
            InlineKeyboardButton("🔙 Menu Principale", callback_data="menu_main"),
        ])
        return InlineKeyboardMarkup(keyboard)

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
                keyboard.append([InlineKeyboardButton(label, callback_data=f"start_prog_{pid}")])

        keyboard.append([
            InlineKeyboardButton("🔄 Aggiorna", callback_data="menu_programs"),
            InlineKeyboardButton("🔙 Menu Principale", callback_data="menu_main"),
        ])
        return InlineKeyboardMarkup(keyboard)

    # -------------------------------------------------------------
    # Comandi Base (/start, /menu, /stato, /stop, /pioggia, /help)
    # -------------------------------------------------------------
    @authorized_only
    async def cmd_start(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Comando /start o /menu: mostra la dashboard principale."""
        # Forziamo richiesta aggiornamento all'ESP
        self.mqtt.request_info()
        text = (
            "👋 *Benvenuto nel Sistema di Irrigazione!*\n\n"
            "Usa i pulsanti sottostanti per verificare lo stato e comandare l'impianto in tempo reale."
        )
        await update.message.reply_text(
            text,
            parse_mode=ParseMode.MARKDOWN,
            reply_markup=self._build_main_keyboard(),
        )

    @authorized_only
    async def cmd_stato(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Comando /stato rapido."""
        self.mqtt.request_info()
        text = self._format_status_text()
        keyboard = InlineKeyboardMarkup([
            [InlineKeyboardButton("🔄 Aggiorna", callback_data="refresh_status")],
            [InlineKeyboardButton("🔙 Menu Principale", callback_data="menu_main")],
        ])
        await update.message.reply_text(text, parse_mode=ParseMode.MARKDOWN, reply_markup=keyboard)

    @authorized_only
    async def cmd_stop(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Comando /stop: stop immediato."""
        self.mqtt.stop_program()
        await update.message.reply_text("🛑 *Comando STOP inviato all'ESP8266!*", parse_mode=ParseMode.MARKDOWN)

    @authorized_only
    async def cmd_pioggia(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Comando /pioggia: toggle rain mode."""
        state = self.mqtt.get_state()
        cur_mode = state.get("rainMode", False) if state else False
        new_mode = not cur_mode
        self.mqtt.set_rain_mode(new_mode)
        status_str = "ATTIVATA 🔴 (Impianto bloccato)" if new_mode else "DISATTIVATA 🟢 (Impianto abilitato)"
        await update.message.reply_text(f"🌧️ Modalità Pioggia {status_str}", parse_mode=ParseMode.MARKDOWN)

    @authorized_only
    async def cmd_help(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Comando /help: elenco funzionalità."""
        help_text = (
            "📖 *Guida Comandi Bot Irrigazione*\n\n"
            "• `/menu` o `/start` — Apre la dashboard principale con pulsanti interattivi.\n"
            "• `/stato` — Mostra lo stato dettagliato di relè, orario e programmi.\n"
            "• `/stop` — Blocca all'istante qualsiasi irrigazione o settore attivo.\n"
            "• `/pioggia` — Attiva/disattiva la modalità pioggia.\n\n"
            "💡 *Autonomia:* I programmi sono salvati sull'ESP8266. L'irrigazione funziona regolarmente anche se internet è assente o il bot è spento."
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

        if data == "noop":
            await query.answer()
            return

        elif data == "menu_main" or data == "refresh_main":
            self.mqtt.request_info()
            await query.answer("Aggiornamento menu...")
            text = "🌿 *Dashboard Controllo Irrigazione*"
            await query.edit_message_text(
                text,
                parse_mode=ParseMode.MARKDOWN,
                reply_markup=self._build_main_keyboard(),
            )

        elif data == "menu_status" or data == "refresh_status":
            self.mqtt.request_info()
            await query.answer("Aggiornamento stato...")
            text = self._format_status_text()
            keyboard = InlineKeyboardMarkup([
                [InlineKeyboardButton("🔄 Aggiorna", callback_data="refresh_status")],
                [InlineKeyboardButton("🔙 Menu Principale", callback_data="menu_main")],
            ])
            try:
                await query.edit_message_text(text, parse_mode=ParseMode.MARKDOWN, reply_markup=keyboard)
            except Exception:
                pass

        elif data == "menu_sectors":
            self.mqtt.request_info()
            await query.answer("Caricamento settori...")
            text = (
                "🚰 *Controllo Manuale Settori*\n\n"
                "Tocca un settore per attivarlo (🟢) o disattivarlo (⚫).\n"
                "⚠️ _Nota: Se accendi un settore manualmente, l'eventuale sequenza automatica viene interrotta._"
            )
            await query.edit_message_text(
                text,
                parse_mode=ParseMode.MARKDOWN,
                reply_markup=self._build_sectors_keyboard(),
            )

        elif data.startswith("toggle_relay_"):
            relay_num = int(data.replace("toggle_relay_", ""))
            state = self.mqtt.get_state()
            relays = state.get("relays", [False] * 8) if state else [False] * 8
            current_is_on = relays[relay_num - 1] if 1 <= relay_num <= 8 else False
            new_state = not current_is_on

            self.mqtt.set_relay(relay_num, new_state)
            action_desc = "ACCESO" if new_state else "SPENTO"
            await query.answer(f"Settore {relay_num} {action_desc}!")

            # Aggiorna la vista dopo il comando
            text = "🚰 *Controllo Manuale Settori*\n\nComando inviato! Stato in aggiornamento..."
            await query.edit_message_text(
                text,
                parse_mode=ParseMode.MARKDOWN,
                reply_markup=self._build_sectors_keyboard(),
            )

        elif data == "menu_programs":
            self.mqtt.request_info()
            await query.answer("Caricamento programmi...")
            text = (
                "▶️ *Avvio Programma per Test*\n\n"
                "Seleziona un programma per avviarlo immediatamente senza attendere l'orario schedulato:"
            )
            await query.edit_message_text(
                text,
                parse_mode=ParseMode.MARKDOWN,
                reply_markup=self._build_programs_keyboard(),
            )

        elif data.startswith("start_prog_"):
            prog_id = data.replace("start_prog_", "")
            self.mqtt.start_program(prog_id)
            await query.answer(f"Avvio test '{prog_id}' inviato!", show_alert=True)
            text = f"▶️ *Comando di avvio test inviato per il programma:* `{prog_id}`"
            keyboard = InlineKeyboardMarkup([
                [InlineKeyboardButton("📊 Visualizza Stato", callback_data="menu_status")],
                [InlineKeyboardButton("🔙 Menu Principale", callback_data="menu_main")],
            ])
            await query.edit_message_text(text, parse_mode=ParseMode.MARKDOWN, reply_markup=keyboard)

        elif data == "toggle_rain":
            state = self.mqtt.get_state()
            cur_mode = state.get("rainMode", False) if state else False
            new_mode = not cur_mode
            self.mqtt.set_rain_mode(new_mode)
            desc = "ATTIVATA 🔴" if new_mode else "DISATTIVATA 🟢"
            await query.answer(f"Modalità Pioggia {desc}")

            text = f"🌧️ *Modalità Pioggia {desc}*\nComando inviato alla centralina."
            await query.edit_message_text(
                text,
                parse_mode=ParseMode.MARKDOWN,
                reply_markup=self._build_main_keyboard(),
            )

        elif data == "cmd_stop":
            self.mqtt.stop_program()
            await query.answer("🛑 STOP inviato!", show_alert=True)
            text = "🛑 *STOP Emergenza Inviato!*\nLa sequenza e i relè attivi sono stati arrestati."
            keyboard = InlineKeyboardMarkup([
                [InlineKeyboardButton("📊 Controlla Stato", callback_data="menu_status")],
                [InlineKeyboardButton("🔙 Menu Principale", callback_data="menu_main")],
            ])
            await query.edit_message_text(text, parse_mode=ParseMode.MARKDOWN, reply_markup=keyboard)
