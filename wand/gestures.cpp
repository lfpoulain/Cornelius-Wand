#include "gestures.h"
#include "config.h"
#include "sampler.h"
#include "settings.h"

// Bibliothèque exportée par Edge Impulse (Deployment → Arduino library).
// Adapter le nom à celui de la bibliothèque exportée si nécessaire.
#include <MagicWand_inferencing.h>

#define AXES 6  // ax, ay, az, gx, gy, gz, dans l'ordre de la collecte

#if EI_CLASSIFIER_RAW_SAMPLES_PER_FRAME != AXES
#error "Le modèle doit avoir 6 axes : ax, ay, az, gx, gy, gz"
#endif

static_assert(EI_CLASSIFIER_INTERVAL_MS == 10, "Le modele doit etre entraine a 100 Hz");
static_assert(EI_CLASSIFIER_RAW_SAMPLE_COUNT <= SAMPLER_CAPACITY,
              "La fenetre du modele depasse le tampon IMU (256 echantillons)");

static float features[EI_CLASSIFIER_DSP_INPUT_FRAME_SIZE];
static uint32_t lastInference = 0;
static bool cooling = false;      // pause en cours jusqu'à cooldownUntil (sinon il n'a pas de sens)
static uint32_t cooldownUntil = 0;
static bool ignoredHold = false;  // pause d'un sort ignoré : finit aussi quand son score est vraiment retombé
static int candidate = -1;  // indice du libellé en cours de confirmation
static int confirmations = 0;
static bool prepared = false;  // préparation déjà signalée pour la tentative en cours
static bool verbose = false;

void gesturesSetVerbose(bool enabled) { verbose = enabled; }

static void startPause(uint32_t until, bool ignored) {
  cooldownUntil = until;
  cooling = true;
  ignoredHold = ignored;
}

void gesturesStartCooldown() {
  startPause(millis() + SPELL_COOLDOWN_MS, false);
  prepared = true;
}

// Le mouvement ignoré (souvent la levée vers la cible) reste dans la fenêtre du modèle
// jusqu'à ce qu'elle ait glissé au-delà de son dernier instant de mouvement : jusque-là, ni
// préparation ni confirmation, sinon il serait reconnu une seconde fois. Le vrai sort ne
// commence qu'après la pause de visée (au moins stillMinMs), et le modèle ne le reconnaît
// qu'une fois presque tout son geste dans la fenêtre : la pause finit alors à peu près quand
// il devient reconnaissable, et plus tôt dès que le score des sorts retombe sous la moitié
// du seuil de préparation (fin de tentative : le mouvement ignoré n'est plus reconnu). La
// visée annoncée à la fin de la pause, elle, part sans attendre.
void gesturesRetryPrepare() { prepared = false; }

void gesturesIgnoredSpell() {
  uint32_t movedAt = millis() - samplerStillMs();
  startPause(movedAt + (uint32_t)(EI_CLASSIFIER_RAW_SAMPLE_COUNT * EI_CLASSIFIER_INTERVAL_MS), true);
  prepared = false;  // le vrai sort pourra prévenir à son tour
}

static uint8_t buttonFor(const char* label) {
  for (const SpellDef& s : SPELLS) {
    if (strcmp(s.label, label) == 0) return s.button;
  }
  return 0;
}

bool gesturesBegin(Stream& out) {
  out.print(F("Modèle : "));
  out.print(EI_CLASSIFIER_LABEL_COUNT);
  out.print(F(" classes, fenêtre de "));
  out.print(EI_CLASSIFIER_RAW_SAMPLE_COUNT * EI_CLASSIFIER_INTERVAL_MS, 0);
  out.println(F(" ms"));

  bool ok = true;
  for (size_t i = 0; i < EI_CLASSIFIER_LABEL_COUNT; i++) {
    const char* label = ei_classifier_inferencing_categories[i];
    uint8_t button = buttonFor(label);
    out.print(F("  ")); out.print(label);
    if (button) {
      out.print(F(" -> Button ")); out.println(button);
    } else if (strcmp(label, IDLE_LABEL) == 0) {
      out.println(F(" (repos)"));
    } else {
      out.println(F(" -> absent de SPELLS dans config.h, ignoré"));
    }
  }
  for (const SpellDef& s : SPELLS) {
    if (s.button < 1 || s.button > BTHOME_BUTTONS) {
      out.print(F("Erreur : bouton invalide pour ")); out.println(s.label);
      ok = false;
    }
  }
  return ok;
}

