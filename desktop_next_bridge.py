"""Turn Windows slideshow events into one verified MV shuffle operation."""
import json
import ctypes
import os
import random
import subprocess
import threading
import time
import urllib.request
import winreg
from collections import deque
from pathlib import Path

from wallpaper_control import command, ensure_proxy, properties, selected_playlist

HERE = Path(__file__).resolve().parent
STATUS = HERE / 'desktop-next-status.json'
BUSY = threading.Event()
PENDING = deque()
CONDITION = threading.Condition()
STATUS_LOCK = threading.Lock()
HANDLED_LOCK = threading.Lock()
HANDLED_PID = None
NO_WINDOW = getattr(subprocess, 'CREATE_NO_WINDOW', 0)
LOCAL_HTTP = urllib.request.build_opener(urllib.request.ProxyHandler({}))
SNAPSHOT_FIELDS = ['pid', 'path', 'pause', 'time-pos', 'playlist-count', 'idle-active']


def bridge_active():
    return BUSY.is_set()


def handled_pid():
    with HANDLED_LOCK:
        return HANDLED_PID


def fingerprint():
    with winreg.OpenKey(winreg.HKEY_CURRENT_USER, r'Control Panel\Personalization\Desktop Slideshow') as key:
        return tuple(winreg.QueryValueEx(key, value)[0] for value in ('LastTickLow', 'LastTickHigh'))


def write_status(value):
    with STATUS_LOCK:
        temporary = STATUS.with_name(STATUS.name + '.tmp')
        temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding='utf-8')
        os.replace(temporary, STATUS)


def helper_snapshot():
    try:
        data = json.loads((HERE / 'helper-state.json').read_text(encoding='utf-8-sig'))
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def playlist_urls(path):
    rows = path.read_text(encoding='utf-8-sig').splitlines()
    return list(dict.fromkeys(row.strip() for row in rows if row.strip() and not row.lstrip().startswith('#')))


def warm_target(target):
    # Only warm metadata and one byte in memory; never save the video.
    try:
        request = urllib.request.Request(target, headers={'Range': 'bytes=0-0'})
        with LOCAL_HTTP.open(request, timeout=25) as response:
            response.read(1)
    except Exception:
        pass


def terminate_player(pid, executable):
    """Terminate only the verified installed engine using the same process handle."""
    if not isinstance(pid, int) or pid <= 0:
        return
    kernel = ctypes.WinDLL('kernel32', use_last_error=True)
    kernel.OpenProcess.argtypes = [ctypes.c_ulong, ctypes.c_int, ctypes.c_ulong]
    kernel.OpenProcess.restype = ctypes.c_void_p
    kernel.QueryFullProcessImageNameW.argtypes = [ctypes.c_void_p, ctypes.c_ulong,
                                               ctypes.c_wchar_p, ctypes.POINTER(ctypes.c_ulong)]
    kernel.TerminateProcess.argtypes = [ctypes.c_void_p, ctypes.c_uint]
    kernel.WaitForSingleObject.argtypes = [ctypes.c_void_p, ctypes.c_ulong]
    kernel.CloseHandle.argtypes = [ctypes.c_void_p]
    handle = kernel.OpenProcess(0x1000 | 0x0001 | 0x00100000, False, pid)
    if not handle:
        if ctypes.get_last_error() == 87:
            return  # The old engine already exited.
        raise ctypes.WinError(ctypes.get_last_error())
    try:
        filename = ctypes.create_unicode_buffer(32768)
        size = ctypes.c_ulong(len(filename))
        if not kernel.QueryFullProcessImageNameW(handle, 0, filename, ctypes.byref(size)):
            raise ctypes.WinError(ctypes.get_last_error())
        if os.path.normcase(os.path.abspath(filename.value)) != os.path.normcase(str(executable.resolve())):
            raise OSError('Refused to terminate an unrelated process')
        if not kernel.TerminateProcess(handle, 0):
            raise ctypes.WinError(ctypes.get_last_error())
        if kernel.WaitForSingleObject(handle, 3000) != 0:
            raise OSError('The old wallpaper engine did not exit')
    finally:
        kernel.CloseHandle(handle)


