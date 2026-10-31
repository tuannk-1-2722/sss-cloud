#include <Arduino.h>
#include <WiFi.h>
#include <HTTPClient.h>
#include <NetworkClientSecure.h>
#include <WebSocketsClient.h>
#include <esp_heap_caps.h>
#include <time.h>
#include <sys/time.h>
#include <new>
#include <ArduinoJson.h>
#include "esp_camera.h"
#include "esp_system.h"
#include "esp_timer.h"
#include "AlarmLogic.h"
#include "FrameFreshness.h"
#include "NodeState.h"
#include "secrets.h"
#include "ServerTrust.h"
#include "ServerAddress.h"
#include "VideoWindow.h"

// AI-Thinker ESP32-CAM + OV2640. No SD card. Review against the actual board.
constexpr int PIR_PIN = 13, ALARM_PIN = 14;
constexpr uint32_t CONTROL_TIMEOUT = 8000;
portMUX_TYPE stateMux = portMUX_INITIALIZER_UNLOCKED;

SharedState shared;

QueueHandle_t alarmQueue, ackQueue;
char bootId[33];
bool cameraReady = false;
SemaphoreHandle_t videoMutex;
struct VideoFrame {
  uint8_t* bytes = nullptr;
  size_t jpegLength = 0;
  uint64_t seq = 0;
  int64_t capturedUs = 0;
  SharedState state;
};
VideoFrame* pendingVideo = nullptr;
void freeVideo(VideoFrame* frame) { if (frame) { free(frame->bytes); delete frame; } }
VideoFrame* takeVideo() {
  xSemaphoreTake(videoMutex, portMAX_DELAY);
  VideoFrame* result = pendingVideo; pendingVideo = nullptr;
  xSemaphoreGive(videoMutex); return result;
}
bool transportReady(const ServerAddress& address) {
  return transportPermitted(address,time(nullptr),strlen(SSS_SERVER_ROOT_CA));
}

// Use a random RFC6455 mask for large JPEGs too (the library's generic large send uses zero).
class VideoSocket : public WebSocketsClient {
 public:
  VideoSocket() {
    clearClientCert();
  }
  void clearClientCert() {
    _client_cert = nullptr;
    _client_key = nullptr;
    _CA_cert = nullptr;
    _CA_bundle = nullptr;
#if defined(ESP32) && ESP_ARDUINO_VERSION >= ESP_ARDUINO_VERSION_VAL(3, 0, 4)
    _CA_bundle_size = 0;
#endif
  }
  bool sendVideo(uint8_t* bytes, size_t length) {
    if (!isConnected() || !_client.tcp) return false;
    _client.tcp->setTimeout(1000);
    uint8_t mask[4], header[WEBSOCKETS_MAX_HEADER_SIZE];
    esp_fill_random(mask, sizeof(mask));
    uint8_t count = createHeader(header, WSop_binary, length, true, mask, true);
    for (size_t i=0; i<length; ++i) bytes[i] ^= mask[i%4];
    return write(&_client, header, count)==count && write(&_client, bytes, length)==length;
  }
};

SharedState snapshot() {
  portENTER_CRITICAL(&stateMux);
  SharedState copy = shared;
  portEXIT_CRITICAL(&stateMux);
  return copy;
}

void queueOff(bool reset = false) {
  AlarmMessage message;
  message.control = reset ? 2 : 1;
  // Short bounded wait; independent alarm task continues to enforce its deadline.
  if (xQueueSend(alarmQueue, &message, pdMS_TO_TICKS(30)) != pdTRUE) {
    xQueueReset(alarmQueue);
    portENTER_CRITICAL(&stateMux);
    shared.forceOff = true;
    shared.resetAlarm = shared.resetAlarm || reset;
    portEXIT_CRITICAL(&stateMux);
  }
}

