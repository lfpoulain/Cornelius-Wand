// Réglages modifiables depuis Home Assistant (service Bluetooth de remote.cpp).
// Les valeurs par défaut sont celles de config.h ; les réglages reçus sont gardés en flash.
#pragma once
#include <Arduino.h>

struct Settings {
  float spellThreshold;    // confiance minimale d'un sort (0 à 1)
  float prepareThreshold;  // score qui déclenche l'annonce « préparation » (0 à 1)
  float spellEndStillMs;   // immobilité qui marque la fin du geste
  float stillGyroDps;      // vitesse de rotation sous laquelle la baguette est immobile
  float stillMinMs;        // durée minimale d'une pause de visée
  float aimFallbackDps;    // sans pause, moment le plus calme retenu s'il reste sous cette vitesse
  float aimMaxGestureMs;   // âge maximal de la visée à la fin du geste
  float sleepAfterS;       // veille après cette durée d'immobilité
  float wakeThresholdMg;   // mouvement qui réveille la baguette
  float ignoreUnaimed;     // 1 : un sort reconnu sans visée est ignoré, 0 : il part sans cible
};

// Bornes de chaque réglage, dans l'ordre des champs de Settings
struct SettingLimits {
  float min, max;
};
#define SETTINGS_COUNT 10
extern const SettingLimits SETTINGS_LIMITS[SETTINGS_COUNT];

extern Settings settings;

void settingsBegin();          // charge la flash, ou les valeurs par défaut
float* settingsField(int i);   // i-ème réglage (ordre des champs)
bool settingsSet(int i, float value);  // borne la valeur ; renvoie true si elle a changé
bool settingsSave();
void settingsReset();          // valeurs par défaut (sans les enregistrer)