def restart_player(pid=None):
    executable = Path(os.environ.get('LOCALAPPDATA', str(Path.home() / 'AppData/Local'))) / 'Sucrose/Sucrose.Live.MpvPlayer/Sucrose.Live.MpvPlayer.exe'
    if not executable.is_file():
        raise OSError('Sucrose restart command is missing')
    if pid is None:
        pid = helper_snapshot().get('pid')
    terminate_player(pid, executable)
    # RestartLive can wait for a desktop window that Explorer already removed.
    # The existing engine executable reads the same selected theme directly.
    subprocess.Popen([str(executable)], creationflags=NO_WINDOW)


def check_deadline(deadline):
    if time.monotonic() >= deadline:
        raise TimeoutError('The wallpaper did not reach the selected video within 40 seconds')


def snapshot(deadline):
    check_deadline(deadline)
    remaining = deadline - time.monotonic()
    data = properties(SNAPSHOT_FIELDS, timeout=min(2, max(0.1, remaining)))
    if not isinstance(data, dict) or not isinstance(data.get('pid'), int) or not data['pid']:
        raise OSError('MPV player is unavailable')
    return data


def send(cmd, deadline):
    # command() itself is bounded at eight seconds; do not start it when that
    # would exceed the transaction's whole deadline.
    if deadline - time.monotonic() < 8:
        raise TimeoutError('Not enough time remains for a bounded MPV command')
    return command(cmd)


def selected_unchanged(path):
    current = selected_playlist()
    return current is not None and current.resolve() == path.resolve()


