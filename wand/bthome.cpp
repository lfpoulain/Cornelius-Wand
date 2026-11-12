#include "bthome.h"
#include <ArduinoBLE.h>
#include "config.h"
#include "power.h"
#include "remote.h"
#include "settings.h"

#define BTHOME_UUID 0xFCD2
// Version 2, sans chiffrement, émission irrégulière (sur événement)
#define BTHOME_DEVICE_INFO 0x44

// Identifiants d'objets BTHome, à écrire dans l'ordre croissant
#define OBJ_PACKET_ID 0x00
#define OBJ_VOLTAGE   0x0C  // uint16, 0,001 V
#define OBJ_BUTTON    0x3A  // événement de bouton
#define OBJ_ROTATION  0x3F  // sint16, 0,1° -> inclinaison
#define OBJ_DURATION  0x42  // uint24, 0,001 s -> âge de la visée

#define BUTTON_NONE  0x00
#define BUTTON_PRESS 0x01
#define BUTTON_HOLD  0x80  // « hold_press » : sort en préparation

static bool started = false;
static bool advertising = false;
static bool burst = false;  // rafale d'un sort, d'une préparation ou d'une visée en cours
static uint32_t burstEnd = 0;

// Annonce de la rafale en cours (button 0 : visée seule). Avec une visée, elle est réémise
// toutes les BTHOME_REFRESH_MS avec le même numéro de paquet, pour que l'âge de la visée
// reste juste quelle que soit l'annonce reçue ; sans visée, ses octets ne changent pas et
// elle n'est pas réémise.
static struct {
  uint8_t packetId, button, event;
  bool hasAim;
  float pitch, voltage;
  uint32_t aimAgeMs;
  uint32_t start, sent;  // millis() au début de la rafale et à la dernière émission
} spell;

// Visée finie pendant la rafale d'un sort : elle part à la fin de la rafale, avec son âge
// à jour (le sort n'est jamais écourté : un proxy Bluetooth peut ne scanner qu'une fraction
// du temps)
static struct {
  bool set;
  float pitch, voltage;
  uint32_t endedAt;  // millis() de la fin de la pause
} pendingAim;

// Le numéro de paquet doit changer à chaque annonce (sort, préparation, visée), y compris
// après un réveil : GPREGRET2 est conservé pendant la veille profonde. L'annonce de présence
// garde le numéro de la dernière annonce, sans bouton ni visée : Home Assistant n'y voit ni
// sort, ni préparation, ni visée, mais y lit la tension et sait que la baguette est
// joignable pour les réglages.
static uint8_t nextPacketId() {
  uint8_t id = (NRF_POWER->GPREGRET2 + 1) & 0xFF;
  NRF_POWER->GPREGRET2 = id;
  return id;
}

static void putU16(uint8_t* p, uint16_t v) {
  p[0] = v & 0xFF;
  p[1] = v >> 8;
}

static void advertise(uint8_t packetId, uint8_t button, uint8_t event, bool hasAim, float pitch,
                      uint32_t aimAgeMs, float voltage, uint16_t interval) {
  uint8_t data[24];
  size_t len = 0;
  data[len++] = BTHOME_DEVICE_INFO;

  data[len++] = OBJ_PACKET_ID;
  data[len++] = packetId;

  data[len++] = OBJ_VOLTAGE;
  putU16(data + len, (uint16_t)constrain(voltage * 1000.0f, 0.0f, 65535.0f));
  len += 2;

  // Un objet bouton par sort, toujours dans le même ordre : seul celui du sort a un événement
  for (uint8_t b = 1; b <= BTHOME_BUTTONS; b++) {
    data[len++] = OBJ_BUTTON;
    data[len++] = (b == button) ? event : BUTTON_NONE;
  }

  if (!hasAim) {
    pitch = BTHOME_NO_AIM_PITCH;
    aimAgeMs = 0;
  }
  data[len++] = OBJ_ROTATION;
  putU16(data + len, (uint16_t)(int16_t)lroundf(pitch * 10.0f));
  len += 2;

  aimAgeMs = min(aimAgeMs, (uint32_t)0xFFFFFF);
  data[len++] = OBJ_DURATION;
  data[len++] = aimAgeMs & 0xFF;
  data[len++] = (aimAgeMs >> 8) & 0xFF;
  data[len++] = (aimAgeMs >> 16) & 0xFF;

  BLEAdvertisingData adv;
  if (!adv.setAdvertisedServiceData(BTHOME_UUID, data, len)) {
    Serial.println(F("Erreur : annonce BTHome trop longue, réduis BTHOME_BUTTONS"));
    return;
  }
  BLEAdvertisingData scan;
  scan.setLocalName(DEVICE_NAME);

  if (advertising) BLE.stopAdvertise();
  BLE.setAdvertisingInterval(interval);
  BLE.setAdvertisingData(adv);
  BLE.setScanResponseData(scan);
  advertising = BLE.advertise() == 1;
}