void alarmTask(void*) {
  AlarmLogic alarm;
  for (;;) {
    portENTER_CRITICAL(&stateMux);
    bool forceOff = shared.forceOff;
    bool resetAlarm = shared.resetAlarm;
    shared.forceOff = false;
    shared.resetAlarm = false;
    portEXIT_CRITICAL(&stateMux);
    if (resetAlarm) { alarm.resetSession(); xQueueReset(ackQueue); }
    if (forceOff) alarm.off();
    AlarmMessage message;
    while (xQueueReceive(alarmQueue, &message, 0) == pdTRUE) {
      if (message.control == 1) alarm.off();
      else if (message.control == 2) { alarm.resetSession(); xQueueReset(ackQueue); }
      else {
        SharedState current = snapshot();
        // Queue time and intervening mode/session changes are part of command validity.
        message.command.allowed = message.command.allowed
          && strcmp(message.session, current.session) == 0
          && message.revision == current.revision
          && (message.command.type == 2 || (message.command.type == 1 && current.mode == 1)
              || (message.command.type == 3 && current.mode == 0 && current.purpose == 0));
        uint8_t result = alarm.apply(message.command, millis());
        CommandAck ack;
        strncpy(ack.id, message.command.id, sizeof(ack.id) - 1);
        ack.generation = message.command.generation;
        ack.status = result;
        ack.on = alarm.on;
        ack.uptime = millis();
        if (xQueueSend(ackQueue, &ack, 0) != pdTRUE) {
          alarm.off();
          xQueueReset(alarmQueue);
          Serial.println("ACK_QUEUE_OVERFLOW_OFF");
        }
      }
    }
    SharedState state = snapshot();
    alarm.tick(millis(), state.lastControl);
    digitalWrite(ALARM_PIN, alarm.on == ALARM_ACTIVE_HIGH ? HIGH : LOW);
    portENTER_CRITICAL(&stateMux);
    shared.buzzer = alarm.on;
    portEXIT_CRITICAL(&stateMux);
    vTaskDelay(pdMS_TO_TICKS(20));
  }
}

bool cameraInit() {
  if (!psramFound()) return false;
  camera_config_t c = {};
  c.ledc_channel = LEDC_CHANNEL_0;
  c.ledc_timer = LEDC_TIMER_0;
  c.pin_d0 = 5; c.pin_d1 = 18; c.pin_d2 = 19; c.pin_d3 = 21;
  c.pin_d4 = 36; c.pin_d5 = 39; c.pin_d6 = 34; c.pin_d7 = 35;
  c.pin_xclk = 0; c.pin_pclk = 22; c.pin_vsync = 25; c.pin_href = 23;
  c.pin_sccb_sda = 26; c.pin_sccb_scl = 27;
  c.pin_pwdn = 32; c.pin_reset = -1;
  c.xclk_freq_hz = 20000000;
  c.pixel_format = PIXFORMAT_JPEG;
  c.frame_size = FRAMESIZE_VGA;
  c.jpeg_quality = 15;
  c.jpeg_buffer_size = 131072;
  c.fb_count = 2;
  c.fb_location = CAMERA_FB_IN_PSRAM;
  c.grab_mode = CAMERA_GRAB_LATEST;
  if (esp_camera_init(&c) != ESP_OK) return false;
  // Keep auto exposure, white balance and gain enabled when daylight changes.
  // These controls CANNOT recover usable faces in darkness without illumination.
  sensor_t* sensor = esp_camera_sensor_get();
  if (!sensor) return false;
  if (sensor->set_exposure_ctrl(sensor, 1) != 0 ||
      sensor->set_gain_ctrl(sensor, 1) != 0 ||
      sensor->set_whitebal(sensor, 1) != 0) {
    Serial.println("CAM_AUTO_EXPOSURE_CONFIG_UNVERIFIED");
  }
  return true;
}

uint32_t cameraFrameAge(const camera_fb_t* frame) {
  int64_t capturedUs = int64_t(frame->timestamp.tv_sec) * 1000000LL + frame->timestamp.tv_usec;
  return frameAgeMs(esp_timer_get_time(), capturedUs);
}

bool capturedAfter(const camera_fb_t* frame, int64_t barrierUs) {
  int64_t capturedUs = int64_t(frame->timestamp.tv_sec) * 1000000LL + frame->timestamp.tv_usec;
  return capturedUs >= barrierUs;
}

