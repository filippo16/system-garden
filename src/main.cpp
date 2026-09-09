#include <ESP8266WiFi.h>
#include <WiFiClientSecure.h>
#include <PubSubClient.h>
#include <ArduinoJson.h>
#include <Wire.h>
#include <LittleFS.h>
#include <time.h>

const char* ssid = "Vodafone-A85432648";
const char* password = "Crispymango258";

const char* MQTT_HOST = "288ea69720d64b498ef73c73d5c405a5.s1.eu.hivemq.cloud";
const uint16_t MQTT_PORT = 8883;
const char* MQTT_USER = "ESP-8266";
const char* MQTT_PASS = "c'R6:V`|';a:467,%sc5=6i|NR";
const char* CLIENT_ID = "esp8266-client";

const char* TOPIC_COMMAND = "command";
const char* TOPIC_INFO = "info";
const char* TOPIC_LOG = "log";
const uint16_t MQTT_BUFFER_SIZE = 4096;
const char* MQTT_LWT_MESSAGE = "{\"error\":\"ESP8266 disconnected unexpectedly (LWT)\"}";

const char* PROGRAM_FILE = "/programs.json";
const char* SETTINGS_FILE = "/settings.json";
const size_t MAX_PROGRAMS = 12;
const uint8_t MAX_STEPS_PER_PROGRAM = 8;
const uint16_t STEP_TRANSITION_DELAY_SEC = 3;

const char* NTP_SERVER_1 = "pool.ntp.org";
const char* NTP_SERVER_2 = "time.google.com";
const long TZ_OFFSET_SECONDS = 3600;  // CET
const int DST_OFFSET_SECONDS = 3600;  // CEST

byte relayState = 0xFF; // all relays off
const byte pcfAddress = 0x20;

WiFiClientSecure secureClient;
PubSubClient mqttClient(secureClient);

bool mqttWasConnected = false;
unsigned long lastWifiAttemptMs = 0;
const unsigned long WIFI_RETRY_INTERVAL_MS = 10000;
unsigned long lastMqttAttemptMs = 0;
const unsigned long MQTT_RETRY_INTERVAL_MS = 5000;
bool rainMode = false;
String sectorNames[8] = {
  "Settore 1 sud dinamici", "Settore 2 sud giro casa/nord pozzo statici", "Settore 3 ovest dinamici e statici lato nord giardinetto", "Settore 4 aiuole nord e bonsai grandi",
  "Settore 5 dinamici Giordano e serra casa", "Settore 6 statici casa vecchia lato ovest ed est", "Settore 7", "Settore 8"
};

struct ProgramStep {
  uint8_t relay;       // 0..7
  uint16_t durationMin;
};

struct ProgramItem {
  String id;
  uint8_t startHour;    // 0..23
  uint8_t startMinute;  // 0..59
  bool enabled;
  uint8_t stepCount;
  ProgramStep steps[MAX_STEPS_PER_PROGRAM];
  int lastRunDay;
};

ProgramItem programs[MAX_PROGRAMS];
size_t programCount = 0;
unsigned long lastProgramCheckMs = 0;

bool sequenceRunning = false;
int runningProgramIndex = -1;
uint8_t runningStepIndex = 0;
time_t runningStepEndTs = 0;
int activeRelayFromSequence = -1;
bool waitingBetweenSteps = false;
time_t transitionPauseUntilTs = 0;

bool isRelayOn(uint8_t relay) {
  if (relay > 7) return false;
  return ((relayState & (1 << relay)) == 0);
}

void publishLog(const char* message) {
  mqttClient.publish(TOPIC_LOG, message);
}

String getIsoLocalTime() {
  time_t now = time(nullptr);
  if (now < 100000) return "unsynced";

  struct tm localTm;
  localtime_r(&now, &localTm);

  char buffer[25];
  strftime(buffer, sizeof(buffer), "%Y-%m-%dT%H:%M:%S", &localTm);
  return String(buffer);
}

