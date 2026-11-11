"""Deterministic historical windows. Local calendars, explicit UTC boundaries."""
from dataclasses import dataclass,asdict
from datetime import datetime,timedelta,time,timezone
from zoneinfo import ZoneInfo
import re,calendar

class HistoryError(ValueError):
 def __init__(self,status,message,details=None):super().__init__(message);self.status=status;self.details=details or {}

@dataclass(frozen=True)
class TimeWindow:
 start:datetime
 end:datetime
 timezone:str
 interpretation:str
 def serial(self):return {**asdict(self),'start':self.start.isoformat(),'end':self.end.isoformat()}
 @property
 def seconds(self):return self.end.timestamp()-self.start.timestamp()

def localize(value,tz):
 if value.tzinfo:return value.astimezone(tz)
 candidates=[]
 for fold in (0,1):
  aware=value.replace(tzinfo=tz,fold=fold)
  if aware.astimezone(timezone.utc).astimezone(tz).replace(tzinfo=None)==value and all(aware.utcoffset()!=a.utcoffset() for a in candidates):candidates.append(aware)
 if len(candidates)!=1:raise HistoryError('AMBIGUOUS_TIME','Local time is ambiguous or does not exist; provide an ISO time with its UTC offset.')
 return candidates[0]

class WindowParser:
 def __init__(self,timezone_name='Asia/Kolkata',night_start=21,night_end=7):self.tz=ZoneInfo(timezone_name);self.night_start=night_start;self.night_end=night_end
 def parse(self,value='last 24 hours',now=None):
  now=(now or datetime.now(timezone.utc)).astimezone(self.tz);today=now.date()
  def day(date,hour=0):return localize(datetime.combine(date,time(hour)),self.tz)
  text=value.strip().lower().rstrip('.?!') if isinstance(value,str) else ''
  if isinstance(value,dict):
   try:start=localize(datetime.fromisoformat(value['start'].replace('Z','+00:00')),self.tz);end=localize(datetime.fromisoformat(value['end'].replace('Z','+00:00')),self.tz)
   except (KeyError,ValueError) as e:raise HistoryError('AMBIGUOUS_TIME','Provide explicit ISO start/end times.') from e
   meaning='explicit start/end'
  elif (match:=re.fullmatch(r'(?:last|past) (?:(\d+) ?(hours?|h|days?|minutes?)|hour)',text)):
   n=int(match[1] or 1);unit=match[2] or 'hour'
   seconds=n*(86400 if unit.startswith('day') else 60 if unit.startswith('minute') else 3600)
   end=now;start=datetime.fromtimestamp(now.timestamp()-seconds,self.tz);meaning=text
  elif text in ('today','yesterday'):
   start=day(today-timedelta(days=text=='yesterday'));end=day(today) if text=='yesterday' else now;meaning=text
  elif text in ('this week','last week'):
   monday=today-timedelta(days=today.weekday());start=day(monday-timedelta(days=7 if text=='last week' else 0));end=day(monday) if text=='last week' else now;meaning=text+'; Monday-based local calendar'
  elif text in ('last night','the night before','night before last'):
   ending=today if now.hour>=self.night_end else today
   startdate=ending-timedelta(days=1)
   if text!='last night':startdate-=timedelta(days=1)
   start=day(startdate,self.night_start);end=min(day(startdate+timedelta(days=1),self.night_end),now);meaning=text+f'; local {self.night_start:02}:00–{self.night_end:02}:00'
  elif text=='this morning':
   start=day(today,6);end=min(day(today,12),now);meaning='this morning; local 06:00–12:00, clipped to now'
  elif text.startswith('since '):
   clock=self.clock(text[6:]);start=localize(datetime.combine(today,clock),self.tz)
   if start>now:start=localize(datetime.combine(today-timedelta(days=1),clock),self.tz)
   end=now;meaning=text+'; most recent occurrence'
  elif (match:=re.fullmatch(r'between (.+) and (.+)',text)):
   a,b=self.clock(match[1]),self.clock(match[2]);end=localize(datetime.combine(today,b),self.tz)
   if end>now:end=localize(datetime.combine(today-timedelta(days=1),b),self.tz)
   date=end.date()-timedelta(days=a>=b);start=localize(datetime.combine(date,a),self.tz);meaning=text+'; most recent completed interval'
  elif (match:=re.fullmatch(r'from (monday|tuesday|wednesday|thursday|friday|saturday|sunday) to (monday|tuesday|wednesday|thursday|friday|saturday|sunday)',text)):
   days=[d.lower() for d in calendar.day_name];a,b=days.index(match[1]),days.index(match[2]);startdate=today-timedelta(days=(today.weekday()-a)%7);enddate=startdate+timedelta(days=(b-a)%7+1)
   if day(enddate)>now:startdate-=timedelta(days=7);enddate-=timedelta(days=7)
   start,end=day(startdate),day(enddate);meaning=text+'; inclusive days, most recent completed range'
  else:
   date=None
   for fmt in ('%b %d %Y','%B %d %Y','%b %d','%B %d','%Y-%m-%d'):
    try:
     parsed=datetime.strptime(text if '%Y' in fmt else text+' '+str(today.year),fmt if '%Y' in fmt else fmt+' %Y');year=parsed.year;date=parsed.date()
     if '%Y' not in fmt and date>today:date=date.replace(year=year-1)
     break
    except ValueError:pass
   if date is None:raise HistoryError('AMBIGUOUS_TIME','Specify a supported historical window or ISO start/end times.')
   start,end=day(date),min(day(date+timedelta(days=1)),now);meaning=text+'; local calendar day (most recent if year omitted)'
  if end.timestamp()>now.timestamp()+60 or start.timestamp()>=end.timestamp():raise HistoryError('AMBIGUOUS_TIME','The requested period is future, empty, or reversed.')
  if end.timestamp()-start.timestamp()>366*86400:raise HistoryError('UNSUPPORTED_METRIC','Historical windows are limited to 366 days.')
  return TimeWindow(start,end,str(self.tz),meaning)
 def clock(self,text):
  m=re.fullmatch(r'(\d{1,2})(?::(\d{2}))?\s*(am|pm)',text.strip())
  if m:
   h=int(m[1]);minute=int(m[2] or 0)
   if not 1<=h<=12 or minute>59:raise HistoryError('AMBIGUOUS_TIME','Invalid clock time.')
   return time(h%12+(12 if m[3]=='pm' else 0),minute)
  m=re.fullmatch(r'(\d{1,2}):(\d{2})',text.strip())
  if m and int(m[1])<24 and int(m[2])<60:return time(int(m[1]),int(m[2]))
  raise HistoryError('AMBIGUOUS_TIME','Use AM/PM or a 24-hour HH:MM time.')
