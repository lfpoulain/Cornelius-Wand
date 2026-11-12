// Caméra au plafond pour la baguette Cornelius - Freenove ESP32-WROVER CAM (OV2640)
//
// - Photo JPEG 1024 x 768 servie sur http://<ip>/capture.jpg : Home Assistant l'envoie à
//   Jev pour situer la personne qui lance un sort et la direction de son bras. Sans
//   paramètre, la photo la plus récente ; ?ago=800 donne celle d'il y a 0,8 s, le moment
//   où l'on visait (la baguette dit depuis combien de temps).
// - Le serveur web tourne dans sa propre tâche : il répond tout de suite, sans attendre
//   la prochaine image de la caméra.
// - Page de mise au point http://<ip>/ (désactivable dans config.h). Le serveur ne traite
//   qu'une requête à la fois : fermer la page une fois la caméra cadrée.
// - Mise à jour sans fil (OTA) : port réseau « cornelius-camera » dans l'IDE Arduino. Il faut un
//   schéma de partition avec OTA (Outils > Partition Scheme > Minimal SPIFFS), flashé une
//   première fois en USB.

#include <WiFi.h>
#include <ArduinoOTA.h>
#include "esp_camera.h"
#include "esp_http_server.h"
#include "config.h"

// Brochage de la Freenove ESP32-WROVER CAM (identique à l'ESP32 WROVER-KIT)
#define PWDN_GPIO_NUM  -1
#define RESET_GPIO_NUM -1
#define XCLK_GPIO_NUM  21
#define SIOD_GPIO_NUM  26
#define SIOC_GPIO_NUM  27
#define Y9_GPIO_NUM    35
#define Y8_GPIO_NUM    34
#define Y7_GPIO_NUM    39
#define Y6_GPIO_NUM    36
#define Y5_GPIO_NUM    19
#define Y4_GPIO_NUM    18
#define Y3_GPIO_NUM    5
#define Y2_GPIO_NUM    4
#define VSYNC_GPIO_NUM 25
#define HREF_GPIO_NUM  23
#define PCLK_GPIO_NUM  22

// Dernières photos, pour retrouver celle du moment où l'on visait : chaque image de la caméra,
// rangée à la suite de la précédente dans photoBuf (on repart au début quand la fin est atteinte)
static_assert(PHOTO_BUFFER_BYTES >= PHOTO_MAX_BYTES, "tampon des photos trop petit");
struct Photo {
  size_t offset;  // position dans photoBuf
  size_t len;
  uint32_t time;  // début de la prise, sur l'horloge de millis()
};
static uint8_t* photoBuf;
static Photo photos[PHOTO_SLOTS];  // de la plus ancienne à la plus récente, à partir de photoFirst
static int photoFirst = 0, photoCount = 0;
static uint32_t nextStore = 0;  // horodatage à partir duquel ranger la prochaine image
static SemaphoreHandle_t photosLock;  // partagé entre la boucle et le serveur web
static uint8_t* sendBuf;  // copie de la photo en cours d'envoi (le serveur web n'a qu'une tâche)
static uint32_t lastFrame = 0;   // millis() de la dernière image rendue par la caméra
static httpd_handle_t httpd = nullptr;

static bool photosBegin() {
  photosLock = xSemaphoreCreateMutex();
  photoBuf = (uint8_t*)ps_malloc(PHOTO_BUFFER_BYTES);
  sendBuf = (uint8_t*)ps_malloc(PHOTO_MAX_BYTES);
  return photosLock != nullptr && photoBuf != nullptr && sendBuf != nullptr;
}

static const Photo& photoAt(int i) { return photos[(photoFirst + i) % PHOTO_SLOTS]; }

// Place pour len octets dans photoBuf (position dans at), en effaçant les plus anciennes photos.
// Faux si l'une d'elles a moins de PHOTO_KEEP_MS : l'image est sautée (à appeler avec photosLock pris)
static bool makeRoom(size_t len, uint32_t time, size_t& at) {
  while (photoCount) {
    const Photo& oldest = photoAt(0);
    const Photo& newest = photoAt(photoCount - 1);
    size_t end = newest.offset + newest.len;
    if (photoCount < PHOTO_SLOTS) {
      bool wrapped = oldest.offset >= end;  // les plus récentes sont reparties au début
      if (PHOTO_BUFFER_BYTES - end >= len && (!wrapped || oldest.offset - end >= len)) {
        at = end;  // juste après la plus récente
        return true;
      }
      if (!wrapped && oldest.offset >= len) {
        at = 0;  // plus de place à la fin : on repart au début
        return true;
      }
    }
    if (time - oldest.time < PHOTO_KEEP_MS) return false;
    photoFirst = (photoFirst + 1) % PHOTO_SLOTS;
    photoCount--;
  }
  at = 0;
  return true;
}

