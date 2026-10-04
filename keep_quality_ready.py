"""Keep streaming and MPV ready without overriding an explicit pause."""
import json
import msvcrt
import os
import random
import subprocess
import threading
import time
from pathlib import Path

from wallpaper_control import command, properties, ensure_proxy, selected_playlist, shuffle, python_executable

HERE = Path(__file__).resolve().parent
NO_WINDOW = getattr(subprocess, 'CREATE_NO_WINDOW', 0)
LOG_LOCK = threading.Lock()
RECENT_ERRORS = {}


def log_event(message, error=None):
    text = message + (': ' + str(error) if error is not None else '')
    now = time.monotonic()
    with LOG_LOCK:
        if error is not None and now - RECENT_ERRORS.get(text, -1000) < 60:
            return
        RECENT_ERRORS[text] = now
        try:
            with (HERE / 'helper-events.log').open('a', encoding='utf-8') as output:
                output.write(time.strftime('%Y-%m-%d %H:%M:%S ') + text[:1000] + '\n')
        except OSError:
            pass


def acquire_lock():
    lock = (HERE / 'keep_quality_ready.lock').open('a+b')
    lock.seek(0, 2)
    if lock.tell() == 0:
        lock.write(b'0')
        lock.flush()
    lock.seek(0)
    try:
        msvcrt.locking(lock.fileno(), msvcrt.LK_NBLCK, 1)
    except OSError:
        lock.close()
        return None
    return lock


def refresh_regularly():
    updater = HERE / 'refresh_playlists.py'
    status_path = HERE / 'playlist-refresh-status.json'
    while True:
        started = time.time()
        try:
            result = subprocess.run(
                [str(python_executable()), str(updater)], creationflags=NO_WINDOW,
                stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=180,
                encoding='utf-8', errors='replace',
                env={**os.environ, 'PYTHONIOENCODING': 'utf-8'},
            )
            status = {'checked_at': started, 'exit_code': result.returncode,
                      'results': result.stdout, 'error': result.stderr[-2000:]}
            if result.returncode:
                log_event('Playlist refresh failed', result.stderr[-500:] or f'exit {result.returncode}')
        except Exception as error:
            status = {'checked_at': started, 'error': str(error)}
            log_event('Playlist refresh failed', error)
        try:
            status_path.write_text(json.dumps(status, ensure_ascii=False, indent=2), encoding='utf-8')
        except Exception as error:
            log_event('Could not write refresh status', error)
        time.sleep(3600)


def bridge_worker():
    try:
        from desktop_next_bridge import watch
        watch()
    except Exception as error:
        log_event('Desktop-next bridge stopped', error)


class PlayerState:
    def __init__(self):
        self.pid = None
        self.last_progress = time.monotonic()
        self.last_time = None
        self.last_recovery = -1000


def inspect_player(state):
    from desktop_next_bridge import bridge_active, handled_pid
    if bridge_active():
        state.last_progress = time.monotonic()
        return
    now = time.monotonic()
    snapshot = properties(['pid', 'pause', 'idle-active', 'playlist-count', 'path', 'time-pos', 'options/shuffle'])
    if bridge_active():
        state.last_progress = time.monotonic()
        return
    temporary = HERE / 'helper-state.json.tmp'
    temporary.write_text(json.dumps({**snapshot, 'checked_at': time.time()}), encoding='utf-8')
    os.replace(temporary, HERE / 'helper-state.json')
    pid = snapshot['pid']
    paused = snapshot['pause']
    if paused:
        # Reset stall detection while the user or Sucrose deliberately pauses it.
        state.last_progress = now
        state.last_time = None
        return

    idle = bool(snapshot['idle-active'])
    count = snapshot['playlist-count']
    if pid != state.pid:
        command(['set_property', 'loop-playlist', 'inf'])
        command(['set_property', 'sub-visibility', False])
        if (isinstance(count, int) and count > 0 and not idle and pid != handled_pid()
                and snapshot['options/shuffle'] is not True):
            shuffle()
        state.pid = pid
        state.last_time = None
        state.last_progress = now
        log_event('MPV player connected')

    if idle:
        if now - state.last_recovery < 15:
            return
        # Recheck pause immediately before a recovery; never write pause=False.
        if command(['get_property', 'pause']):
            return
        if isinstance(count, int) and count > 0:
            position = command(['get_property', 'playlist-pos'])
            choices = [index for index in range(count) if index != position]
            target = random.choice(choices) if choices else 0
            command(['set_property', 'playlist-pos', target])
            log_event('Recovered idle player from its playlist')
        else:
            target = selected_playlist()
            if target is None:
                log_event('Idle player has no selected MV playlist', 'Select the wallpaper in Sucrose')
                state.last_recovery = now
                return
            command(['loadlist', str(target), 'replace'])
            log_event('Reloaded the selected MV playlist')
        state.last_recovery = now
        state.last_progress = now
        state.last_time = None
        return

    position = snapshot['time-pos']
    if position is not None and position != state.last_time:
        state.last_progress = now
    elif now - state.last_progress > 75:
        if not command(['get_property', 'pause']):
            command(['playlist-next', 'force'])
            log_event('Advanced past a stalled video')
            state.last_progress = now
    state.last_time = position


def run_iteration(state):
    try:
        ensure_proxy(timeout=10)
    except Exception as error:
        log_event('Streaming helper not ready; will retry', error)
        return
    try:
        inspect_player(state)
    except Exception as error:
        log_event('Player check failed; will retry', error)


def main():
    try:
        lock = acquire_lock()
    except Exception as error:
        log_event('Could not acquire helper lock', error)
        return 1
    if lock is None:
        return 0
    try:
        log_event('Wallpaper helper started')
        threading.Thread(target=refresh_regularly, daemon=True, name='MV playlist refresh').start()
        threading.Thread(target=bridge_worker, daemon=True, name='Desktop-next bridge').start()
        state = PlayerState()
        while True:
            try:
                run_iteration(state)
            except Exception as error:
                log_event('Unexpected monitor error; will retry', error)
            time.sleep(5)
    finally:
        lock.close()


if __name__ == '__main__':
    raise SystemExit(main())
