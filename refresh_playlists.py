"""Sucrose の YouTube プレイリストを取得し、動画壁紙の一覧を更新する。"""
import json
import os
import re
import sys
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import yt_dlp
from wallpaper_control import command

ROOT = Path(os.environ['APPDATA']) / 'Sucrose'


def read(path):
    return json.loads(path.read_text(encoding='utf-8-sig'))


def playlist_url(url):
    parsed = urlparse(url)
    if parsed.hostname not in ('youtube.com', 'www.youtube.com', 'm.youtube.com', 'youtu.be'):
        return None
    pid = parse_qs(parsed.query).get('list', [''])[0]
    if not re.fullmatch(r'[A-Za-z0-9_-]+', pid):
        return None
    return 'https://www.youtube.com/playlist?list=' + pid


def discover_playlists(root=ROOT):
    library = root / 'Library'
    if not library.exists():
        raise RuntimeError('Sucrose を一度起動し、YouTube のプレイリストをライブラリに登録してください。')
    themes = []
    for folder in library.iterdir():
        infofile = folder / 'SucroseInfo.json'
        marker = folder / 'SucroseMV.source.json'
        if not infofile.exists():
            continue
        info = read(infofile)
        url = playlist_url(info.get('Source', '')) if info.get('Type') == 4 else None
        if not url and info.get('Source') == 'highest.m3u' and marker.exists():
            url = playlist_url(read(marker).get('url', ''))
        if url:
            themes.append({'folder': folder, 'info': info, 'url': url})
    return themes


def prepare_playlists(themes):
    """取得に失敗しても、この段階では Sucrose のファイルを変更しない。"""
    ready = []
    errors = []
    for theme in themes:
        title = theme['info'].get('Title', theme['folder'].name)
        print(f'「{title}」の動画一覧を YouTube から確認しています…', flush=True)
        try:
            with yt_dlp.YoutubeDL({'extract_flat': 'in_playlist', 'quiet': True,
                                  'skip_download': True, 'socket_timeout': 20, 'retries': 2}) as ydl:
                data = ydl.extract_info(theme['url'], download=False)
            ids = list(dict.fromkeys(entry['id'] for entry in (data or {}).get('entries', [])
                                    if entry and re.fullmatch(r'[A-Za-z0-9_-]{11}', entry.get('id', ''))))
            if not ids:
                raise RuntimeError('公開動画を取得できませんでした。プレイリストの URL と公開状態を確認してください。')
            ready.append({**theme, 'ids': ids})
        except Exception as error:
            errors.append(f'「{title}」：{error}')
    return ready, errors


def apply_playlists(themes, live_update=True):
    for theme in themes:
        folder, info, url = theme['folder'], theme['info'], theme['url']
        infofile = folder / 'SucroseInfo.json'
        urls = ['http://127.0.0.1:18743/v/' + vid for vid in theme['ids']]
        target = folder / 'highest.m3u'
        text = '#EXTM3U\n' + '\n'.join(urls) + '\n'
        old = target.read_text(encoding='utf-8-sig') if target.exists() else ''
        if text != old:
            if old:
                target.with_suffix('.m3u.bak').write_text(old, encoding='utf-8')
            temporary = target.with_suffix('.m3u.tmp')
            temporary.write_text(text, encoding='utf-8')
            os.replace(temporary, target)
        if info.get('Type') == 4:
            backup = folder / 'SucroseInfo.before-mv.json'
            if not backup.exists():
                backup.write_bytes(infofile.read_bytes())
            (folder / 'SucroseMV.source.json').write_text(
                json.dumps({'url': url}, ensure_ascii=False, indent=2), encoding='utf-8')
            info.update(Type=3, Source='highest.m3u')
            infofile.write_text(json.dumps(info, ensure_ascii=False, indent=2), encoding='utf-8')
        added = 0
        if live_update:
            try:
                entries = command(['get_property', 'playlist']) or []
                if any(folder.name in entry.get('playlist-path', '') for entry in entries):
                    loaded = {entry['filename'] for entry in entries}
                    for entry in urls:
                        if entry not in loaded:
                            command(['loadfile', entry, 'append'])
                            added += 1
            except OSError:
                pass
        print(f'「{info.get("Title", folder.name)}」：{len(theme["ids"])} 本を確認しました。'
              f'（再生中の一覧への追加：{added} 本）', flush=True)


def main():
    try:
        themes = discover_playlists()
        if not themes:
            raise RuntimeError('対象のプレイリストがありません。Sucrose の「＋」→「YouTube」でプレイリスト URL を登録してください。')
        ready, errors = prepare_playlists(themes)
        apply_playlists(ready)
        for error in errors:
            print('更新できませんでした：' + error, flush=True)
        return int(bool(errors))
    except Exception as error:
        print('更新できませんでした：' + str(error), flush=True)
        return 1


if __name__ == '__main__':
    if '--show-result' in sys.argv:
        import contextlib
        import ctypes
        import io
        output = io.StringIO()
        with contextlib.redirect_stdout(output), contextlib.redirect_stderr(output):
            result = main()
        log = Path(__file__).with_name('playlist-refresh.log')
        log.write_text(output.getvalue(), encoding='utf-8')
        message = ('更新が完了しました！\nSucrose を開き直し、推しの壁紙を選び直してください。'
                   if result == 0 else '更新を完了できませんでした。プレイリストの URL とネット接続を確認してください。'
                   '\n詳しい記録：\n' + str(log))
        ctypes.windll.user32.MessageBoxW(None, message, '壁紙プレイリストの更新', 0 if result == 0 else 48)
        raise SystemExit(result)
    raise SystemExit(main())
