#pragma once
struct FakeImu { bool enabled=true,ok=true;float value=32.5f;int calls=0;bool isEnabled(){return enabled;}bool getTemp(float* v){++calls;*v=value;return ok;} };
struct FakeM5 { FakeImu Imu; };
extern FakeM5 M5;
