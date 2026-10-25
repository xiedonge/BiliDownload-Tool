"""Download a standalone best audio stream, preserving its original codec."""
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import threading
from datetime import datetime

import video_downloader as core


def probe_audio(path, ffprobe):
    path = Path(path)
    result = subprocess.run([str(ffprobe), '-v', 'error', '-show_entries',
        'format=duration:stream=codec_type,codec_name,bit_rate,sample_rate', '-of', 'json', str(path)],
        capture_output=True, text=True, encoding='utf-8', errors='replace', timeout=45,
        creationflags=core.CREATE_NO_WINDOW)
    if result.returncode:
        raise RuntimeError('音频文件校验失败：' + core.clean_message(result.stderr))
    data = json.loads(result.stdout)
    streams = data.get('streams', [])
    audio = [s for s in streams if s.get('codec_type') == 'audio']
    if not audio or any(s.get('codec_type') == 'video' for s in streams) or not path.stat().st_size:
        raise RuntimeError('文件不是有效的纯音频，未标记为完成。')
    return {'path': str(path.resolve()), 'bytes': path.stat().st_size,
            'duration': float(data.get('format', {}).get('duration') or 0), 'audio_only': True,
            'audio_codec': audio[0].get('codec_name'), 'audio_tracks': len(audio),
            'abr': float(audio[0].get('bit_rate') or 0)/1000,
            'sample_rate': audio[0].get('sample_rate')}


def duration_matches(media, info):
    expected = float(info.get('duration') or 0)
    return media['duration'] > 0 and (not expected or abs(media['duration']-expected) <= max(2, expected*.02))


def audio_name(info):
    # The source format ID distinguishes different quality tracks without inventing bitrate.
    title = f"{info['title']} [音频 {info.get('acodec') or '原始'} {info['format_id']}]"
    return core.complete_filename(title, info['id'], '.'+info['ext'], None, None)


def existing_audio(folder, info, ffprobe):
    exact = Path(folder)/audio_name(info)
    marker = f"[音频 {info.get('acodec') or '原始'} {info['format_id']}]"
    candidates = [exact]
    if Path(folder).is_dir():
        candidates += [p for p in Path(folder).iterdir() if p != exact and p.suffix == '.'+info['ext']
                       and f"[{info['id']}]" in p.name and marker in p.name]
    for candidate in candidates:
        if not candidate.is_file() or candidate.is_symlink():
            continue
        try:
            media = probe_audio(candidate, ffprobe)
            if duration_matches(media, info): return media
        except (OSError, ValueError, RuntimeError, subprocess.SubprocessError):
            continue
    return None


