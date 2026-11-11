"""Append one authenticated read-only REST command; preserve all other YAML bytes."""
from pathlib import Path
import re,yaml,hashlib,os,stat,tempfile,shutil,json
root=Path(__file__).parent;path=Path('/home/pi/homeassistant/configuration.yaml');original=path.read_text();node=yaml.compose(original)
rest=next(v for k,v in node.value if k.value=='rest_command');commands={k.value:v for k,v in rest.value}
if 'sunday_feelings' in commands:raise SystemExit('sunday_feelings already exists; review before changing it')
authorization=None
for name,value in commands.items():
 props={k.value:v for k,v in value.value};url=props.get('url')
 if url and url.value.startswith('http://127.0.0.1:8090/v1/') and 'headers' in props:
  authorization=next((v for k,v in props['headers'].value if k.value.lower()=='authorization'),None)
  if authorization:break
assert authorization and authorization.tag=='!secret','Expected a reusable existing brain auth secret'
secret=authorization.value;assert re.fullmatch(r'[a-zA-Z0-9_.-]+',secret)
block=f'''  sunday_feelings:
    url: "http://127.0.0.1:8090/v1/alive/status"
    method: GET
    timeout: 10
    headers:
      Authorization: !secret {secret}
'''
lines=original.splitlines(keepends=True);start=next(i for i,line in enumerate(lines) if re.match(r'^rest_command:\s*(?:#.*)?$',line.rstrip()))
end=next((i for i in range(start+1,len(lines)) if lines[i].strip() and not lines[i].startswith((' ','\t','#'))),len(lines))
# Only append inside this mapping; no YAML serialization of unrelated settings.
new=''.join(lines[:end]).rstrip('\n')+'\n'+block+'\n'+''.join(lines[end:]);parsed=yaml.compose(new)
newrest=next(v for k,v in parsed.value if k.value=='rest_command');assert [k.value for k,v in newrest.value]==[k.value for k,v in rest.value]+['sunday_feelings']
assert [k.value for k,v in parsed.value]==[k.value for k,v in node.value]
backup=root/'configuration.before.yaml';assert not backup.exists();shutil.copy2(path,backup);backup.chmod(0o600)
info=path.stat();fd,tmp=tempfile.mkstemp(prefix='.feelings-',dir=path.parent)
try:
 with os.fdopen(fd,'w') as f:f.write(new);f.flush();os.fsync(f.fileno())
 os.chmod(tmp,stat.S_IMODE(info.st_mode));os.chown(tmp,info.st_uid,info.st_gid);os.replace(tmp,path)
finally:
 if os.path.exists(tmp):os.unlink(tmp)
(root/'config-change.json').write_text(json.dumps({'before_sha256':hashlib.sha256(original.encode()).hexdigest(),'after_sha256':hashlib.sha256(new.encode()).hexdigest(),'added_command':'sunday_feelings','reused_existing_auth_secret':True},indent=2)+'\n')
print('Added one read-only REST command using the existing secret; other configuration preserved.')
