#pragma once
#include <Arduino.h>
namespace glance {
void receive(const char* json); // audio task: bounded copy only
bool touch(uint32_t now);      // loop task, after M5.update; true for a touch gesture
uint32_t age(uint32_t now);
uint32_t drawAge(uint32_t now);
int currentPage(uint32_t now);
void dismiss();
void invalidate();
void compose(uint16_t* fb, int width, int height, uint32_t now, bool night);
void draw(uint32_t now, bool night); // loop task, after the original face
}
