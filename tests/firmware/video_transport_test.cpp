#include <cassert>
#include <initializer_list>
#include "../../firmware/security_node/ServerAddress.h"
#include "../../firmware/security_node/VideoWindow.h"

int main() {
  assert(serverAddress("https://camera.example/").valid);
  assert(serverAddress("https://camera.example:8443").port==8443);
  assert(serverAddress("http://192.168.1.10:8000").valid);
  assert(serverAddress("http://10.0.0.1").valid);
  assert(serverAddress("http://172.16.0.1").valid);
  assert(!serverAddress("http://172.32.0.1").valid);
  for (const char* bad : {"", "http://camera.example", "http://8.8.8.8", "https://user:pw@camera.example", "https://camera.example/path", "https://camera.example?token=x", "https://camera.example:65536", "https://camera.example:99999999999", "https://camera.example:0", "https://", "wss://camera.example"}) {
    assert(!serverAddress(bad).valid);
  }
  auto tls=serverAddress("https://camera.example");
  assert(!transportPermitted(tls,0,100));
  assert(!transportPermitted(tls,1800000000,0));
  assert(transportPermitted(tls,1800000000,100));
  VideoWindow window;
  assert(window.add(1,100)); assert(window.add(2,200));
  assert(!window.canSend() && !window.add(3,300));
  assert(!window.ack(2)); assert(window.count==2);
  assert(window.ack(1)); assert(window.add(3,300));
  assert(!window.ack(1)); assert(window.ack(2)); assert(window.ack(3));
  assert(window.canSend() && !window.expired(5000));
  window.add(4,0xfffffff0u);
  assert(!window.expired(100)); assert(!window.expired(2000));
  assert(!window.expired(2984)); // Exactly 3000 ms after 0xfffffff0, across wrap.
  assert(window.expired(2985)); // Timeout is strictly greater than 3000 ms.
  window.clear(); assert(window.count==0);
}
