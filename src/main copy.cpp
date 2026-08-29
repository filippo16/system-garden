#include <ESP8266WiFi.h>
#include <ESP8266WebServer.h>
#include <LittleFS.h>
#include <time.h>
#include <ESP8266mDNS.h>

// ====== WIFI ======
const char* WIFI_SSID = "Vodafone-A85432648";
const char* WIFI_PASS = "Crispymango258";


// ====== WEB SERVER ======
ESP8266WebServer server(80);

// ====== TIME (solo logica, non UI) ======
const char* NTP_SERVER_1 = "pool.ntp.org";
const char* NTP_SERVER_2 = "time.nist.gov";
const char* TZ_INFO = "CET-1CEST,M3.5.0/2,M10.5.0/3"; // Italia

// ====== PATHS ======
static const char* PATH_INDEX  = "/index.html";
static const char* PATH_STYLE  = "/style.css";
static const char* PATH_CONFIG = "/config.json";

// ------------------------------------------------------------
// Utility: content-type base
// ------------------------------------------------------------
String contentTypeFor(const String& path) {
  if (path.endsWith(".html")) return "text/html; charset=utf-8";
  if (path.endsWith(".css"))  return "text/css; charset=utf-8";
  if (path.endsWith(".js"))   return "application/javascript; charset=utf-8";
  if (path.endsWith(".json")) return "application/json; charset=utf-8";
  if (path.endsWith(".txt"))  return "text/plain; charset=utf-8";
  return "application/octet-stream";
}

// ------------------------------------------------------------
// TIME: pronto per logiche programmate
// ------------------------------------------------------------
void setupTimeNTP() {
  configTime(0, 0, NTP_SERVER_1, NTP_SERVER_2);
  setenv("TZ", TZ_INFO, 1);
  tzset();
}

time_t timeNow() {
  // Se non sincronizzato, torna un valore basso: gestibile nelle tue logiche.
  return time(nullptr);
}

// ------------------------------------------------------------
// INFO DIAGNOSTICA
// ------------------------------------------------------------
String buildInfoText() {
  String s;
  s.reserve(512);
  s += "SSID: " + String(WIFI_SSID) + "\n";
  s += "IP: " + WiFi.localIP().toString() + "\n";
  s += "MAC: " + WiFi.macAddress() + "\n";
  s += "RSSI: " + String(WiFi.RSSI()) + " dBm\n";
  s += "Uptime: " + String(millis() / 1000) + " s\n";
  s += "FreeHeap: " + String(ESP.getFreeHeap()) + " bytes\n";
  s += "FlashChipSize: " + String(ESP.getFlashChipSize()) + " bytes\n";
  s += "SketchSize: " + String(ESP.getSketchSize()) + " bytes\n";
  s += "FreeSketchSpace: " + String(ESP.getFreeSketchSpace()) + " bytes\n";

  FSInfo fs_info;
  if (LittleFS.info(fs_info)) {
    s += "FS total: " + String(fs_info.totalBytes) + " bytes\n";
    s += "FS used: " + String(fs_info.usedBytes) + " bytes\n";
  } else {
    s += "FS info: unavailable\n";
  }
  return s;
}

// ------------------------------------------------------------
// LittleFS helpers
// ------------------------------------------------------------
bool ensureDefaultConfig() {
  if (LittleFS.exists(PATH_CONFIG)) return true;

  File f = LittleFS.open(PATH_CONFIG, "w");
  if (!f) return false;

  // Config base "pronta progetto": qui in futuro metti scheduling, modalità relè, ecc.
  // (JSON semplice, senza librerie: lo trattiamo come blob testuale)
  const char* defaultJson =
    "{\n"
    "  \"deviceName\": \"esp8266-test\",\n"
    "  \"timezone\": \"CET-1CEST,M3.5.0/2,M10.5.0/3\",\n"
    "  \"schedule\": {\n"
    "    \"enabled\": false,\n"
    "    \"rules\": []\n"
    "  }\n"
    "}\n";

  f.print(defaultJson);
  f.close();
  return true;
}

bool streamFileOr404(const char* path) {
  if (!LittleFS.exists(path)) return false;
  File f = LittleFS.open(path, "r");
  if (!f) return false;
  server.streamFile(f, contentTypeFor(path));
  f.close();
  return true;
}

// ------------------------------------------------------------
// ROUTES: static UI
// ------------------------------------------------------------
void handleRoot() {
  // UI statica da LittleFS: efficiente (stream) e modificabile senza ricompilare firmware.
  if (!streamFileOr404(PATH_INDEX)) {
    server.send(500, "text/plain; charset=utf-8",
                "index.html non trovato su LittleFS. Carica i file (data folder) e riprova.");
  }
}

void handleStyle() {
  if (!streamFileOr404(PATH_STYLE)) {
    server.send(404, "text/plain; charset=utf-8", "style.css non trovato.");
  }
}

// ------------------------------------------------------------
// ROUTES: info / config (read/write)
// ------------------------------------------------------------
void handleInfo() {
  server.send(200, "text/plain; charset=utf-8", buildInfoText());
}

