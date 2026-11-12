// Reconnaissance des sorts avec le modèle Edge Impulse
#pragma once
#include <Arduino.h>

bool gesturesBegin(Stream& out);

struct GestureEvent {
  uint8_t preparing;  // bouton du sort qui commence (score au-dessus de PREPARE_THRESHOLD), 0 sinon
  uint8_t cast;       // bouton du sort reconnu, 0 sinon
};

// À appeler en boucle ; chaque préparation n'est signalée qu'une fois par tentative
GestureEvent gesturesPoll(Stream& out);

// Le sort vient de partir : pause de SPELL_COOLDOWN_MS, à la fin de laquelle la
// préparation se réarme
void gesturesStartCooldown();
// Sort reconnu mais ignoré : pause le temps que son mouvement sorte de la fenêtre du
// modèle, ou que son score retombe sous la moitié du seuil de préparation ; le vrai sort
// peut suivre
void gesturesIgnoredSpell();
// Préparation non envoyée (pas encore de visée) : retentée à l'inférence suivante
void gesturesRetryPrepare();

// Affiche les scores de chaque inférence (commande 'd')
void gesturesSetVerbose(bool enabled);
