// Alimentation : tension, veille profonde et réveil par mouvement
#pragma once
#include <Arduino.h>

bool powerWokeFromMotion();  // à appeler une fois au démarrage
float powerReadVdd();        // tension d'alimentation du nRF52840, en volts
bool powerOnUsb();           // câble USB branché (tension VBUS présente)

// Arme le réveil par mouvement du BMI270 puis éteint le nRF52840.
// Le réveil redémarre la carte : on repart de setup().
[[noreturn]] void powerSleep();
