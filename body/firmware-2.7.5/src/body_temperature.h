#pragma once
namespace bodytemperature {
// BMI270 die temperature in Celsius. Not calibrated room temperature.
// No bus access while camera owns I2C; false leaves the output untouched.
bool read(float* celsius);
}
