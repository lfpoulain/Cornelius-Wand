// Collecte IMU pour Edge Impulse - Arduino Nano 33 BLE Rev2 (BMI270 + BMM150)
// Envoie "ax,ay,az,gx,gy,gz" à 100 Hz sur le port série,
// à lire avec : edge-impulse-data-forwarder --frequency 100

#include <Arduino_BMI270_BMM150.h>

const unsigned long SAMPLE_PERIOD_US = 10000;  // 100 Hz

unsigned long nextSample = 0;
float ax, ay, az, gx, gy, gz;

void setup() {
  Serial.begin(115200);
  while (!Serial);

  if (!IMU.begin()) {
    Serial.println("Erreur : IMU introuvable");
    while (1);
  }

  pinMode(LED_BUILTIN, OUTPUT);
  digitalWrite(LED_BUILTIN, HIGH);  // LED allumée = collecte en cours
  nextSample = micros();
}

void loop() {
  if ((long)(micros() - nextSample) < 0) return;
  nextSample += SAMPLE_PERIOD_US;

  // On garde la dernière valeur si une nouvelle mesure n'est pas encore prête
  if (IMU.accelerationAvailable()) IMU.readAcceleration(ax, ay, az);  // en g
  if (IMU.gyroscopeAvailable()) IMU.readGyroscope(gx, gy, gz);        // en °/s

  Serial.print(ax, 4); Serial.print(',');
  Serial.print(ay, 4); Serial.print(',');
  Serial.print(az, 4); Serial.print(',');
  Serial.print(gx, 2); Serial.print(',');
  Serial.print(gy, 2); Serial.print(',');
  Serial.println(gz, 2);
}
