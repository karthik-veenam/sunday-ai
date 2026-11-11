import re,urllib.request
from pathlib import Path
folder=Path('/home/djpi/ha-assets/remote-apps')
for name,url in [('jiohotstar-wordmark.png','https://cdn.jiostar.com/jiostar/wp-content/uploads/2025/07/JioHot-Star-1.png')]:
 with urllib.request.urlopen(url,timeout=20) as r: blob=r.read()
 (folder/name).write_bytes(blob);print('Downloaded:',name,len(blob))
with urllib.request.urlopen('https://brand.youtube/',timeout=20) as r: html=r.read().decode()
(folder/'youtube-brand.html').write_text(html)
links=set(re.findall(r'https[^\s"<>]+',html))
for link in sorted(links):
 if any(s in link.lower() for s in ['.zip','.svg','.png','logo']):print(link[:350])
