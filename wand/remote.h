// Réglages à distance : service Bluetooth auquel Home Assistant se connecte quand la
// baguette est réveillée (intégration : custom_components/cornelius_wand/remote.py).
//
// Service c0e10001-1d6b-4e7b-9c3e-436f726e656c :
//   c0e10010 à c0e10019  un réglage par caractéristique (float, lecture/écriture),
//                        dans l'ordre des champs de Settings (settings.h)
//   c0e10020             commande (octet, écriture) : REMOTE_CALIBRATE, REMOTE_SLEEP,
//                        REMOTE_RESET ; exécutée après la déconnexion
//   c0e10021             état (lecture, 8 octets) : voir RemoteInfo dans remote.cpp
#pragma once
#include <Arduino.h>

#define REMOTE_CALIBRATE 1  // calibration de l'axe de visée, guidée par la LED
#define REMOTE_SLEEP     2  // veille tout de suite
#define REMOTE_RESET     3  // réglages par défaut

void remoteBegin();        // à appeler après BLE.begin(), avant la première annonce
void remoteRefreshInfo();  // met à jour la caractéristique d'état (au démarrage et après une calibration)

// À appeler en boucle : enregistre les réglages reçus une fois Home Assistant déconnecté,
// et renvoie la commande reçue (0 sinon)
uint8_t remotePoll();
