"""Bounded MPV commands and self-healing entry point for wallpaper shortcuts."""
import ctypes
import json
import os
import random
import shutil
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent
PROXY = HERE / 'quality_proxy.py'
HEALTH_URL = 'http://127.0.0.1:18743/health'
PROXY_IDENT = 'sucrose-mv-stream-v1'
LOCAL_HTTP = urllib.request.build_opener(urllib.request.ProxyHandler({}))
NO_WINDOW = getattr(subprocess, 'CREATE_NO_WINDOW', 0)


def python_executable(windowless=False):
    candidate = Path(sys.executable).with_name('pythonw.exe' if windowless else 'python.exe')
    return candidate if candidate.is_file() else Path(sys.executable)


def proxy_healthy(timeout=1):
    try:
        with LOCAL_HTTP.open(HEALTH_URL, timeout=timeout) as response:
            return response.status == 200 and response.read(1024).decode('utf-8') == PROXY_IDENT
    except Exception:
        return False


def ensure_proxy(timeout=10):
    if proxy_healthy():
        return
    if not PROXY.is_file():
        raise OSError('Streaming helper is missing: ' + str(PROXY))
    try:
        result = subprocess.run(
            [str(python_executable()), str(PROXY), '--ensure'],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=timeout,
            encoding='utf-8', errors='replace', creationflags=NO_WINDOW,
        )
    except (OSError, subprocess.SubprocessError) as error:
        # The launcher can time out after its independent daemon has started.
        if proxy_healthy():
            return
        raise OSError('Streaming helper did not become ready: ' + str(error)) from error
    if not proxy_healthy():
        detail = result.stderr.strip()[-500:]
        raise OSError(f'Streaming helper is unavailable (exit {result.returncode}). {detail}')


def ensure_watchdog():
    helper = HERE / 'keep_quality_ready.py'
    if not helper.is_file():
        raise OSError('Wallpaper watchdog is missing: ' + str(helper))
    # Its file lock makes concurrent shortcut clicks safe. Capture startup failures.
    with (HERE / 'helper-startup.log').open('ab') as output:
        subprocess.Popen(
            [str(python_executable(windowless=True)), str(helper)],
            cwd=str(HERE), stdout=output, stderr=output, creationflags=NO_WINDOW,
        )


def command(cmd):
    node = shutil.which('node') or str(Path(os.environ.get('ProgramFiles', 'C:/Program Files')) / 'nodejs/node.exe')
    script = r"""
const net = require('net');
const socket = net.connect('\\\\.\\pipe\\sucrose-hq');
let buffer = '', finished = false;
const timer = setTimeout(() => finish(1), 5000);
function finish(code, reply) {
  if (finished) return;
  finished = true;
  clearTimeout(timer);
  if (reply) process.stdout.write(JSON.stringify(reply) + '\n');
  socket.destroy();
  process.exitCode = code;
}
socket.on('connect', () => socket.write(JSON.stringify({command: JSON.parse(process.argv[1]), request_id: 902}) + '\n'));
socket.on('data', data => {
  buffer += data;
  while (buffer.includes('\n')) {
    const index = buffer.indexOf('\n');
    const line = buffer.slice(0, index);
    buffer = buffer.slice(index + 1);
    try {
      const reply = JSON.parse(line);
      if (reply.request_id === 902) finish(0, reply);
    } catch (_) { finish(1); }
  }
});
socket.on('error', () => finish(1));
socket.on('close', () => { if (!finished) finish(1); });
"""
    try:
        result = subprocess.run(
            [str(node), '-e', script, json.dumps(cmd)], capture_output=True,
            encoding='utf-8', errors='replace', timeout=8, creationflags=NO_WINDOW,
        )
        if result.returncode or not result.stdout.strip():
            raise OSError('Player unavailable or IPC request timed out')
        reply = json.loads(result.stdout.strip())
        if not isinstance(reply, dict) or reply.get('error') != 'success':
            raise OSError('MPV command failed: ' + str(reply.get('error') if isinstance(reply, dict) else reply))
        return reply.get('data')
    except (subprocess.SubprocessError, ValueError, TypeError) as error:
        raise OSError('Invalid or timed out MPV reply: ' + str(error)) from error