int jsonPost(const char* route, JsonDocument& request, JsonDocument& result) {
  static HTTPClient http;
  static NetworkClient plain;
  static NetworkClientSecure secure;
  ServerAddress address = serverAddress(SERVER_URL);
  if (!transportReady(address)) {
    static uint32_t lastLog=0;
    if (uint32_t(millis()-lastLog)>5000) {
      lastLog=millis(); Serial.println(address.valid ? "TLS_WAIT_FOR_VALID_CLOCK_AND_CA" : "SERVER_URL_INVALID");
    }
    return -3;
  }
  secure.setCACert(SSS_SERVER_ROOT_CA);
  secure.setHandshakeTimeout(5);
  String base(SERVER_URL);
  if (base.endsWith("/")) base.remove(base.length()-1);
  String url = base + "/api/v2/devices/" + DEVICE_ID + route;
  http.setConnectTimeout(5000);
  http.setTimeout(5000);
  NetworkClient& client = address.tls ? static_cast<NetworkClient&>(secure) : plain;
  if (!http.begin(client, url)) return -1;
  http.setReuse(true);
  http.addHeader("Authorization", String("Bearer ") + DEVICE_TOKEN);
  http.addHeader("Content-Type", "application/json");
  String body;
  serializeJson(request, body);
  int status = http.POST(body);
  if (status == 200) {
    String response = http.getString();
    if (response.length() > 16384 || deserializeJson(result, response)) status = -2;
  }
  http.end();
  return status;
}

