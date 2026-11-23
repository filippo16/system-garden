"""
Configurazione centralizzata per il servizio VPS Telegram & MQTT Bridge.
Carica le variabili da file .env o dall'ambiente di sistema.
"""

import os
import sys
from pathlib import Path
ENV_PATH = Path(__file__).resolve().parent / ".env"

try:
    from dotenv import load_dotenv
    load_dotenv(dotenv_path=ENV_PATH)
except ImportError:
    # Fallback integrato senza dipendenze per caricare .env
    if ENV_PATH.exists():
        with open(ENV_PATH, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith("#") and "=" in line:
                    k, v = line.split("=", 1)
                    k = k.strip()
                    v = v.strip().strip("'\"")
                    if k not in os.environ:
                        os.environ[k] = v



TELEGRAM_BOT_TOKEN: str = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()

# Parsing lista ID autorizzati
_raw_allowed = os.getenv("ALLOWED_USER_IDS", "").strip()
ALLOWED_USER_IDS: set[int] = set()
if _raw_allowed:
    for uid in _raw_allowed.split(","):
        uid = uid.strip()
        if uid.isdigit():
            ALLOWED_USER_IDS.add(int(uid))

# Parametri MQTT
MQTT_HOST: str = os.getenv("MQTT_HOST", "").strip()
MQTT_PORT: int = int(os.getenv("MQTT_PORT", "8883"))
MQTT_USER: str = os.getenv("MQTT_USER", "").strip()
MQTT_PASS: str = os.getenv("MQTT_PASS", "").strip()
MQTT_CLIENT_ID: str = os.getenv("MQTT_CLIENT_ID", "oracle-vps-bot").strip()

MQTT_TOPIC_COMMAND: str = os.getenv("MQTT_TOPIC_COMMAND", "command").strip()
MQTT_TOPIC_INFO: str = os.getenv("MQTT_TOPIC_INFO", "info").strip()
MQTT_TOPIC_LOG: str = os.getenv("MQTT_TOPIC_LOG", "log").strip()

# Dead Man's Switch (timeout di inattività in minuti)
HEARTBEAT_TIMEOUT_MINUTES: int = int(os.getenv("HEARTBEAT_TIMEOUT_MINUTES", "15"))


def is_user_allowed(user_id: int) -> bool:
    """Verifica se l'ID utente Telegram specificato appartiene alla whitelist."""
    return user_id in ALLOWED_USER_IDS


def validate_config() -> None:
    """Valida che i parametri minimi siano stati configurati correttamente."""
    errors = []
    if not TELEGRAM_BOT_TOKEN or "INSERISCI_QUI" in TELEGRAM_BOT_TOKEN:
        errors.append("TELEGRAM_BOT_TOKEN non è configurato o contiene il valore placeholder in .env")
    if not ALLOWED_USER_IDS:
        errors.append("ALLOWED_USER_IDS è vuoto. Devi specificare almeno un Telegram User ID valido.")
    if not MQTT_HOST:
        errors.append("MQTT_HOST non configurato.")

    if errors:
        print("\n[ERRORE CONFIGURAZIONE]")
        for err in errors:
            print(f" - {err}")
        print("\nCompila correttamente il file .env prima di avviare il servizio.\n")
        sys.exit(1)
