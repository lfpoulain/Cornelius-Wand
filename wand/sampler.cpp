#include "sampler.h"
#include <mbed.h>
#include <Arduino_BMI270_BMM150.h>
#include "config.h"
#include "orientation.h"
#include "settings.h"

using namespace std::chrono_literals;

#define RING_SIZE SAMPLER_CAPACITY
#define BLOCK_SAMPLES 10  // visée évaluée par blocs de 100 ms
#define BLOCK_MS (BLOCK_SAMPLES * 10)
#define SEGMENTS 4
#define BLOCKS 32         // 3,2 s de blocs, pour le repli sans pause

struct Sample {
  float v[6];
};

// Pause de visée terminée : inclinaison moyenne, et son étendue (baguette posée ou tenue)
struct StillSegment {
  uint32_t start, end;
  float pitchSum;
  uint16_t n;
  float pitchMin, pitchMax;
};

// Inclinaison de chaque bloc de 100 ms, même en mouvement : repli quand la pause avant le
// geste a été trop courte pour former une pause de visée
struct AimBlock {
  uint32_t time;
  float pitch;
  float gyrMax;
};

static Sample ring[RING_SIZE];
static uint32_t written = 0;
static rtos::Mutex lock;
static rtos::Thread thread(osPriorityAboveNormal, 4096, nullptr, "imu");

static volatile bool pauseRequested = false;
static volatile bool isPaused = false;
static volatile uint32_t lastMotion = 0;
static volatile bool live = false;

static StillSegment segments[SEGMENTS];  // pauses de visée terminées, la plus récente en segHead - 1
static int segHead = 0, segCount = 0;
static StillSegment current;
static bool currentOpen = false;
static bool currentCut = false;     // la pause en cours a commencé avant le dernier sort
static AimBlock blocks[BLOCKS];
static int blockHead = 0, blockCount = 0;
static uint32_t aimConsumedAt = 0;  // ce qui précède a déjà servi à un sort, ou ne vise rien
static bool pauseEnded = false;     // une pause de visée s'est finie, pas encore annoncée
static bool restRecent = false;     // la dernière immobilité écartée était une baguette posée
static uint32_t restEnd = 0;        // ... finie à cet instant

// Règle unique de la visée, pour son annonce comme pour le sort : seule compte la part d'une
// immobilité qui suit le dernier sort (début borné par aimConsumedAt), si elle dure au moins
// stillMinMs. Plus de AIM_PAUSE_MAX_MS sans le moindre tremblement (inclinaison stable à
// REST_PITCH_RANGE_DEG près), la baguette était posée : ce n'est pas une visée ; tenue à la
// main, même longtemps, c'en est une.
// Range la pause en cours si c'est une pause de visée ; renvoie vrai si elle l'a été.
static bool closeSegment(uint32_t now) {
  if (!currentOpen) return false;
  int32_t length = (int32_t)(current.end - current.start);
  bool rested = length > AIM_PAUSE_MAX_MS && current.pitchMax - current.pitchMin < REST_PITCH_RANGE_DEG;
  bool kept = length >= settings.stillMinMs && !rested && current.n > 0;
  if (kept) {
    segments[segHead] = current;
    segHead = (segHead + 1) % SEGMENTS;
    if (segCount < SEGMENTS) segCount++;
    restRecent = false;
  } else if (currentCut || rested) {
    // Arrêt trop bref après un sort (on baisse la baguette), ou baguette posée : ses blocs,
    // jusqu'à celui qui la remet en mouvement, ne servent pas non plus au repli
    aimConsumedAt = now;
    if (rested) {
      restRecent = true;
      restEnd = current.end;
    }
  }
  currentOpen = false;
  return kept;
}

