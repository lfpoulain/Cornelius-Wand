#include "remote.h"
#include <ArduinoBLE.h>
#include "config.h"
#include "orientation.h"
#include "power.h"
#include "settings.h"

#define REMOTE_UUID(n) "c0e1" n "-1d6b-4e7b-9c3e-436f726e656c"

// État lu par Home Assistant (remote.py : INFO_FORMAT)
struct __attribute__((packed)) RemoteInfo {
  uint16_t firmware;   // FIRMWARE_VERSION
  uint8_t calibrated;  // axe de visée présent en flash
  uint8_t calResult;   // CalResult de la dernière calibration depuis le démarrage
  float voltage;       // tension de la pile, en V, mesurée au démarrage et après une calibration
                       // (Home Assistant lit plutôt celle des annonces BTHome)
};
static_assert(sizeof(RemoteInfo) == 8, "RemoteInfo : 8 octets");

static BLEService service(REMOTE_UUID("0001"));
static BLEFloatCharacteristic values[SETTINGS_COUNT] = {
    BLEFloatCharacteristic(REMOTE_UUID("0010"), BLERead | BLEWrite),
    BLEFloatCharacteristic(REMOTE_UUID("0011"), BLERead | BLEWrite),
    BLEFloatCharacteristic(REMOTE_UUID("0012"), BLERead | BLEWrite),
    BLEFloatCharacteristic(REMOTE_UUID("0013"), BLERead | BLEWrite),
    BLEFloatCharacteristic(REMOTE_UUID("0014"), BLERead | BLEWrite),
    BLEFloatCharacteristic(REMOTE_UUID("0015"), BLERead | BLEWrite),
    BLEFloatCharacteristic(REMOTE_UUID("0016"), BLERead | BLEWrite),
    BLEFloatCharacteristic(REMOTE_UUID("0017"), BLERead | BLEWrite),
    BLEFloatCharacteristic(REMOTE_UUID("0018"), BLERead | BLEWrite),
    BLEFloatCharacteristic(REMOTE_UUID("0019"), BLERead | BLEWrite),
};
static_assert(sizeof(values) / sizeof(values[0]) == SETTINGS_COUNT, "un réglage par caractéristique");
static BLEByteCharacteristic command(REMOTE_UUID("0020"), BLEWrite);
static BLECharacteristic info(REMOTE_UUID("0021"), BLERead, sizeof(RemoteInfo), true);

static bool dirty = false;           // réglages reçus, pas encore en flash
static uint8_t pendingCommand = 0;

static void publishValues() {
  for (int i = 0; i < SETTINGS_COUNT; i++) values[i].writeValue(*settingsField(i));
}

static void onValueWritten(BLEDevice, BLECharacteristic characteristic) {
  for (int i = 0; i < SETTINGS_COUNT; i++) {
    if (strcmp(characteristic.uuid(), values[i].uuid()) != 0) continue;
    if (settingsSet(i, values[i].value())) dirty = true;
    values[i].writeValue(*settingsField(i));  // valeur bornée, relue par Home Assistant
  }
}

static void onCommandWritten(BLEDevice, BLECharacteristic) { pendingCommand = command.value(); }

void remoteRefreshInfo() {
  RemoteInfo state = {FIRMWARE_VERSION, orientationCalibrated(), orientationLastResult(), powerReadVdd()};
  info.writeValue((const uint8_t*)&state, sizeof(state));
}

void remoteBegin() {
  for (int i = 0; i < SETTINGS_COUNT; i++) {
    values[i].setEventHandler(BLEWritten, onValueWritten);
    service.addCharacteristic(values[i]);
  }
  command.setEventHandler(BLEWritten, onCommandWritten);
  service.addCharacteristic(command);
  service.addCharacteristic(info);
  BLE.addService(service);
  publishValues();
  remoteRefreshInfo();
}

uint8_t remotePoll() {
  if (BLE.connected()) return 0;  // la commande attend la fin de la connexion
  if (dirty) {
    dirty = false;
    Serial.println(settingsSave() ? F("Réglages reçus et enregistrés") : F("Erreur : réglages non enregistrés"));
  }
  uint8_t cmd = pendingCommand;
  pendingCommand = 0;
  if (cmd == REMOTE_RESET) {
    settingsReset();
    settingsSave();
    publishValues();
    Serial.println(F("Réglages par défaut"));
    return 0;
  }
  return cmd;
}