static void advertiseSpell() {
  uint32_t now = millis();
  advertise(spell.packetId, spell.button, spell.event, spell.hasAim, spell.pitch,
            spell.aimAgeMs + (now - spell.start), spell.voltage, BTHOME_INTERVAL);
  spell.sent = now;
}

static void advertiseBeacon() {
  advertise(NRF_POWER->GPREGRET2 & 0xFF, 0, BUTTON_NONE, false, 0, 0, powerReadVdd(),
            BTHOME_BEACON_INTERVAL);
  burst = false;
}

bool bthomeBegin() {
  if (!BLE.begin()) return false;
  BLE.setLocalName(DEVICE_NAME);
  BLE.setDeviceName(DEVICE_NAME);
  BLE.setConnectable(true);  // Home Assistant s'y connecte pour les réglages
  remoteBegin();
  started = true;
  advertiseBeacon();
  return true;
}

void bthomeEnd() {
  if (!started) return;
  if (advertising) BLE.stopAdvertise();
  BLE.end();
  started = advertising = burst = pendingAim.set = false;
}

bool bthomeSendSpell(uint8_t button, bool preparing, bool hasAim, float pitch, uint32_t aimAgeMs,
                     float voltage) {
  if (!started) return false;
  // Pendant une connexion, la baguette ne peut pas émettre d'annonce. Le sort passe avant
  // les réglages : il coupe la connexion, Home Assistant renverra les réglages plus tard.
  // Une préparation, non : un simple mouvement de la main pendant une calibration demandée
  // couperait l'échange ; elle est retentée (gesturesRetryPrepare) une fois déconnectée.
  if (BLE.connected()) {
    if (preparing) return false;
    BLE.disconnect();
  }
  pendingAim.set = false;  // le sort ou la préparation porte sa propre visée (samplerGetAim)
  spell = {nextPacketId(), button, (uint8_t)(preparing ? BUTTON_HOLD : BUTTON_PRESS), hasAim, pitch,
           voltage, aimAgeMs, millis(), 0};
  advertiseSpell();
  burst = true;
  burstEnd = spell.start + (preparing ? BTHOME_PREPARE_MS : BTHOME_BURST_MS);
  return true;
}

void bthomeSendAim(float pitch, uint32_t aimAgeMs, float voltage) {
  if (!started) return;
  // Une visée remplace une autre visée ou une préparation (la nouvelle pause compte plus) ;
  // un sort, jamais : la visée attend la fin de sa rafale (bthomePoll), et une visée plus
  // récente remplace celle qui attend. Pendant une connexion de Home Assistant, elle attend
  // aussi : la moindre pause de la main tenue en finit une, et la couper à chaque fois
  // empêchait les réglages et la calibration de passer. Sans elle, Home Assistant analyse
  // la photo à la préparation ou au sort.
  uint32_t now = millis();
  if (BLE.connected() || (burst && spell.event == BUTTON_PRESS && (int32_t)(now - burstEnd) < 0)) {
    pendingAim = {true, pitch, voltage, now - aimAgeMs};
    return;
  }
  pendingAim.set = false;
  spell = {nextPacketId(), 0, BUTTON_NONE, true, pitch, voltage, aimAgeMs, now, 0};
  advertiseSpell();
  burst = true;
  burstEnd = spell.start + BTHOME_AIM_MS;
}

void bthomePoll() {
  if (!started) return;
  BLE.poll();
  if (BLE.connected()) {
    advertising = false;  // l'annonce s'arrête pendant une connexion
    return;
  }
  // La visée en attente part à la fin de la rafale du sort ou de la connexion, à la place de
  // la balise, si elle peut encore servir à un sort
  if (pendingAim.set && (int32_t)(millis() - burstEnd) >= 0) {
    uint32_t age = millis() - pendingAim.endedAt;
    if (age <= (uint32_t)settings.aimMaxGestureMs) {
      bthomeSendAim(pendingAim.pitch, age, pendingAim.voltage);
      return;
    }
    pendingAim.set = false;
  }
  // Fin de rafale, ou fin de connexion : retour à l'annonce de présence
  if ((burst && (int32_t)(millis() - burstEnd) >= 0) || !advertising) {
    advertiseBeacon();
  } else if (burst && spell.hasAim && millis() - spell.sent >= BTHOME_REFRESH_MS) {
    advertiseSpell();
  }
}

bool bthomeBusy() { return started && (burst || BLE.connected()); }
