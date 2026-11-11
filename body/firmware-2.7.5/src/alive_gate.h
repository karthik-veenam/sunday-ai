#pragma once
#include <stdint.h>
// A render gate only: no emotion, motor, sound, network, or time scheduling policy.
namespace sunday {
class AliveGate {
 public:
  bool enabled=false, manual=false;
  uint32_t epoch=0, seq=0, deadline=0;
  int hint=-1, manualExpr=0, idleHint=-1;
  uint32_t idleDeadline=0; uint8_t idleFlags=0;
  bool idleValid(uint32_t now) const {return enabled && idleHint>=0 && (int32_t)(idleDeadline-now)>0;}
  void disconnect() { enabled=false; hint=-1; epoch=seq=deadline=idleDeadline=0;idleHint=-1;idleFlags=0; }
  bool update(uint32_t e,uint32_t s,bool on,int expression,uint32_t ttl,uint32_t now,int idle=-1,uint32_t idleTtl=0,uint8_t flags=0) {
    if (!e) {disconnect();return true;}
    if (epoch==e && s<seq)return false;
    if (epoch!=e) {epoch=e;seq=0;hint=-1;}
    seq=s;enabled=on;
    hint=on && expression>=0 && expression<13 && ttl>0 ? expression:-1;
    deadline=now+(ttl>120000?120000:ttl);
    idleHint=on && idle>=0 && idle<13 && idleTtl>0 ? idle:-1;
    idleDeadline=now+(idleTtl>45000?45000:idleTtl);idleFlags=on?flags:0;
    return true;
  }
  void setManual(int expression) {manual=true;manualExpr=expression;}
  void clearManual() {manual=false;}
  bool performing(uint32_t now) const {return enabled && hint>=0 && (int32_t)(deadline-now)>0;}
  int choose(int activity,int pet,bool protectedActivity,bool night,uint32_t now,bool idleAllowed=true) const {
    if (!enabled)return pet>=0?pet:activity;
    if (night)return activity;
    if (protectedActivity)return activity;
    if (manual)return manualExpr;
    if (performing(now))return hint;
    if (pet>=0)return pet;
    if (idleAllowed && (activity==0 || activity==6) && (idleFlags&1) && idleValid(now))return idleHint;
    return activity;
  }
};
}