void publishInfo(const char* reason) {
  JsonDocument out;
  out["reason"] = reason;
  out["time"] = getIsoLocalTime();
  out["rainMode"] = rainMode;

  JsonArray relayArray = out["relays"].to<JsonArray>();
  JsonArray namesArray = out["sectorNames"].to<JsonArray>();
  for (uint8_t i = 0; i < 8; i++) {
    relayArray.add(isRelayOn(i));
    namesArray.add(sectorNames[i]);
  }

  JsonArray programArray = out["programs"].to<JsonArray>();
  for (size_t i = 0; i < programCount; i++) {
    JsonObject p = programArray.add<JsonObject>();
    p["id"] = programs[i].id;
    p["startHour"] = programs[i].startHour;
    p["startMinute"] = programs[i].startMinute;
    p["enabled"] = programs[i].enabled;

    JsonArray seq = p["sequence"].to<JsonArray>();
    for (uint8_t s = 0; s < programs[i].stepCount; s++) {
      JsonObject st = seq.add<JsonObject>();
      st["relay"] = programs[i].steps[s].relay + 1;
      st["duration"] = programs[i].steps[s].durationMin;
    }
  }

  out["sequenceRunning"] = sequenceRunning;
  if (sequenceRunning && runningProgramIndex >= 0) {
    out["runningProgramId"] = programs[runningProgramIndex].id;
    out["runningStep"] = runningStepIndex + 1;
  }

  String payload;
  serializeJson(out, payload);
  if (payload.length() == 0) {
    publishLog("{\"error\":\"Empty info payload\"}");
    return;
  }

  // Keep margin for MQTT fixed header + topic bytes.
  if (payload.length() > (MQTT_BUFFER_SIZE - 128)) {
    publishLog("{\"error\":\"Info payload too large\"}");
    return;
  }

  if (!mqttClient.publish(TOPIC_INFO, payload.c_str())) {
    publishLog("{\"error\":\"Failed to publish info\"}");
  }
}

void publishBackup() {
  JsonDocument out;
  out["reason"] = "backup";
  out["time"] = getIsoLocalTime();

  JsonObject settings = out["settings"].to<JsonObject>();
  settings["rainMode"] = rainMode;
  JsonArray namesArray = settings["sectorNames"].to<JsonArray>();
  for (uint8_t i = 0; i < 8; i++) {
    namesArray.add(sectorNames[i]);
  }

  JsonArray programArray = out["programs"].to<JsonArray>();
  for (size_t i = 0; i < programCount; i++) {
    JsonObject p = programArray.add<JsonObject>();
    p["id"] = programs[i].id;
    p["startHour"] = programs[i].startHour;
    p["startMinute"] = programs[i].startMinute;
    p["enabled"] = programs[i].enabled;

    JsonArray seq = p["sequence"].to<JsonArray>();
    for (uint8_t s = 0; s < programs[i].stepCount; s++) {
      JsonObject st = seq.add<JsonObject>();
      st["relay"] = programs[i].steps[s].relay + 1;
      st["duration"] = programs[i].steps[s].durationMin;
    }
  }

  String payload;
  serializeJson(out, payload);
  if (payload.length() == 0) {
    publishLog("{\"error\":\"Empty backup payload\"}");
    return;
  }

  if (payload.length() > (MQTT_BUFFER_SIZE - 128)) {
    publishLog("{\"error\":\"Backup payload too large\"}");
    return;
  }

  if (mqttClient.publish(TOPIC_INFO, payload.c_str())) {
    publishLog("{\"message\":\"Backup published to info topic\"}");
  } else {
    publishLog("{\"error\":\"Failed to publish backup\"}");
  }
}

int findProgramIndexById(const String& id) {
  for (size_t i = 0; i < programCount; i++) {
    if (programs[i].id == id) return (int)i;
  }
  return -1;
}

bool saveSettings() {
  File file = LittleFS.open(SETTINGS_FILE, "w");
  if (!file) {
    publishLog("{\"error\":\"Cannot open settings file for write\"}");
    return false;
  }

  JsonDocument doc;
  doc["rainMode"] = rainMode;
  
  JsonArray names = doc["sectorNames"].to<JsonArray>();
  for (int i = 0; i < 8; i++) {
    names.add(sectorNames[i]);
  }

  if (serializeJson(doc, file) == 0) {
    file.close();
    publishLog("{\"error\":\"Failed to serialize settings\"}");
    return false;
  }

  file.close();
  return true;
}

