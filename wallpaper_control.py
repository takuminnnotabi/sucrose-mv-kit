import ctypes, json, random, time, sys
import shutil
import subprocess
from pathlib import Path
def command(cmd):
    node = shutil.which('node') or 'C:/Program Files/nodejs/node.exe'
    script = "const s=require('net').connect('\\\\\\\\.\\\\pipe\\\\sucrose-hq');let b='';s.on('connect',()=>s.write(JSON.stringify({command:JSON.parse(process.argv[1]),request_id:902})+'\\n'));s.on('data',d=>{b+=d;while(b.includes('\\n')){let i=b.indexOf('\\n'),r=JSON.parse(b.slice(0,i));b=b.slice(i+1);if(r.request_id===902){console.log(JSON.stringify(r));s.destroy();}}});s.on('error',()=>process.exit(1));setTimeout(()=>process.exit(1),5000).unref();"
    result = subprocess.run([str(node), '-e', script, json.dumps(cmd)], capture_output=True, text=True, timeout=10, creationflags=subprocess.CREATE_NO_WINDOW)
    if result.returncode or not result.stdout.strip(): raise OSError('Player unavailable')
    reply = json.loads(result.stdout.strip())
    if reply.get('error') != 'success': raise OSError(reply.get('error'))
    return reply.get('data')
def shuffle():
    count=command(['get_property','playlist-count'])
    command(['playlist-shuffle'])
    position=command(['get_property','playlist-pos'])
    choices=[i for i in range(count) if i!=position]
    if choices: command(['set_property','playlist-pos',random.choice(choices)])
    command(['set_property','pause',False])
if __name__=='__main__':
    try:
        if sys.argv[1]=='shuffle': shuffle()
        else:
            command(['playlist-next','force'])
            command(['set_property','pause',False])
    except Exception as error:
        Path(__file__).with_name('control-error.txt').write_text(str(error),encoding='utf-8')
        ctypes.windll.user32.MessageBoxW(None,'壁紙の再生エンジンに接続できませんでした。Sucroseで壁紙を選択してください。','壁紙の操作',0)
