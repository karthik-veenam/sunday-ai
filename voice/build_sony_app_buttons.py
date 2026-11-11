import asyncio,base64,html,json,re,textwrap
from pathlib import Path
import aiohttp
ROOT=Path('/home/djpi');OUT=Path('/home/pi/homeassistant/www/remote-apps/sony');OUT.mkdir(exist_ok=True)
async def main():
 token=json.load(open(ROOT/'sunday/config.json'))['ha_token'];apps=json.load(open(ROOT/'sunday/sony-installed-apps.json'))['children'];unique={}
 for app in apps:
  if app['title'] not in ['YouTube','JioHotstar']:unique.setdefault(app['title'],app)
 semaphore=asyncio.Semaphore(4)
 async with aiohttp.ClientSession(headers={'Authorization':'Bearer '+token},timeout=aiohttp.ClientTimeout(total=12)) as s:
  async def make(title,app):
   slug=re.sub('[^a-z0-9]+','-',title.lower()).strip('-');image='';ok=False
   if app.get('thumbnail'):
    try:
     async with semaphore:
      async with s.get('http://127.0.0.1:8123'+app['thumbnail']) as r:
       if r.status==200:
        content=await r.read();mime=r.headers.get('Content-Type','image/png').split(';')[0]
        if mime.startswith('image/'):
         image=f'<image x="14" y="18" width="100" height="100" preserveAspectRatio="xMidYMid meet" href="data:{mime};base64,{base64.b64encode(content).decode()}"/>';ok=True
    except (TimeoutError,aiohttp.ClientError):pass
   if not image:image='<rect x="24" y="32" width="72" height="72" rx="14" fill="#e7eef6"/><text x="60" y="78" text-anchor="middle" font-family="sans-serif" font-size="26" fill="#294765">'+html.escape(title[:2].upper())+'</text>'
   lines=textwrap.wrap(title,width=23)[:2];size=30 if len(title)<21 else 26
   text=''.join(f'<text x="130" y="{79 if len(lines)==1 else 60+i*34}" font-family="Arial, sans-serif" font-size="{size}" font-weight="600" fill="#233247">{html.escape(line)}</text>' for i,line in enumerate(lines))
   svg=f'<svg xmlns="http://www.w3.org/2000/svg" width="480" height="136" viewBox="0 0 480 136"><rect width="480" height="136" rx="8" fill="#ffffff"/>{image}{text}</svg>'
   (OUT/(slug+'.svg')).write_text(svg)
   return {'title':title,'image':'/local/remote-apps/sony/'+slug+'.svg','has_tv_icon':ok}
  results=await asyncio.gather(*(make(title,app) for title,app in sorted(unique.items(),key=lambda x:x[0].lower())))
 (ROOT/'sunday/sony-app-buttons.json').write_text(json.dumps(results,indent=2));print('Built',len(results),'app buttons;',sum(x['has_tv_icon'] for x in results),'with TV-provided icons.')
asyncio.run(main())
