// Baguette Cornelius - Arduino Nano 33 BLE Rev2
//
// Réveil par mouvement -> reconnaissance du sort -> inclinaison de la visée
// -> annonce BTHome reçue par Home Assistant -> veille profonde après 30 s d'immobilité.
//
// La direction à plat se lit sur la photo de la caméra au plafond (Home Assistant). Pour
// qu'il prenne la photo du moment où l'on visait, chaque annonce dit depuis combien de
// temps la pause de visée est finie. Dès que la pause finit (la baguette se remet à bouger),
// une annonce « visée » part : la photo de la pause est analysée pendant tout le geste, et la
// lampe réagit dès l'annonce du sort. Quand le score d'un sort commence à monter, une annonce
// « préparation » fait de même si la visée n'a pas été reçue.
//
// Commandes sur le port série (115200 bauds) :
//   c  calibrer l'axe de visée (lever vers le plafond)
//   o  afficher / masquer l'inclinaison en direct
//   i  informations
//   t  envoyer un sort de test (bouton 1) avec la visée actuelle, même sans visée
//      (« Ignorer les sorts sans visée » ne s'y applique pas)
//   d  afficher / masquer les scores du modèle à chaque inférence
//   s  passer en veille tout de suite (test du réveil)
//
// Réglages et calibration aussi depuis Home Assistant, en Bluetooth, quand la baguette
// est réveillée (remote.cpp).

#include <Arduino_BMI270_BMM150.h>
#include "config.h"
#include "orientation.h"
#include "sampler.h"
#include "gestures.h"
#include "bthome.h"
#include "power.h"
#include "remote.h"
#include "settings.h"

static bool liveOrientation = false;
static bool verboseGestures = false;
static bool sleepBlockedReported = false;
static uint8_t pendingButton = 0;  // sort reconnu, en attente de la fin du geste
static uint32_t pendingSince = 0;
static bool flashing = false;  // retour d'un sort en cours, éteint par loop() à flashEnd
static uint32_t flashEnd = 0;

// LED RGB du Nano : actives à l'état bas
static void led(bool r, bool g, bool b) {
  digitalWrite(LEDR, r ? LOW : HIGH);
  digitalWrite(LEDG, g ? LOW : HIGH);
  digitalWrite(LEDB, b ? LOW : HIGH);
}

// Retour LED et vibreur
static void indicate(bool r, bool g, bool b) {
  led(r, g, b);
#if VIBRATION_PIN >= 0
  digitalWrite(VIBRATION_PIN, r || g || b ? HIGH : LOW);
#endif
}

// Retour bloquant : seulement là où la boucle ne doit pas tourner (démarrage, calibration,
// erreur fatale)
static void feedback(bool r, bool g, bool b, uint16_t ms) {
  indicate(r, g, b);
  delay(ms);
  indicate(false, false, false);
}

// Retour d'un sort, sans bloquer la boucle : l'annonce du sort et l'âge de sa visée
// restent à jour pendant ce temps (bthomePoll)
static void flash(bool r, bool g, bool b, uint16_t ms) {
  indicate(r, g, b);
  flashing = true;
  flashEnd = millis() + ms;
}

static void flashPoll() {
  if (flashing && (int32_t)(millis() - flashEnd) >= 0) {
    indicate(false, false, false);
    flashing = false;
  }
}

static void fatal(const __FlashStringHelper* message) {
  Serial.println(message);
  while (true) {
    feedback(true, false, false, 200);
    delay(800);
  }
}

static bool ignoringUnaimed() { return settings.ignoreUnaimed >= 0.5f; }

// Renvoie faux si rien n'est envoyé (pas encore de visée) : elle est alors retentée
static bool prepareSpell(uint8_t button) {
  float pitch = 0;
  uint32_t age = 0;
  bool hasAim = samplerGetAim(pitch, age, false);
  if (!hasAim && ignoringUnaimed()) return false;  // ce sort serait ignoré : rien à préparer
  // Home Assistant connecté (réglages, calibration) : pas d'annonce possible, on retentera
  if (!bthomeSendSpell(button, true, hasAim, pitch, age, powerReadVdd())) return false;
  Serial.print(F("Préparation : bouton ")); Serial.println(button);
  return true;
}

// Visée et fin du geste trop différentes en inclinaison : la pause était un repos (baguette
// pendante ou tenue pointe en bas) et le geste la levait vers la cible, ce n'est pas un sort
static bool aimIsRest(float aimPitch) {
  float now;
  return samplerCurrentPitch(now) && fabsf(now - aimPitch) > AIM_PITCH_JUMP_DEG;
}

// test : sort de test (commande 't'), envoyé même sans visée
static void castSpell(uint8_t button, bool test = false) {
  float pitch = 0;
  uint32_t age = 0;
  // Visée regardée sans la consommer : un sort ignoré la laisse au vrai sort
  bool hasAim = samplerGetAim(pitch, age, false);
  bool rest = hasAim && !test && aimIsRest(pitch);
  if ((!hasAim || rest) && ignoringUnaimed() && !test) {
    // Souvent le mouvement qui lève la baguette vers la cible : rien n'est envoyé, et le vrai
    // sort, après la pause de visée, n'attend pas la pause d'après sort
    Serial.print(F("  -> bouton ")); Serial.print(button); Serial.println(F(" ignoré : visée inconnue"));
    flash(true, false, false, 40);  // bref rouge
    gesturesIgnoredSpell();
    return;
  }
  hasAim = samplerGetAim(pitch, age) && !rest;
  float voltage = powerReadVdd();
  bthomeSendSpell(button, false, hasAim, pitch, age, voltage);

  Serial.print(F("  -> bouton ")); Serial.print(button);
  if (hasAim) {
    Serial.print(F(", inclinaison ")); Serial.print(pitch, 1);
    Serial.print(F("°, visée il y a ")); Serial.print(age); Serial.print(F(" ms"));
  } else {
    Serial.print(F(", visée inconnue"));
  }
  Serial.print(F(", ")); Serial.print(voltage, 2); Serial.println(F(" V"));

  // Vert : sort et visée ; jaune : sort sans visée (sorts sans visée envoyés)
  flash(!hasAim, true, false, 150);
  gesturesStartCooldown();
}