static void storePhoto(const camera_fb_t* fb) {
  if (fb->len > PHOTO_MAX_BYTES) {
    // Photo jetée : signalée au plus une fois par seconde
    static uint32_t rejected = 0, lastLog = 0;
    rejected++;
    if (millis() - lastLog >= 1000) {
      lastLog = millis();
      Serial.printf("Photos trop grosses jetées : %lu (la dernière : %u octets)\n", (unsigned long)rejected,
                    (unsigned)fb->len);
      rejected = 0;
    }
    return;
  }
  uint32_t time = (uint32_t)((uint64_t)fb->timestamp.tv_sec * 1000ULL + fb->timestamp.tv_usec / 1000);
  // Écart moyen entre photos rangées pour que PHOTO_KEEP_MS de photos de cette taille tiennent
  // dans le tampon (20 % de marge) : plus court que l'écart entre images tant que la place
  // suffit, chaque image est alors rangée. Sinon, une image sur une grille régulière
  const uint32_t keep = PHOTO_KEEP_MS * 6 / 5;
  uint32_t spacing = max((uint32_t)((uint64_t)keep * fb->len / PHOTO_BUFFER_BYTES), keep / PHOTO_SLOTS);
  if ((int32_t)(time - nextStore) < 0) return;
  nextStore += spacing;
  if ((int32_t)(time - nextStore) > 0) nextStore = time;  // images plus espacées : la suivante sera rangée
  size_t at;
  xSemaphoreTake(photosLock, portMAX_DELAY);
  if (makeRoom(fb->len, time, at)) {
    memcpy(photoBuf + at, fb->buf, fb->len);
    photos[(photoFirst + photoCount) % PHOTO_SLOTS] = {at, fb->len, time};
    photoCount++;
  }
  xSemaphoreGive(photosLock);
}

// Photo la plus proche de « il y a ago millisecondes » (à appeler avec photosLock pris)
static const Photo* findPhoto(uint32_t ago) {
  uint32_t wanted = millis() - ago;
  const Photo* best = nullptr;
  uint32_t bestGap = UINT32_MAX;
  for (int i = 0; i < photoCount; i++) {
    const Photo& p = photoAt(i);
    int32_t d = (int32_t)(p.time - wanted);  // signé : juste même si millis() - ago repasse sous zéro
    uint32_t gap = d < 0 ? (uint32_t)(-(int64_t)d) : (uint32_t)d;
    if (gap < bestGap) {
      best = &p;
      bestGap = gap;
    }
  }
  return best;
}

// GET /capture.jpg[?ago=ms] : photo pour Jev, avec son âge dans X-Photo-Age-Ms
static esp_err_t sendCapture(httpd_req_t* req) {
  uint32_t ago = 0;
  char query[32], value[12];
  if (httpd_req_get_url_query_str(req, query, sizeof(query)) == ESP_OK &&
      httpd_query_key_value(query, "ago", value, sizeof(value)) == ESP_OK) {
    ago = strtoul(value, nullptr, 10);
  }
  // Copie sous le verrou, envoi sans lui : la boucle continue de ranger les photos
  xSemaphoreTake(photosLock, portMAX_DELAY);
  const Photo* photo = findPhoto(ago);
  size_t len = 0;
  uint32_t takenAt = 0;
  if (photo) {
    memcpy(sendBuf, photoBuf + photo->offset, photo->len);
    len = photo->len;
    takenAt = photo->time;
  }
  xSemaphoreGive(photosLock);
  if (!len) {
    httpd_resp_set_status(req, "503 Service Unavailable");
    return httpd_resp_sendstr(req, "pas encore de photo");
  }
  char age[12];
  snprintf(age, sizeof(age), "%lu", (unsigned long)(millis() - takenAt));
  httpd_resp_set_type(req, "image/jpeg");
  httpd_resp_set_hdr(req, "Cache-Control", "no-store");
  httpd_resp_set_hdr(req, "X-Photo-Age-Ms", age);
  return httpd_resp_send(req, (const char*)sendBuf, len);
}

#if DEBUG_WEB_SERVER
static esp_err_t sendDebugPage(httpd_req_t* req) {
  httpd_resp_set_type(req, "text/html; charset=utf-8");
  return httpd_resp_sendstr(
      req,
      "<!doctype html><meta name=viewport content='width=device-width'>"
      "<title>Caméra baguette</title><body style='background:#111;color:#eee;font-family:sans-serif'>"
      "<h3>Caméra baguette</h3><img id=c style='max-width:100%'>"
      "<script>c.onload=c.onerror=()=>setTimeout(()=>c.src='/capture.jpg?t='+Date.now(),500);c.onerror()</script>");
}
#endif