bool loadSettings() {
  rainMode = false;

  if (!LittleFS.exists(SETTINGS_FILE)) {
    return saveSettings();
  }

  File file = LittleFS.open(SETTINGS_FILE, "r");
  if (!file) {
    publishLog("{\"error\":\"Cannot open settings file for read\"}");
    return false;
  }

  JsonDocument doc;
  DeserializationError err = deserializeJson(doc, file);
  file.close();
  if (err) {
    publishLog("{\"error\":\"Invalid settings JSON\"}");
    return false;
  }

  rainMode = doc["rainMode"] | false;
  
  if (doc["sectorNames"].is<JsonArray>()) {
    JsonArray names = doc["sectorNames"].as<JsonArray>();
    for (size_t i = 0; i < 8 && i < names.size(); i++) {
        sectorNames[i] = names[i].as<String>();
    }
  }

  return true;
}

bool savePrograms() {
  File file = LittleFS.open(PROGRAM_FILE, "w");
  if (!file) {
    publishLog("{\"error\":\"Cannot open programs file for write\"}");
    return false;
  }

  JsonDocument doc;
  JsonArray arr = doc["programs"].to<JsonArray>();
  for (size_t i = 0; i < programCount; i++) {
    JsonObject p = arr.add<JsonObject>();
    p["id"] = programs[i].id;
    p["startHour"] = programs[i].startHour;
    p["startMinute"] = programs[i].startMinute;
    p["enabled"] = programs[i].enabled;

    JsonArray seq = p["sequence"].to<JsonArray>();
    for (uint8_t s = 0; s < programs[i].stepCount; s++) {
      JsonObject st = seq.add<JsonObject>();
      st["relay"] = programs[i].steps[s].relay + 1;
      st["duration"] = programs[i].steps[s].durationMin;
    }
  }

  if (serializeJson(doc, file) == 0) {
    file.close();
    publishLog("{\"error\":\"Failed to serialize programs\"}");
    return false;
  }

  file.close();
  return true;
}

bool loadPrograms() {
  programCount = 0;

  if (!LittleFS.exists(PROGRAM_FILE)) {
    savePrograms();
    return true;
  }

  File file = LittleFS.open(PROGRAM_FILE, "r");
  if (!file) {
    publishLog("{\"error\":\"Cannot open programs file for read\"}");
    return false;
  }

  JsonDocument doc;
  DeserializationError err = deserializeJson(doc, file);
  file.close();
  if (err) {
    publishLog("{\"error\":\"Invalid programs JSON\"}");
    return false;
  }

  JsonArray arr = doc["programs"].as<JsonArray>();
  for (JsonObject p : arr) {
    if (programCount >= MAX_PROGRAMS) break;

    ProgramItem item;
    item.id = String((const char*)(p["id"] | ""));
    item.startHour = p["startHour"] | 0;
    item.startMinute = p["startMinute"] | 0;
    item.enabled = p["enabled"] | true;
    item.stepCount = 0;
    item.lastRunDay = -1;

    if (item.id.length() == 0) {
      item.id = String("program_") + String(programCount + 1);
    }

    if (item.startHour > 23 || item.startMinute > 59) {
      continue;
    }

    JsonArray seq = p["sequence"].as<JsonArray>();
    for (JsonObject st : seq) {
      if (item.stepCount >= MAX_STEPS_PER_PROGRAM) break;
      int relayNumber = st["relay"] | -1;
      int duration = st["duration"] | -1;
      int relayIndex = (relayNumber >= 1 && relayNumber <= 8) ? (relayNumber - 1) : relayNumber;

      if (relayIndex < 0 || relayIndex > 7 || duration <= 0) continue;
      item.steps[item.stepCount].relay = (uint8_t)relayIndex;
      item.steps[item.stepCount].durationMin = (uint16_t)duration;
      item.stepCount++;
    }

    if (item.stepCount == 0) continue;
    programs[programCount] = item;
    programCount++;
  }

  return true;
}

