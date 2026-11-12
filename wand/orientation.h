// Inclinaison de la baguette : angle entre son axe de visée et l'horizontale, mesuré par
// l'accéléromètre. La direction à plat vient de la caméra au plafond (Home Assistant).
#pragma once
#include <Arduino.h>

struct Vec3 {
  float x, y, z;
};

// Repère du BMI270 (direct). La bibliothèque Arduino renvoie un repère permuté ;
// cette fonction convertit ses valeurs vers le repère du capteur.
Vec3 toSensorFrame(float libX, float libY, float libZ);

bool orientationBegin();  // charge l'axe de visée depuis la flash
bool orientationCalibrated();

// Inclinaison (-90 à 90°) d'après l'accélération moyenne au repos, dans le repère capteur
bool orientationPitch(const Vec3& acc, float& pitch);

// Résultat de la calibration, aussi lu par Home Assistant (remote.cpp)
enum CalResult : uint8_t {
  CAL_NONE = 0,         // pas encore lancée depuis le démarrage
  CAL_OK = 1,
  CAL_NOT_STILL = 2,    // la baguette n'a pas été levée puis tenue immobile vers le plafond
  CAL_FLASH_ERROR = 3,
};

// Calibration de l'axe de visée : la baguette est levée vers le plafond, puis immobile (bloquante).
// holding : appelée au début, pour guider avec la LED ; messages sur out.
CalResult orientationCalibrate(Stream& out, void (*holding)() = nullptr);
CalResult orientationLastResult();
void orientationPrintInfo(Stream& out);