void controlTask(void*) {
  uint32_t sequence = 0, nextAttempt = 0, backoff = 1000;
  CommandAck pending[8];
  int pendingCount = 0;
  char knownSession[65] = {};
  for (;;) {
    uint32_t now = millis();
    if (WiFi.status() != WL_CONNECTED) {
      queueOff();
      portENTER_CRITICAL(&stateMux);
      shared.session[0] = 0; shared.mode = 0; shared.videoUntil = 0;
      portEXIT_CRITICAL(&stateMux);
      if (int32_t(now - nextAttempt) >= 0) {
        WiFi.disconnect(); WiFi.begin(WIFI_SSID, WIFI_PASSWORD);
        nextAttempt = now + backoff + esp_random() % 300;
        backoff = min(uint32_t(backoff * 2), uint32_t(15000));
      }
      vTaskDelay(pdMS_TO_TICKS(250));
      continue;
    }
    backoff = 1000;
    if (time(nullptr) < 1700000000) {
      static bool timeSeeded = false;
      if (!timeSeeded) {
        timeSeeded = true;
        timeval tv = { 1793448000, 0 };
        settimeofday(&tv, nullptr);
        Serial.println("TIME_SEEDED_FOR_TLS");
        configTime(0, 0, "time.google.com", "pool.ntp.org");
      }
    }
    SharedState state = snapshot();
    if (!state.session[0]) {
      JsonDocument request, response;
      request["protocol_version"] = 2;
      request["boot_id"] = bootId;
      request["firmware_version"] = "2.2.0";
      request["source"] = "hardware";
      request["psram_bytes"] = ESP.getPsramSize();
      uint32_t start = millis();
      int status = jsonPost("/hello", request, response);
      uint32_t elapsed = millis() - start;
      if (status == 200 && elapsed <= CONTROL_TIMEOUT) {
        const char* receivedSession = response["session_id"] | "";
        Serial.printf("HELLO_OK session=%s elapsed=%u\n", receivedSession, unsigned(elapsed));
        bool newSession = strcmp(knownSession, receivedSession) != 0;
        queueOff(newSession);
        if (newSession) {
          strlcpy(knownSession, receivedSession, sizeof(knownSession));
          sequence = 0; pendingCount = 0;
        }
        portENTER_CRITICAL(&stateMux);
        strlcpy(shared.session, response["session_id"] | "", sizeof(shared.session));
        shared.revision = response["mode_revision"];
        shared.mode = 0;
        shared.purpose = 0;
        shared.captureAfterUs = esp_timer_get_time();
        portEXIT_CRITICAL(&stateMux);
      } else {
        static uint32_t lastHelloErr = 0;
        if (uint32_t(now - lastHelloErr) >= 3000) {
          lastHelloErr = now;
          Serial.printf("HELLO_FAIL status=%d elapsed=%u\n", status, unsigned(elapsed));
        }
      }
      vTaskDelay(pdMS_TO_TICKS(1000));
      continue;
    }
    CommandAck ack;
    while (pendingCount < 8 && xQueueReceive(ackQueue, &ack, 0) == pdTRUE) {
      bool replaced = false;
      for (int i = 0; i < pendingCount; ++i) if (strcmp(pending[i].id, ack.id) == 0) {
        pending[i] = ack; replaced = true; break;
      }
      if (!replaced) pending[pendingCount++] = ack;
    }
    JsonDocument request, response;
    request["session_id"] = state.session; request["boot_id"] = bootId;
    request["sync_seq"] = ++sequence; request["uptime_ms"] = uint64_t(esp_timer_get_time()/1000);
    request["buzzer_state"] = state.buzzer ? "ON" : "OFF";
    request["pir_high"] = digitalRead(PIR_PIN) == HIGH;
    JsonObject telemetry = request["telemetry"].to<JsonObject>();
    telemetry["control_rtt_ms"] = state.controlRtt;
    telemetry["free_heap_bytes"] = ESP.getFreeHeap();
    telemetry["free_psram_bytes"] = ESP.getFreePsram();
    telemetry["frames_captured"] = state.capturedFrames;
    telemetry["frames_replaced"] = state.replacedFrames;
    telemetry["last_jpeg_bytes"] = state.lastJpegBytes;
    telemetry["video_reconnects"] = state.videoReconnects;
    JsonArray acks = request["acks"].to<JsonArray>();
    for (int i = 0; i < pendingCount; ++i) {
      JsonObject a = acks.add<JsonObject>();
      a["command_id"] = pending[i].id; a["generation"] = pending[i].generation;
      a["status"] = pending[i].status == 0 ? "applied" : pending[i].status == 1 ? "duplicate" : "rejected";
      a["buzzer_state"] = pending[i].on ? "ON" : "OFF";
      a["executed_uptime_ms"] = pending[i].uptime;
    }
    uint32_t start = millis();
    int status = jsonPost("/sync", request, response);
    uint32_t rtt = millis() - start;
    if (status == 409 || status == 401 || status == 403) {
      queueOff();
      portENTER_CRITICAL(&stateMux); shared.session[0] = 0; shared.mode = 0; shared.videoUntil = 0; portEXIT_CRITICAL(&stateMux);
    } else if (status == 200 && rtt <= CONTROL_TIMEOUT && response["sync_seq"].as<uint32_t>() == sequence
               && strcmp(response["session_id"] | "", state.session) == 0
               && response["mode_revision"].as<uint32_t>() >= state.revision) {
      const char* mode = response["mode"] | "DISARMED";
      uint8_t newMode = strcmp(mode, "ARMED") == 0 ? 1 : strcmp(mode, "ENROLLMENT") == 0 ? 2 : 0;
      uint32_t revision = response["mode_revision"];
      if (revision != state.revision || newMode == 2) queueOff();
      for (JsonVariant id : response["acked_command_ids"].as<JsonArray>()) {
        for (int i = 0; i < pendingCount; ++i) if (strcmp(pending[i].id, id.as<const char*>()) == 0) {
          pending[i] = pending[--pendingCount]; --i;
        }
      }
      portENTER_CRITICAL(&stateMux);
      if (revision != shared.revision) shared.captureAfterUs = esp_timer_get_time();
      shared.mode = newMode; shared.revision = revision; shared.lastControl = millis();
      shared.controlRtt = rtt;
      shared.holdUntil = millis() + min(response["capture_hold_ms"].as<uint32_t>(), uint32_t(3000));
      uint32_t lease = response["capture_lease_ms"];
      shared.captureUntil = millis() + (lease > rtt ? lease - rtt : 0);
      const char* purpose = response["capture_purpose"] | "";
      shared.purpose = strcmp(purpose, "preview") == 0 ? 1 : strcmp(purpose, "enrollment") == 0 ? 2 : 0;
      strlcpy(shared.captureSession, response["capture_session_id"] | "", sizeof(shared.captureSession));
      shared.sentinelMs = response["camera"]["sentinel_ms"] | 1000u;
      shared.activeMs = response["camera"]["active_ms"] | 500u;
      uint32_t videoLease = min(response["video"]["lease_ms"].as<uint32_t>(), uint32_t(5000));
      shared.videoUntil = millis() + ((response["video"]["enabled"] | false) && videoLease > rtt ? videoLease-rtt : 0);
      shared.videoInterval = constrain(response["video"]["interval_ms"] | 125u, 100u, 1000u);
      shared.warmupMs = response["camera"]["pir_warmup_ms"] | 60000u;
      shared.burstMs = response["camera"]["pir_burst_ms"] | 10000u;
      portEXIT_CRITICAL(&stateMux);
      JsonObject cmd = response["command"].as<JsonObject>();
      if (!cmd.isNull()) {
        AlarmMessage message; message.control = 3;
        strlcpy(message.session, state.session, sizeof(message.session));
        message.revision = revision;
        message.command.queued = true;
        message.command.queuedAt = millis();
        strlcpy(message.command.id, cmd["command_id"] | "", sizeof(message.command.id));
        message.command.generation = cmd["generation"];
        message.command.durationMs = cmd["duration_ms"];
        message.command.leaseMs = cmd["remaining_ttl_ms"].as<int32_t>() - int32_t(rtt);
        const char* type = cmd["type"] | "";
        message.command.type = strcmp(type,"START_ALARM")==0?1:strcmp(type,"STOP_ALARM")==0?2:strcmp(type,"TEST_ALARM")==0?3:0;
        message.command.allowed = strcmp(cmd["session_id"] | "", state.session) == 0
          && strcmp(cmd["boot_id"] | "", bootId) == 0 && cmd["mode_revision"].as<uint32_t>() == revision
          && (message.command.type == 2 || (message.command.type == 1 && newMode == 1)
              || (message.command.type == 3 && newMode == 0
                  && response["capture_lease_ms"].as<uint32_t>() == 0));
        if (xQueueSend(alarmQueue, &message, pdMS_TO_TICKS(30)) != pdTRUE) queueOff();
      }
    }
    vTaskDelay(pdMS_TO_TICKS(1000));
  }
}