bool upsertProgram(const JsonObjectConst& src) {
  String id = String((const char*)(src["id"] | ""));
  JsonObjectConst startObj = src["start"].as<JsonObjectConst>();
  JsonArrayConst seq = src["sequence"].as<JsonArrayConst>();

  int startHour = startObj["hour"] | -1;
  int startMinute = startObj["minute"] | -1;
  bool enabled = src["enabled"] | true;

  if (id.length() == 0 || startHour < 0 || startHour > 23 || startMinute < 0 || startMinute > 59 || seq.isNull()) {
    publishLog("{\"error\":\"Invalid program payload\"}");
    return false;
  }

  ProgramItem candidate;
  candidate.id = id;
  candidate.startHour = (uint8_t)startHour;
  candidate.startMinute = (uint8_t)startMinute;
  candidate.enabled = enabled;
  candidate.stepCount = 0;
  candidate.lastRunDay = -1;

  for (JsonObjectConst st : seq) {
    if (candidate.stepCount >= MAX_STEPS_PER_PROGRAM) break;
    int relayNumber = st["relay"] | -1;
    int duration = st["duration"] | -1;
    int relayIndex = (relayNumber >= 1 && relayNumber <= 8) ? (relayNumber - 1) : relayNumber;

    if (relayIndex < 0 || relayIndex > 7 || duration <= 0) {
      publishLog("{\"error\":\"Invalid sequence step\"}");
      return false;
    }

    candidate.steps[candidate.stepCount].relay = (uint8_t)relayIndex;
    candidate.steps[candidate.stepCount].durationMin = (uint16_t)duration;
    candidate.stepCount++;
  }

  if (candidate.stepCount == 0) {
    publishLog("{\"error\":\"Program sequence is empty\"}");
    return false;
  }

  int index = findProgramIndexById(id);
  if (index < 0) {
    if (programCount >= MAX_PROGRAMS) {
      publishLog("{\"error\":\"Program list full\"}");
      return false;
    }
    index = (int)programCount;
    programCount++;
  }

  programs[index] = candidate;
  if (!savePrograms()) return false;
  publishLog("{\"message\":\"Program saved\"}");
  return true;
}

bool setProgramEnabled(const String& id, bool enabled) {
  int index = findProgramIndexById(id);
  if (index < 0) {
    publishLog("{\"error\":\"Program id not found\"}");
    return false;
  }

  programs[index].enabled = enabled;
  programs[index].lastRunDay = -1;
  if (!savePrograms()) return false;

  publishLog(enabled ? "{\"message\":\"Program enabled\"}" : "{\"message\":\"Program disabled\"}");
  return true;
}

bool deleteProgram(const String& id) {
  int index = findProgramIndexById(id);
  if (index < 0) {
    publishLog("{\"error\":\"Program id not found\"}");
    return false;
  }

  for (size_t i = index; i + 1 < programCount; i++) {
    programs[i] = programs[i + 1];
  }
  programCount--;

  if (!savePrograms()) return false;
  publishLog("{\"message\":\"Program deleted\"}");
  return true;
}

void writePCF() {
  Wire.beginTransmission(pcfAddress);
  Wire.write(relayState);
  Wire.endTransmission();
}

void setRelay(int relay, bool on) {
  if (on)
    relayState &= ~(1 << relay);  // ON
  else
    relayState |= (1 << relay);   // OFF

  writePCF();
}

void stopSequence() {
  if (activeRelayFromSequence >= 0) {
    setRelay(activeRelayFromSequence, false);
  }
  sequenceRunning = false;
  runningProgramIndex = -1;
  runningStepIndex = 0;
  runningStepEndTs = 0;
  activeRelayFromSequence = -1;
  waitingBetweenSteps = false;
  transitionPauseUntilTs = 0;
}