def download_audio(raw_url, output_dir=core.DEFAULT_OUTPUT, use_chrome=False, emit=None, stop=None,
                   cookie_file=None, preview_only=False, expected_quality=None, rate_controller=None):
    import yt_dlp
    emit = emit or (lambda *args: None)
    stop = stop or threading.Event()
    url = core.normalize_url(raw_url)
    folder = Path(output_dir).resolve()
    ffmpeg, ffprobe = core.find_media_tools()
    signature = core.quality_signature(url, use_chrome, cookie_file)
    def cancelled():
        if stop.is_set():
            raise core.Cancelled('已暂停音频下载，片段保留供继续。')
    cancelled()
    if expected_quality:
        if not expected_quality.get('audio_only') or expected_quality.get('signature') != signature:
            raise ValueError('音频模式或登录信息已变化，请重新检查音轨。')
        if not preview_only:
            found = existing_audio(folder, expected_quality, ffprobe)
            if found:
                return found | {'title': expected_quality['title'], 'reused': True}
    job = hashlib.sha256(('audio\n'+url).encode()).hexdigest()[:16]
    cache = folder/'下载缓存'/job
    paths = []
    def progress(data):
        cancelled()
        if data.get('status') == 'downloading':
            total = data.get('total_bytes') or data.get('total_bytes_estimate') or 0
            done = data.get('downloaded_bytes') or 0
            emit('stage', '下载音频')
            emit('progress', {'part': '音频', 'downloaded': done, 'total': total,
                'percent': min(100, done/total*100) if total else None,
                'speed': data.get('speed') or 0, 'eta': data.get('eta'),
                'total_estimated': not bool(data.get('total_bytes'))})
    options = {'format': 'ba', 'noplaylist': True, 'playlistend': 1, 'cachedir': False,
        'quiet': True, 'noprogress': True, 'logger': core.DownloadLogger(emit),
        'socket_timeout': 25, 'retries': 5, 'fragment_retries': 5, 'extractor_retries': 2,
        'concurrent_fragment_downloads': 1, 'continuedl': True, 'overwrites': False,
        'windowsfilenames': True, 'ffmpeg_location': str(ffmpeg.parent),
        'outtmpl': {'default': '%(title).150B [%(id)s].%(ext)s'},
        'progress_hooks': [progress], 'post_hooks': [lambda p: paths.append(Path(p))],
        'ratelimit': core.configured_rate_limit()}
    if use_chrome and not cookie_file:
        options['cookiesfrombrowser'] = ('chrome',)
    if rate_controller is not None:
        core.install_live_rate_support()
        options.update(ratelimit=None, local_live_rate_controller=rate_controller, local_stop_event=stop)
    try:
        with yt_dlp.YoutubeDL(options) as downloader:
            if core.bangumi_id(url, 'ep'):
                core.install_bangumi_guard(downloader)
            if cookie_file:
                downloader.cookiejar = core.load_cookie_file(cookie_file, url)
            emit('status', '正在检查最高可用独立音轨…')
            info = downloader.extract_info(url, download=False)
            cancelled()
            if not info or info.get('_type', 'video') != 'video':
                raise ValueError('请先识别清单，再选择具体条目下载音频。')
            if info.get('vcodec') != 'none' or info.get('acodec') in (None, 'none'):
                raise ValueError('没有可用的独立音轨，未下载视频流。')
            if core.bangumi_id(url, 'ep'):
                if options['logger'].preview_only:
                    raise ValueError('当前账号只能获取试看音轨，请确认观看权限。')
                core.verify_bangumi_streams(downloader, info, url, stop, emit)
                info['id'] = core.bangumi_id(url, 'ep')
                info['title'] = core.bangumi_title(downloader, info['id'], info.get('title') or info['id'])
            quality = {k: info.get(k) for k in ('id','title','ext','format_id','duration','acodec','abr','asr')}
            quality['title'] = str(quality.get('title') or quality['id'])
            quality.update(audio_only=True, has_audio=True, signature=signature,
                estimated_bytes=info.get('filesize') or info.get('filesize_approx') or 0,
                size_partial=not bool(info.get('filesize') or info.get('filesize_approx')),
                cookie_file_used=bool(cookie_file), chrome_used=bool(use_chrome and not cookie_file))
            if preview_only:
                return quality
            if expected_quality and any(expected_quality.get(k) != quality.get(k) for k in ('format_id','acodec','ext')):
                raise ValueError('音轨画质已变化，请重新检查后下载。')
            found = existing_audio(folder, quality, ffprobe)
            if found:
                return found | {'title': quality['title'], 'reused': True}
            # Validate the complete destination name before any media download.
            destination = core.unique_destination(folder/audio_name(quality))
            folder.mkdir(parents=True, exist_ok=True)
            if quality['estimated_bytes'] and shutil.disk_usage(folder).free < quality['estimated_bytes']*1.2:
                raise RuntimeError('磁盘空间不足，请清理空间后继续。')
            cache = cache/hashlib.sha256(str(info['format_id']).encode()).hexdigest()[:8]
            cache.mkdir(parents=True, exist_ok=True)
            downloader.params['paths'] = {'home': str(cache), 'temp': str(cache)}
            downloader.process_info(info)
            cancelled()
            if not paths or paths[-1].resolve().parent != cache.resolve():
                raise RuntimeError('音频下载未生成预期文件，已保留缓存。')
            completed = paths[-1].resolve()
            emit('stage', '校验音频')
            media = probe_audio(completed, ffprobe)
            if not duration_matches(media, info):
                raise RuntimeError('音频时长不完整，已保留缓存，请重试。')
            try:
                completed.rename(destination)
            except FileExistsError:
                destination = core.unique_destination(destination)
                completed.rename(destination)
            media.update(path=str(destination), title=quality['title'], reused=False)
            try:
                records = folder/'下载记录'; records.mkdir(exist_ok=True)
                receipt = records/f'{datetime.now():%Y%m%d-%H%M%S-%f}-{job}.json'
                with receipt.open('x', encoding='utf-8') as handle:
                    json.dump(media | {'source_ref': info['id'], 'selected_format': info['format_id']},handle,ensure_ascii=False)
            except OSError:
                emit('log','音频已保存，但下载记录暂时无法写入。')
            emit('status','音频已保存并通过轨道、时长校验。')
            return media
    except Exception:
        cancelled()
        raise
