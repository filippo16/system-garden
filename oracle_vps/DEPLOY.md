# Guida al Deploy: Bot Telegram & MQTT Bridge su VPS (Oracle Cloud / Linux)

Questa guida spiega come configurare e avviare il servizio di controllo irrigazione sulla tua VPS.

---

## 1. Prerequisiti: Token Telegram e User ID

### A. Ottenere il Token del Bot Telegram
1. Apri Telegram e cerca **`@BotFather`**.
2. Invia `/newbot`.
3. Scegli un nome visualizzato (es. `Irrigazione Giardino`) e un username univoco (es. `mio_giardino_bot`).
4. `@BotFather` ti restituirà un token del tipo:
   `123456789:ABCdefGHIjklMNOpqrSTUvwxYZ`
5. Salvalo, andrà inserito in `.env`.

### B. Recuperare il tuo User ID (Whitelist)
1. Cerca **`@userinfobot`** su Telegram e premi `/start`.
2. Ti risponderà con il tuo `Id` numerico (es. `478034542`).
3. Se in futuro vuoi autorizzare altre persone, ti basterà farti dare il loro ID e aggiungerlo separato da virgola (es. `478034542,987654321`).

---

## 2. Configurazione file `.env`

Nella cartella `oracle_vps/`, apri il file `.env` e incolla il token appena ottenuto:

```env
TELEGRAM_BOT_TOKEN=IL_TUO_TOKEN_DI_BOTFATHER
ALLOWED_USER_IDS=478034542
MQTT_HOST=288ea69720d64b498ef73c73d5c405a5.s1.eu.hivemq.cloud
MQTT_PORT=8883
MQTT_USER=ESP-8266
MQTT_PASS=c'R6:V`|';a:467,%sc5=6i|NR
MQTT_CLIENT_ID=oracle-vps-bot
MQTT_TOPIC_COMMAND=command
MQTT_TOPIC_INFO=info
MQTT_TOPIC_LOG=log
HEARTBEAT_TIMEOUT_MINUTES=15
```

---

## 3. Test Rapido in Locale (Opzionale)

Se vuoi provare il bot sul tuo computer prima di metterlo sulla VPS:

```bash
cd oracle_vps
pip install -r requirements.txt
python main.py
```

Apri Telegram, cerca il tuo bot e premi `/start` o `/menu`!

---

## 4. Deploy su VPS con Docker (Consigliato)

Su Oracle Cloud (o qualsiasi VPS Ubuntu/Debian):

### Passaggio 1: Installa Docker e Docker Compose (se non presenti)
```bash
sudo apt update
sudo apt install -y docker.io docker-compose
sudo systemctl enable --now docker
```

### Passaggio 2: Copia la cartella `oracle_vps` sulla VPS
Puoi usare `scp`, `git` o un client SFTP (es. FileZilla):
```bash
# Esempio da terminale locale:
scp -r oracle_vps ubuntu@IP_DELLA_TUA_VPS:~/
```

### Passaggio 3: Avvia il container
Entra nella cartella sulla VPS:
```bash
cd ~/oracle_vps
docker-compose up -d --build
```

Il servizio ora è attivo in background e si **riavvierà automaticamente** anche in caso di riavvio della VPS (`restart: unless-stopped`).

### Comandi utili sulla VPS:
* **Visualizzare i log in tempo reale:**
  ```bash
  docker-compose logs -f
  ```
* **Fermare il servizio:**
  ```bash
  docker-compose down
  ```
* **Riavviare dopo aver modificato `.env`:**
  ```bash
  docker-compose restart
  ```

---

## 5. Deploy Alternativo con Systemd (senza Docker)

Se preferisci non usare Docker sulla VPS:

1. Installa Python e venv:
   ```bash
   sudo apt update
   sudo apt install -y python3 python3-pip python3-venv
   ```
2. Crea l'ambiente virtuale ed installa le dipendenze:
   ```bash
   cd ~/oracle_vps
   python3 -m venv venv
   source venv/bin/activate
   pip install -r requirements.txt
   ```
3. Crea un servizio di sistema `systemd`:
   ```bash
   sudo nano /etc/systemd/system/irrigation-bot.service
   ```
   Incolla:
   ```ini
   [Unit]
   Description=Irrigation Telegram Bot & MQTT Bridge
   After=network.target

   [Service]
   Type=simple
   User=ubuntu
   WorkingDirectory=/home/ubuntu/oracle_vps
   ExecStart=/home/ubuntu/oracle_vps/venv/bin/python main.py
   Restart=always
   RestartSec=10

   [Install]
   WantedBy=multi-user.target
   ```
4. Abilita e avvia il servizio:
   ```bash
   sudo systemctl daemon-reload
   sudo systemctl enable --now irrigation-bot.service
   sudo systemctl status irrigation-bot.service
   ```