void captureTask(void*) {
  uint64_t sequence = 0;
  uint32_t lastCapture = 0, lastInit = 0, dropped = 0, lastLog = 0;
  for (;;) {
    uint32_t now = millis();
    SharedState state = snapshot();
    if (!cameraReady && uint32_t(now-lastInit) >= 5000) {
      lastInit = now; cameraReady = cameraInit();
      if (!cameraReady) Serial.println("CAM_INIT_FAILED_CHECK_PSRAM_POWER");
    }
    bool allowed = state.session[0] && uint32_t(now-state.lastControl)<5000
                   && int32_t(state.videoUntil-now)>0;
    if (!cameraReady || WiFi.status()!=WL_CONNECTED || !allowed) {
      freeVideo(takeVideo()); vTaskDelay(pdMS_TO_TICKS(25)); continue;
    }
    if (uint32_t(now-lastCapture) < state.videoInterval) { vTaskDelay(pdMS_TO_TICKS(5)); continue; }
    lastCapture = now;
    camera_fb_t* frame = esp_camera_fb_get();
    if (!frame) { Serial.println("FB_NULL"); continue; }
    if (cameraFrameAge(frame)>1000 || !capturedAfter(frame,state.captureAfterUs)) {
      esp_camera_fb_return(frame); frame=esp_camera_fb_get();
      if (!frame) { Serial.println("FB_NULL_AFTER_STALE"); continue; }
    }
    VideoFrame* copy = nullptr;
    if (frame->len<=524288 && cameraFrameAge(frame)<=1000 && capturedAfter(frame,state.captureAfterUs)) {
      copy = new(std::nothrow) VideoFrame;
      if (copy) copy->bytes = static_cast<uint8_t*>(heap_caps_malloc(2050+frame->len, MALLOC_CAP_SPIRAM|MALLOC_CAP_8BIT));
      if (copy && copy->bytes) {
        copy->jpegLength=frame->len; copy->seq=++sequence; copy->state=state;
        copy->capturedUs=int64_t(frame->timestamp.tv_sec)*1000000LL+frame->timestamp.tv_usec;
        memcpy(copy->bytes+2050,frame->buf,frame->len);
      } else { freeVideo(copy); copy=nullptr; Serial.println("VIDEO_ALLOC_FAILED"); }
    }
    esp_camera_fb_return(frame); // Never retain a driver framebuffer during network I/O.
    size_t copiedLength = copy ? copy->jpegLength : 0;
    if (copy) {
      xSemaphoreTake(videoMutex,portMAX_DELAY);
      VideoFrame* old=pendingVideo; pendingVideo=copy;
      xSemaphoreGive(videoMutex);
      if (old) ++dropped;
      freeVideo(old);
    }
    portENTER_CRITICAL(&stateMux);
    shared.capturedFrames = uint32_t(sequence);
    shared.replacedFrames = dropped;
    shared.lastJpegBytes = uint32_t(copiedLength);
    portEXIT_CRITICAL(&stateMux);
    if (uint32_t(now-lastLog)>=5000) {
      lastLog=now;
      Serial.printf("VIDEO_CAPTURE seq=%llu bytes=%u replaced=%lu heap=%u psram=%u stack_hwm=%u\n",
        (unsigned long long)sequence, unsigned(copiedLength),
        (unsigned long)dropped, ESP.getFreeHeap(), ESP.getFreePsram(),
        unsigned(uxTaskGetStackHighWaterMark(NULL)));
    }
  }
}

