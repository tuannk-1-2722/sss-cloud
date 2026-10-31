#pragma once
#include <stdint.h>
#include <stddef.h>
#include <string.h>

struct ServerAddress {
  char host[254] = {};
  uint16_t port = 0;
  bool tls = false, valid = false;
};

// No credentials, path, query or public plaintext endpoint in a provisioned base URL.
inline ServerAddress serverAddress(const char* base) {
  ServerAddress out;
  if (!base) return out;
  out.tls = strncmp(base,"https://",8)==0;
  if (!out.tls && strncmp(base,"http://",7)!=0) return out;
  const char* value = base + (out.tls ? 8 : 7);
  size_t length = strlen(value);
  if (length && value[length-1]=='/') --length;
  if (!length || length>259) return out;
  size_t hostLength=0;
  while (hostLength<length && value[hostLength]!=':') ++hostLength;
  if (!hostLength || hostLength>=sizeof(out.host)) return out;
  for (size_t i=0; i<hostLength; ++i) {
    char c=value[i];
    if (!((c>='a' && c<='z') || (c>='A' && c<='Z') || (c>='0' && c<='9') || c=='.' || c=='-')) return out;
  }
  memcpy(out.host,value,hostLength);
  out.port=out.tls ? 443 : 80;
  if (hostLength<length) {
    uint32_t port=0;
    if (hostLength+1==length) return out;
    for (size_t i=hostLength+1; i<length; ++i) {
      if (value[i]<'0' || value[i]>'9') return out;
      port=port*10+uint32_t(value[i]-'0');
      if (port>65535) return out;
    }
    if (!port) return out;
    out.port=uint16_t(port);
  }
  if (!out.tls) {
    unsigned octets[4]={}, count=0, digits=0;
    for (size_t i=0; i<=hostLength; ++i) {
      char c=i<hostLength ? value[i] : '.';
      if (c=='.') {
        if (!digits || count>=4) return out;
        ++count; digits=0;
      } else {
        if (c<'0' || c>'9' || count>=4 || ++digits>3) return out;
        octets[count]=octets[count]*10+unsigned(c-'0');
        if (octets[count]>255) return out;
      }
    }
    if (count!=4 || !(octets[0]==10 || (octets[0]==172 && octets[1]>=16 && octets[1]<=31)
                          || (octets[0]==192 && octets[1]==168))) return out;
  }
  out.valid=true; return out;
}

inline bool transportPermitted(const ServerAddress& address, int64_t unixTime, size_t rootLength) {
  return address.valid && (!address.tls || (rootLength>0 && unixTime>1700000000));
}
