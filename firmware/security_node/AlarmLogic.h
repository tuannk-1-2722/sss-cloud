#pragma once
#include <stdint.h>
#include <string.h>

// GPIO-free state machine. The alarm task is the only caller on the board.
struct AlarmCommand {
  char id[65] = {};
  uint64_t generation = 0;
  uint32_t durationMs = 0;
  int32_t leaseMs = 0;
  uint8_t type = 0; // 1 START, 2 STOP, 3 TEST
  bool allowed = false;
  bool queued = false;
  uint32_t queuedAt = 0;
};

class AlarmLogic {
 public:
  bool on = false;
  uint64_t generation = 0;
  uint32_t deadline = 0;
  char lastId[65] = {};

  void off() { on = false; }
  void resetSession() { off(); generation = 0; lastId[0] = 0; }
  void tick(uint32_t now, uint32_t lastControl) {
    if (on && (int32_t(now - deadline) >= 0 || uint32_t(now - lastControl) > 5000)) off();
  }
  // 0 applied, 1 duplicate, 2 rejected. Repeated commands cannot extend a deadline.
  uint8_t apply(const AlarmCommand& cmd, uint32_t now) {
    if (!cmd.allowed || cmd.generation < generation) return 2;
    if (strcmp(cmd.id, lastId) == 0 && cmd.generation == generation) return 1;
    if (cmd.generation == generation && lastId[0]) return 2;
    if (cmd.type == 2) off();
    else if ((cmd.type == 1 || cmd.type == 3) && cmd.leaseMs > 0
             && (!cmd.queued || uint32_t(now - cmd.queuedAt) < uint32_t(cmd.leaseMs))
             && cmd.durationMs > 0
             && cmd.durationMs <= (cmd.type == 3 ? 1000u : 3000u)) {
      on = true;
      deadline = now + cmd.durationMs;
    } else return 2;
    generation = cmd.generation;
    strncpy(lastId, cmd.id, sizeof(lastId) - 1);
    return 0;
  }
};
