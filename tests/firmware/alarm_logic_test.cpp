#include <assert.h>
#include "../../firmware/security_node/AlarmLogic.h"
int main() {
  AlarmLogic logic;
  AlarmCommand start;
  strcpy(start.id, "first"); start.generation = 1; start.type = 1;
  start.durationMs = 2000; start.leaseMs = 5000; start.allowed = true;
  assert(logic.apply(start, 100) == 0 && logic.on);
  assert(logic.apply(start, 900) == 1 && logic.deadline == 2100);
  logic.tick(2100, 2000); assert(!logic.on);
  start.generation = 2; strcpy(start.id, "expired"); start.leaseMs = 0;
  assert(logic.apply(start, 2200) == 2 && !logic.on);
  start.leaseMs = 5000; strcpy(start.id, "second");
  assert(logic.apply(start, UINT32_MAX - 500) == 0);
  logic.tick(1499, 1400); assert(!logic.on); // deadline works across millis wrap
  AlarmCommand stop; strcpy(stop.id, "stop"); stop.generation = 3; stop.type = 2; stop.allowed = true;
  assert(logic.apply(stop, 1700) == 0);
  assert(logic.apply(start, 1800) == 2 && !logic.on); // stale START after STOP
  start.generation = 4; strcpy(start.id, "queued-expired"); start.queued = true;
  start.queuedAt = 1900; start.leaseMs = 100;
  assert(logic.apply(start, 2000) == 2 && !logic.on);
  start.queued = false; start.leaseMs = 5000;
  start.generation = 4; start.durationMs = 3001;
  assert(logic.apply(start, 2000) == 2);
}