bool restoreBackup(const JsonObjectConst& doc) {
  if (sequenceRunning) {
    stopSequence();
  }

  // 1. Process Settings if present
  bool settingsFound = false;
  JsonObjectConst settingsObj = doc["settings"].as<JsonObjectConst>();
  if (!settingsObj.isNull()) {
    if (settingsObj["rainMode"].is<bool>()) {
      rainMode = settingsObj["rainMode"].as<bool>();
      settingsFound = true;
    }
    if (settingsObj["sectorNames"].is<JsonArrayConst>()) {
      JsonArrayConst names = settingsObj["sectorNames"].as<JsonArrayConst>();
      for (size_t i = 0; i < 8 && i < names.size(); i++) {
        sectorNames[i] = names[i].as<String>();
      }
      settingsFound = true;
    }
  } else {
    if (doc["rainMode"].is<bool>()) {
      rainMode = doc["rainMode"].as<bool>();
      settingsFound = true;
    }
    if (doc["sectorNames"].is<JsonArrayConst>()) {
      JsonArrayConst names = doc["sectorNames"].as<JsonArrayConst>();
      for (size_t i = 0; i < 8 && i < names.size(); i++) {
        sectorNames[i] = names[i].as<String>();
      }
      settingsFound = true;
    }
  }

  if (settingsFound) {
    if (!saveSettings()) {
      publishLog("{\"error\":\"Failed to save restored settings\"}");
      return false;
    }
  }

  // 2. Process Programs if present
  if (doc["programs"].is<JsonArrayConst>()) {
    JsonArrayConst arr = doc["programs"].as<JsonArrayConst>();
    programCount = 0;

    for (JsonObjectConst p : arr) {
      if (programCount >= MAX_PROGRAMS) break;

      ProgramItem item;
      item.id = String((const char*)(p["id"] | ""));
      if (p["start"].is<JsonObjectConst>()) {
        JsonObjectConst stObj = p["start"].as<JsonObjectConst>();
        item.startHour = stObj["hour"] | 0;
        item.startMinute = stObj["minute"] | 0;
      } else {
        item.startHour = p["startHour"] | 0;
        item.startMinute = p["startMinute"] | 0;
      }

      item.enabled = p["enabled"] | true;
      item.stepCount = 0;
      item.lastRunDay = -1;

      if (item.id.length() == 0) {
        item.id = String("program_") + String(programCount + 1);
      }
      if (item.startHour > 23 || item.startMinute > 59) continue;

      JsonArrayConst seq = p["sequence"].as<JsonArrayConst>();
      if (!seq.isNull()) {
        for (JsonObjectConst st : seq) {
          if (item.stepCount >= MAX_STEPS_PER_PROGRAM) break;
          int relayNumber = st["relay"] | -1;
          int duration = st["duration"] | -1;
          int relayIndex = (relayNumber >= 1 && relayNumber <= 8) ? (relayNumber - 1) : relayNumber;

          if (relayIndex < 0 || relayIndex > 7 || duration <= 0) continue;
          item.steps[item.stepCount].relay = (uint8_t)relayIndex;
          item.steps[item.stepCount].durationMin = (uint16_t)duration;
          item.stepCount++;
        }
      }

      if (item.stepCount == 0) continue;
      programs[programCount] = item;
      programCount++;
    }

    if (!savePrograms()) {
      publishLog("{\"error\":\"Failed to save restored programs\"}");
      return false;
    }
  }

  publishLog("{\"message\":\"Backup restored successfully\"}");
  return true;
}

void startProgramSequence(int index, time_t now, bool forceStart = false) {
  if (rainMode && !forceStart) return;
  if (index < 0 || index >= (int)programCount) return;
  if (programs[index].stepCount == 0) return;

  ProgramStep step = programs[index].steps[0];
  setRelay(step.relay, true);

  sequenceRunning = true;
  runningProgramIndex = index;
  runningStepIndex = 0;
  runningStepEndTs = now + (time_t)step.durationMin * 60;
  activeRelayFromSequence = step.relay;

  publishLog("{\"message\":\"Program started\"}");
  publishInfo("program_started");
}

