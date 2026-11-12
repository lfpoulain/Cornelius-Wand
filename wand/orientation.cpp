#include "orientation.h"
#include <Arduino_BMI270_BMM150.h>
#include "FlashIAP.h"

static float dot(const Vec3& a, const Vec3& b) { return a.x * b.x + a.y * b.y + a.z * b.z; }
static float norm(const Vec3& a) { return sqrtf(dot(a, a)); }
static Vec3 normalize(const Vec3& a) {
  float n = norm(a);
  return n > 1e-6f ? Vec3{a.x / n, a.y / n, a.z / n} : Vec3{0, 0, 0};
}

Vec3 toSensorFrame(float libX, float libY, float libZ) {
  // Sur le Nano 33 BLE, la bibliothèque renvoie x = -raw.y et y = -raw.x
  return {-libY, -libX, libZ};
}

// ---------------------------------------------------------------------------
// Axe de visée, stocké dans le dernier secteur de la flash
// ---------------------------------------------------------------------------
#define CAL_MAGIC 0x434F524EUL  // "CORN"
#define CAL_VERSION 2           // format : l'axe de visée seul

struct __attribute__((aligned(4))) CalData {
  uint32_t magic;
  uint32_t version;
  Vec3 forward;  // axe de visée dans le repère capteur, unitaire
  uint32_t crc;
};

static CalData cal;
static bool calValid = false;
static CalResult lastResult = CAL_NONE;

static uint32_t calCrc(const CalData& c) {
  const uint8_t* p = (const uint8_t*)&c;
  uint32_t h = 2166136261UL;
  for (size_t i = 0; i < offsetof(CalData, crc); i++) h = (h ^ p[i]) * 16777619UL;
  return h;
}

static uint32_t calAddress(mbed::FlashIAP& flash) {
  uint32_t end = flash.get_flash_start() + flash.get_flash_size();
  return end - flash.get_sector_size(end - 1);
}

static bool calLoad() {
  mbed::FlashIAP flash;
  if (flash.init() != 0) return false;
  CalData c;
  flash.read(&c, calAddress(flash), sizeof(c));
  flash.deinit();
  if (c.magic != CAL_MAGIC || c.version != CAL_VERSION || c.crc != calCrc(c)) return false;
  cal = c;
  return true;
}

static bool calSave() {
  mbed::FlashIAP flash;
  if (flash.init() != 0) return false;
  uint32_t addr = calAddress(flash);
  uint32_t page = flash.get_page_size();
  uint32_t size = (sizeof(CalData) + page - 1) / page * page;
  static uint8_t buf[256];
  memset(buf, 0xFF, sizeof(buf));
  memcpy(buf, &cal, sizeof(cal));
  bool ok = flash.erase(addr, flash.get_sector_size(addr)) == 0 && flash.program(buf, addr, size) == 0;
  flash.deinit();
  return ok;
}

bool orientationBegin() {
  calValid = calLoad();
  return calValid;
}

bool orientationCalibrated() { return calValid; }

bool orientationPitch(const Vec3& acc, float& pitch) {
  if (!calValid || norm(acc) < 0.5f) return false;
  // Au repos, l'accéléromètre mesure le haut : l'inclinaison est l'angle entre l'axe
  // de visée et l'horizontale
  pitch = asinf(constrain(dot(cal.forward, normalize(acc)), -1.0f, 1.0f)) * RAD_TO_DEG;
  return true;
}

// ---------------------------------------------------------------------------
// Calibration
// ---------------------------------------------------------------------------
// Attend que la baguette soit levée puis reste immobile holdMs, et renvoie l'accélération
// moyenne. L'immobilité de départ ne compte pas : posée sur la table, l'axe serait faux.
static bool waitStill(Vec3& accAvg, uint32_t holdMs, uint32_t timeoutMs) {
  uint32_t start = millis(), stillSince = millis();
  Vec3 sum = {0, 0, 0};
  int n = 0;
  bool moved = false;  // la baguette a été levée depuis le passage au bleu
  while (millis() - start < timeoutMs) {
    float x, y, z;
    IMU.readGyroscope(x, y, z);
    Vec3 gyr = toSensorFrame(x, y, z);
    IMU.readAcceleration(x, y, z);
    Vec3 acc = toSensorFrame(x, y, z);
    float w = norm(gyr);
    if (w > 30.0f) moved = true;
    if (!moved || w > 5.0f) {
      stillSince = millis();
      sum = {0, 0, 0};
      n = 0;
    } else {
      sum = {sum.x + acc.x, sum.y + acc.y, sum.z + acc.z};
      n++;
      if (millis() - stillSince >= holdMs) {
        accAvg = {sum.x / n, sum.y / n, sum.z / n};
        return true;
      }
    }
    delay(10);
  }
  return false;
}

CalResult orientationCalibrate(Stream& out, void (*holding)()) {
  out.println(F("Calibration : lève la baguette vers le PLAFOND puis ne bouge plus..."));
  if (holding) holding();
  Vec3 accUp;
  if (!waitStill(accUp, 1500, 30000)) {
    out.println(F("Abandon : la baguette n'a pas été levée puis tenue immobile."));
    return lastResult = CAL_NOT_STILL;
  }
  cal = {CAL_MAGIC, CAL_VERSION, normalize(accUp), 0};
  cal.crc = calCrc(cal);
  if (!calSave()) {
    calValid = calLoad();  // comme au prochain réveil : l'ancien axe, s'il est resté en flash
    out.println(F("Erreur : impossible d'écrire en flash."));
    return lastResult = CAL_FLASH_ERROR;
  }
  calValid = true;
  out.println(F("Axe de visée enregistré. Tape 'o' pour afficher l'inclinaison en direct."));
  return lastResult = CAL_OK;
}

CalResult orientationLastResult() { return lastResult; }

void orientationPrintInfo(Stream& out) {
  if (!calValid) {
    out.println(F("Axe de visée pas calibré : tape 'c', ou bouton Calibrer dans Home Assistant."));
    return;
  }
  out.print(F("Axe de visée : "));
  out.print(cal.forward.x, 2); out.print(' ');
  out.print(cal.forward.y, 2); out.print(' ');
  out.println(cal.forward.z, 2);
}
