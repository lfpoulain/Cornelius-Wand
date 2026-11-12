// Réglages de la caméra au plafond - Freenove ESP32-WROVER CAM (OV2640)
#pragma once

// Identifiants Wi-Fi : copier secrets.example.h en secrets.h et le remplir
#include "secrets.h"

// Nom sur le réseau : http://cornelius-camera.local et port OTA dans l'IDE Arduino
#define CAMERA_HOSTNAME "cornelius-camera"

// Photo envoyée à Jev par Home Assistant (http://<ip>/capture.jpg)
#define PHOTO_FRAMESIZE FRAMESIZE_XGA  // 1024 x 768
#define PHOTO_QUALITY   12             // qualité JPEG : 10 (meilleure) à 63
#define PHOTO_MAX_BYTES (120 * 1024)   // au-delà, la photo est jetée

// Photos gardées pour /capture.jpg?ago=... : chaque image du capteur (10 à 15 par seconde en
// XGA) est rangée à la suite des autres dans un tampon en PSRAM, qui efface les plus anciennes.
// Si PHOTO_KEEP_MS de ces images n'y tiennent pas (grosses photos), seule une partie d'entre
// elles est rangée, à intervalles réguliers.
// PSRAM (4 Mo) : 2 x 154 Ko pour les images du pilote, ce tampon et une copie de PHOTO_MAX_BYTES
// pour l'envoi, soit 3,4 Mo
#define PHOTO_BUFFER_BYTES (3 * 1024 * 1024)  // chaque image jusqu'à 1 Mo/s (65 Ko à 15 images/s)
#define PHOTO_SLOTS        64                 // photos au plus : 4,2 s à 15 images/s
#define PHOTO_KEEP_MS      2600               // durée de photos toujours gardée. L'âge demandé vaut
                                              // au plus aimMaxGestureMs + spellEndStillMs + 150 ms
                                              // (réglages de la baguette : 2,25 s par défaut, 3,35 s
                                              // aux bornes) ; plus loin, Home Assistant refuse la
                                              // photo trop éloignée du moment visé

// Page de mise au point (http://<ip>/) : la photo en direct, pour cadrer la caméra
#define DEBUG_WEB_SERVER 1