void handleGetConfig() {
  if (!LittleFS.exists(PATH_CONFIG)) {
    server.send(404, "text/plain; charset=utf-8", "config.json non esiste.");
    return;
  }
  File f = LittleFS.open(PATH_CONFIG, "r");
  if (!f) {
    server.send(500, "text/plain; charset=utf-8", "Impossibile aprire config.json.");
    return;
  }
  server.streamFile(f, "application/json; charset=utf-8");
  f.close();
}

void handlePostConfig() {
  // Body raw: JSON (o testo). Salviamo come blob per essere flessibili.
  String body = server.arg("plain");
  if (body.length() == 0) {
    server.send(400, "text/plain; charset=utf-8", "Body vuoto. Invia JSON nel body.");
    return;
  }
  if (body.length() > 4096) {
    server.send(413, "text/plain; charset=utf-8", "Config troppo grande (max 4096 bytes).");
    return;
  }

  File f = LittleFS.open(PATH_CONFIG, "w");
  if (!f) {
    server.send(500, "text/plain; charset=utf-8", "Impossibile aprire config.json in scrittura.");
    return;
  }
  size_t w = f.print(body);
  f.close();

  if (w != body.length()) {
    server.send(500, "text/plain; charset=utf-8", "Scrittura config incompleta.");
    return;
  }
  server.send(200, "text/plain; charset=utf-8", "OK");
}

// ------------------------------------------------------------
// ROUTES: file generic read/write (test NVM)
//   GET  /file?name=/test.txt
//   POST /file?name=/test.txt   (body raw)
// ------------------------------------------------------------
bool isSafePath(const String& p) {
  // minime regole per evitare path strani:
  // - deve iniziare con "/"
  // - non deve contenere ".."
  if (!p.startsWith("/")) return false;
  if (p.indexOf("..") != -1) return false;
  return true;
}

void handleGetFile() {
  String name = server.arg("name");
  if (!isSafePath(name)) {
    server.send(400, "text/plain; charset=utf-8", "Parametro name non valido.");
    return;
  }
  if (!LittleFS.exists(name)) {
    server.send(404, "text/plain; charset=utf-8", "File non esiste: " + name);
    return;
  }
  File f = LittleFS.open(name, "r");
  if (!f) {
    server.send(500, "text/plain; charset=utf-8", "Impossibile aprire file in lettura.");
    return;
  }
  server.streamFile(f, contentTypeFor(name));
  f.close();
}

void handlePostFile() {
  String name = server.arg("name");
  if (!isSafePath(name)) {
    server.send(400, "text/plain; charset=utf-8", "Parametro name non valido.");
    return;
  }
  String body = server.arg("plain");
  if (body.length() == 0) {
    server.send(400, "text/plain; charset=utf-8", "Body vuoto.");
    return;
  }
  if (body.length() > 8192) {
    server.send(413, "text/plain; charset=utf-8", "Testo troppo grande (max 8192 bytes).");
    return;
  }

  File f = LittleFS.open(name, "w");
  if (!f) {
    server.send(500, "text/plain; charset=utf-8", "Impossibile aprire file in scrittura.");
    return;
  }
  size_t w = f.print(body);
  f.close();

  if (w != body.length()) {
    server.send(500, "text/plain; charset=utf-8", "Scrittura file incompleta.");
    return;
  }
  server.send(200, "text/plain; charset=utf-8", "OK");
}

// ------------------------------------------------------------
// WIFI connect
// ------------------------------------------------------------
void connectWiFi() {
  WiFi.mode(WIFI_STA);
  WiFi.begin(WIFI_SSID, WIFI_PASS);

  Serial.printf("Connessione WiFi a %s", WIFI_SSID);
  unsigned long start = millis();
  while (WiFi.status() != WL_CONNECTED && (millis() - start) < 20000) {
    delay(250);
    Serial.print(".");
  }
  Serial.println();

  if (WiFi.status() == WL_CONNECTED) {
    Serial.print("Connesso. IP: ");
    Serial.println(WiFi.localIP());
  } else {
    Serial.println("NON connesso (timeout).");
  }
}

void setup() {
  Serial.begin(9600);
  delay(200);
  Serial.println("\nBoot...");

  if (!LittleFS.begin()) {
    Serial.println("LittleFS.begin() FALLITO. Provo format (cancella contenuti)...");
    LittleFS.format();
    if (!LittleFS.begin()) Serial.println("LittleFS ancora FALLITO dopo format.");
  }

  // info FS (LittleFS.info() disponibile su core ESP8266 recente)
  FSInfo fs_info;
  LittleFS.info(fs_info);
  Serial.printf("FS total=%u used=%u\n", fs_info.totalBytes, fs_info.usedBytes);

  ensureDefaultConfig();

  connectWiFi();
  setupTimeNTP(); // solo per logica futura

  // Static UI
  server.on("/", HTTP_GET, handleRoot);
  server.on("/style.css", HTTP_GET, handleStyle);

  // API
  server.on("/info", HTTP_GET, handleInfo);
  server.on("/config", HTTP_GET, handleGetConfig);
  server.on("/config", HTTP_POST, handlePostConfig);
  server.on("/file", HTTP_GET, handleGetFile);
  server.on("/file", HTTP_POST, handlePostFile);

  server.onNotFound([]() {
    server.send(404, "text/plain; charset=utf-8", "Not found");
  });

  if (MDNS.begin("irrigation")) {
    MDNS.addService("http", "tcp", 80);
    Serial.println("mDNS: http://irrigation.local/");
  } else {
    Serial.println("mDNS start fallito");
  }

  server.begin();
  Serial.println("WebServer pronto (porta 80).");
}


