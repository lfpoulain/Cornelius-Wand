#include "power.h"
#include <Wire.h>
#include <Arduino_BMI270_BMM150.h>
#include "config.h"
#include "settings.h"

#define BMI270_INT1_PIN 11   // P0.11 : INT1 du BMI270 sur le Nano 33 BLE Rev2
#define BMM150_ADDR 0x10
#define BMM150_POWER_CTRL 0x4B

bool powerWokeFromMotion() {
  uint32_t reason = NRF_POWER->RESETREAS;
  NRF_POWER->RESETREAS = reason;  // effacement en écrivant les bits lus
  return reason & POWER_RESETREAS_OFF_Msk;
}

bool powerOnUsb() {
  return NRF_POWER->USBREGSTATUS & POWER_USBREGSTATUS_VBUSDETECT_Msk;
}

float powerReadVdd() {
  static volatile int16_t result;
  NRF_SAADC->ENABLE = SAADC_ENABLE_ENABLE_Enabled;
  NRF_SAADC->RESOLUTION = SAADC_RESOLUTION_VAL_12bit;
  NRF_SAADC->CH[0].CONFIG = (SAADC_CH_CONFIG_GAIN_Gain1_6 << SAADC_CH_CONFIG_GAIN_Pos) |
                            (SAADC_CH_CONFIG_REFSEL_Internal << SAADC_CH_CONFIG_REFSEL_Pos) |
                            (SAADC_CH_CONFIG_TACQ_10us << SAADC_CH_CONFIG_TACQ_Pos);
  NRF_SAADC->CH[0].PSELP = SAADC_CH_PSELP_PSELP_VDD;
  NRF_SAADC->CH[0].PSELN = SAADC_CH_PSELN_PSELN_NC;
  NRF_SAADC->RESULT.PTR = (uint32_t)&result;
  NRF_SAADC->RESULT.MAXCNT = 1;

  NRF_SAADC->TASKS_START = 1;
  while (!NRF_SAADC->EVENTS_STARTED) {}
  NRF_SAADC->EVENTS_STARTED = 0;
  NRF_SAADC->TASKS_SAMPLE = 1;
  while (!NRF_SAADC->EVENTS_END) {}
  NRF_SAADC->EVENTS_END = 0;
  NRF_SAADC->TASKS_STOP = 1;
  while (!NRF_SAADC->EVENTS_STOPPED) {}
  NRF_SAADC->EVENTS_STOPPED = 0;
  NRF_SAADC->ENABLE = SAADC_ENABLE_ENABLE_Disabled;

  // Gain 1/6 et référence 0,6 V : pleine échelle à 3,6 V
  return max((int16_t)0, result) * 3.6f / 4096.0f;
}

// ---------------------------------------------------------------------------
// Accès direct au BMI270 pour configurer le réveil par mouvement
// ---------------------------------------------------------------------------
static uint8_t bmiAddr = BMI2_I2C_PRIM_ADDR;

static int8_t bmiRead(uint8_t reg, uint8_t* data, uint32_t len, void* intf) {
  uint8_t addr = *(uint8_t*)intf;
  Wire1.beginTransmission(addr);
  Wire1.write(reg);
  if (Wire1.endTransmission() != 0) return -1;
  if (Wire1.requestFrom(addr, len) != len) return -1;
  for (uint32_t i = 0; i < len; i++) data[i] = Wire1.read();
  return 0;
}

static int8_t bmiWrite(uint8_t reg, const uint8_t* data, uint32_t len, void* intf) {
  uint8_t addr = *(uint8_t*)intf;
  Wire1.beginTransmission(addr);
  Wire1.write(reg);
  Wire1.write(data, (int)len);
  return Wire1.endTransmission() == 0 ? 0 : -1;
}

static void bmiDelay(uint32_t us, void*) { delayMicroseconds(us); }