def selected_playlist():
    """Only reload the wallpaper currently selected by Sucrose."""
    root = Path(os.environ.get('APPDATA', str(Path.home() / 'AppData/Roaming'))) / 'Sucrose'
    try:
        selection = json.loads((root / 'Setting/Library.json').read_text(encoding='utf-8-sig'))['Properties']['Selected']
        if not isinstance(selection, str) or not selection or Path(selection).name != selection or selection in ('.', '..'):
            return None
        folder = root / 'Library' / selection
        info = json.loads((folder / 'SucroseInfo.json').read_text(encoding='utf-8-sig'))
        if info.get('Source') != 'highest.m3u':
            return None
        target = folder / 'highest.m3u'
        return target if target.is_file() else None
    except (OSError, ValueError, KeyError, TypeError):
        return None


def properties(names, timeout=2):
    """Read a player snapshot with one bounded IPC connection instead of many processes."""
    node = shutil.which('node') or str(Path(os.environ.get('ProgramFiles', 'C:/Program Files')) / 'nodejs/node.exe')
    script = r"""
const names=JSON.parse(process.argv[1]), net=require('net');
const socket=net.connect('\\\\.\\pipe\\sucrose-hq');
let buffer='', replies={}, count=0, finished=false;
const timer=setTimeout(()=>finish(1),Number(process.argv[2]));
function finish(code) {
  if(finished)return;
  finished=true;clearTimeout(timer);socket.destroy();
  if(code===0)process.stdout.write(JSON.stringify(replies)+'\n');
  process.exitCode=code;
}
socket.on('connect',()=>names.forEach((name,index)=>socket.write(JSON.stringify({
  command:['get_property',name],request_id:index+1000})+'\n')));
socket.on('data',data=>{
  buffer+=data;
  while(buffer.includes('\n')) {
    const index=buffer.indexOf('\n'),line=buffer.slice(0,index);buffer=buffer.slice(index+1);
    let reply;try{reply=JSON.parse(line);}catch(_){finish(1);return;}
    const key=reply.request_id-1000;
    if(key>=0&&key<names.length&&!Object.hasOwn(replies,names[key])) {
      replies[names[key]]=reply.error==='success'?reply.data:null;
      if(++count===names.length)finish(0);
    }
  }
});
socket.on('error',()=>finish(1));socket.on('close',()=>{if(!finished)finish(1);});
"""
    try:
        result = subprocess.run([str(node), '-e', script, json.dumps(names), str(int(timeout * 1000))],
                                capture_output=True, encoding='utf-8', errors='replace',
                                timeout=timeout + 2, creationflags=NO_WINDOW)
        if result.returncode or not result.stdout.strip():
            raise OSError('Player snapshot unavailable')
        reply = json.loads(result.stdout)
        if not isinstance(reply, dict) or any(name not in reply for name in names):
            raise OSError('Incomplete player snapshot')
        return reply
    except (subprocess.SubprocessError, ValueError, TypeError) as error:
        raise OSError('Invalid or timed out snapshot: ' + str(error)) from error


def shuffle():
    count = command(['get_property', 'playlist-count'])
    if not isinstance(count, int) or count < 1:
        raise OSError('The wallpaper playlist is empty')
    if count > 1:
        command(['playlist-shuffle'])
        position = command(['get_property', 'playlist-pos'])
        choices = [index for index in range(count) if index != position]
        command(['set_property', 'playlist-pos', random.choice(choices)])
    # Changing the position respects MPV's pause property. Never force unpause.


def restore_empty_playlist():
    if command(['get_property', 'pause']):
        return
    if command(['get_property', 'playlist-count']):
        return
    target = selected_playlist()
    if target is not None:
        command(['loadlist', str(target), 'replace'])


def record_control_error(error):
    try:
        (HERE / 'control-error.txt').write_text(
            time.strftime('%Y-%m-%d %H:%M:%S ') + str(error), encoding='utf-8',
        )
    except OSError:
        pass


def main():
    try:
        ensure_proxy()
        try:
            ensure_watchdog()
        except OSError as error:
            record_control_error(error)
        restore_empty_playlist()
        action = sys.argv[1] if len(sys.argv) > 1 else 'shuffle'
        if action == 'shuffle':
            shuffle()
        elif action == 'next':
            command(['playlist-next', 'force'])
        else:
            raise OSError('Unknown wallpaper action: ' + action)
        return 0
    except Exception as error:
        record_control_error(error)
        ctypes.windll.user32.MessageBoxW(
            None,
            '壁紙の再生に接続できませんでした。Sucroseで壁紙を選び直してください。\n詳細は control-error.txt に記録しました。',
            '壁紙の操作', 0,
        )
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
