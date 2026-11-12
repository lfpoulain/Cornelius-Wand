// Test du modèle Edge Impulse sur le Nano 33 BLE Rev2 (BMI270)
// Remplace l'exemple nano_ble33_sense_accelerometer_continuous, prévu pour la Rev1 et 3 axes.
// Affiche les scores de chaque classe toutes les 200 ms sur le port série (115200 bauds).

#include <Arduino_BMI270_BMM150.h>
#include <MagicWand_inferencing.h>

#define AXES 6  // ax, ay, az, gx, gy, gz, dans l'ordre de la collecte
#define STRIDE_SAMPLES 20  // une inférence toutes les 20 mesures (200 ms à 100 Hz)

#if EI_CLASSIFIER_RAW_SAMPLES_PER_FRAME != AXES
#error "Le modèle doit avoir 6 axes : ax, ay, az, gx, gy, gz"
#endif

static float ring[EI_CLASSIFIER_DSP_INPUT_FRAME_SIZE];  // fenêtre glissante circulaire
static float window[EI_CLASSIFIER_DSP_INPUT_FRAME_SIZE];
static size_t head = 0;      // prochain échantillon à écrire
static size_t collected = 0;
static unsigned long nextSample = 0;

void setup() {
  Serial.begin(115200);
  while (!Serial);

  if (!IMU.begin()) {
    Serial.println("Erreur : IMU introuvable");
    while (1);
  }

  Serial.print("Classes :");
  for (size_t i = 0; i < EI_CLASSIFIER_LABEL_COUNT; i++) {
    Serial.print(' ');
    Serial.print(ei_classifier_inferencing_categories[i]);
  }
  Serial.println();
  nextSample = micros();
}

void loop() {
  if ((long)(micros() - nextSample) < 0) return;
  nextSample += (unsigned long)(EI_CLASSIFIER_INTERVAL_MS * 1000);

  float* s = ring + head * AXES;
  IMU.readAcceleration(s[0], s[1], s[2]);  // en g
  IMU.readGyroscope(s[3], s[4], s[5]);     // en °/s
  head = (head + 1) % EI_CLASSIFIER_RAW_SAMPLE_COUNT;
  collected++;

  if (collected < EI_CLASSIFIER_RAW_SAMPLE_COUNT || collected % STRIDE_SAMPLES != 0) return;

  // Remet la fenêtre dans l'ordre chronologique
  size_t tail = (EI_CLASSIFIER_RAW_SAMPLE_COUNT - head) * AXES;
  memcpy(window, ring + head * AXES, tail * sizeof(float));
  memcpy(window + tail, ring, head * AXES * sizeof(float));

  signal_t signal;
  numpy::signal_from_buffer(window, EI_CLASSIFIER_DSP_INPUT_FRAME_SIZE, &signal);
  ei_impulse_result_t result = {0};
  if (run_classifier(&signal, &result, false) != EI_IMPULSE_OK) {
    Serial.println("Erreur d'inférence");
    return;
  }

  size_t best = 0;
  for (size_t i = 0; i < EI_CLASSIFIER_LABEL_COUNT; i++) {
    Serial.print(result.classification[i].label);
    Serial.print(' ');
    Serial.print(result.classification[i].value, 2);
    Serial.print("  ");
    if (result.classification[i].value > result.classification[best].value) best = i;
  }
  Serial.print("(");
  Serial.print(result.timing.dsp + result.timing.classification);
  Serial.print(" ms)");
  if (result.classification[best].value >= 0.8f) {
    Serial.print("  => ");
    Serial.print(result.classification[best].label);
  }
  Serial.println();

  // Ne pas rattraper les mesures manquées pendant l'inférence
  nextSample = micros();
}
