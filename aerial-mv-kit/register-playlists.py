#!/usr/bin/env python3
"""Aerial 4.1.6 の YouTube 登録一覧を更新する。動画本体は保存しない。"""
import argparse
import copy
import json
import math
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
import time
from urllib.parse import parse_qs, urlparse
import uuid

HERE = Path(__file__).resolve().parent
SOURCES = HERE / 'sources.json'
TARGET = Path('/Users/Shared/Aerial/live-feeds.json')
APPLE_EPOCH = 978307200
VIDEO_ID = re.compile(r'[A-Za-z0-9_-]{11}')
HOSTS = {'youtube.com', 'www.youtube.com', 'm.youtube.com', 'youtu.be'}


def normalize_url(value):
    url = urlparse(value.strip())
    if url.scheme not in ('http', 'https') or url.hostname not in HOSTS or url.username or url.password:
        raise ValueError('YouTube の動画かプレイリストの URL を入力してください：' + value)
    query = parse_qs(url.query)
    playlist = query.get('list', [''])[0]
    if playlist:
        if not re.fullmatch(r'[A-Za-z0-9_-]+', playlist):
            raise ValueError('プレイリスト ID を確認してください。')
        return 'https://www.youtube.com/playlist?list=' + playlist
    video = (url.path.strip('/') if url.hostname == 'youtu.be' else
             query.get('v', [''])[0] if url.path == '/watch' else
             url.path.split('/')[2] if url.path.startswith(('/shorts/', '/live/')) else '')
    if not VIDEO_ID.fullmatch(video):
        raise ValueError('動画 ID を確認してください：' + value)
    return 'https://www.youtube.com/watch?v=' + video


