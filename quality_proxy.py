import http.server, urllib.request, urllib.error, json, os, sys, time, threading, subprocess, re
from pathlib import Path
# yt-dlp is installed in this kit's .venv; use its Python interpreter.
import yt_dlp
PORT=18743
ROOT=Path(__file__).resolve().parent
IDENT='sucrose-mv-stream-v1'

def healthy():
    try:
        return urllib.request.urlopen(f'http://127.0.0.1:{PORT}/health',timeout=1).read().decode()==IDENT
    except Exception:return False

if '--ensure' in sys.argv:
    if not healthy():
        subprocess.Popen([sys.executable, str(Path(__file__).resolve())],creationflags=subprocess.CREATE_NO_WINDOW,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
        for _ in range(40):
            if healthy():break
            time.sleep(.1)
    sys.exit(0 if healthy() else 1)

class Quiet:
    def debug(self,msg):pass
    def warning(self,msg):pass
    def error(self,msg):pass

cache={}
locks={}
state={'last':time.time(),'active':0}
def extract(vid, format_id=None):
    options={'format':format_id or 'bestvideo[protocol=https]/best[protocol=https]',
             'format_sort':['res','fps','br'],'format_sort_force':True,
             'quiet':True,'logger':Quiet(),'js_runtimes':{'node':{}},
             'noplaylist':True,'socket_timeout':20,'retries':1,'cachedir':False}
    with yt_dlp.YoutubeDL(options) as ydl:
        info=ydl.extract_info('https://www.youtube.com/watch?v='+vid,download=False)
    if format_id and info.get('format_id') != format_id:
        raise RuntimeError('The refreshed URL changed the video representation')
    cache[vid]=(time.time(),info)
    return info

def open_range(vid,info,start,end):
    """Renew an expired upstream URL once; retain the same video representation."""
    for attempt in range(2):
        request=urllib.request.Request(info['url'],headers={**info.get('http_headers',{}),
                                       'Range':f'bytes={start}-{end}'})
        try:
            return urllib.request.urlopen(request,timeout=30),info
        except urllib.error.HTTPError as error:
            if error.code not in (403,410) or attempt:raise
            with locks.setdefault(vid,threading.Lock()):
                current=cache.get(vid)
                if (current and current[1]['url']!=info['url']
                        and current[1].get('format_id')==info.get('format_id')):
                    info=current[1]
                else:
                    if not info.get('format_id'):
                        raise
                    cache.pop(vid,None)
                    info=extract(vid,info.get('format_id'))
class Handler(http.server.BaseHTTPRequestHandler):
    def log_message(self,*args):pass
    def do_GET(self):
        state['last']=time.time()
        if self.path=='/health':
            self.send_response(200);self.end_headers();self.wfile.write(IDENT.encode());return
        match=re.fullmatch(r'/v/([a-zA-Z0-9_-]{11})',self.path)
        if not match:self.send_error(404);return
        vid=match.group(1);state['active']+=1
        try:
            lock=locks.setdefault(vid,threading.Lock())
            with lock:
                cached=cache.get(vid)
                if not cached or time.time()-cached[0]>7200:
                    extract(vid)
                info=cache[vid][1]
            total=info.get('filesize')
            response,info=open_range(vid,info,0,0)
            with response:
                if response.headers.get('Content-Range'):
                    total=int(response.headers['Content-Range'].split('/')[-1])
                if not total:raise RuntimeError('Upstream did not report video length')
            rng=re.fullmatch(r'bytes=(\d+)-(\d*)',self.headers.get('Range',''))
            start=int(rng.group(1)) if rng else 0
            end=min(int(rng.group(2)),total-1) if rng and rng.group(2) else total-1
            if start>=total:self.send_error(416);return
            self.send_response(206 if rng else 200)
            self.send_header('Content-Type','video/mp4' if info['ext']=='mp4' else 'video/webm')
            self.send_header('Content-Length',str(end-start+1));self.send_header('Accept-Ranges','bytes')
            if rng:self.send_header('Content-Range',f'bytes {start}-{end}/{total}')
            self.end_headers()
            (ROOT/'quality-status.json').write_text(json.dumps({'id':vid,'width':info.get('width'),'height':info.get('height'),'format':info.get('format'),'time':time.time()},ensure_ascii=False),encoding='utf-8')
            pos=start
            while pos<=end:
                chunkend=min(pos+1024*1024*5-1,end)
                response,info=open_range(vid,info,pos,chunkend)
                with response:
                    remaining=chunkend-pos+1
                    while remaining:
                        data=response.read(min(65536,remaining))
                        if not data:raise RuntimeError('Stream ended early')
                        self.wfile.write(data);remaining-=len(data);pos+=len(data)
        except (BrokenPipeError,ConnectionResetError,ConnectionAbortedError):pass
        except Exception as e:
            with (ROOT/'proxy-errors.log').open('a',encoding='utf-8') as log:log.write(f'{vid}: {type(e).__name__}: {str(e)[:160]}\n')
        finally:state['active']-=1;state['last']=time.time()

server=http.server.ThreadingHTTPServer(('127.0.0.1',PORT),Handler)
server.daemon_threads=True
threading.Thread(target=server.serve_forever,daemon=True).start()
while state['active'] or time.time()-state['last']<120:time.sleep(2)
server.shutdown()



