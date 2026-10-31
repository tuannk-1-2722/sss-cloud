#include <cassert>
#include <cstdint>
#include <limits>
#include "../../firmware/security_node/FrameFreshness.h"

int main() {
  assert(frameAgeMs(2000000, 2000000) == 0);
  assert(frameAgeMs(2000001, 2000000) == 1);
  assert(frameAgeMs(3000000, 2000000) == 1000);
  assert(frameAgeMs(3000001, 2000000) > 1000);
  assert(frameAgeMs(2000000, 0) == std::numeric_limits<uint32_t>::max());
  assert(frameAgeMs(2000000, 2000001) == std::numeric_limits<uint32_t>::max());
  assert(frameAgeMs(3600000001LL, 1) > 1000); // Buffer predating a long DISARMED period.
  assert(frameAgeMs(std::numeric_limits<int64_t>::max(), 1) == std::numeric_limits<uint32_t>::max());
}