static void run() {
  auto next = rtos::Kernel::Clock::now();
  Vec3 accSum = {0, 0, 0};
  float gyrMax = 0;
  int n = 0;

  while (true) {
    next += 10ms;
    rtos::ThisThread::sleep_until(next);

    if (pauseRequested) {
      lock.lock();
      closeSegment(millis());
      lock.unlock();
      isPaused = true;
      accSum = {0, 0, 0};
      gyrMax = 0;
      n = 0;
      next = rtos::Kernel::Clock::now();
      continue;
    }
    isPaused = false;

    float ax, ay, az, gx, gy, gz;
    IMU.readAcceleration(ax, ay, az);
    IMU.readGyroscope(gx, gy, gz);
    uint32_t now = millis();

    lock.lock();
    ring[written % RING_SIZE] = {{ax, ay, az, gx, gy, gz}};
    written++;
    lock.unlock();

    float g = sqrtf(gx * gx + gy * gy + gz * gz);
    if (g > settings.stillGyroDps) lastMotion = now;

    Vec3 acc = toSensorFrame(ax, ay, az);
    accSum = {accSum.x + acc.x, accSum.y + acc.y, accSum.z + acc.z};
    gyrMax = max(gyrMax, g);
    if (++n < BLOCK_SAMPLES) continue;

    float pitch;
    bool ok = orientationPitch({accSum.x / n, accSum.y / n, accSum.z / n}, pitch);
    bool still = gyrMax <= settings.stillGyroDps;

    lock.lock();
    // Seuls comptent les BLOCKS derniers blocs : aimConsumedAt reste récent, sans quoi les
    // comparaisons d'instants se fausseraient après 24,8 jours sans sort
    if ((int32_t)(now - aimConsumedAt) > BLOCKS * BLOCK_MS) aimConsumedAt = now - BLOCKS * BLOCK_MS;
    if (ok) {
      blocks[blockHead] = {now, pitch, gyrMax};
      blockHead = (blockHead + 1) % BLOCKS;
      if (blockCount < BLOCKS) blockCount++;
    }
    if (ok && still) {
      if (!currentOpen) {
        // Début du premier bloc immobile ; immobile depuis avant le dernier sort, seule
        // compte la part qui le suit
        currentCut = (int32_t)(lastMotion - aimConsumedAt) < 0;
        current = {currentCut ? aimConsumedAt : now - BLOCK_MS, now, 0, 0, pitch, pitch};
        currentOpen = true;
      }
      current.pitchMin = min(current.pitchMin, pitch);
      current.pitchMax = max(current.pitchMax, pitch);
      current.end = now;
      current.pitchSum += pitch;
      current.n++;
    } else if (closeSegment(now)) {
      pauseEnded = true;  // la baguette repart : le geste commence peut-être
    }
    lock.unlock();

    if (live && ok) {
      Serial.print(F("inclinaison ")); Serial.print(pitch, 1);
      Serial.println(still ? F("°  immobile") : F("°"));
    }

    accSum = {0, 0, 0};
    gyrMax = 0;
    n = 0;
  }
}

void samplerStart(bool afterRest) {
  lastMotion = millis();
  // Réveil par mouvement : la baguette était posée jusqu'ici, le geste qui la ramasse ne vise rien
  restRecent = afterRest;
  restEnd = lastMotion;
  thread.start(run);
}

void samplerPause(bool paused) {
  if (!paused) {
    // Reprise après une calibration : la baguette a bougé, la fenêtre du modèle et la
    // visée repartent de zéro (le thread est encore en pause)
    lock.lock();
    written = 0;
    segCount = 0;
    blockHead = blockCount = 0;
    pauseEnded = false;
    restRecent = false;
    lock.unlock();
    lastMotion = millis();
  }
  pauseRequested = paused;
  while (isPaused != paused) delay(5);
}

bool samplerCopyWindow(float* out, size_t samples) {
  lock.lock();
  bool ok = samples <= RING_SIZE && written >= samples;
  if (ok) {
    uint32_t first = written - samples;
    for (size_t i = 0; i < samples; i++) {
      memcpy(out + i * 6, ring[(first + i) % RING_SIZE].v, sizeof(Sample));
    }
  }
  lock.unlock();
  return ok;
}

uint32_t samplerStillMs() {
  uint32_t last = lastMotion;  // avant millis() : le thread IMU peut l'avancer entre les deux
  return millis() - last;
}