static bool armMotionWake() {
  struct bmi2_dev dev = {};
  dev.intf = BMI2_I2C_INTF;
  dev.read = bmiRead;
  dev.write = bmiWrite;
  dev.delay_us = bmiDelay;
  dev.intf_ptr = &bmiAddr;
  dev.read_write_len = 30;  // limite du tampon de Wire
  dev.config_file_ptr = NULL;

  // Réinitialise le capteur : gyroscope éteint, accéléromètre seul
  if (bmi270_init(&dev) != BMI2_OK) return false;

  // Après l'init, le BMI270 est en basse consommation et ignore les écritures groupées
  // (dont celle des sorties d'interruption), alors que l'API croit ce mode désactivé.
  if (bmi2_set_adv_power_save(BMI2_DISABLE, &dev) != BMI2_OK) return false;

  struct bmi2_sens_config cfg[2];
  cfg[0].type = BMI2_ACCEL;
  cfg[1].type = BMI2_ANY_MOTION;
  if (bmi270_get_sensor_config(cfg, 2, &dev) != BMI2_OK) return false;
  cfg[0].cfg.acc.odr = BMI2_ACC_ODR_50HZ;
  cfg[0].cfg.acc.range = BMI2_ACC_RANGE_4G;
  cfg[0].cfg.acc.bwp = BMI2_ACC_NORMAL_AVG4;
  cfg[0].cfg.acc.filter_perf = BMI2_POWER_OPT_MODE;
  cfg[1].cfg.any_motion.duration = WAKE_DURATION;
  cfg[1].cfg.any_motion.threshold = (uint16_t)(settings.wakeThresholdMg / 0.48f);  // 0,48 mg par unité
  cfg[1].cfg.any_motion.select_x = BMI2_ENABLE;
  cfg[1].cfg.any_motion.select_y = BMI2_ENABLE;
  cfg[1].cfg.any_motion.select_z = BMI2_ENABLE;
  if (bmi270_set_sensor_config(cfg, 2, &dev) != BMI2_OK) return false;

  struct bmi2_int_pin_config pin = {};
  pin.pin_type = BMI2_INT1;
  if (bmi2_get_int_pin_config(&pin, &dev) != BMI2_OK) return false;
  pin.pin_type = BMI2_INT1;
  pin.int_latch = BMI2_INT_LATCH;  // la broche reste haute jusqu'au prochain démarrage
  pin.pin_cfg[0].lvl = BMI2_INT_ACTIVE_HIGH;
  pin.pin_cfg[0].od = BMI2_INT_PUSH_PULL;
  pin.pin_cfg[0].output_en = BMI2_INT_OUTPUT_ENABLE;
  pin.pin_cfg[0].input_en = BMI2_INT_INPUT_DISABLE;
  if (bmi2_set_int_pin_config(&pin, &dev) != BMI2_OK) return false;

  // Vérification, et réécriture seule si besoin : actif à l'état haut, push-pull, sortie activée
  const uint8_t ioCtrl = 0x0A;
  uint8_t value = 0;
  bmiRead(BMI2_INT1_IO_CTRL_ADDR, &value, 1, &bmiAddr);
  if (value != ioCtrl) {
    bmiWrite(BMI2_INT1_IO_CTRL_ADDR, &ioCtrl, 1, &bmiAddr);
    delay(1);
    bmiRead(BMI2_INT1_IO_CTRL_ADDR, &value, 1, &bmiAddr);
    if (value != ioCtrl) return false;
  }

  struct bmi2_sens_int_config map = {};
  map.type = BMI2_ANY_MOTION;
  map.hw_int_pin = BMI2_INT1;
  if (bmi270_map_feat_int(&map, 1, &dev) != BMI2_OK) return false;

  uint8_t sensors[] = {BMI2_ACCEL, BMI2_ANY_MOTION};
  if (bmi270_sensor_enable(sensors, 2, &dev) != BMI2_OK) return false;
  if (bmi2_set_adv_power_save(BMI2_ENABLE, &dev) != BMI2_OK) return false;

  uint16_t status;
  bmi2_get_int_status(&status, &dev);  // efface une éventuelle interruption en attente
  return true;
}

static void suspendMagnetometer() {
  Wire1.beginTransmission(BMM150_ADDR);
  Wire1.write(BMM150_POWER_CTRL);
  Wire1.write(0x00);  // mode suspendu (~1 µA)
  Wire1.endTransmission();
}

void powerSleep() {
  suspendMagnetometer();
  if (!armMotionWake()) {
    // Sans réveil par mouvement, on ne s'endort pas : on redémarre et on réessaiera
    Serial.println(F("Erreur : réveil par mouvement impossible à configurer"));
    delay(100);
    NVIC_SystemReset();
  }

  // LED éteintes (les sorties gardent leur état en veille profonde)
  digitalWrite(LEDR, HIGH);
  digitalWrite(LEDG, HIGH);
  digitalWrite(LEDB, HIGH);
  digitalWrite(LED_BUILTIN, LOW);
  digitalWrite(LED_PWR, LOW);
#if VIBRATION_PIN >= 0
  digitalWrite(VIBRATION_PIN, LOW);
#endif

  // Réveil quand INT1 passe à l'état haut
  NRF_P0->PIN_CNF[BMI270_INT1_PIN] =
      (GPIO_PIN_CNF_DIR_Input << GPIO_PIN_CNF_DIR_Pos) |
      (GPIO_PIN_CNF_INPUT_Connect << GPIO_PIN_CNF_INPUT_Pos) |
      (GPIO_PIN_CNF_PULL_Pulldown << GPIO_PIN_CNF_PULL_Pos) |
      (GPIO_PIN_CNF_SENSE_High << GPIO_PIN_CNF_SENSE_Pos);

  Serial.println(F("Veille profonde"));
  Serial.flush();
  delay(50);

  NRF_POWER->SYSTEMOFF = 1;
  while (true) {}
}