def finite(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def check_target(path):
    if path.is_symlink() or path.parent.is_symlink():
        raise ValueError('シンボリックリンクの設定ファイルには書き込みません。')


def read_document(path):
    check_target(path)
    document = json.loads(path.read_text(encoding='utf-8')) if path.exists() else {'version': 1, 'feeds': []}
    if (not isinstance(document, dict) or type(document.get('version')) is not int
            or document['version'] != 1 or not isinstance(document.get('feeds'), list)):
        raise ValueError('Aerial の既存設定の形式が対応していません。変更せず中止します。')
    ids = set()
    for feed in document['feeds']:
        if not isinstance(feed, dict):
            raise ValueError('既存の動画登録データが壊れています。')
        identifier = feed.get('id')
        if not isinstance(identifier, str) or str(uuid.UUID(identifier)).upper() != identifier or identifier in ids:
            raise ValueError('既存の登録 ID が無効か重複しています。')
        ids.add(identifier)
        if any(not isinstance(feed.get(key), str) or not feed[key] for key in ('displayName', 'sourceURL', 'kind')):
            raise ValueError('既存のタイトルか URL が無効です。')
        if not finite(feed.get('playbackSeconds')) or feed['playbackSeconds'] <= 0 or not finite(feed.get('addedAt')):
            raise ValueError('既存の登録日時か再生秒数が無効です。')
    return document


def fetch_videos(sources):
    videos = {}
    for source in sources:
        print('動画一覧を確認しています：' + source, flush=True)
        result = subprocess.run(['yt-dlp', '--ignore-config', '--flat-playlist', '--skip-download', '--dump-single-json',
                                 '--quiet', '--no-warnings', '--socket-timeout', '20', '--retries', '1', source],
                                capture_output=True, text=True, encoding='utf-8', timeout=180)
        if result.returncode:
            raise RuntimeError('取得に失敗しました。URL の公開状態とネット接続を確認してください：' + source)
        info = json.loads(result.stdout)
        entries = info.get('entries') if info.get('_type') in ('playlist', 'multi_video') else [info]
        if not isinstance(entries, list):
            raise ValueError('YouTube の応答を読み取れませんでした。')
        accepted = 0
        for entry in entries:
            if not isinstance(entry, dict) or entry.get('availability') in ('private', 'needs_auth'):
                continue
            title, video = entry.get('title'), entry.get('id', '')
            if title in ('[Private video]', '[Deleted video]'):
                continue
            if not isinstance(video, str) or not VIDEO_ID.fullmatch(video) or not isinstance(title, str) or not title.strip():
                raise ValueError('取得した動画データが不完全です。設定を変更せず中止します。')
            duration = entry.get('duration')
            videos[video] = {'title': title, 'seconds': float(duration) if finite(duration) and duration > 0 else 300.0}
            accepted += 1
        if not accepted:
            raise ValueError('公開動画が 0 本の URL がありました。設定を変更せず中止します。')
    if not videos:
        raise ValueError('登録する動画が 0 本です。')
    return videos


def merge_feeds(document, videos, now=None):
    output = copy.deepcopy(document)
    unseen = dict(videos)
    for feed in output['feeds']:
        if feed['kind'] != 'youtube':
            continue
        try:
            canonical = normalize_url(feed['sourceURL'])
            video = parse_qs(urlparse(canonical).query).get('v', [''])[0]
        except ValueError:
            continue
        if video in videos:
            feed.update(displayName=videos[video]['title'], playbackSeconds=videos[video]['seconds'])
            unseen.pop(video, None)
    added_at = (time.time() if now is None else now) - APPLE_EPOCH
    for video, value in unseen.items():
        output['feeds'].append({'id': str(uuid.uuid4()).upper(), 'displayName': value['title'],
                                'sourceURL': 'https://www.youtube.com/watch?v=' + video, 'kind': 'youtube',
                                'playbackSeconds': value['seconds'], 'addedAt': added_at})
    return output, len(unseen)


def aerial_running():
    result = subprocess.run(['pgrep', '-x', 'Aerial'], capture_output=True, timeout=10)
    if result.returncode not in (0, 1):
        raise RuntimeError('Aerial が終了しているか確認できません。')
    return result.returncode == 0


def quit_aerial():
    if not aerial_running():
        return
    result = subprocess.run(['osascript', '-e', 'tell application "Aerial" to quit'],
                            capture_output=True, timeout=20)
    if result.returncode:
        raise RuntimeError('Aerial を終了できませんでした。アプリを終了してからやり直してください。')
    deadline = time.monotonic() + 15
    while aerial_running():
        if time.monotonic() >= deadline:
            raise RuntimeError('Aerial の終了待ちが終わりません。設定を変更せず中止します。')
        time.sleep(0.3)


def atomic_json(path, value):
    check_target(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    name = None
    try:
        with tempfile.NamedTemporaryFile(mode='w', encoding='utf-8', dir=path.parent, prefix='.' + path.name,
                                         suffix='.tmp', delete=False) as output:
            name = output.name
            json.dump(value, output, ensure_ascii=False, indent=2)
            output.write('\n')
            output.flush()
            os.fsync(output.fileno())
        check_target(path)
        os.replace(name, path)
        name = None
    finally:
        if name is not None:
            Path(name).unlink(missing_ok=True)


def saved_sources(path):
    check_target(path)
    if not path.exists():
        return []
    try:
        values = json.loads(path.read_text(encoding='utf-8'))
        if not isinstance(values, list) or any(not isinstance(value, str) for value in values):
            raise ValueError('URL 一覧は文字列のリストにしてください。')
        return [normalize_url(value) for value in values]
    except (ValueError, OSError) as error:
        raise ValueError('保存した URL 一覧が壊れているか読めません。sources.json を確認してください。') from error


def register(sources, target=TARGET, source_file=SOURCES):
    sources = list(dict.fromkeys(saved_sources(source_file) + [normalize_url(value) for value in sources]))
    if not sources:
        raise ValueError('URL がありません。')
    read_document(target)  # 壊れた既存設定は、取得を始める前にも検出する。
    videos = fetch_videos(sources)  # すべての URL が成功するまでは設定を変更しない。
    quit_aerial()
    document = read_document(target)  # 終了前に Aerial が保存した内容を再読込する。
    output, added = merge_feeds(document, videos)
    backup = None
    if target.exists():
        backup = target.with_name(target.name + '.backup-' + str(time.time_ns()))
        shutil.copy2(target, backup)
    atomic_json(target, output)
    try:
        atomic_json(source_file, sources)
    except OSError as error:
        raise RuntimeError('Aerial の登録は保存済みですが、次回用の URL を保存できません。Aerial を手動で開いてください。') from error
    print(f'{len(videos)} 本を確認、{added} 本を新規登録しました。削除済みの登録は保持します。', flush=True)
    if backup:
        print('元の設定のバックアップ：' + str(backup), flush=True)
    try:
        result = subprocess.run(['open', '-a', 'Aerial'], capture_output=True, timeout=20)
    except (OSError, subprocess.TimeoutExpired) as error:
        raise RuntimeError('登録は保存しましたが Aerial を開けませんでした。アプリを手動で開いてください。') from error
    if result.returncode:
        raise RuntimeError('登録は保存しましたが Aerial を開けませんでした。アプリを手動で開いてください。')
    print('Aerial を開きました。ネットワーク動画を有効にし、シャッフル再生を選んでください。')


def main(argv=None):
    if sys.platform != 'darwin':
        print('このスクリプトは macOS 専用です。設定は変更していません。', file=sys.stderr)
        return 1
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('urls', nargs='*')
    parser.add_argument('--noninteractive', action='store_true')
    args = parser.parse_args(argv)
    try:
        sources = args.urls
        if not sources and not args.noninteractive:
            print('新しい推しの YouTube URL を 1 行ずつ貼り付けて Enter。保存済みの一覧に追加します。')
            print('最後は空行で Enter。空行だけなら、前回の一覧をもう一度確認します。')
            while True:
                value = input('URL：').strip()
                if not value:
                    break
                sources.append(value)
        register(sources)
        return 0
    except Exception as error:
        print('中止しました：' + str(error), file=sys.stderr)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())