GestureEvent gesturesPoll(Stream& out) {
  GestureEvent event = {0, 0};
  uint32_t now = millis();
  if (now - lastInference < INFERENCE_STRIDE_MS) return event;
  lastInference = now;

  if (!samplerCopyWindow(features, EI_CLASSIFIER_DSP_INPUT_FRAME_SIZE / AXES)) return event;

  signal_t signal;
  if (numpy::signal_from_buffer(features, EI_CLASSIFIER_DSP_INPUT_FRAME_SIZE, &signal) != 0) return event;

  ei_impulse_result_t result = {0};
  uint32_t started = micros();
  if (run_classifier(&signal, &result, false) != EI_IMPULSE_OK) return event;
  uint32_t elapsed = micros() - started;

  // Meilleur libellé, et meilleur sort (hors repos) pour la préparation
  int best = 0, bestSpell = -1;
  for (size_t i = 0; i < EI_CLASSIFIER_LABEL_COUNT; i++) {
    float value = result.classification[i].value;
    if (value > result.classification[best].value) best = i;
    if (buttonFor(result.classification[i].label) &&
        (bestSpell < 0 || value > result.classification[bestSpell].value)) {
      bestSpell = i;
    }
  }
  const char* label = result.classification[best].label;
  float confidence = result.classification[best].value;

  if (verbose) {
    for (size_t i = 0; i < EI_CLASSIFIER_LABEL_COUNT; i++) {
      out.print(result.classification[i].label); out.print(' ');
      out.print(result.classification[i].value * 100, 0); out.print(F(" %  "));
    }
    out.print('('); out.print(elapsed / 1000); out.println(F(" ms)"));
  }

  float spellScore = bestSpell >= 0 ? result.classification[bestSpell].value : 0;
  if (cooling) {
    bool over = (int32_t)(now - cooldownUntil) >= 0 ||
                (ignoredHold && spellScore < settings.prepareThreshold / 2);
    if (!over) return event;
    // Le geste d'avant est sorti de la fenêtre (ou n'est plus reconnu) : la préparation se
    // réarme, même si le geste suivant a commencé pendant la pause (sorts enchaînés)
    cooling = false;
    prepared = false;
  }

  // Préparation : une fois par tentative, quand le score d'un sort commence à monter ;
  // la tentative se termine quand le score retombe sous la moitié du seuil
  if (!prepared && spellScore >= settings.prepareThreshold) {
    prepared = true;
    event.preparing = buttonFor(result.classification[bestSpell].label);
  } else if (spellScore < settings.prepareThreshold / 2) {
    prepared = false;
  }

  uint8_t button = buttonFor(label);
  if (!button || confidence < settings.spellThreshold) {
    candidate = -1;
    confirmations = 0;
    return event;
  }

  if (best == candidate) {
    confirmations++;
  } else {
    candidate = best;
    confirmations = 1;
  }
  if (confirmations < SPELL_CONFIRMATIONS) return event;

  candidate = -1;
  confirmations = 0;
  prepared = true;  // pas de nouvelle préparation tant que le score n'est pas retombé
  startPause(now + SPELL_END_MAX_MS + SPELL_COOLDOWN_MS, false);  // jusqu'au lancement au plus tard

  out.print(F("Sort : ")); out.print(label);
  out.print(F(" (")); out.print(confidence * 100, 0); out.println(F(" %)"));
  event.cast = button;
  return event;
}
