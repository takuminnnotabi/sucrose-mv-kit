"""Keep the existing local high-quality streaming helper ready for Sucrose."""
import subprocess, time, urllib.request, json, msvcrt, threading, sys
from pathlib import Path
from wallpaper_control import command, shuffle
proxy = Path(__file__).resolve().with_name('quality_proxy.py')
python = Path(sys.executable).with_name('python.exe')
lock = open(Path(__file__).with_suffix('.lock'), 'a+b')
lock.write(b'0'); lock.flush(); lock.seek(0)
try:
    msvcrt.locking(lock.fileno(), msvcrt.LK_NBLCK, 1)
except OSError:
    raise SystemExit(0)
last_pid = None
last_progress = time.monotonic()
last_time = None
def refresh_regularly():
    updater = Path(__file__).with_name('refresh_playlists.py')
    status_path = Path(__file__).with_name('playlist-refresh-status.json')
    while True:
        started = time.time()
        try:
            result = subprocess.run([str(python), str(updater)], creationflags=subprocess.CREATE_NO_WINDOW,
                                    stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=180,
                                    encoding='utf-8', errors='replace', env={**__import__('os').environ, 'PYTHONIOENCODING': 'utf-8'})
            status = {'checked_at': started, 'exit_code': result.returncode,
                      'results': result.stdout, 'error': result.stderr[-2000:]}
        except Exception as error:
            status = {'checked_at': started, 'error': str(error)}
        status_path.write_text(json.dumps(status, ensure_ascii=False, indent=2), encoding='utf-8')
        time.sleep(3600)
threading.Thread(target=refresh_regularly, daemon=True).start()
while True:
    try:
        urllib.request.urlopen('http://127.0.0.1:18743/health', timeout=2).close()
    except Exception:
        subprocess.run([str(python), str(proxy), '--ensure'], creationflags=subprocess.CREATE_NO_WINDOW, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        pid = command(['get_property', 'pid'])
        if pid and pid != last_pid:
            shuffle()
            command(['set_property', 'loop-playlist', 'inf'])
            command(['set_property', 'sub-visibility', False])
            last_pid = pid
            last_progress = time.monotonic()
        try:
            position = command(['get_property', 'time-pos'])
        except OSError:
            position = None
        paused = command(['get_property', 'pause'])
        if paused or (position is not None and position != last_time):
            last_progress = time.monotonic()
        elif time.monotonic() - last_progress > 75:
            command(['playlist-next', 'force'])
            last_progress = time.monotonic()
        last_time = position
    except OSError:
        pass
    time.sleep(5)
