#pragma once
#include <stdint.h>
#include "AlarmLogic.h"

struct SharedState {
  char session[65] = {};
  char captureSession[65] = {};
  uint32_t revision = 0, lastControl = 0, holdUntil = 0, captureUntil = 0;
  uint32_t sentinelMs = 1000, activeMs = 500, warmupMs = 60000, burstMs = 10000;
  uint32_t videoUntil = 0, videoInterval = 125;
  uint32_t controlRtt = 0, capturedFrames = 0, replacedFrames = 0;
  uint32_t lastJpegBytes = 0, videoReconnects = 0;
  int64_t captureAfterUs = 0;
  uint8_t mode = 0, purpose = 0; // mode: 0 DISARMED, 1 ARMED, 2 ENROLLMENT
  bool buzzer = false, forceOff = false, resetAlarm = false;
};

struct AlarmMessage {
  AlarmCommand command;
  char session[65] = {};
  uint32_t revision = 0;
  uint8_t control = 0; // 1 OFF, 2 SESSION RESET, 3 COMMAND
};
struct CommandAck {
  char id[65] = {};
  uint64_t generation = 0;
  uint32_t uptime = 0;
  uint8_t status = 0;
  bool on = false;
};
