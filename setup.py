"""初回設定。取得と安全確認が終わるまで Sucrose の設定を変更しない。"""
import json
import os
import socket
import subprocess
from pathlib import Path

from refresh_playlists import apply_playlists, discover_playlists, prepare_playlists

ROOT = Path(os.environ['APPDATA']) / 'Sucrose'
MPV_CONFIG = r'''vo=gpu
gpu-context=d3d11
hwdec=auto-safe
audio=no
sid=no
sub-visibility=no
osc=no
osd-level=0
keepaspect=yes
panscan=0
video-zoom=0
loop-playlist=inf
demuxer-max-bytes=128MiB
ytdl=no
input-ipc-server=\\.\pipe\sucrose-hq
'''


def check_sucrose_closed():
    result = subprocess.run(['tasklist', '/FO', 'CSV', '/NH'], capture_output=True,
                            text=True, errors='replace', creationflags=subprocess.CREATE_NO_WINDOW)
    if result.returncode:
        raise RuntimeError('Sucrose が終了しているか確認できませんでした。Windows を再起動してからやり直してください。')
    if any(line.lower().startswith('"sucrose') for line in result.stdout.splitlines()):
        raise RuntimeError('Sucrose が起動中です。画面を閉じるだけでなく、タスクトレイから Sucrose を終了してください。')


def check_port_free():
    with socket.socket() as probe:
        probe.settimeout(1)
        if probe.connect_ex(('127.0.0.1', 18743)) == 0:
            raise RuntimeError('既存の壁紙補助プログラムが動作しています。このキットを重ねて設定しないでください。')


def main():
    engine = ROOT / 'Setting/Engine.json'
    if not engine.exists():
        raise RuntimeError('Sucrose を一度起動し、YouTube のプレイリストを登録してから終了してください。')
    check_sucrose_closed()
    check_port_free()
    themes = discover_playlists(ROOT)
    if not themes:
        raise RuntimeError('対象のプレイリストが 0 件です。Sucrose の「＋」→「YouTube」でプレイリスト URL を登録してください。')
    data = json.loads(engine.read_text(encoding='utf-8-sig'))
    if not isinstance(data.get('Properties'), dict):
        raise RuntimeError('Sucrose の設定形式が対応していません。設定は変更していません。')
    ready, errors = prepare_playlists(themes)
    if errors:
        raise RuntimeError('動画一覧の取得に失敗したため、設定は変更していません。\n' + '\n'.join(errors))
    check_sucrose_closed()
    check_port_free()
    backup = engine.with_name('Engine.before-mv.json')
    if not backup.exists():
        backup.write_bytes(engine.read_bytes())
    config = ROOT / 'Cache/MpvPlayer/uMpvPlayer.config'
    config.parent.mkdir(parents=True, exist_ok=True)
    saved = config.with_name('uMpvPlayer.before-mv.config')
    if config.exists() and not saved.exists():
        saved.write_bytes(config.read_bytes())
    data['Properties'].update(Video='MpvPlayerLive', StretchType='Uniform', WallpaperVolume=0,
                              WallpaperLoop=False, WallpaperShuffle=True, InputType='Close')
    engine.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding='utf-8')
    config.write_text(MPV_CONFIG, encoding='utf-8')
    apply_playlists(ready, live_update=False)
    print(f'{len(ready)} 個のプレイリストを設定しました。', flush=True)
    return 0


if __name__ == '__main__':
    try:
        raise SystemExit(main())
    except Exception as error:
        print('セットアップを中止しました：' + str(error), flush=True)
        raise SystemExit(1)
