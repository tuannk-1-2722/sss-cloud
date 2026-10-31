#pragma once
// Copy to secrets.h locally. Never commit the real values.
#define WIFI_SSID "YOUR_WIFI"
#define WIFI_PASSWORD "YOUR_WIFI_PASSWORD"
#define SERVER_URL "https://your-camera-domain.example"
#define DEVICE_ID "camera-1"
#define DEVICE_TOKEN "PROVISION_WITH_TOOLS_MANAGE_BOOTSTRAP"
#define ALARM_ACTIVE_HIGH true
// Optional: #define SSS_SERVER_ROOT_CA R"PEM(...your trusted public root CA...)PEM"
// ISRG Root X1 is supplied by ServerTrust.h. Public HTTP and insecure TLS are refused.
