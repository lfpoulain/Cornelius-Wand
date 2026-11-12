// Annonces BLE au format BTHome v2 (sans chiffrement), connectables pour les réglages
#pragma once
#include <Arduino.h>

bool bthomeBegin();  // démarre le Bluetooth et le service de réglages (remote.cpp)
void bthomeEnd();

// button : 1 à BTHOME_BUTTONS. Sans visée, l'inclinaison vaut BTHOME_NO_AIM_PITCH.
// preparing : le sort commence (événement « hold_press »), sinon il est lancé (« press »).
// aimAgeMs : depuis combien de temps la pause de visée est finie (photo de ce moment-là).
// L'annonce est émise en rafale rapide (avec une visée, son âge est remis à jour toutes les
// BTHOME_REFRESH_MS), puis la baguette revient à l'annonce de présence.
// Un sort coupe une connexion de Home Assistant (il réessaie plus tard) ; une préparation
// n'est pas envoyée pendant une connexion : renvoie faux, à retenter.
bool bthomeSendSpell(uint8_t button, bool preparing, bool hasAim, float pitch, uint32_t aimAgeMs,
                     float voltage);

// Fin d'une pause de visée : aucun bouton, mais l'inclinaison et l'âge de la pause, avec un
// nouveau numéro de paquet. Home Assistant analyse la photo de la pause pendant le geste qui
// commence. Courte rafale (BTHOME_AIM_MS) ; remplace une préparation, attend la fin de la
// rafale d'un sort ou d'une connexion de Home Assistant (sans la couper), puis part avec son
// âge à jour s'il ne dépasse pas l'âge maximal de la visée.
void bthomeSendAim(float pitch, uint32_t aimAgeMs, float voltage);

// Entre deux sorts, une annonce lente (BTHOME_BEACON_INTERVAL) dit à Home Assistant que la
// baguette est réveillée : il peut s'y connecter pour envoyer des réglages.
void bthomePoll();
bool bthomeBusy();  // rafale en cours ou Home Assistant connecté : pas de veille
