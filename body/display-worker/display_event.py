"""Best-effort local display events: no waits, background tasks, or transcript storage."""
import json,socket
_sock=socket.socket(socket.AF_INET,socket.SOCK_DGRAM);_sock.setblocking(False)
def publish(kind,text=''):
 text=str(text)
 if len(text)>220:text=('...'+text[-217:]) if kind=='partial_transcript' else (text[:217]+'...')
 try:_sock.sendto(json.dumps({'type':kind,'text':text},ensure_ascii=False).encode()[:2048],('127.0.0.1',8777))
 except (OSError,ValueError,TypeError):pass