void videoTask(void*) {
  static VideoSocket socket;
  VideoWindow window;
  bool configured=false, ready=false;
  char session[65]={};
  String headers=String("Authorization: Bearer ")+DEVICE_TOKEN;
  socket.onEvent([&](WStype_t type, uint8_t* payload, size_t length) {
    if (type==WStype_CONNECTED) {
      Serial.printf("WS_CONNECTED max_alloc=%u heap=%u psram=%u stack_hwm=%u\n",
                    ESP.getMaxAllocHeap(), ESP.getFreeHeap(), ESP.getFreePsram(),
                    unsigned(uxTaskGetStackHighWaterMark(NULL)));
    }
    if (type==WStype_DISCONNECTED || type==WStype_ERROR) {
      ready=false; window.clear();
      portENTER_CRITICAL(&stateMux); ++shared.videoReconnects; portEXIT_CRITICAL(&stateMux);
      static uint32_t lastLog=0;
      if (uint32_t(millis()-lastLog)>2000) {
        lastLog=millis();
        String reason = (payload && length > 0) ? String((const char*)payload, length) : String("NONE");
        Serial.printf("WS_DISCONNECTED type=%d reason=%s max_alloc=%u heap=%u psram=%u\n",
                      (int)type, reason.c_str(), ESP.getMaxAllocHeap(), ESP.getFreeHeap(), ESP.getFreePsram());
      }
    }
    if (type!=WStype_TEXT) return;
    JsonDocument response;
    if (length>1024 || deserializeJson(response,payload,length)) { socket.disconnect(); return; }
    if (response["protocol_version"].as<int>()==1 && response["window_frames"].as<int>()==2) {
      ready=true; window.clear();
      Serial.println("WS_READY_STREAMING");
      return;
    }
    if (strcmp(response["state"] | "", "video_received")!=0 || !window.ack(response["seq"].as<uint64_t>())) {
      socket.disconnect(); ready=false;
    }
  });
  for (;;) {
    SharedState state=snapshot();
    ServerAddress address=serverAddress(SERVER_URL);
    uint32_t now=millis();
    bool allowed=WiFi.status()==WL_CONNECTED && state.session[0]
      && uint32_t(now-state.lastControl)<5000 && int32_t(state.videoUntil-now)>0
      && transportReady(address);
    if (!allowed || (configured && strcmp(session,state.session)!=0)) {
      if (configured) socket.disconnect();
      configured=ready=false; window.clear(); freeVideo(takeVideo());
      vTaskDelay(pdMS_TO_TICKS(25)); continue;
    }
    if (!configured) {
      Serial.printf("WS_CONNECT_INIT max_alloc=%u heap=%u psram=%u\n",
                    ESP.getMaxAllocHeap(), ESP.getFreeHeap(), ESP.getFreePsram());
      socket.clearClientCert();
      String path=String("/api/v2/devices/")+DEVICE_ID+"/video";
      if (address.tls) {
        socket.beginSslWithCA(address.host,address.port,path.c_str(),SSS_SERVER_ROOT_CA,"");
        socket.clearClientCert();
      } else {
        socket.begin(address.host,address.port,path.c_str(),"");
      }
      socket.setExtraHeaders(headers.c_str()); socket.setReconnectInterval(1000);
      socket.enableHeartbeat(5000,4000,2);
      strlcpy(session,state.session,sizeof(session)); configured=true;
    }
    socket.loop();
    if (window.expired(millis())) { socket.disconnect(); ready=false; window.clear(); Serial.println("VIDEO_ACK_TIMEOUT"); }
    if (ready && window.canSend()) {
      VideoFrame* frame=takeVideo();
      if (frame) {
        SharedState current=snapshot();
        uint32_t age=frameAgeMs(esp_timer_get_time(),frame->capturedUs);
        bool fresh=age<=1000 && strcmp(frame->state.session,current.session)==0
          && frame->state.revision==current.revision && uint32_t(millis()-current.lastControl)<5000
          && int32_t(current.videoUntil-millis())>0;
        if (fresh) {
          JsonDocument meta;
          uint64_t sent=uint64_t(esp_timer_get_time()/1000), captured=uint64_t(frame->capturedUs/1000);
          meta["protocol_version"]=1; meta["session_id"]=frame->state.session; meta["boot_id"]=bootId;
          meta["seq"]=frame->seq; meta["mode_revision"]=frame->state.revision;
          if (frame->state.purpose==2) meta["capture_session_id"]=frame->state.captureSession;
          meta["captured_uptime_ms"]=captured; meta["sent_uptime_ms"]=sent; meta["capture_age_ms"]=sent-captured;
          size_t length=measureJson(meta);
          if (length<=2048) {
            static char header[2049];
            serializeJson(meta,header,sizeof(header));
            memmove(frame->bytes+2+length,frame->bytes+2050,frame->jpegLength);
            frame->bytes[0]=uint8_t(length); frame->bytes[1]=uint8_t(length>>8);
            memcpy(frame->bytes+2,header,length);
            uint32_t start=millis();
            if (!socket.sendVideo(frame->bytes,2+length+frame->jpegLength)
                || !window.add(frame->seq,start)) { socket.disconnect(); ready=false; window.clear(); }
          }
        }
        freeVideo(frame);
      }
    }
    vTaskDelay(pdMS_TO_TICKS(5));
  }
}