// Calibration de l'axe de visée, aussi lancée depuis Home Assistant : la LED guide.
// Bleu fixe : lever la baguette vers le plafond puis ne plus bouger ; vert : réussie, rouge : ratée.
static void calibrate() {
  samplerPause(true);
  CalResult result = orientationCalibrate(Serial, [] { led(false, false, true); });
  samplerPause(false);
  remoteRefreshInfo();
  feedback(result != CAL_OK, result == CAL_OK, false, 800);  // vert : réussie, rouge : ratée
}

static void goToSleep() {
  samplerPause(true);
  bthomeEnd();
  powerSleep();
}

static void handleSerial() {
  if (!Serial.available()) return;
  char c = Serial.read();
  switch (c) {
    case 'c':
      calibrate();
      break;
    case 'o':
      liveOrientation = !liveOrientation;
      samplerSetLiveOrientation(liveOrientation);
      break;
    case 'i':
      orientationPrintInfo(Serial);
      Serial.print(F("Alimentation : ")); Serial.print(powerReadVdd(), 2); Serial.println(F(" V"));
      Serial.print(F("Immobile depuis ")); Serial.print(samplerStillMs() / 1000.0f, 1);
      Serial.print(F(" s (veille à ")); Serial.print(settings.sleepAfterS, 0); Serial.println(F(" s)"));
      break;
    case 't':
      castSpell(1, true);
      break;
    case 'd':
      verboseGestures = !verboseGestures;
      gesturesSetVerbose(verboseGestures);
      break;
    case 's':
      goToSleep();
      break;
  }
}

void setup() {
  bool wokeFromMotion = powerWokeFromMotion();
  Serial.begin(115200);

  pinMode(LEDR, OUTPUT);
  pinMode(LEDG, OUTPUT);
  pinMode(LEDB, OUTPUT);
  pinMode(LED_PWR, OUTPUT);
  digitalWrite(LED_PWR, LOW);  // économise ~1 mA
  led(false, false, false);
#if VIBRATION_PIN >= 0
  pinMode(VIBRATION_PIN, OUTPUT);
#endif

  if (!IMU.begin()) fatal(F("Erreur : IMU introuvable"));
  Wire1.setClock(400000);
  settingsBegin();

  if (!orientationBegin()) {
    Serial.println(F("Pas encore calibrée : ouvre le moniteur série et tape 'c'."));
  }
  // Dès que l'IMU est prête : une visée faite pendant le démarrage du Bluetooth, juste
  // après le réveil, n'est plus perdue
  samplerStart(wokeFromMotion);
  if (!bthomeBegin()) fatal(F("Erreur : BLE indisponible"));
  if (!gesturesBegin(Serial)) fatal(F("Erreur : configuration des sorts invalide"));
  Serial.println(wokeFromMotion ? F("Réveil par mouvement") : F("Démarrage"));
  feedback(false, false, true, 80);  // bleu : prête
}

void loop() {
  handleSerial();

  flashPoll();

  // Fin d'une pause de visée : Home Assistant analyse sa photo pendant le geste qui suit.
  // Même règle que la visée du sort (sampler.cpp) : une immobilité commencée avant le
  // dernier sort ne compte que pour la part qui suit le sort ; au moins stillMinMs, on
  // enchaîne vers la même cible ; moins, on baisse la baguette et cet arrêt ne vise rien
  // (pas de Jev pour rien).
  float aimPitch;
  uint32_t aimAge;
  if (samplerTakePauseEnd(aimPitch, aimAge)) bthomeSendAim(aimPitch, aimAge, powerReadVdd());

  // Le modèle peut reconnaître le sort avant la fin du geste : on attend que la baguette
  // s'arrête pour le lancer, sans retarder indéfiniment s'il continue de bouger.
  GestureEvent event = gesturesPoll(Serial);
  if (event.preparing && !pendingButton && !prepareSpell(event.preparing)) gesturesRetryPrepare();
  if (event.cast && !pendingButton) {
    pendingButton = event.cast;
    pendingSince = millis();
  }
  if (pendingButton) {
    bool stopped = samplerStillMs() >= settings.spellEndStillMs;
    if (stopped || millis() - pendingSince >= SPELL_END_MAX_MS) {
      castSpell(pendingButton);
      pendingButton = 0;
    }
  }

  bthomePoll();
  switch (remotePoll()) {
    case REMOTE_CALIBRATE:
      calibrate();
      break;
    case REMOTE_SLEEP:
      goToSleep();
      break;
  }

  // Pas de veille sur USB : la carte doit rester joignable pour le moniteur série
  // et pour les téléversements (sinon elle disparaît en plein upload)
  bool idle = samplerStillMs() > settings.sleepAfterS * 1000;
  if (idle && !bthomeBusy()) {
    if (!powerOnUsb()) goToSleep();
    if (!sleepBlockedReported) {
      Serial.println(F("Veille bloquée : alimentée par USB. Tape 's' pour tester la veille."));
      sleepBlockedReported = true;
    }
  } else if (!idle) {
    sleepBlockedReported = false;
  }

  delay(5);
}
