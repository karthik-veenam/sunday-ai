#include "M5Unified.h"
#include "body_temperature.h"
#include <cassert>
#include <cmath>
#include <cstdio>
FakeM5 M5;
static bool borrowed=false;
namespace cam { bool busBorrowed(){return borrowed;} }
int main(){
 float c=-99;
 borrowed=true;assert(!bodytemperature::read(&c));assert(M5.Imu.calls==0&&c==-99);
 borrowed=false;M5.Imu.enabled=false;assert(!bodytemperature::read(&c));assert(M5.Imu.calls==0);
 M5.Imu.enabled=true;assert(!bodytemperature::read(nullptr));assert(M5.Imu.calls==0);
 M5.Imu.ok=false;assert(!bodytemperature::read(&c));assert(c==-99);
 M5.Imu.ok=true;M5.Imu.value=NAN;assert(!bodytemperature::read(&c));assert(c==-99);
 M5.Imu.value=-41.0f;assert(!bodytemperature::read(&c));assert(c==-99);
 M5.Imu.value=32.5f;assert(bodytemperature::read(&c));assert(c==32.5f);
 puts("Temperature adapter: camera ownership, disabled IMU, null output, failed read, NaN, valid reading passed.");
}