def perform_event(event):
    global HANDLED_PID
    started = time.monotonic()
    deadline = started + 40
    cached = helper_snapshot()
    try:
        completed = json.loads(STATUS.read_text(encoding='utf-8'))
        if (completed.get('verified') and completed.get('time', 0) > cached.get('checked_at', 0)):
            cached.update(pid=completed['player_pid'], path=completed['path'], pause=completed['paused'])
    except (OSError, ValueError, KeyError, TypeError):
        pass
    before_path = cached.get('path')
    previous_pid = cached.get('pid', cached.get('lastpid', cached.get('last_pid')))
    previous_pause = cached.get('pause') is True
    chosen_playlist = selected_playlist()
    if chosen_playlist is None:
        raise OSError('Select the MV wallpaper in Sucrose first')
    urls = playlist_urls(chosen_playlist)
    if not urls:
        raise OSError('The selected wallpaper playlist is empty')
    choices = [url for url in urls if url != before_path]
    if not choices:
        raise OSError('The selected playlist has no different video to shuffle to')
    target = random.choice(choices)
    ensure_proxy(timeout=10)
    threading.Thread(target=warm_target, args=(target,), daemon=True, name='MV target warmup').start()

    # Let Explorer finish its immediate desktop update before touching MPV.
    time.sleep(0.4)
    entries_by_pid = {}
    write_status({'time': time.time(), 'result': 'pending', 'event_id': event['id'],
                  'before_path': before_path, 'target_path': target})
    # Explorer replaces the wallpaper host on this operation. Do not wait for
    # IPC requests to a closing render thread before rebuilding the engine.
    restart_player(previous_pid)
    restarted = True
    initial = None
    target_index = None
    loaded_pid = None
    selected_pid = None
    selected_since = None
    verified_since = None

    while True:
        check_deadline(deadline)
        if not selected_unchanged(chosen_playlist):
            raise OSError('The selected Sucrose wallpaper changed during this operation')
        try:
            current = snapshot(deadline)
        except TimeoutError:
            raise
        except OSError:
            if not restarted:
                restart_player(previous_pid)
                restarted = True
                time.sleep(0.4)
            else:
                time.sleep(0.2)
            continue
        pid = current['pid']
        if current.get('path') == target:
            # The command may have applied before its reply timed out. Reuse
            # that result instead of loading or advancing the playlist again.
            selected_pid = pid
            if selected_since is None:
                selected_since = time.monotonic()
            if previous_pause and current.get('pause') is not True:
                try:
                    send(['set_property', 'pause', True], deadline)
                except TimeoutError:
                    raise
                except OSError:
                    if not restarted:
                        restart_player(pid)
                        restarted = True
                    time.sleep(0.2)
                continue
        if selected_pid != pid:
            # An engine recreated by Explorer needs the same full M3U restored.
            use_existing = initial is not None and pid == initial['pid'] and target_index is not None
            try:
                if not use_existing:
                    if loaded_pid != pid:
                        # Sucrose normally already loaded the selected M3U when
                        # it recreated the engine. Reuse that complete queue.
                        existing = send(['get_property', 'playlist'], deadline) or []
                        existing_urls = {entry.get('filename') for entry in existing if isinstance(entry, dict)}
                        if previous_pause:
                            send(['set_property', 'pause', True], deadline)
                        if set(urls).issubset(existing_urls):
                            entries_by_pid[pid] = existing
                        else:
                            send(['loadlist', str(chosen_playlist), 'replace'], deadline)
                            entries_by_pid.pop(pid, None)
                        loaded_pid = pid
                    # Only fetch the queue once per player/load; verification
                    # polls use the lightweight batch property reader.
                    if pid not in entries_by_pid:
                        entries_by_pid[pid] = send(['get_property', 'playlist'], deadline)
                    actual_entries = entries_by_pid[pid]
                    index = next((item for item, entry in enumerate(actual_entries or [])
                                  if isinstance(entry, dict) and entry.get('filename') == target), None)
                    if index is None:
                        raise OSError('The target is missing from the restored MPV playlist')
                else:
                    index = target_index
                if previous_pause:
                    send(['set_property', 'pause', True], deadline)
                send(['set_property', 'playlist-pos', index], deadline)
            except TimeoutError:
                raise
            except OSError:
                # A reply can time out although MPV applied the selection.
                # Check that before restarting an already playing target.
                try:
                    applied = snapshot(deadline)
                except TimeoutError:
                    raise
                except OSError:
                    applied = None
                if applied is not None and applied.get('path') == target:
                    selected_pid = applied['pid']
                    selected_since = time.monotonic()
                    verified_since = None
                    continue
                if not restarted:
                    restart_player(pid)
                    restarted = True
                initial = None
                loaded_pid = None
                time.sleep(0.4)
                continue
            selected_pid = pid
            selected_since = time.monotonic()
            verified_since = None
            continue

        path_matches = current.get('path') == target
        position = current.get('time-pos')
        progressing = isinstance(position, (int, float)) and position > 0
        # A deliberate pause is preserved; selecting the path is sufficient then.
        verified = path_matches and (previous_pause or progressing)
        if verified:
            if verified_since is None:
                verified_since = time.monotonic()
            # A short stability check catches a host destroyed just after selection.
            if time.monotonic() - verified_since >= 0.4:
                with HANDLED_LOCK:
                    HANDLED_PID = pid
                status = {
                    'time': time.time(), 'result': 'next-video', 'verified': True,
                    'event_id': event['id'], 'player_pid': pid,
                    'previous_pid': previous_pid, 'before_path': before_path,
                    'target_path': target, 'path': current.get('path'),
                    'time_pos': position, 'paused': current.get('pause'),
                    'restarted': restarted, 'elapsed_time': time.monotonic() - started,
                }
                write_status(status)
                return status
        else:
            verified_since = None
            # A PID that exists but stops responding or never opens a file may
            # need one restart. Never advance twice or choose another target.
            if (selected_since is not None and time.monotonic() - selected_since > 12
                    and not restarted and not path_matches
                    and current.get('idle-active') is True and current.get('pause') is not True):
                restart_player(pid)
                restarted = True
                selected_pid = None
                loaded_pid = None
                initial = None
                time.sleep(0.4)
                continue
        time.sleep(0.15)


def worker():
    while True:
        with CONDITION:
            while not PENDING:
                CONDITION.wait()
            event = PENDING.popleft()
            BUSY.set()
        try:
            perform_event(event)
        except Exception as error:
            try:
                write_status({'time': time.time(), 'result': 'error', 'event_id': event['id'],
                              'error': str(error), 'elapsed_time': time.monotonic() - event['seen']})
            except Exception:
                pass
        finally:
            with CONDITION:
                if not PENDING:
                    BUSY.clear()


def watch():
    threading.Thread(target=worker, daemon=True, name='Desktop-next operation').start()
    previous = None
    event_id = 0
    last_error = -1000
    while True:
        try:
            current = fingerprint()
            if previous is None:
                previous = current
            elif current != previous:
                previous = current
                event_id += 1
                with CONDITION:
                    PENDING.append({'id': event_id, 'fingerprint': current, 'seen': time.monotonic()})
                    BUSY.set()
                    CONDITION.notify()
        except Exception as error:
            if time.monotonic() - last_error >= 5:
                try:
                    write_status({'time': time.time(), 'result': 'watch-error', 'error': str(error)})
                except Exception:
                    pass
                last_error = time.monotonic()
        time.sleep(0.1)


if __name__ == '__main__':
    watch()
