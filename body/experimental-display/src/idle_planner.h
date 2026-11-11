#pragma once
#include <stdint.h>
// Local character behavior, not visual tracking. Eyes lead a bounded servo glance.
// No frame-rate hardware commands: emit only at lead/hold/return transitions.
namespace sunday {
struct IdlePlan {
  int expression=-1; float gazeX=0,gazeY=0;
  bool move=false,sound=false; int yaw=0,pitch=0,speed=200;
};
class IdlePlanner {
 public:
  uint32_t next=0,until=0,hourStart=0,lastSound=0,soundReady=0;
  unsigned actions=0; int active=0,last=0,phase=0,lastYawSign=0;
  int targetYaw=0,targetPitch=0; float eyeX=0,eyeY=0;
  static bool due(uint32_t now,uint32_t deadline) {return (int32_t)(now-deadline)>=0;}
  static uint32_t gap(bool bright,bool subdued,uint32_t random) {
    return subdued ? 22000+random%16001 : bright ? 10000+random%12001 : 14000+random%16001;
  }
  IdlePlan tick(uint32_t now,bool eligible,bool bright,bool subdued,bool motion,bool sounds,uint32_t random) {
    IdlePlan out;
    if (!eligible || !motion) {
      active=phase=0;targetYaw=targetPitch=0;eyeX=eyeY=0;
      if (!lastSound || due(now,soundReady))soundReady=now+8000+random%12001;
      next=now+5000+random%5001; // settle after the interaction; no long startup wait
      return out;
    }
    if (!next) {next=now+5000+random%5001;soundReady=now+8000+random%12001;}
    if (!hourStart || now-hourStart>=3600000) {hourStart=now;actions=0;}
    if (phase==1 && due(now,until)) {
      phase=2;until=now+2800+(random%2001);
      out.move=true;out.yaw=targetYaw;out.pitch=targetPitch;
      out.speed=subdued?200:bright?280:260;
      if (sounds && !subdued && due(now,soundReady) && (!lastSound || ((random>>20)%3)==0)) {
        out.sound=true;lastSound=now;soundReady=now+45000+random%45001;
      }
    } else if (phase==2 && due(now,until)) {
      phase=3;until=now+1800;
      out.move=true;out.yaw=out.pitch=0;out.speed=190;targetYaw=targetPitch=0;
      eyeX=eyeY=0;
    } else if (phase==3 && due(now,until)) {
      active=phase=0;
    }
    if (!phase && due(now,next)) {
      next=now+gap(bright,subdued,random);
      unsigned chance=(random>>8)%100;
      // Long rests and no-op prevent a metronomic scan loop.
      if (actions>=2 && chance<8)next+=15000+random%10001;
      if ((actions<2 || chance>=10) && actions<180) {
        int choice=actions<2 ? (int)actions+1 : 1+(int)((random>>16)%4);
        if (choice==last)choice=1+choice%4;
        int sign=lastYawSign>=0?-1:1;
        if (choice!=3) {
          if (choice!=4)choice=sign<0?1:2;
          lastYawSign=sign;
        }
        active=last=choice;actions++;phase=1;until=now+200;
        int amplitude=subdued?150+(random%71):250+(random%101); // <=35 degrees yaw
        targetYaw=choice==3?0:sign*amplitude;
        targetPitch=choice==3?(subdued?100:150)+(int)(random%71):choice==4?100+(int)(random%61):40+(int)(random%51);
        eyeX=targetYaw<0?-7.f:targetYaw>0?7.f:0.f;
        eyeY=choice==3?-6.f:choice==4?-3.f:-1.f;
      }
    }
    if (phase==1 || phase==2) {
      out.gazeX=eyeX;out.gazeY=eyeY;
      if (bright && active==4)out.expression=1; // Happy, never painted over Sad
    }
    return out;
  }
};
}