void setup() {
  pinMode(ALARM_PIN, OUTPUT);
  digitalWrite(ALARM_PIN, ALARM_ACTIVE_HIGH ? LOW : HIGH);
  pinMode(PIR_PIN, INPUT);
  Serial.begin(115200);
  for (int i = 0; i < 4; ++i) snprintf(bootId + i * 8, 9, "%08lx", (unsigned long)esp_random());
  alarmQueue = xQueueCreate(8, sizeof(AlarmMessage));
  ackQueue = xQueueCreate(8, sizeof(CommandAck));
  videoMutex = xSemaphoreCreateMutex();
  if (!alarmQueue || !ackQueue || !videoMutex) { Serial.println("QUEUE_INIT_FAILED"); return; }
  WiFi.mode(WIFI_STA); WiFi.setSleep(false); WiFi.begin(WIFI_SSID, WIFI_PASSWORD);
  timeval tv = { 1793448000, 0 };
  settimeofday(&tv, nullptr);
  configTime(0,0,"pool.ntp.org","time.google.com");
  if (!serverAddress(SERVER_URL).valid) Serial.println("SERVER_URL_INVALID_TLS_REQUIRED_FOR_PUBLIC_HOST");
  if (xTaskCreatePinnedToCore(alarmTask, "alarm", 2560, nullptr, 3, nullptr, 1) != pdPASS) {
    Serial.println("ALARM_TASK_INIT_FAILED"); return;
  }
  if (xTaskCreatePinnedToCore(controlTask, "control", 7168, nullptr, 2, nullptr, 0) != pdPASS) {
    Serial.println("CONTROL_TASK_INIT_FAILED"); return;
  }
  if (xTaskCreatePinnedToCore(videoTask, "video", 7168, nullptr, 1, nullptr, 0) != pdPASS) {
    Serial.println("VIDEO_TASK_INIT_FAILED"); return;
  }
  if (xTaskCreatePinnedToCore(captureTask, "capture", 4096, nullptr, 1, nullptr, 1) != pdPASS) {
    Serial.println("CAPTURE_TASK_INIT_FAILED"); return;
  }
  vTaskDelete(NULL);
}

size_t getArduinoLoopTaskStackSize(void) {
  return 3072;
}

void loop() {}
