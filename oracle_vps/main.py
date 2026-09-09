"""
Entrypoint principale del servizio Telegram & MQTT Bridge su VPS.
"""

import asyncio
import logging
import sys
from telegram.ext import (
    Application,
    ApplicationBuilder,
    CallbackQueryHandler,
    CommandHandler,
)

from config import TELEGRAM_BOT_TOKEN, validate_config
from mqtt_manager import MQTTManager
from bot_handlers import BotHandlers
from notifier import Notifier

logging.basicConfig(
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    level=logging.INFO,
    handlers=[logging.StreamHandler(sys.stdout)],
)
# Silenzia i log ripetitivi del polling di Telegram e richieste HTTP
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("httpcore").setLevel(logging.WARNING)
logging.getLogger("telegram.ext.Application").setLevel(logging.WARNING)

logger = logging.getLogger("Main")



def main() -> None:
    # 1. Validazione configurazione
    validate_config()
    logger.info("Configurazione validata con successo.")

    # 2. Inizializzazione MQTT Manager
    mqtt_mgr = MQTTManager()
    mqtt_mgr.start()

    # 3. Creazione Bot Handlers
    handlers = BotHandlers(mqtt_mgr)

    # 4. Costruzione applicazione Telegram
    app = ApplicationBuilder().token(TELEGRAM_BOT_TOKEN).build()

    # 5. Notifier e Dead Man's Switch
    notifier = Notifier(app.bot, mqtt_mgr)

    async def on_startup(application: Application) -> None:
        loop = asyncio.get_running_loop()
        handlers.set_bot(application.bot)
        handlers.set_event_loop(loop)
        notifier.set_event_loop(loop)
        # Avvia il task in background per il monitoraggio della centralina
        asyncio.create_task(notifier.run_dead_man_switch())
        logger.info("Servizio Notifier, Handlers e Dead Man's Switch avviati.")


    async def on_shutdown(application: Application) -> None:
        logger.info("Arresto in corso...")
        mqtt_mgr.stop()

    app.post_init = on_startup
    app.post_shutdown = on_shutdown

    # Registrazione Comandi
    app.add_handler(CommandHandler(["start", "menu"], handlers.cmd_start))
    app.add_handler(CommandHandler("stato", handlers.cmd_stato))
    app.add_handler(CommandHandler("stop", handlers.cmd_stop))
    app.add_handler(CommandHandler("pioggia", handlers.cmd_pioggia))
    app.add_handler(CommandHandler("help", handlers.cmd_help))

    # Registrazione Callback Queries (Pulsanti Inline)
    app.add_handler(CallbackQueryHandler(handlers.handle_callback_query))

    logger.info("Avvio polling Telegram Bot...")
    try:
        app.run_polling(drop_pending_updates=True)
    except KeyboardInterrupt:
        logger.info("Interruzione manuale ricevuta.")
    finally:
        mqtt_mgr.stop()


if __name__ == "__main__":
    main()
