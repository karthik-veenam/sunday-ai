#include <cassert>
#include <cstdint>
#include "network_recovery.h"
int main(){
 sunday::NetworkRecovery n;n.begin(0);
 auto a=n.tick(20001,true,false,false);assert(a.startRecoveryAp&&!a.retryStation);
 a=n.tick(30000,true,false,true);assert(a.retryStation&&!a.startRecoveryAp);
 a=n.tick(60000,true,false,true);assert(a.retryStation);
 a=n.tick(61000,true,true,true);assert(a.stopRecoveryAp&&!a.retryStation);
 a=n.tick(70000,true,false,false);assert(!a.startRecoveryAp&&!a.retryStation);
 a=n.tick(90000,true,false,false);assert(a.startRecoveryAp);
 a=n.tick(100000,true,false,true);assert(a.retryStation);
 sunday::NetworkRecovery u;u.begin(0);a=u.tick(100000,false,false,true);assert(!a.retryStation&&!a.stopRecoveryAp);
 sunday::NetworkRecovery wrap;wrap.begin(UINT32_MAX-10000);a=wrap.tick(20000,true,false,true);assert(a.retryStation);
}