void advanceSequence(time_t now) {
  if (!sequenceRunning || runningProgramIndex < 0) return;

  // Handle pause between steps
  if (waitingBetweenSteps) {
    if (now < transitionPauseUntilTs) return;  // Still waiting

    // Pause finished, activate next step
    waitingBetweenSteps = false;
    ProgramItem& p = programs[runningProgramIndex];

    if (runningStepIndex >= p.stepCount) {
      stopSequence();
      publishLog("{\"message\":\"Program completed\"}");
      publishInfo("program_completed");
      return;
    }

    ProgramStep step = p.steps[runningStepIndex];
    setRelay(step.relay, true);
    activeRelayFromSequence = step.relay;
    runningStepEndTs = now + (time_t)step.durationMin * 60;
    publishInfo("program_step");
    return;
  }

  // Check if current step duration is done
  if (now < runningStepEndTs) return;

  // Current step done, turn off relay and enter pause
  if (activeRelayFromSequence >= 0) {
    setRelay(activeRelayFromSequence, false);
  }

  runningStepIndex++;

  // Start pause before next step
  waitingBetweenSteps = true;
  transitionPauseUntilTs = now + STEP_TRANSITION_DELAY_SEC;
  publishInfo("step_pause");
}

void processPrograms() {
  if (millis() - lastProgramCheckMs < 1000) return;
  lastProgramCheckMs = millis();

  time_t now = time(nullptr);
  if (now < 100000) return;

  if (rainMode) return;

  if (sequenceRunning) {
    advanceSequence(now);
    return;
  }

  struct tm localTm;
  localtime_r(&now, &localTm);
  int day = localTm.tm_yday;

  for (size_t i = 0; i < programCount; i++) {
    if (!programs[i].enabled) continue;
    if (programs[i].startHour != localTm.tm_hour || programs[i].startMinute != localTm.tm_min) continue;
    if (programs[i].lastRunDay == day) continue;

    programs[i].lastRunDay = day;
    startProgramSequence((int)i, now, false);
    break;
  }
}

bool startProgramById(const String& id, bool forceStart = true) {
  int index = findProgramIndexById(id);
  if (index < 0) {
    publishLog("{\"error\":\"Program id not found\"}");
    return false;
  }

  if (sequenceRunning) {
    stopSequence();
  }

  time_t now = time(nullptr);
  if (now < 100000) {
    publishLog("{\"error\":\"Clock not synced\"}");
    return false;
  }

  startProgramSequence(index, now, forceStart);
  if (!sequenceRunning) {
    publishLog("{\"error\":\"Failed to start program\"}");
    return false;
  }

  publishLog("{\"message\":\"Program test started\"}");
  publishInfo("program_test_started");
  return true;
}

void onMqttMessage(char* topic, byte* payload, unsigned int length) {
  if (String(topic) != TOPIC_COMMAND) return;

  JsonDocument doc;
  DeserializationError err = deserializeJson(doc, payload, length);
  if (err) {
    mqttClient.publish(TOPIC_LOG, "{\"error\": \"Invalid JSON format\"}");
    return;
  }

  String action = String((const char*)(doc["action"] | ""));
  action.trim();

  if (action == "get_info") {
    publishInfo("on_demand");
    return;
  }

  if (action == "get_backup") {
    publishBackup();
    return;
  }

  if (action == "restore_backup") {
    if (restoreBackup(doc.as<JsonObjectConst>())) {
      publishInfo("backup_restored");
    }
    return;
  }

  if (action == "set_program") {
    if (upsertProgram(doc.as<JsonObjectConst>())) {
      publishInfo("program_saved");
    }
    return;
  }

  if (action == "disable_program" || action == "enable_program") {
    String id = String((const char*)(doc["id"] | ""));
    bool enabled = (action == "enable_program");
    if (setProgramEnabled(id, enabled)) {
      publishInfo("program_toggled");
    }
    return;
  }

  if (action == "delete_program") {
    String id = String((const char*)(doc["id"] | ""));
    if (deleteProgram(id)) {
      publishInfo("program_deleted");
    }
    return;
  }

  if (action == "stop_program") {
    stopSequence();
    publishInfo("program_stopped");
    return;
  }

  if (action == "start_program") {
    String id = String((const char*)(doc["id"] | ""));
    if (!startProgramById(id, true)) {
      publishInfo("program_test_failed");
    }
    return;
  }

  if (action == "set_rain_mode") {
    bool enabled = doc["enabled"] | false;
    rainMode = enabled;

    if (rainMode && sequenceRunning) {
      stopSequence();
    }

    if (!saveSettings()) {
      publishLog("{\"error\":\"Failed to save rain mode\"}");
    }

    publishInfo("rain_mode_updated");
    return;
  }

  if (action == "set_sector_names") {
    JsonArray names = doc["names"].as<JsonArray>();
    if (names.size() == 8) {
      for (int i = 0; i < 8; i++) {
        sectorNames[i] = names[i].as<String>();
      }
      if (!saveSettings()) {
        publishLog("{\"error\":\"Failed to save sector names\"}");
      }
      publishInfo("sector_names_updated");
    } else {
      publishLog("{\"error\":\"names array must contain exactly 8 elements\"}");
    }
    return;
  }

  bool active = doc["active"] | false;
  int relayNumber = doc["relay"] | -1;

  int relayIndex = (relayNumber >= 1 && relayNumber <= 8) ? (relayNumber - 1) : relayNumber;
  if (relayIndex < 0 || relayIndex > 7) return;

  if (rainMode && active) {
    publishLog("{\"error\":\"Rain mode active: manual start blocked\"}");
    publishInfo("rain_mode_blocked");
    return;
  }

  if (sequenceRunning) {
    stopSequence();
  }

  if (active) {
    setRelay(relayIndex, true);
    mqttClient.publish(TOPIC_LOG, "{\"message\": \"Relay activated\"}");
  } else {
    setRelay(relayIndex, false);
    mqttClient.publish(TOPIC_LOG, "{\"message\": \"Relay deactivated\"}");
  }

  publishInfo("relay_updated");
}

