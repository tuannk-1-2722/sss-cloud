#pragma once
#include <stdint.h>

// Credits apply to transport, never to AI parallelism. No JPEG retry queue.
class VideoWindow {
 public:
  uint64_t seq[2] = {};
  uint32_t sent[2] = {};
  uint8_t count = 0;
  void clear() { count = 0; }
  bool canSend() const { return count < 2; }
  bool add(uint64_t value, uint32_t now) {
    if (!canSend()) return false;
    seq[count] = value; sent[count++] = now; return true;
  }
  bool ack(uint64_t value) {
    if (!count || seq[0] != value) return false;
    if (count == 2) { seq[0] = seq[1]; sent[0] = sent[1]; }
    --count; return true;
  }
  bool expired(uint32_t now) const { return count && uint32_t(now - sent[0]) > 3000; }
};
