#pragma once

#include <stdint.h>
#include <limits>

// esp32-camera timestamps the first DMA buffer using esp_timer_get_time().
// Round up conservatively; invalid/future timestamps are never considered fresh.
inline uint32_t frameAgeMs(int64_t nowUs, int64_t capturedUs) {
  const uint32_t invalid = std::numeric_limits<uint32_t>::max();
  if (capturedUs <= 0 || capturedUs > nowUs) return invalid;
  uint64_t ageMs = (uint64_t(nowUs - capturedUs) + 999u) / 1000u;
  return ageMs > invalid ? invalid : uint32_t(ageMs);
}
