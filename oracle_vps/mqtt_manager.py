"""
Gestione connessione MQTT over TLS verso HiveMQ Cloud, cache di stato
e interfaccia comandi per l'ESP8266.
"""

import json
import logging
import ssl
import time
from typing import Any, Callable, Optional
import paho.mqtt.client as mqtt

from config import (
    MQTT_HOST,
    MQTT_PORT,
    MQTT_USER,
    MQTT_PASS,
    MQTT_CLIENT_ID,
    MQTT_TOPIC_COMMAND,
    MQTT_TOPIC_INFO,
    MQTT_TOPIC_LOG,
)

logger = logging.getLogger("MQTTManager")


class MQTTManager:
    def __init__(self):
        # Supporta sia paho-mqtt v2 che v1
        try:
            self.client = mqtt.Client(
                callback_api_version=mqtt.CallbackAPIVersion.VERSION2,
                client_id=MQTT_CLIENT_ID,
            )
        except AttributeError:
            self.client = mqtt.Client(client_id=MQTT_CLIENT_ID)

        self.client.username_pw_set(MQTT_USER, MQTT_PASS)

        # Configura TLS per HiveMQ Cloud (porta 8883)
        self.client.tls_set(
            cert_reqs=ssl.CERT_REQUIRED,
            tls_version=ssl.PROTOCOL_TLS_CLIENT,
        )

        self.client.on_connect = self._on_connect
        self.client.on_disconnect = self._on_disconnect
        self.client.on_message = self._on_message

        # Cache e stato
        self.is_connected = False
        self.last_info_data: dict[str, Any] = {}
        self.last_seen_timestamp: float = 0.0  # Unix timestamp ultimo messaggio ricevuto dall'ESP
        self.device_online: bool = False

        # Callback esterne (es. per notifiche Telegram)
        self._info_listeners: list[Callable[[dict[str, Any]], None]] = []
        self._log_listeners: list[Callable[[dict[str, Any]], None]] = []

    def add_info_listener(self, callback: Callable[[dict[str, Any]], None]) -> None:
        self._info_listeners.append(callback)

    def add_log_listener(self, callback: Callable[[dict[str, Any]], None]) -> None:
        self._log_listeners.append(callback)

    def start(self) -> None:
        """Avvia la connessione MQTT e il loop in background."""
        logger.info(f"Connessione al broker HiveMQ Cloud {MQTT_HOST}:{MQTT_PORT}...")
        try:
            self.client.connect(MQTT_HOST, MQTT_PORT, keepalive=60)
            self.client.loop_start()
        except Exception as e:
            logger.error(f"Errore durante la connessione MQTT iniziale: {e}")

    def stop(self) -> None:
        """Ferma il loop e disconnette il client."""
        logger.info("Chiusura connessione MQTT...")
        self.client.loop_stop()
        self.client.disconnect()

    def _on_connect(self, client, userdata, flags, rc, properties=None) -> None:
        if rc == 0:
            self.is_connected = True
            logger.info("Connesso con successo al broker HiveMQ Cloud via TLS.")
            # Iscrizione ai topic dell'ESP8266
            client.subscribe([(MQTT_TOPIC_INFO, 0), (MQTT_TOPIC_LOG, 0)])
            logger.info(f"Iscritto ai topic: {MQTT_TOPIC_INFO}, {MQTT_TOPIC_LOG}")
            # Richiedi subito lo stato aggiornato
            self.request_info()
        else:
            self.is_connected = False
            logger.error(f"Connessione MQTT fallita con codice di ritorno: {rc}")

    def _on_disconnect(self, client, userdata, disconnect_flags_or_rc, reason_code=None, properties=None) -> None:
        self.is_connected = False
        logger.warning("Disconnesso dal broker MQTT HiveMQ Cloud.")

    def _on_message(self, client, userdata, msg) -> None:
        topic = msg.topic
        payload_str = msg.payload.decode("utf-8", errors="ignore")
        self.last_seen_timestamp = time.time()
        self.device_online = True

        try:
            data = json.loads(payload_str)
        except json.JSONDecodeError:
            logger.warning(f"Payload non JSON ricevuto su {topic}: {payload_str}")
            return

        if topic == MQTT_TOPIC_INFO:
            self.last_info_data = data
            for listener in self._info_listeners:
                try:
                    listener(data)
                except Exception as e:
                    logger.error(f"Errore listener info: {e}")

        elif topic == MQTT_TOPIC_LOG:
            for listener in self._log_listeners:
                try:
                    listener(data)
                except Exception as e:
                    logger.error(f"Errore listener log: {e}")

    def publish_command(self, payload: dict[str, Any]) -> bool:
        """Serializza e pubblica un payload JSON sul topic command dell'ESP."""
        if not self.is_connected:
            logger.warning("Impossibile inviare comando: broker MQTT non connesso.")
            return False

        msg = json.dumps(payload)
        logger.info(f"Invio su {MQTT_TOPIC_COMMAND}: {msg}")
        result = self.client.publish(MQTT_TOPIC_COMMAND, msg, qos=1)
        return result.rc == mqtt.MQTT_ERR_SUCCESS

    # Metodi di alto livello conformi a MQTT_COMMANDS.md
    def request_info(self) -> bool:
        return self.publish_command({"action": "get_info"})

    def set_relay(self, relay_number_1_to_8: int, active: bool) -> bool:
        """Attiva o disattiva un singolo relay (1..8)."""
        return self.publish_command({"relay": relay_number_1_to_8, "active": active})

    def stop_program(self) -> bool:
        """Arresto di emergenza della sequenza in corso."""
        return self.publish_command({"action": "stop_program"})

    def start_program(self, program_id: str) -> bool:
        """Avvia manualmente un programma per test."""
        return self.publish_command({"action": "start_program", "id": program_id})

    def set_rain_mode(self, enabled: bool) -> bool:
        """Attiva o disattiva la modalità pioggia."""
        return self.publish_command({"action": "set_rain_mode", "enabled": enabled})

    def set_program_enabled(self, program_id: str, enabled: bool) -> bool:
        action = "enable_program" if enabled else "disable_program"
        return self.publish_command({"action": action, "id": program_id})

    def get_state(self) -> dict[str, Any]:
        """Restituisce una copia dell'ultimo stato noto."""
        return dict(self.last_info_data)