void handleWiFi() {
  if (WiFi.status() == WL_CONNECTED) return;

  unsigned long now = millis();
  if (now - lastWifiAttemptMs >= WIFI_RETRY_INTERVAL_MS) {
    lastWifiAttemptMs = now;
    WiFi.disconnect();
    WiFi.begin(ssid, password);
  }
}

void handleMqtt() {
  if (WiFi.status() != WL_CONNECTED) return;

  if (mqttClient.connected()) {
    mqttClient.loop();
    return;
  }

  unsigned long now = millis();
  if (now - lastMqttAttemptMs >= MQTT_RETRY_INTERVAL_MS) {
    lastMqttAttemptMs = now;

    if (mqttClient.connect(CLIENT_ID, MQTT_USER, MQTT_PASS, TOPIC_LOG, 1, false, MQTT_LWT_MESSAGE)) {
      mqttClient.subscribe(TOPIC_COMMAND);

      if (mqttWasConnected) {
        mqttClient.publish(TOPIC_LOG, "{\"message\": \"Reconnected after disconnection\"}");
        publishInfo("reconnected");
      } else {
        publishInfo("connected");
      }
      mqttWasConnected = true;
    }
  }
}

void setup() {
  Wire.begin();
  writePCF(); // Spegne immediatamente tutti i relay all'avvio per massima sicurezza hardware

  LittleFS.begin();
  loadSettings();
  loadPrograms();

  WiFi.mode(WIFI_STA);
  WiFi.setAutoReconnect(true);
  WiFi.begin(ssid, password);

  // Tentativo di connessione iniziale con timeout breve di 8 secondi, per non bloccare lo startup se offline
  unsigned long startWifiMs = millis();
  while (WiFi.status() != WL_CONNECTED && (millis() - startWifiMs < 8000)) {
    delay(100);
  }

  configTime(TZ_OFFSET_SECONDS, DST_OFFSET_SECONDS, NTP_SERVER_1, NTP_SERVER_2);

  secureClient.setInsecure(); // TLS without certificate validation
  mqttClient.setServer(MQTT_HOST, MQTT_PORT);
  mqttClient.setBufferSize(MQTT_BUFFER_SIZE);
  mqttClient.setCallback(onMqttMessage);

  if (WiFi.status() == WL_CONNECTED) {
    if (mqttClient.connect(CLIENT_ID, MQTT_USER, MQTT_PASS, TOPIC_LOG, 1, false, MQTT_LWT_MESSAGE)) {
      mqttClient.subscribe(TOPIC_COMMAND);
      mqttWasConnected = true;
      publishInfo("connected");
    }
  }
}

void loop() {
  handleWiFi();
  handleMqtt();
  processPrograms();
}