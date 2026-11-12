// Réglages de la baguette Cornelius - Arduino Nano 33 BLE Rev2
#pragma once

#include <Arduino.h>

// Le nom de la bibliothèque Edge Impulse se règle en haut de gestures.cpp.
//
// Les valeurs marquées [HA] sont les réglages par défaut : elles se changent ensuite depuis
// Home Assistant (appareil Baguette Cornelius), sans USB, et sont gardées en flash.

#define FIRMWARE_VERSION 7  // lu par Home Assistant

// ---------------------------------------------------------------------------
// Sorts : libellé Edge Impulse -> numéro de bouton BTHome (1 à BTHOME_BUTTONS), dans
// l'ordre des noms des sorts des options de l'intégration.
// ---------------------------------------------------------------------------
struct SpellDef {
  const char* label;
  uint8_t button;
};

static const SpellDef SPELLS[] = {
  {"Alohomora", 1},
};

#define IDLE_LABEL "Repos"

// Reconnaissance
#define SPELL_THRESHOLD      0.60f  // [HA] confiance minimale
#define SPELL_CONFIRMATIONS  2      // inférences consécutives d'accord
#define SPELL_COOLDOWN_MS    1500   // pause après le lancement d'un sort (au moins la fenêtre du modèle) ;
                                    // la préparation se réarme à sa fin
#define INFERENCE_STRIDE_MS  100    // une inférence toutes les 100 ms
#define SPELL_END_STILL_MS   100    // [HA] le sort part quand la baguette s'arrête aussi longtemps
#define SPELL_END_MAX_MS     1500   // ... ou au plus tard après ce délai
// Sort reconnu sans visée (pas de pause avant le geste) : souvent le mouvement qui lève la
// baguette vers la cible. Ignoré (1) : rien n'est envoyé, la LED clignote brièvement en
// rouge, la visée reste au sort suivant, et ce mouvement n'est pas reconnu une seconde fois
// (pause le temps qu'il sorte de la fenêtre du modèle, gestures.cpp) ; envoyé sans cible (0).
#define IGNORE_UNAIMED       1      // [HA]

// Préparation : dès que le score d'un sort dépasse ce seuil, la baguette prévient Home
// Assistant, qui analyse la photo pendant la fin du geste. La lampe réagit ainsi dès la
// fin du sort, sans attendre la vision.
#define PREPARE_THRESHOLD    0.30f  // [HA]

// Visée : une courte pause avant le geste donne l'inclinaison, et l'instant de la photo
#define STILL_GYRO_DPS   15.0f  // [HA] vitesse de rotation sous laquelle la baguette est immobile
#define STILL_MIN_MS     200    // [HA] durée minimale d'une pause de visée (après le dernier sort)
#define AIM_PAUSE_MAX_MS 3000   // au-delà, sans le moindre tremblement, la baguette était posée
#define REST_PITCH_RANGE_DEG 0.5f // posée, son inclinaison varie moins que ça ; tenue à la main,
                                  // elle dérive de plus d'un degré en quelques secondes
#define AIM_PITCH_JUMP_DEG 60.0f // visée et fin du geste plus éloignées : la pause était un repos
                                 // (baguette pendante), le geste la levait vers la cible
#define AIM_MAX_GESTURE_MS 2000 // [HA] durée maximale entre la fin de l'immobilité et la fin du geste :
                                // au-delà, la baguette a bougé avant le sort et la visée est ignorée
#define AIM_FALLBACK_DPS 40.0f  // [HA] sans pause assez longue, on prend le moment le plus calme juste
                                // avant le geste, s'il reste sous cette vitesse de rotation
#define AIM_PHOTO_MARGIN_MS 150 // même valeur que PHOTO_MARGIN_MS dans wand.py

// Veille
#define SLEEP_AFTER_MS     30000  // [HA] immobile depuis 30 s -> veille profonde (pas entre deux sorts)
#define WAKE_THRESHOLD_MG  60     // [HA] seuil de mouvement pour le réveil (mg)
#define WAKE_DURATION      2      // nombre d'échantillons à 50 Hz au-dessus du seuil

// BTHome
#define DEVICE_NAME       "Cornelius"
#define BTHOME_BUTTONS    4     // 4 sorts au plus (MAX_SPELLS dans l'intégration)
#define BTHOME_BURST_MS   1000  // durée d'émission d'un sort
#define BTHOME_PREPARE_MS 400   // durée d'émission d'une préparation (le sort suit vite)
#define BTHOME_AIM_MS     300   // durée d'émission d'une visée (fin de pause, le geste commence)
#define BTHOME_BEACON_INTERVAL 800  // annonce de présence entre deux sorts (800 = 500 ms)
#define BTHOME_INTERVAL   48    // intervalle d'annonce en 0,625 ms (48 = 30 ms)
#define BTHOME_REFRESH_MS 60    // pendant la rafale, l'âge de la visée est remis à jour à ce rythme
#define BTHOME_NO_AIM_PITCH -180.0f  // inclinaison envoyée quand la visée est inconnue

// Retour haptique : broche d'un moteur vibreur (via transistor), -1 si absent
#define VIBRATION_PIN D2