void loop() {
  server.handleClient();
  MDNS.update();
  // Qui in futuro metti la tua logica programmata basata su timeNow()
  // time_t now = timeNow();
}



// -----------------------------------------

#include <Arduino.h>
#include <ESP8266WiFi.h>
#include <WiFiClientSecure.h>
#include <PubSubClient.h>

// ====== WIFI ======
const char* WIFI_SSID = "Vodafone-A85432648";
const char* WIFI_PASS = "Crispymango258";

// ====== MQTT (TLS) ======
const char* MQTT_HOST = "288ea69720d64b498ef73c73d5c405a5.s1.eu.hivemq.cloud";
const uint16_t MQTT_PORT = 8883;
const char* MQTT_USER = "ESP-8266";
const char* MQTT_PASS = "c'R6:V`|';a:467,%sc5=6i|NR";
const char* MQTT_TOPIC = "test";
const char* MQTT_WAKE_TOPIC = "wake";
const char* CLIENT_ID = "esp8266-test-client";

WiFiClientSecure secureClient;
PubSubClient mqttClient(secureClient);

// Root CA example (replace with your broker CA).
// For demo with public test broker, verification is disabled below.
// static const char CA_CERT[] PROGMEM = R"EOF(
// -----BEGIN CERTIFICATE-----
// YOUR_CA_CERTIFICATE_HERE
// -----END CERTIFICATE-----
// )EOF";

unsigned long lastPublishMs = 0;

void connectWiFi() {
	if (WiFi.status() == WL_CONNECTED) {
		return;
	}

	Serial.print("Connessione WiFi a: ");
	Serial.println(WIFI_SSID);
	WiFi.mode(WIFI_STA);
	WiFi.begin(WIFI_SSID, WIFI_PASS);

	while (WiFi.status() != WL_CONNECTED) {
		delay(500);
		Serial.print(".");
	}

	Serial.println();
	Serial.print("WiFi connesso. IP: ");
	Serial.println(WiFi.localIP());
}

void mqttCallback(char* topic, byte* payload, unsigned int length) {
	String incomingMessage;
	incomingMessage.reserve(length);

	Serial.print("Messaggio su ");
	Serial.print(topic);
	Serial.print(": ");
	for (unsigned int i = 0; i < length; i++) {
		char c = static_cast<char>(payload[i]);
		Serial.print(c);
		incomingMessage += c;
	}
	Serial.println();

	if (strcmp(topic, MQTT_WAKE_TOPIC) == 0) {
		String response = "Messaggio ricevuto: " + incomingMessage;
		mqttClient.publish(MQTT_TOPIC, response.c_str());
		Serial.println("Inviata risposta su topic test");
	}
}

void connectMQTT() {
	while (!mqttClient.connected()) {
		Serial.print("Connessione MQTT TLS...");

		bool connected;
		if (strlen(MQTT_USER) > 0) {
			connected = mqttClient.connect(CLIENT_ID, MQTT_USER, MQTT_PASS);
		} else {
			connected = mqttClient.connect(CLIENT_ID);
		}

		if (connected) {
			Serial.println(" OK");
			mqttClient.subscribe(MQTT_TOPIC);
			mqttClient.subscribe(MQTT_WAKE_TOPIC);
			mqttClient.publish(MQTT_TOPIC, "ESP8266 online (TLS)");
		} else {
			Serial.print(" KO, rc=");
			Serial.print(mqttClient.state());
			Serial.println(" -> retry in 5s");
			delay(5000);
		}
	}
}

void setup() {
	Serial.begin(9600);
	delay(100);

	connectWiFi();

	// TLS with no certificate validation (quick start).
	// For production, comment setInsecure() and use setTrustAnchors().
	secureClient.setInsecure();

	// Example for production certificate validation:
	// BearSSL::X509List cert(CA_CERT);
	// secureClient.setTrustAnchors(&cert);

	mqttClient.setServer(MQTT_HOST, MQTT_PORT);
	mqttClient.setCallback(mqttCallback);
}

void loop() {
	if (WiFi.status() != WL_CONNECTED) {
		connectWiFi();
	}

	if (!mqttClient.connected()) {
		connectMQTT();
	}

	mqttClient.loop();

	const unsigned long now = millis();
	if (now - lastPublishMs > 10000) {
		lastPublishMs = now;
		mqttClient.publish(MQTT_TOPIC, "ping da ESP8266 (TLS)");
		Serial.println("Pubblicato su topic test");
	}
}

// --------------------------------
