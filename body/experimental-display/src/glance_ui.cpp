#include "glance_ui.h"
#include "glance_compositor.h"
#include <M5Unified.h>
#include <math.h>
#include <stdlib.h>
#include <string.h>
namespace glance {
struct Data { char title[40],line1[128],line2[96],room1[96],room2[96],timer1[64],media1[96],media2[96],sleep1[64],sleep2[128],alert1[64],alert2[96];uint32_t received=0,expires=0,timerEnd=0,timerDuration=0;uint8_t muted=2; };
static Data shared;
static M5Canvas canvas;
static lgfx::LGFXBase* destination = &M5.Display;
static lgfx::LGFXBase& surface(){return *destination;}
static uint16_t* attachedBuffer=nullptr;
static portMUX_TYPE mux=portMUX_INITIALIZER_UNLOCKED;
static uint32_t painted=0;
static int lastPage=-1;static char lastA[128]="",lastB[128]="";static uint32_t lastRemaining=0;static bool lastStale=false,lastNight=false;static uint8_t lastMuted=2;
static unsigned page=0;static uint32_t pageUntil=0;static int downX=0;
static const char* value(const char* j,const char* key) {char find[40];snprintf(find,sizeof(find),"\"%s\":",key);const char* p=strstr(j,find);if(!p)return nullptr;p+=strlen(find);while(*p==' ')++p;return p;}
static void text(const char* j,const char* key,char* out,size_t n) {const char* p=value(j,key);if(!p||*p++!='"'){out[0]=0;return;}size_t i=0;while(*p&&*p!='"'&&i+1<n){unsigned char c=*p++;out[i++]=(c>=32&&c<127&&c!='\\')?c:' ';}out[i]=0;}
static uint32_t number(const char* j,const char* key,uint32_t cap){const char* p=value(j,key);return p?min((uint32_t)strtoul(p,nullptr,10),cap):0;}
void receive(const char* j){Data d;uint32_t now=millis();
#define FIELD(n) text(j,#n,d.n,sizeof(d.n))
FIELD(title);FIELD(line1);FIELD(line2);FIELD(room1);FIELD(room2);FIELD(timer1);FIELD(media1);FIELD(media2);FIELD(sleep1);FIELD(sleep2);FIELD(alert1);FIELD(alert2);
#undef FIELD
 d.received=now;d.expires=now+number(j,"ttl_ms",30000);d.timerEnd=now+number(j,"timer_seconds",604800)*1000;d.timerDuration=number(j,"timer_duration",604800);d.muted=number(j,"muted",2);
 portENTER_CRITICAL(&mux);shared=d;portEXIT_CRITICAL(&mux);
}
bool touch(uint32_t now){if(age(now)==UINT32_MAX)return false;auto t=M5.Touch.getDetail();if(t.wasPressed())downX=t.x;if(!t.wasReleased())return false;int dx=t.x-downX;if(abs(dx)>45)page=(page+(dx<0?1:5))%6;else if(pageUntil&&int32_t(pageUntil-now)>0){pageUntil=0;return true;}pageUntil=now+(page==5?30000:12000);return true;}
static void wrap(const char* value,int y,int rows,int width=292,int size=1){
 char rest[128];strlcpy(rest,value,sizeof(rest));surface().setTextSize(size);surface().setFont(&fonts::Font2);
 for(int row=0;row<rows&&rest[0];++row){
  char line[128];strlcpy(line,rest,sizeof(line));size_t n=strlen(line);
  while(n>1&&surface().textWidth(line)>width)line[--n]=0;
  size_t cut=n;if(rest[n]){size_t space=0;for(size_t i=0;i<n;++i)if(rest[i]==' ')space=i;if(space>n/2)cut=space;line[cut]=0;}
  if(row==rows-1&&rest[cut]){size_t len=strlen(line);char trial[132];while(len>2){snprintf(trial,sizeof(trial),"%s...",line);if(surface().textWidth(trial)<=width)break;line[--len]=0;}strlcat(line,"...",sizeof(line));}
  surface().drawString(line,14,y+row*19*size);const char* next=rest+cut;while(*next==' ')++next;memmove(rest,next,strlen(next)+1);
 }
}
uint32_t age(uint32_t now){uint32_t received;portENTER_CRITICAL(&mux);received=shared.received;portEXIT_CRITICAL(&mux);return received?now-received:UINT32_MAX;}
uint32_t drawAge(uint32_t now){return painted?now-painted:UINT32_MAX;}
int currentPage(uint32_t now){return pageUntil&&int32_t(pageUntil-now)>0?(int)page:-1;}
void compose(uint16_t* fb,int w,int h,uint32_t now,bool night){
 // Attach the existing RGB565 framebuffer, without allocation or rescaling.
 if(attachedBuffer!=fb){canvas.setColorDepth(lgfx::rgb565_nonswapped);canvas.setBuffer(fb,w,h);attachedBuffer=fb;}
 destination=&canvas;draw(now,night);destination=&M5.Display;
}
void dismiss(){if(page==5&&currentPage(millis())==5){pageUntil=millis()+30000;return;}pageUntil=0;lastPage=-1;}
void invalidate(){lastPage=-1;}
static const char* countdown(uint32_t seconds){static char s[20];if(seconds>=3600)snprintf(s,sizeof(s),"%u:%02u:%02u",seconds/3600,(seconds/60)%60,seconds%60);else snprintf(s,sizeof(s),"%u:%02u",seconds/60,seconds%60);return s;}
void draw(uint32_t now,bool night){Data d;portENTER_CRITICAL(&mux);d=shared;portEXIT_CRITICAL(&mux);if(!d.received)return;bool stale=uint32_t(now-d.received)>15000;uint16_t fg=night?0x8410:0xffff,accent=night?0x8410:0x07ff;
 surface().setTextColor(fg,0);surface().setTextSize(1);surface().setFont(&fonts::Font2);
 bool detail=pageUntil&&int32_t(pageUntil-now)>0;
 uint32_t remain=!stale&&int32_t(d.timerEnd-now)>0?(d.timerEnd-now+999)/1000:0;
 if(detail){
 const char* first[]={d.room1,d.timer1,d.media1,d.sleep1,d.alert1,d.line2};const char* second[]={d.room2,"",d.media2,d.sleep2,d.alert2,""};
 bool changed=lastPage!=(int)page||strcmp(first[page],lastA)||strcmp(second[page],lastB)||stale!=lastStale||night!=lastNight||d.muted!=lastMuted;
 if(!changed){
  if(page==1&&remain!=lastRemaining){surface().fillRect(14,110,292,76,0);if(remain){surface().setTextColor(fg,0);surface().setTextSize(3);surface().drawCentreString(countdown(remain),160,116);}lastRemaining=remain;painted=now;}
  surface().setTextSize(1);surface().setFont(&fonts::Font0);return;
 }
 lastPage=page;strlcpy(lastA,first[page],sizeof(lastA));strlcpy(lastB,second[page],sizeof(lastB));lastStale=stale;lastNight=night;lastMuted=d.muted;lastRemaining=remain;painted=now;
 surface().fillScreen(0);const char* titles[]={"Room","Timers","Media","Sleep","Alerts","Transcript"};surface().setTextColor(accent,0);surface().drawString(titles[page],14,14);surface().setTextColor(fg,0);
 if(stale){wrap("Status unavailable",60,2);}else if(page==0){wrap(d.room1,64,3);wrap(d.room2,132,3);}else if(page==1){wrap(d.timer1[0]?d.timer1:"No active timers",54,2);if(remain){surface().setTextSize(3);surface().drawCentreString(countdown(remain),160,116);surface().setTextSize(1);}}else if(page==2){wrap(d.media1,60,3);wrap(d.media2,130,3);}else if(page==3){wrap(d.sleep1,60,2);wrap(d.sleep2,112,5);}else if(page==4){wrap(d.alert1[0]?d.alert1:"No recent alerts",60,2);wrap(d.alert2,112,4);}else{wrap(d.line2[0]?d.line2:"Speak to Sunday to see your words here",52,4,292,2);surface().setTextSize(1);}
 surface().setFont(&fonts::Font0);surface().setTextColor(night?0x630c:0x9cf3,0);surface().drawCentreString("Swipe to browse  |  Tap to return",160,224);
 }else{
 lastPage=-1;painted=now;
 if(remain){int radius=108,cy=120;float fraction=d.timerDuration?min(1.f,(float)remain/d.timerDuration):1.f;for(int i=0;i<60;++i){float a=(i*6-90)*3.14159265f/180;surface().fillCircle(160+radius*cosf(a),cy+radius*sinf(a),1,i<60*fraction?accent:0x3186);}surface().setFont(&fonts::Font0);surface().fillRect(126,4,68,14,0);surface().drawCentreString(countdown(remain),160,5);}
 if(!stale&&d.line1[0]&&int32_t(d.expires-now)>0){
  // Fixed 308x58 viewport: no resizing, no full-screen clear, only newest lines.
  surface().fillRoundRect(6,178,308,58,6,0);surface().drawRoundRect(6,178,308,58,6,night?0x4208:0x3186);
  surface().setFont(&fonts::Font4);surface().setTextSize(1);surface().setTextColor(fg,0);
  static CaptionLines lines;static char captionText[128]="";
  if(strcmp(captionText,d.line1)){lines=rollingLines(d.line1,292,[](const char* text){return surface().textWidth(text);});strlcpy(captionText,d.line1,sizeof(captionText));}
  if(lines.count>1)surface().drawString(lines.line[0],14,180);
  surface().drawString(lines.line[1],14,lines.count>1?207:180);
 }
 }
 if(!detail&&!stale&&!(d.line1[0]&&int32_t(d.expires-now)>0)&&d.room1[0]&&!strstr(d.room1,"unavailable")){surface().fillRect(0,227,320,13,0);surface().setFont(&fonts::Font0);surface().setTextColor(night?0x630c:0x9cf3,0);char comfort[196];snprintf(comfort,sizeof(comfort),"%s | %s",d.room1,d.room2);comfort[51]=0;surface().drawCentreString(comfort,160,230);}
 if(stale||d.muted){surface().fillRoundRect(212,4,104,24,5,0);surface().drawRoundRect(212,4,104,24,5,night?0x8410:0xf986);surface().setFont(&fonts::Font2);surface().setTextColor(night?0x8410:0xf986,0);surface().drawCentreString((stale||d.muted==2)?"MIC ?":"NO LISTEN",264,7);}
 surface().setTextSize(1);surface().setFont(&fonts::Font0);
}
}
