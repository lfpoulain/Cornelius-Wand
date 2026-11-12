// Échantillonnage de l'IMU à 100 Hz dans un thread dédié
#pragma once
#include <Arduino.h>

constexpr size_t SAMPLER_CAPACITY = 256;  // 2,56 s à 100 Hz

void samplerStart(bool afterRest);  // afterRest : réveil par mouvement, la baguette était posée
void samplerPause(bool paused);  // libère l'IMU (calibration, mise en veille) ; la reprise vide l'historique

// Copie les n derniers échantillons (ax, ay, az, gx, gy, gz) dans out ;
// renvoie false si le tampon n'est pas encore rempli.
bool samplerCopyWindow(float* out, size_t samples);

uint32_t samplerStillMs();  // depuis combien de millisecondes la baguette ne bouge plus

// Pause de visée : la part d'une immobilité qui suit le dernier sort, d'au moins stillMinMs ;
// plus de AIM_PAUSE_MAX_MS sans le moindre tremblement, la baguette était posée. Même règle pour son
// annonce (samplerTakePauseEnd) et pour la visée du sort (samplerGetAim).

// Visée : inclinaison pendant la dernière pause de visée avant le geste en cours, et depuis
// combien de millisecondes elle est finie (Home Assistant prend la photo AIM_PHOTO_MARGIN_MS
// plus tôt). Un arrêt plus récent, plus bref que stillMinMs (on a pivoté vers une autre
// cible), l'emporte. Sans pause de visée, l'instant est décalé pour viser le bloc le plus
// calme, sauf juste après une baguette posée (le geste la ramasse).
// consume : la visée ne sert ensuite plus (au sort) ; sinon elle reste (préparation, sort ignoré).
bool samplerGetAim(float& pitch, uint32_t& ageMs, bool consume = true);

// Une pause de visée vient de finir (la baguette se remet à bouger) : son inclinaison, et
// depuis combien de millisecondes elle est finie. Vrai une seule fois par pause.
bool samplerTakePauseEnd(float& pitch, uint32_t& ageMs);

// Inclinaison du dernier bloc de 100 ms (la fin du geste, au lancement d'un sort)
bool samplerCurrentPitch(float& pitch);

// Affichage de l'inclinaison en direct (commande 'o')
void samplerSetLiveOrientation(bool enabled);