bool samplerGetAim(float& pitch, uint32_t& ageMs, bool consume) {
  // La pause qui compte est la dernière pause de visée rangée (closeSegment : au moins
  // stillMinMs après le dernier sort), terminée quand le geste a commencé. Celle qui suit le
  // geste est encore ouverte et n'est donc pas prise en compte. Finie plus de
  // aimMaxGestureMs avant la fin du geste, elle est ignorée : la baguette a trop bougé
  // depuis. Un arrêt plus récent, plus bref que stillMinMs (pivot vers une autre cible),
  // l'emporte.
  bool found = false;
  uint32_t aimedAt = 0;
  lock.lock();
  uint32_t motion = lastMotion;  // lu une fois : le thread IMU l'écrit hors du verrou
  if (segCount > 0) {
    const StillSegment& s = segments[(segHead + SEGMENTS - 1) % SEGMENTS];
    if ((int32_t)(motion - s.end) <= settings.aimMaxGestureMs) {
      pitch = s.pitchSum / s.n;
      aimedAt = s.end;
      found = true;
      // Arrêt plus récent, plus bref que stillMinMs (on a pivoté vers une autre cible) : son
      // dernier bloc immobile est la nouvelle visée. Les blocs qui suivent la pause sont en
      // mouvement par construction : un bloc immobile après elle est un nouvel arrêt (un
      // bloc entier sous stillGyroDps n'arrive pas au milieu d'un geste vif).
      const AimBlock* last = nullptr;
      for (int i = 0; i < blockCount; i++) {
        const AimBlock& b = blocks[i];
        if ((int32_t)(b.time - s.end) <= 0 || (int32_t)(motion - b.time) <= 0) continue;
        if ((int32_t)(b.time - aimConsumedAt) <= 0 || b.gyrMax > settings.stillGyroDps) continue;
        if (!last || (int32_t)(b.time - last->time) > 0) last = &b;
      }
      if (last) {
        pitch = last->pitch;
        aimedAt = last->time + AIM_PHOTO_MARGIN_MS - BLOCK_MS / 2;  // milieu du bloc, comme le repli
      }
    }
  }
  // Repli : pause trop courte (on vise et on enchaîne le geste). Le moment le plus calme
  // dans les aimMaxGestureMs avant la fin du geste est la visée : pendant le geste la
  // baguette tourne bien plus vite, et ce qui suit le geste est exclu (après lastMotion).
  // Juste après une baguette posée, le geste la ramasse et ne vise rien, même lent.
  bool pickedUp = restRecent && (int32_t)(motion - restEnd) <= settings.aimMaxGestureMs;
  if (!found && !pickedUp) {
    const AimBlock* best = nullptr;
    for (int i = 0; i < blockCount; i++) {
      const AimBlock& b = blocks[i];
      int32_t before = (int32_t)(motion - b.time);
      if (before <= 0 || before > settings.aimMaxGestureMs) continue;
      // Ni les blocs déjà utilisés par un sort, ni ceux d'un arrêt qui ne vise rien
      if ((int32_t)(b.time - aimConsumedAt) <= 0 || b.gyrMax > settings.aimFallbackDps) continue;
      if (!best || b.gyrMax < best->gyrMax) best = &b;
    }
    if (best) {
      // Home Assistant prend la photo AIM_PHOTO_MARGIN_MS avant aimedAt : ici, au milieu du
      // bloc, et non avant son début
      pitch = best->pitch;
      aimedAt = best->time + AIM_PHOTO_MARGIN_MS - BLOCK_MS / 2;
      found = true;
    }
  }
  if (found) {
    int32_t age = (int32_t)(millis() - aimedAt);  // aimedAt peut être à venir (repli)
    ageMs = age > 0 ? age : 0;
  }
  if (consume) {  // une visée ne sert qu'à un seul sort
    segCount = 0;
    aimConsumedAt = millis();
    // L'immobilité en cours ne compte plus que pour la part qui suit ce sort : si elle dure
    // au moins stillMinMs, on enchaîne vers la même cible (closeSegment)
    if (currentOpen) {
      current.start = aimConsumedAt;
      currentCut = true;
    }
  }
  lock.unlock();
  return found;
}

bool samplerTakePauseEnd(float& pitch, uint32_t& ageMs) {
  lock.lock();
  bool ended = pauseEnded && segCount > 0;
  if (ended) {
    const StillSegment& s = segments[(segHead + SEGMENTS - 1) % SEGMENTS];
    pitch = s.pitchSum / s.n;
    ageMs = millis() - s.end;
  }
  pauseEnded = false;
  lock.unlock();
  return ended;
}

bool samplerCurrentPitch(float& pitch) {
  lock.lock();
  bool ok = blockCount > 0;
  if (ok) pitch = blocks[(blockHead + BLOCKS - 1) % BLOCKS].pitch;
  lock.unlock();
  return ok;
}

void samplerSetLiveOrientation(bool enabled) { live = enabled; }
