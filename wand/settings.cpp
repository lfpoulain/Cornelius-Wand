#include "settings.h"
#include "FlashIAP.h"
#include "config.h"

// Bornes, dans l'ordre des champs de Settings (même table dans l'intégration : remote.py)
const SettingLimits SETTINGS_LIMITS[SETTINGS_COUNT] = {
    {0.3f, 0.99f},     // spellThreshold
    {0.05f, 0.9f},     // prepareThreshold
    {0, 1000},         // spellEndStillMs
    {3, 60},           // stillGyroDps
    {50, 1000},        // stillMinMs
    {5, 120},          // aimFallbackDps
    {500, 2200},       // aimMaxGestureMs : l'âge demandé à la caméra vaut au plus
                       // aimMaxGestureMs + spellEndStillMs + 150 ms (2,25 s par défaut, 3,35 s
                       // aux bornes) ; elle garde au moins 2,6 s de photos (PHOTO_KEEP_MS de
                       // camera/config.h), et Home Assistant refuse une photo trop loin de
                       // l'instant visé
    {5, 600},          // sleepAfterS
    {20, 500},         // wakeThresholdMg
    {0, 1},            // ignoreUnaimed
};

static const Settings DEFAULTS = {
    SPELL_THRESHOLD,      PREPARE_THRESHOLD,  SPELL_END_STILL_MS,
    STILL_GYRO_DPS,       STILL_MIN_MS,       AIM_FALLBACK_DPS,
    AIM_MAX_GESTURE_MS,   SLEEP_AFTER_MS / 1000.0f, WAKE_THRESHOLD_MG,
    IGNORE_UNAIMED,
};

Settings settings = DEFAULTS;

// Enregistrés dans l'avant-dernier secteur de la flash (le dernier garde la calibration)
#define SETTINGS_MAGIC 0x53455454UL  // "SETT"
#define SETTINGS_VERSION 2
#define SETTINGS_V1_COUNT 9  // version 1 (firmwares 3 et 4) : sans ignoreUnaimed

struct __attribute__((aligned(4))) Stored {
  uint32_t magic;
  uint32_t version;
  Settings values;
  uint32_t crc;
};

// Version 1 : les mêmes réglages, moins le dernier ; relue pour ne pas perdre les réglages
struct __attribute__((aligned(4))) StoredV1 {
  uint32_t magic;
  uint32_t version;
  float values[SETTINGS_V1_COUNT];
  uint32_t crc;
};

static uint32_t crcOfBytes(const void* data, size_t len) {
  const uint8_t* p = (const uint8_t*)data;
  uint32_t h = 2166136261UL;
  for (size_t i = 0; i < len; i++) h = (h ^ p[i]) * 16777619UL;
  return h;
}

static uint32_t crcOf(const Stored& s) { return crcOfBytes(&s, offsetof(Stored, crc)); }

static uint32_t sectorAddress(mbed::FlashIAP& flash) {
  uint32_t end = flash.get_flash_start() + flash.get_flash_size();
  uint32_t last = end - flash.get_sector_size(end - 1);
  return last - flash.get_sector_size(last - 1);
}

float* settingsField(int i) { return &((float*)&settings)[i]; }

bool settingsSet(int i, float value) {
  if (i < 0 || i >= SETTINGS_COUNT || isnan(value)) return false;
  value = constrain(value, SETTINGS_LIMITS[i].min, SETTINGS_LIMITS[i].max);
  float* field = settingsField(i);
  if (*field == value) return false;
  *field = value;
  return true;
}

void settingsReset() { settings = DEFAULTS; }

void settingsBegin() {
  settings = DEFAULTS;
  mbed::FlashIAP flash;
  if (flash.init() != 0) return;
  Stored s;
  StoredV1 v1;
  uint32_t addr = sectorAddress(flash);
  flash.read(&s, addr, sizeof(s));
  flash.read(&v1, addr, sizeof(v1));
  flash.deinit();
  // Chaque valeur repasse par ses bornes : une flash abîmée ne peut pas dérégler la baguette
  if (s.magic == SETTINGS_MAGIC && s.version == SETTINGS_VERSION && s.crc == crcOf(s)) {
    for (int i = 0; i < SETTINGS_COUNT; i++) settingsSet(i, ((float*)&s.values)[i]);
  } else if (v1.magic == SETTINGS_MAGIC && v1.version == 1 &&
             v1.crc == crcOfBytes(&v1, offsetof(StoredV1, crc))) {
    // Réglages d'avant la mise à jour : gardés, les nouveaux prennent leur valeur par défaut
    for (int i = 0; i < SETTINGS_V1_COUNT; i++) settingsSet(i, v1.values[i]);
  }
}

bool settingsSave() {
  Stored s = {SETTINGS_MAGIC, SETTINGS_VERSION, settings, 0};
  s.crc = crcOf(s);
  mbed::FlashIAP flash;
  if (flash.init() != 0) return false;
  uint32_t addr = sectorAddress(flash);
  uint32_t page = flash.get_page_size();
  static uint8_t buf[256];
  memset(buf, 0xFF, sizeof(buf));
  memcpy(buf, &s, sizeof(s));
  uint32_t size = (sizeof(Stored) + page - 1) / page * page;
  bool ok = flash.erase(addr, flash.get_sector_size(addr)) == 0 && flash.program(buf, addr, size) == 0;
  flash.deinit();
  return ok;
}
