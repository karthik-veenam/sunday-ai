#pragma once
#include <stdint.h>
#include <string.h>
namespace glance {
inline void fitFace(uint16_t* fb,int w,int h){
 if(!fb||w>320||w<3||h<3)return;
 uint16_t line[320];int dw=w*2/3,dh=h*2/3,left=(w-dw)/2;
 for(int y=0;y<dh;++y){int sy=y*h/dh;memcpy(line,fb+sy*w,w*sizeof(uint16_t));memset(fb+y*w,0,w*sizeof(uint16_t));for(int x=0;x<dw;++x)fb[y*w+left+x]=line[x*w/dw];}
 memset(fb+dh*w,0,(h-dh)*w*sizeof(uint16_t));
}
}

namespace glance {
struct CaptionLines {char line[2][128]={{0},{0}};int count=0;};
template<class Measure> inline CaptionLines rollingLines(const char* value,int width,Measure measure){
 CaptionLines out;char rest[128];size_t input=strlen(value);const char* start=value+(input>127?input-127:0);strncpy(rest,start,sizeof(rest)-1);rest[127]=0;
 while(rest[0]){char next[128];strcpy(next,rest);size_t n=strlen(next);while(n>1&&measure(next)>width)next[--n]=0;size_t cut=n;
  if(rest[n]){size_t space=0;for(size_t i=0;i<n;++i)if(rest[i]==' ')space=i;if(space>n/2)cut=space;next[cut]=0;}
  strcpy(out.line[0],out.line[1]);strcpy(out.line[1],next);if(out.count<2)++out.count;
  const char* remain=rest+cut;while(*remain==' ')++remain;memmove(rest,remain,strlen(remain)+1);
 }
 return out;
}
}