static bool webBegin() {
  httpd_config_t config = HTTPD_DEFAULT_CONFIG();
  config.server_port = 80;
  config.lru_purge_enable = true;  // libère les connexions oubliées par les navigateurs
  if (httpd_start(&httpd, &config) != ESP_OK) return false;
  httpd_uri_t capture = {"/capture.jpg", HTTP_GET, sendCapture, nullptr};
  httpd_register_uri_handler(httpd, &capture);
#if DEBUG_WEB_SERVER
  httpd_uri_t page = {"/", HTTP_GET, sendDebugPage, nullptr};
  httpd_register_uri_handler(httpd, &page);
#endif
  return true;
}

static bool cameraBegin() {
  camera_config_t config = {};
  config.ledc_channel = LEDC_CHANNEL_0;
  config.ledc_timer = LEDC_TIMER_0;
  config.pin_d0 = Y2_GPIO_NUM;
  config.pin_d1 = Y3_GPIO_NUM;
  config.pin_d2 = Y4_GPIO_NUM;
  config.pin_d3 = Y5_GPIO_NUM;
  config.pin_d4 = Y6_GPIO_NUM;
  config.pin_d5 = Y7_GPIO_NUM;
  config.pin_d6 = Y8_GPIO_NUM;
  config.pin_d7 = Y9_GPIO_NUM;
  config.pin_xclk = XCLK_GPIO_NUM;
  config.pin_pclk = PCLK_GPIO_NUM;
  config.pin_vsync = VSYNC_GPIO_NUM;
  config.pin_href = HREF_GPIO_NUM;
  config.pin_sccb_sda = SIOD_GPIO_NUM;
  config.pin_sccb_scl = SIOC_GPIO_NUM;
  config.pin_pwdn = PWDN_GPIO_NUM;
  config.pin_reset = RESET_GPIO_NUM;
  config.xclk_freq_hz = 20000000;
  config.pixel_format = PIXFORMAT_JPEG;
  config.frame_size = PHOTO_FRAMESIZE;
  config.jpeg_quality = PHOTO_QUALITY;
  config.fb_count = 2;
  config.fb_location = CAMERA_FB_IN_PSRAM;
  config.grab_mode = CAMERA_GRAB_LATEST;
  return esp_camera_init(&config) == ESP_OK;
}

static void wifiBegin() {
  WiFi.setHostname(CAMERA_HOSTNAME);  // avant WiFi.mode, sinon le nom n'est pas pris
  WiFi.mode(WIFI_STA);
  WiFi.setSleep(false);  // sans veille Wi-Fi : la photo part sans délai de réveil
  WiFi.begin(WIFI_SSID, WIFI_PASSWORD);
  Serial.print("Wi-Fi");
  for (int i = 0; i < 60 && WiFi.status() != WL_CONNECTED; i++) {
    delay(500);
    Serial.print('.');
  }
  Serial.println(WiFi.status() == WL_CONNECTED ? " connecté : " + WiFi.localIP().toString()
                                               : " échec, nouvel essai en arrière-plan");
}

static void otaBegin() {
  ArduinoOTA.setHostname(CAMERA_HOSTNAME);
#ifdef OTA_PASSWORD
  ArduinoOTA.setPassword(OTA_PASSWORD);
#endif
  ArduinoOTA.onStart([]() { Serial.println("Mise à jour OTA…"); });
  ArduinoOTA.onError([](ota_error_t error) { Serial.printf("Erreur OTA %u\n", error); });
  ArduinoOTA.begin();
}

static void halt(const char* message) {
  Serial.println(message);
  delay(5000);
  ESP.restart();
}

void setup() {
  Serial.begin(115200);
  if (!psramFound()) Serial.println("Attention : PSRAM introuvable (activer PSRAM dans le menu Outils)");
  if (!cameraBegin()) halt("Erreur : caméra introuvable");
  if (!photosBegin()) halt("Erreur : mémoire insuffisante");
  wifiBegin();
  if (!webBegin()) halt("Erreur : serveur web");
  otaBegin();
  lastFrame = millis();  // le délai sans image de loop() part de la fin du démarrage
}

void loop() {
  camera_fb_t* fb = esp_camera_fb_get();
  if (fb) {
    lastFrame = millis();
    storePhoto(fb);
    esp_camera_fb_return(fb);
  }
  // Capture figée (le pilote ne rend plus d'image) : on redémarre. Des images toutes trop
  // grosses ne se règlent pas en redémarrant : storePhoto les signale seulement.
  if (millis() - lastFrame > 5000) halt("Erreur : aucune image de la caméra depuis 5 s");

  static uint32_t lastWifiTry = 0;
  if (WiFi.status() != WL_CONNECTED && millis() - lastWifiTry > 10000) {
    lastWifiTry = millis();
    WiFi.reconnect();
  }
  ArduinoOTA.handle();
}
