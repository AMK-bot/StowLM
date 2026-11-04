
import ipaddress, json, os, socket
from pathlib import Path
def allowed(host):
    if isinstance(host, bytes): host=host.decode('ascii','replace')
    if host in (None,'','localhost'): return True
    try: return ipaddress.ip_address(host).is_loopback
    except ValueError: return False
def deny(host):
    with Path(os.environ['REHEARSAL_NETWORK_LOG']).open('a') as f:
        f.write(json.dumps({'pid':os.getpid(),'blocked_host':str(host)})+'\n')
    raise RuntimeError('External network disabled by judge rehearsal')
for name in ('getaddrinfo','gethostbyname','gethostbyname_ex'):
    original=getattr(socket,name)
    def wrapped(host,*a,_original=original,**kw):
        if not allowed(host): deny(host)
        return _original(host,*a,**kw)
    setattr(socket,name,wrapped)
for name in ('connect','connect_ex'):
    original=getattr(socket.socket,name)
    def wrapped(self,address,*a,_original=original,**kw):
        if isinstance(address,tuple) and not allowed(address[0]): deny(address[0])
        return _original(self,address,*a,**kw)
    setattr(socket.socket,name,wrapped)
