from __future__ import annotations

import argparse
import hashlib
import http.cookiejar
import json
import math
import os
from pathlib import Path
import queue
import re
import shutil
import struct
import subprocess
import sys
import threading
import time
from datetime import datetime
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

APP_DIR = Path(__file__).resolve().parent
DEFAULT_OUTPUT = Path(r"F:\BiliDownload")
CREATE_NO_WINDOW = 0x08000000 if os.name == "nt" else 0


class Cancelled(Exception):
    pass


class ProtectedVideoError(RuntimeError):
    """The service explicitly marks this source as protected; retry cannot fix it."""
    retryable = False
    code = 'drm_protected'

    def __init__(self):
        super().__init__('此片源受 DRM 保护，未确认到可直接下载的非加密视频流，当前工具无法下载。'
                         '请在浏览器打开播放页，或查看官方客户端是否提供缓存；此项不自动重试。')


def positive_flag(value):
    return value is True or value == 1 or (isinstance(value, str) and value.lower() in ('1', 'true'))


def bangumi_play_result(data):
    data = data if isinstance(data, dict) else {}
    code = data.get('code')
    for key in ('raw', 'data', 'result'):
        if isinstance(data.get(key), dict):
            data = data[key]
            if code is None:
                code = data.get('code')
    return data, code


def install_bangumi_guard(downloader):
    """Refresh empty SSR data via the ordinary authenticated API; verify marked streams."""
    from yt_dlp.extractor.bilibili import BiliBiliBangumiIE

    class CheckedBangumiIE(BiliBiliBangumiIE):
        @classmethod
        def ie_key(cls):
            return BiliBiliBangumiIE.ie_key()

        def _real_extract(self, url):
            self._marked_source = False
            self._source_url = url
            result = super()._real_extract(url)
            result['_bili_check_clear'] = self._marked_source
            return result

        def _search_json(self, start_pattern, string, name, video_id, **kwargs):
            value = super()._search_json(start_pattern, string, name, video_id, **kwargs)
            if start_pattern != r'playurlSSRData\s*=' or not value:
                return value
            body, code = bangumi_play_result(value)
            video = body.get('video_info') or {}
            if (video.get('dash') or {}).get('video') or video.get('durl'):
                return value
            # Same endpoint and parameters as upstream's no-SSR path. No alternate
            # account, region, client identity, DRM option or entitlement is used.
            fresh = self._download_json('https://api.bilibili.com/pgc/player/web/v2/playurl',
                video_id, 'Refreshing empty episode playback data',
                query={'fnval': 12240, 'ep_id': video_id}, headers={'Referer': self._source_url})
            current, status = bangumi_play_result(fresh)
            full = ((current.get('play_check') or {}).get('play_detail') == 'PLAY_WHOLE'
                    or current.get('play_video_type') == 'whole')
            if status == 0 and full and not positive_flag((current.get('video_info') or {}).get('is_preview')):
                return fresh
            return value

        def extract_formats(self, play_info):
            self._marked_source = positive_flag(play_info.get('is_drm'))
            formats = super().extract_formats(play_info)
            if self._marked_source and not formats:
                raise ProtectedVideoError()
            return formats

    downloader.add_info_extractor(CheckedBangumiIE(downloader))


def plain_mp4_header(data):
    """Require complete, recognized, unencrypted sample descriptions in the init segment."""
    samples = []
    protected = False
    has_moov = False
    invalid = False
    video_types = {b'avc1', b'avc3', b'hev1', b'hvc1', b'av01', b'vp09'}
    audio_types = {b'mp4a', b'ac-3', b'ec-3', b'Opus', b'fLaC'}

    def walk(start, end, strict=False):
        nonlocal protected, has_moov, invalid
        while start + 8 <= end:
            size, kind = struct.unpack_from('>I4s', data, start)
            header = 8
            if size == 1:
                if start + 16 > end:
                    invalid = True
                    return
                size = struct.unpack_from('>Q', data, start + 8)[0]
                header = 16
            if size == 0:
                size = end - start
            if size < header or start + size > end:
                # A bounded read may end inside mdat; an incomplete moov is not proof.
                if strict or kind == b'moov':
                    invalid = True
                return
            body, finish = start + header, start + size
            if kind in (b'pssh', b'encv', b'enca', b'sinf', b'tenc', b'senc'):
                protected = True
            if kind in (b'sgpd', b'sbgp') and data[body+4:body+8] == b'seig':
                protected = True
            if kind == b'moov':
                has_moov = True
            if kind in (b'moov', b'trak', b'mdia', b'minf', b'stbl', b'moof', b'traf'):
                walk(body, finish, True)
            elif kind == b'stsd':
                if body + 8 > finish:
                    invalid = True
                    return
                count = struct.unpack_from('>I', data, body+4)[0]
                position = body + 8
                for _ in range(count):
                    if position + 8 > finish:
                        invalid = True
                        break
                    length, sample = struct.unpack_from('>I4s', data, position)
                    if length < 8 or position + length > finish:
                        invalid = True
                        break
                    samples.append(sample)
                    if sample not in video_types | audio_types:
                        protected = True
                    else:
                        offset = 86 if sample in video_types else 36
                        if length < offset:
                            invalid = True
                        else:
                            walk(position + offset, position + length, True)
                    position += length
                if position != finish or not count:
                    invalid = True
            start = finish
        if strict and start != end:
            invalid = True

    walk(0, len(data))
    return has_moov and bool(samples) and not protected and not invalid


def verify_bangumi_streams(downloader, info, url, stop, emit):
    if not info.get('_bili_check_clear'):
        return
    from yt_dlp.networking import Request
    emit('status', '正在核验账号可播放的视频流是否可直接下载…')
    for stream in info.get('requested_formats') or [info]:
        if stop.is_set():
            raise Cancelled('已停止检查。')
        if stream.get('has_drm') or not stream.get('url'):
            raise ProtectedVideoError()
        headers = {**info.get('http_headers', {}), **stream.get('http_headers', {}),
                   'Referer': url, 'Range': 'bytes=0-262143'}
        with downloader.urlopen(Request(stream['url'], headers=headers)) as response:
            data = response.read(262144)
        if not plain_mp4_header(data):
            raise ProtectedVideoError()
    emit('log', '已确认所选视频和音频为账号可直接访问的非加密流。')


class LiveRateController:
    """Shared pacing budget for this window, changeable while a transfer is active."""
    def __init__(self, limit=None):
        self.lock = threading.Lock()
        self.limit = limit
        self.deadline = time.monotonic()
        self.version = 0

    def set_limit(self, limit):
        with self.lock:
            self.limit, self.deadline = limit, time.monotonic()
            self.version += 1

    def pace(self, byte_count, stop):
        with self.lock:
            limit, version = self.limit, self.version
            if not limit:
                return
            self.deadline = max(time.monotonic(), self.deadline) + byte_count / limit
            deadline = self.deadline
        while time.monotonic() < deadline:
            if stop.wait(min(0.1, max(0, deadline - time.monotonic()))):
                raise Cancelled('已停止，下载片段保留用于续传。')
            with self.lock:
                if self.version != version:
                    return


def install_live_rate_support():
    from yt_dlp.downloader.common import FileDownloader
    if getattr(FileDownloader.slow_down, '_local_live_rate', False):
        return
    original = FileDownloader.slow_down
    def slow_down(fd, start_time, now, byte_counter):
        controller = fd.params.get('local_live_rate_controller')
        if controller is None:
            return original(fd,start_time,now,byte_counter)
        previous = getattr(fd,'_local_bytes',0)
        fd._local_bytes = byte_counter
        controller.pace(max(0,byte_counter-previous) if byte_counter>=previous else byte_counter,
                        fd.params['local_stop_event'])
    slow_down._local_live_rate = True
    FileDownloader.slow_down = slow_down


def clean_message(value):
    """Never put cookie values or signed URL query strings in the UI or logs."""
    value = re.sub(r"\x1b\[[0-9;]*m", "", str(value))
    value = re.sub(r"https?://[^\s<>\"']+", "[网页地址]", value)
    return re.sub(
        r"(?i)\b(cookie|authorization|sessdata|access_token|refresh_token|token)\s*[:=]\s*[^\r\n]+",
        r"\1=[已隐藏]", value,
    )[:1800]


def normalize_url(value):
    value = value.strip().strip('"').strip("'").strip()
    # Accept a BV number as a small convenience, but never searches or playlists.
    if re.fullmatch(r"BV[0-9A-Za-z]{10}", value):
        value = "https://www.bilibili.com/video/" + value
    if any(ch.isspace() for ch in value):
        raise ValueError("请只粘贴一个视频链接，不要包含说明文字或多个链接。")
    parsed = urlsplit(value)
    if parsed.scheme not in ("http", "https") or not parsed.hostname:
        raise ValueError("请粘贴以 https:// 或 http:// 开头的视频链接。")
    if parsed.username or parsed.password:
        raise ValueError("链接不能包含用户名或密码。")
    host = parsed.hostname.lower()
    if host in ('www.bilibili.com', 'bilibili.com') and re.fullmatch(r'/bangumi/play/(?:ss|ep)\d+/?', parsed.path):
        return urlunsplit(('https', 'www.bilibili.com', parsed.path.rstrip('/'), '', ''))
    if host in ("www.bilibili.com", "bilibili.com") and re.fullmatch(
        r"/video/(?:BV[0-9A-Za-z]{10}|av[0-9]+)/?", parsed.path
    ):
        params = [(k, v) for k, v in parse_qsl(parsed.query) if k == "p"]
        value = urlunsplit(("https", "www.bilibili.com", parsed.path.rstrip("/"), urlencode(params), ""))
    return value


def bangumi_id(url, kind=None):
    parsed = urlsplit(normalize_url(url))
    match = re.fullmatch(r'/bangumi/play/((ss|ep)\d+)', parsed.path)
    return match.group(1) if parsed.hostname == 'www.bilibili.com' and match and (not kind or match.group(2) == kind) else None


def format_size(size):
    if size is None or size <= 0:
        return '未知'
    for unit, divisor in [('GB', 1024**3), ('MB', 1024**2), ('KB', 1024)]:
        if size >= divisor:
            return f'{size/divisor:.2f} {unit}'
    return f'{int(size)} B'


def progress_description(value):
    downloaded = value.get('downloaded') or 0
    total = value.get('total') or 0
    speed = value.get('speed') or 0
    eta = value.get('eta')
    if eta is None and total > downloaded and speed > 0:
        eta = (total - downloaded) / speed
    if eta is not None and eta >= 0:
        seconds = int(eta)
        remaining = f'{seconds//3600}小时{seconds%3600//60}分' if seconds >= 3600 else (
            f'{seconds//60}分{seconds%60}秒' if seconds >= 60 else f'{seconds}秒')
    else:
        remaining = '计算中'
    total_label = format_size(total) + ('（估算）' if value.get('total_estimated') and total else '')
    rate = format_size(speed) + '/s' if speed > 0 else '计算中'
    percent = value.get('percent')
    if percent is None and total > 0:
        percent = min(100, max(0, downloaded / total * 100))
    percentage = f'{percent:.1f}%' if percent is not None else '百分比待确认'
    return f"当前{value.get('part','文件')} {percentage}：{format_size(downloaded) if downloaded else '0 B'} / {total_label} · 速度 {rate} · 预计剩余 {remaining}"


def read_settings():
    settings_path = APP_DIR / "settings.json"
    return json.loads(settings_path.read_text(encoding="utf-8")) if settings_path.exists() else {}


def remember_cookie_path(path):
    """Persist only the local path, never the credential contents."""
    settings = read_settings()
    settings['cookie_file'] = str(path) if path else None
    target = APP_DIR / 'settings.json'
    temporary = target.with_suffix('.json.tmp')
    temporary.write_text(json.dumps(settings, ensure_ascii=False, indent=2), encoding='utf-8')
    temporary.replace(target)


def parse_rate_limit(enabled, mb):
    if not enabled:
        return None
    try:
        value = float(mb)
    except (ValueError, TypeError):
        raise ValueError('请输入有效的限速数值，单位为 MB/s。') from None
    if not math.isfinite(value) or not 0.05 <= value <= 1000:
        raise ValueError('限速请填写 0.05～1000 MB/s。')
    return int(value * 1024 * 1024)


def save_rate_limit(enabled, mb):
    parse_rate_limit(enabled, mb)
    config = read_settings()
    config.update({'rate_limit_enabled': bool(enabled), 'rate_limit_mb': str(mb)})
    path = APP_DIR / 'settings.json'
    temporary = path.with_suffix('.json.tmp')
    temporary.write_text(json.dumps(config, ensure_ascii=False, indent=2), encoding='utf-8')
    temporary.replace(path)


def configured_rate_limit():
    config = read_settings()
    return parse_rate_limit(config.get('rate_limit_enabled', True), config.get('rate_limit_mb', '2'))


def load_cookie_file(path, target_url):
    """Read only the chosen site's Netscape cookies into memory; never rewrite the source."""
    import account_login
    if path == account_login.SESSION_REF:
        return account_login.download_jar(target_url)
    from yt_dlp.cookies import YoutubeDLCookieJar
    cookie_path = Path(path)
    if not cookie_path.is_file():
        raise ValueError("找不到所选 Cookie 文件，请重新选择本机的 .txt 文件。")
    if cookie_path.stat().st_size > 5 * 1024 * 1024:
        raise ValueError("Cookie 文件过大，请只导出当前视频站点的 Cookie。")
    try:
        lines = cookie_path.read_text(encoding="utf-8-sig").splitlines()
    except (OSError, UnicodeError):
        raise ValueError("无法读取 Cookie 文件，请重新导出 Netscape 格式的 .txt 文件。") from None
    if not lines or lines[0].strip() not in ("# Netscape HTTP Cookie File", "# HTTP Cookie File"):
        raise ValueError("请选择 Netscape 格式的 Cookie 文件，不是 JSON 或账号密码文本。")
    host = urlsplit(target_url).hostname.lower()
    if host == "b23.tv" or host.endswith(".b23.tv"):
        host = "www.bilibili.com"
    jar = YoutubeDLCookieJar()
    for line in lines[1:]:
        if not line.strip() or (line.startswith("#") and not line.startswith("#HttpOnly_")):
            continue
        http_only = line.startswith("#HttpOnly_")
        if http_only:
            line = line[len("#HttpOnly_"):]
        fields = line.split("\t")
        if len(fields) != 7:
            raise ValueError("Cookie 文件格式不正确，请从扩展重新导出 Netscape 格式。")
        domain, include_subdomains, cookie_path_value, secure, expiry, name, value = fields
        if include_subdomains not in ("TRUE", "FALSE") or secure not in ("TRUE", "FALSE"):
            raise ValueError("Cookie 文件格式不正确，请重新导出。")
        try:
            expires = int(float(expiry)) if expiry else 0
        except (ValueError, OverflowError):
            raise ValueError("Cookie 文件格式不正确，请重新导出。") from None
        cookie_domain = domain.lstrip(".").lower()
        if not cookie_domain or not (host == cookie_domain or host.endswith("." + cookie_domain)):
            continue
        if expires and expires <= time.time():
            continue
        jar.set_cookie(http.cookiejar.Cookie(
            version=0, name=name, value=value, port=None, port_specified=False,
            domain=domain, domain_specified=include_subdomains == "TRUE",
            domain_initial_dot=domain.startswith("."), path=cookie_path_value or "/", path_specified=True,
            secure=secure == "TRUE", expires=expires or None, discard=not expires,
            comment=None, comment_url=None, rest={"HttpOnly": None} if http_only else {}, rfc2109=False,
        ))
    if not list(jar):
        raise ValueError("文件中没有此网站有效的 Cookie。请在已登录的视频页面重新导出当前站点。")
    return jar


def find_media_tools():
    settings = read_settings()
    candidates = [
        APP_DIR / "bin" / "ffmpeg.exe",
        Path(settings.get("ffmpeg_path", "__missing__")),
        Path(os.environ.get("YICHEN_X_SLICER_FFMPEG", "__missing__")),
        Path(shutil.which("ffmpeg") or "__missing__"),
    ]
    for candidate in candidates:
        if candidate.is_file():
            probe = candidate.with_name("ffprobe.exe" if os.name == "nt" else "ffprobe")
            if probe.is_file():
                return candidate, probe
    raise RuntimeError("找不到视频合并工具。请检查“工具/settings.json”中记录的 FFmpeg 位置。")


def probe_media(path, ffprobe):
    result = subprocess.run(
        [str(ffprobe), "-v", "error", "-show_entries",
         "format=duration,size:stream=codec_type,codec_name,width,height,avg_frame_rate,duration,bit_rate",
         "-of", "json", str(path)],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
        timeout=45, creationflags=CREATE_NO_WINDOW,
    )
    if result.returncode:
        raise RuntimeError("下载文件无法通过完整性检查：" + clean_message(result.stderr))
    data = json.loads(result.stdout)
    streams = data.get("streams", [])
    videos = [s for s in streams if s.get("codec_type") == "video"]
    audios = [s for s in streams if s.get("codec_type") == "audio"]
    if not videos or not path.is_file() or path.stat().st_size == 0:
        raise RuntimeError("未检测到有效的视频轨道。")
    video = videos[0]
    numerator, _, denominator = video.get("avg_frame_rate", "0/1").partition("/")
    fps = float(numerator or 0) / (float(denominator or 1) or 1)
    return {
        "path": str(path.resolve()), "bytes": path.stat().st_size,
        "duration": float(data.get("format", {}).get("duration") or 0),
        "width": video.get("width", 0), "height": video.get("height", 0),
        "fps": round(fps, 3), "video_codec": video.get("codec_name"),
        "audio_tracks": len(audios), "audio_codec": audios[0].get("codec_name") if audios else None,
    }


def matches_selection(media, info):
    expected_duration = float(info.get("duration") or 0)
    has_audio = any(f.get("acodec") not in (None, "none") for f in info.get("requested_formats", [info]))
    return (
        (not has_audio or media["audio_tracks"] > 0)
        and media["height"] >= (info.get("height") or 0)
        and media["width"] >= (info.get("width") or 0)
        and media["fps"] + 0.15 >= (info.get("fps") or 0)
        and (not expected_duration or abs(media["duration"] - expected_duration) <= max(3, expected_duration * 0.002))
    )


def verified_merge_selection(media, info, cache_dir, ffprobe):
    """Use the actual selected video stream when a site's declared frame rate is inaccurate."""
    if matches_selection(media, info):
        return info
    for fmt in info.get('requested_formats') or []:
        if fmt.get('vcodec') in (None, 'none'):
            continue
        suffix = '.f' + str(fmt.get('format_id')) + '.' + str(fmt.get('ext'))
        for source in Path(cache_dir).iterdir():
            if not source.is_file() or source.is_symlink() or not source.name.endswith(suffix):
                continue
            if '[' + str(info.get('id')) + ']' not in source.name:
                continue
            original = probe_media(source, ffprobe)
            # Never relax resolution, audio, or duration checks. Only correct reported FPS
            # when the selected raw video and merged output have the same measured FPS.
            if original['fps'] <= 0 or abs(original['fps'] - media['fps']) > 0.15:
                continue
            corrected = info | {'fps': original['fps']}
            raw_check = corrected | {'requested_formats': [{'acodec': 'none'}]}
            if matches_selection(original, raw_check) and matches_selection(media, corrected):
                return corrected
    return info


def unique_destination(path):
    if not path.exists():
        return path
    counter = 2
    while True:
        candidate = path.with_name(f"{path.stem} ({counter}){path.suffix}")
        if not candidate.exists():
            return candidate
        counter += 1


def bangumi_title(downloader, episode_id, fallback):
    from yt_dlp.extractor.bilibili import BiliBiliBangumiIE
    response = BiliBiliBangumiIE(downloader)._download_json(
        'https://api.bilibili.com/pgc/view/web/season', episode_id,
        query={'ep_id':episode_id.removeprefix('ep')}, headers={'Referer':'https://www.bilibili.com/'})
    season = response.get('result') or {}
    if response.get('code') != 0 or not season.get('title'):
        raise ValueError('暂时无法获取完整作品名，请稍后重试。')
    episodes = list(season.get('episodes') or [])
    for section in season.get('section') or []:
        episodes.extend(section.get('episodes') or [])
    entry = next((e for e in episodes if str(e.get('id')) == episode_id.removeprefix('ep')), {})
    label = ' '.join(str(entry.get(k) or '').strip() for k in ('title','long_title')).strip() or fallback
    return str(season['title']) + ' · ' + label


def complete_filename(title, identity, extension, width, height):
    clean_title = re.sub(r'[<>:"/\\|?*\x00-\x1f]', '_', str(title)).strip(' .') or '视频'
    name = f'{clean_title} [{identity}]'
    if width and height:
        name += f' [{int(width)}x{int(height)}]'
    name += extension
    if len(name) > 255:
        raise ValueError('完整标题超出 Windows 文件名长度限制，未静默截断标题。')
    return name


def video_id_from_url(url):
    episode = bangumi_id(url, 'ep')
    if episode:
        return episode
    parsed = urlsplit(normalize_url(url))
    match = re.fullmatch(r'/video/(BV[0-9A-Za-z]{10})/?', parsed.path)
    if parsed.hostname in ('www.bilibili.com','bilibili.com') and match:
        part = dict(parse_qsl(parsed.query)).get('p','1')
        return match.group(1) + (f'_p{part}' if part != '1' else '')
    return None


def same_video_ids(video_id):
    # BV and BV_p1 identify the same first part; other parts stay distinct.
    if re.fullmatch(r'BV[0-9A-Za-z]{10}(?:_p1)?', video_id):
        base = video_id.removesuffix('_p1')
        return {base,base+'_p1'}
    return {video_id}


def duplicate_media(media, info):
    width,height = info.get('width'),info.get('height')
    if width and height and (media['width'],media['height']) != (width,height):
        return False
    duration = float(info.get('duration') or 0)
    has_audio = info.get('has_audio')
    if has_audio is None:
        has_audio = any(f.get('acodec') not in (None,'none') for f in info.get('requested_formats',[info]))
    return (not has_audio or media['audio_tracks'] > 0) and (
        not duration or abs(media['duration']-duration) <= max(3,duration*0.002))


def find_existing_video(output_dir, info, ffprobe):
    video_id = str(info.get('id') or '')
    if not video_id:
        return None
    base = Path(output_dir).resolve()
    # Bilibili identities are stable across title changes and different collection folders.
    if (video_id.startswith('BV') or re.fullmatch(r'ep\d+',video_id)) and base.is_relative_to(DEFAULT_OUTPUT.resolve()):
        base = DEFAULT_OUTPUT.resolve()
    ids = same_video_ids(video_id)
    excluded = {'下载缓存','下载记录','工具','历史版本','__pycache__'}
    for directory, subdirs, files in os.walk(base):
        subdirs[:] = [name for name in subdirs if name not in excluded]
        for name in files:
            path = Path(directory)/name
            if path.suffix.lower() not in ('.mp4','.mkv','.webm','.mov'):
                continue
            if not ids.intersection(re.findall(r'\[([^\]]+)\]',name)):
                continue
            # Raw video-only streams must not be mistaken for a merged final file.
            if re.search(r'\.f[^.]+\.(?:mp4|mkv|webm|mov)$',name):
                continue
            try:
                media = probe_media(path,ffprobe)
                if duplicate_media(media,info):
                    return media
            except (OSError,RuntimeError,ValueError,subprocess.SubprocessError):
                continue
    return None


def resolution_filename(path, width, height):
    path = Path(path)
    return path.with_name(f'{path.stem} [{int(width)}x{int(height)}]{path.suffix}') if width and height else path


def clear_verified_cache(cache_dir, output_dir, destination, info, ffprobe):
    """Remove only this successful format's direct files after verifying its saved output."""
    cache_dir, output_dir, destination = Path(cache_dir), Path(output_dir).resolve(), Path(destination)
    cache_root = output_dir / '下载缓存'
    if (cache_dir.is_symlink() or cache_dir.parent.is_symlink() or cache_root.is_symlink()
            or any(p.is_junction() for p in (cache_dir, cache_dir.parent, cache_root))
            or cache_root.resolve().parent != output_dir
            or cache_dir.resolve().parent.parent != cache_root.resolve()
            or not re.fullmatch(r'[0-9a-f]{16}', cache_dir.parent.name)
            or not re.fullmatch(r'[0-9a-f]{8}', cache_dir.name)
            or destination.resolve().parent != output_dir):
        raise ValueError('缓存路径不符合当前任务范围，未清理。')
    if not matches_selection(probe_media(destination, ffprobe), info):
        raise ValueError('成片未通过核验，保留缓存。')
    identity = str(info.get('id') or '')
    if not identity:
        return 0
    freed = 0
    for file in cache_dir.iterdir():
        if file.is_symlink() or not file.is_file():
            continue
        if file.name != '已完成.txt' and not re.search(r'\[' + re.escape(identity) + r'\](?:\.f[^.]+)?\.(?:mp4|m4a|webm|mkv|part|ytdl)(?:\.part|\.ytdl)?$', file.name):
            continue
        if file.resolve().parent != cache_dir.resolve():
            continue
        size = file.stat().st_size
        file.unlink()
        freed += size
    return freed


def describe_error(exc):
    message = clean_message(exc)
    lower = message.lower()
    if 'no video formats found' in lower:
        return '未获取到可下载的视频格式。请在浏览器确认该版本能否播放；也可能是片源限制或网站解析暂时异常。'
    if "dpapi" in lower or "decrypt" in lower:
        return "Windows 无法读取 Chrome 登录态。可取消勾选登录态，下载公开可用画质。"
    if "cookie database" in lower or "cookies database" in lower:
        return "Chrome 登录数据暂时不可读取。可取消勾选登录态，下载公开可用画质。"
    if "403" in lower or "412" in lower:
        return "网站拒绝了本次请求，请稍后重试或在浏览器中检查该视频是否可播放。\n" + message
    if "unsupported url" in lower:
        return "暂不支持这个链接。请使用视频详情页的完整链接。"
    return message


class DownloadLogger:
    def __init__(self, emit):
        self.emit = emit
        self.preview_only = False

    def debug(self, message):
        # Technical extractor messages may contain media URLs; do not persist them.
        pass

    def warning(self, message):
        if 'only preview format' in str(message).lower():
            self.preview_only = True
        self.emit("log", clean_message(message))

    def error(self, message):
        # The caller reports a sanitized, translated exception once.
        pass


def quality_signature(raw_url, use_chrome=False, cookie_file=None):
    stamp = None
    if cookie_file == '@bili-session':
        import account_login
        stamp = account_login.STORE.signature()
    elif cookie_file:
        path = Path(cookie_file)
        try:
            stat = path.stat()
            stamp = (str(path.resolve()), stat.st_size, stat.st_mtime_ns)
        except OSError:
            stamp = (str(path), None, None)
    return (normalize_url(raw_url), bool(use_chrome), stamp)


def quality_text(quality):
    height = quality.get('height') or 0
    label = '4K' if height == 2160 else (f'{height}P' if height else '未知')
    return f"{quality.get('width') or '?'} × {height or '?'}（{label}） · {quality.get('fps') or '?'} 帧"


def quality_unchanged(info, expected):
    return all(info.get(key) == expected.get(key) for key in ('width', 'height', 'fps', 'format_id'))


def download_video(raw_url, output_dir=DEFAULT_OUTPUT, use_chrome=False, emit=None, stop=None, cookie_file=None,
                   preview_only=False, expected_quality=None, rate_controller=None):
    import yt_dlp
    from yt_dlp.cookies import CookieLoadError
    emit = emit or (lambda kind, value: None)
    stop = stop or threading.Event()
    url = normalize_url(raw_url)
    if bangumi_id(url, 'ss'):
        raise ValueError('这是整季/电影版本清单链接，请在“合集 / 批量下载”中选择具体正片后下载。')
    output_dir = Path(output_dir).resolve()
    ffmpeg, ffprobe = find_media_tools()
    if stop.is_set():
        raise Cancelled('已停止，已下载的片段会保留供重试。')
    if not preview_only and expected_quality is not None:
        if expected_quality.get('signature') != quality_signature(raw_url,use_chrome,cookie_file):
            raise ValueError('链接或登录文件已变化，请重新检查画质后再下载。')
        identity = expected_quality.get('id') or video_id_from_url(url)
        if identity:
            media = find_existing_video(output_dir,expected_quality | {'id':identity},ffprobe)
            if media:
                emit('log','该视频相同分辨率的完整文件已存在，跳过下载。')
                return media | {'title':expected_quality.get('title'),'reused':True,
                                'chrome_used':False,'cookie_file_used':False,'fallback':None}
    job_key = hashlib.sha256(url.encode("utf-8")).hexdigest()[:16]
    cache_dir = output_dir / "下载缓存" / job_key
    records_dir = output_dir / "下载记录"
    last_progress = [0.0]
    completed_paths = []

    def check_cancelled():
        if stop.is_set():
            raise Cancelled("已停止，已下载的片段会保留供重试。")

    def progress(data):
        check_cancelled()
        status = data.get("status")
        now = time.monotonic()
        if status == "downloading" and now - last_progress[0] >= 0.25:
            last_progress[0] = now
            downloaded = data.get("downloaded_bytes") or 0
            total = data.get("total_bytes") or data.get("total_bytes_estimate") or 0
            item = data.get("info_dict") or {}
            emit('stage', '下载音频' if item.get('vcodec') == 'none' else '下载视频')
            emit("progress", {
                "percent": min(100, downloaded / total * 100) if total else None,
                "downloaded": downloaded, "total": total,
                "speed": data.get("speed") or 0,
                "eta": data.get('eta'),
                "total_estimated": not bool(data.get('total_bytes')),
                "part": "音频" if item.get("vcodec") == "none" else "视频",
            })
        elif status == "finished":
            emit("status", "当前文件下载完毕，正在处理音视频…")

    def post_progress(data):
        # Once merging starts, let this short local operation finish before stopping.
        if data.get("status") == "started":
            emit('stage', '合并 / 处理音视频')
            emit("status", "正在合并音视频，请稍候…")

    options = {
        "noplaylist": True, "playlistend": 1, "lazy_playlist": True,
        "cachedir": False, "quiet": True, "noprogress": True,
        "logger": DownloadLogger(emit),
        "socket_timeout": 25, "retries": 5, "fragment_retries": 5, "extractor_retries": 2,
        "concurrent_fragment_downloads": 2,
        "format": "bv*+ba/b", "format_sort": ["res", "fps", "hdr", "vbr", "abr"],
        "merge_output_format": "mp4", "ffmpeg_location": str(ffmpeg.parent),
        "windowsfilenames": True, "continuedl": True,
        "overwrites": False, "post_overwrites": False, "keepvideo": True,
        "paths": {"home": str(cache_dir), "temp": str(cache_dir)},
        "outtmpl": {"default": "%(title).150B [%(id)s].%(ext)s"},
        "progress_hooks": [progress], "postprocessor_hooks": [post_progress],
        "post_hooks": [lambda filename: completed_paths.append(Path(filename))],
    }
    rate_limit = configured_rate_limit()
    if rate_limit:
        options['ratelimit'] = rate_limit
        # Parallel fragment workers each enforce their own limit; use one worker
        # so the combined download stays within the chosen task limit.
        options['concurrent_fragment_downloads'] = 1
        emit('log', f'本次下载限速：{rate_limit / 1048576:g} MB/s。')
    if rate_controller is not None:
        install_live_rate_support()
        options.update({'ratelimit':None,'concurrent_fragment_downloads':1,
                        'local_live_rate_controller':rate_controller,'local_stop_event':stop})
    chrome_used = False
    fallback = None
    downloader = None
    emit("status", "正在检查视频和可用画质…")
    try:
        check_cancelled()
        if use_chrome and not cookie_file:
            options["cookiesfrombrowser"] = ("chrome",)
        downloader = yt_dlp.YoutubeDL(options)
        if bangumi_id(url, 'ep'):
            install_bangumi_guard(downloader)
        if cookie_file:
            downloader.cookiejar = load_cookie_file(cookie_file, url)
            emit("log", "已加载 B 站登录态。")
        try:
            info = downloader.extract_info(url, download=False)
        except (yt_dlp.utils.DownloadError, CookieLoadError) as exc:
            if cookie_file or not use_chrome or stop.is_set():
                raise
            fallback = "Chrome 登录态尝试失败，已改用公开可用画质。"
            emit("log", fallback + " " + describe_error(exc))
            emit("status", "登录态不可用，正在尝试公开画质…")
            downloader.close()
            options.pop("cookiesfrombrowser", None)
            downloader = yt_dlp.YoutubeDL(options)
            if bangumi_id(url, 'ep'):
                install_bangumi_guard(downloader)
            info = downloader.extract_info(url, download=False)
        else:
            chrome_used = use_chrome and not cookie_file
        check_cancelled()
        if not info or info.get("_type", "video") != "video":
            raise ValueError("请提供单个视频详情页链接；这个工具不会自动下载整个合集。")
        if bangumi_id(url, 'ep'):
            if options['logger'].preview_only:
                raise ValueError('当前登录信息只能获取试看，未将试看当作完整正片。请确认账号具有观看权限并更新 Cookie。')
            verify_bangumi_streams(downloader, info, url, stop, emit)
            info['id'] = bangumi_id(url, 'ep')
        title = str(info.get("title") or info.get("id") or "视频")
        if bangumi_id(url, 'ep'):
            title = bangumi_title(downloader,info['id'],title)
        if info.get("vcodec") == "none":
            raise ValueError("该链接没有可下载的视频轨道。")
        emit("metadata", {"title": title, "width": info.get("width"), "height": info.get("height"), "duration": info.get("duration")})
        if preview_only:
            return {key: info.get(key) for key in ('id','title', 'width', 'height', 'fps', 'format_id','duration')} | {
                'title':title,
                'estimated_bytes': sum(f.get('filesize') or f.get('filesize_approx') or 0
                                       for f in info.get('requested_formats', [info])),
                'size_partial': any(not (f.get('filesize') or f.get('filesize_approx'))
                                    for f in info.get('requested_formats', [info])),
                'has_audio':any(f.get('acodec') not in (None,'none') for f in info.get('requested_formats',[info])),
                'chrome_used': chrome_used, 'cookie_file_used': bool(cookie_file), 'fallback': fallback,
                'signature': quality_signature(raw_url, use_chrome, cookie_file)}
        if expected_quality is not None:
            if expected_quality.get('signature') != quality_signature(raw_url, use_chrome, cookie_file):
                raise ValueError('链接或登录文件已变化，请重新检查画质后再下载。')
            if not quality_unchanged(info, expected_quality):
                raise ValueError('当前画质与下载前检查结果不同，已停止，未下载视频。请重新检查画质。')
        output_dir.mkdir(parents=True, exist_ok=True)
        selection_key = hashlib.sha256(str(info.get("format_id", "best")).encode()).hexdigest()[:8]
        cache_dir = cache_dir / selection_key
        downloader.params["paths"] = {"home": str(cache_dir), "temp": str(cache_dir)}
        expected_cache_file = Path(downloader.prepare_filename(info))
        destination = resolution_filename(output_dir / expected_cache_file.name,info.get('width'),info.get('height'))
        media = find_existing_video(output_dir,info,ffprobe)
        if media:
            emit('log','该视频相同分辨率的完整文件已存在，跳过下载。')
            return {**media, 'title':title,'reused':True,'chrome_used':chrome_used,
                    'cookie_file_used':bool(cookie_file),'fallback':fallback}
        destination = unique_destination(destination)
        requested = info.get("requested_formats") or [info]
        estimate = sum(f.get("filesize") or f.get("filesize_approx") or 0 for f in requested)
        if estimate and shutil.disk_usage(output_dir).free < estimate * 2.2:
            raise RuntimeError("保存磁盘空间不足。音视频合并期间需要约两倍视频大小的可用空间。")
        cache_dir.mkdir(parents=True, exist_ok=True)
        # This archive belongs to this single, resumable job only.
        downloader.params["download_archive"] = str(cache_dir / "已完成.txt")
        check_cancelled()
        emit("status", "开始下载当前可用的最高画质…")
        downloader.process_info(info)
        check_cancelled()
        if not completed_paths:
            raise RuntimeError("下载未生成最终文件，请查看提示后重试。")
        completed = completed_paths[-1].resolve()
        if completed.parent != cache_dir.resolve() or not completed.is_file():
            raise RuntimeError("下载产物位置异常，已停止保存。")
        emit('stage', '校验成品')
        emit("status", "正在核验画质、时长和音视频轨道…")
        media = probe_media(completed, ffprobe)
        verified_info = verified_merge_selection(media, info, cache_dir, ffprobe)
        if not matches_selection(media, verified_info):
            raise RuntimeError("文件未通过时长或音视频完整性检查，已保留缓存，请重试。")
        if verified_info.get('fps') != info.get('fps'):
            emit('log', f"接口标注 {info.get('fps')} 帧，源视频与合并成片实测均为 {media['fps']} 帧，已按实际源文件核验。")
        destination = unique_destination(output_dir / complete_filename(title, info['id'], completed.suffix,media['width'],media['height']))
        # Windows rename is atomic on this volume and refuses to overwrite an existing file.
        if os.name != "nt":
            raise RuntimeError("此便捷工具为当前 Windows 电脑制作。")
        try:
            completed.rename(destination)
        except FileExistsError:
            destination = unique_destination(destination)
            completed.rename(destination)
        media.update({"path": str(destination), "title": title, "reused": False,
                      "chrome_used": chrome_used, "cookie_file_used": bool(cookie_file), "fallback": fallback})
        receipt = records_dir / f"{datetime.now():%Y%m%d-%H%M%S-%f}-{job_key}.json"
        try:
            records_dir.mkdir(parents=True, exist_ok=True)
            with receipt.open("x", encoding="utf-8") as handle:
                json.dump({**media, "source_ref": info.get("id"), "selected_format": info.get("format_id"),
                           "completed_at": datetime.now().astimezone().isoformat(), "url_sha256": job_key},
                          handle, ensure_ascii=False, indent=2)
        except OSError:
            emit("log", "视频已保存，但下载记录无法写入。")
        try:
            freed = clear_verified_cache(cache_dir, output_dir, destination, verified_info, ffprobe)
            emit('log', f'成片已核验，对应缓存已清理，释放 {freed / 1048576:.1f} MB。')
        except (OSError, ValueError, RuntimeError, subprocess.SubprocessError):
            emit('log', '视频已保存；缓存清理未完成，可稍后清理已完成任务的缓存。')
        return media
    except Exception:
        if stop.is_set():
            raise Cancelled("已停止，已下载的片段会保留供重试。") from None
        raise
    finally:
        if downloader is not None:
            downloader.close()


def apply_anime_background(root):
    """Keep the illustration behind opaque, readable controls and resize with the window."""
    image_path = APP_DIR / 'assets' / 'anime-background.png'
    if not image_path.is_file():
        return None
    try:
        import tkinter as tk
        from PIL import Image, ImageTk, ImageOps
        with Image.open(image_path) as image:
            original = image.convert('RGB')
    except (ImportError, OSError):
        return None
    background = tk.Label(root, borderwidth=0)
    background.place(x=0, y=0, relwidth=1, relheight=1)
    background.lower()
    pending = [None]
    last_size = [None]
    background.strength = max(0, min(1, float(read_settings().get('background_opacity', 0.2))))
    def paint():
        pending[0] = None
        if not root.winfo_exists():
            return
        size = (max(root.winfo_width(), 2), max(root.winfo_height(), 2))
        if size == last_size[0]:
            return
        last_size[0] = size
        fitted = ImageOps.fit(original, size, method=Image.Resampling.LANCZOS)
        faded = Image.blend(Image.new('RGB',size,'#f8f5ff'), fitted, background.strength)
        background.photo = ImageTk.PhotoImage(faded, master=root)
        background.configure(image=background.photo)
    def set_strength(value):
        background.strength = max(0,min(1,float(value)))
        last_size[0] = None
        if pending[0] is not None:
            root.after_cancel(pending[0])
        pending[0] = root.after(100,paint)
    background.set_strength = set_strength
    def changed(event):
        if event.widget != root:
            return
        if pending[0] is not None:
            root.after_cancel(pending[0])
        pending[0] = root.after(100, paint)
    root.bind('<Configure>', changed, add='+')
    root.after_idle(paint)
    return background


class DownloaderApp:
    def __init__(self, root, initial_url=""):
        import tkinter as tk
        from tkinter import ttk, messagebox
        self.root, self.tk, self.ttk, self.messagebox = root, tk, ttk, messagebox
        self.events = queue.Queue()
        self.stop_event = threading.Event()
        self.running = False
        self.closing = False
        self.final_path = None
        self.cookie_file = read_settings().get('cookie_file') or None
        self.quality = None
        self.output_dir = Path(read_settings().get("output_dir", str(DEFAULT_OUTPUT)))
        root.title("视频下载工具 · 画质补查与去重版")
        root.geometry(f"980x{min(960, root.winfo_screenheight()-100)}")
        root.minsize(900, 800)
        root.configure(background="#f8f5ff")
        style = ttk.Style(root)
        style.theme_use("clam")
        self.background = apply_anime_background(root)
        style.configure("TFrame", background="#f8f5ff")
        style.configure("TLabel", background="#f8f5ff", font=("Microsoft YaHei UI", 10))
        style.configure("Title.TLabel", font=("Microsoft YaHei UI", 22, "bold"), foreground="#152640")
        style.configure("Muted.TLabel", foreground="#53647b")
        style.configure("TCheckbutton", background="#f8f5ff", font=("Microsoft YaHei UI", 10))
        style.configure("TButton", font=("Microsoft YaHei UI", 10), padding=(14, 9))
        style.configure("Primary.TButton", background="#7455bb", foreground="white")
        style.map("Primary.TButton", background=[("active", "#5f409f"), ("disabled", "#9babc8")])
        style.configure("TProgressbar", background="#7455bb", troughcolor="#dfe6f0", thickness=10)
        body = ttk.Frame(root, padding=26)
        body.pack(fill="both", expand=True, padx=24, pady=(100,22) if self.background else 18)
        ttk.Label(body, text="粘贴链接，下载视频", style="Title.TLabel").pack(anchor="w")
        ttk.Label(body, text="自动选择可用最高画质，并合并音视频", style="Muted.TLabel").pack(anchor="w", pady=(4, 18))
        ttk.Label(body, text="视频链接").pack(anchor="w", pady=(0, 6))
        entry_row = ttk.Frame(body)
        entry_row.pack(fill="x")
        self.url = tk.StringVar(value=initial_url)
        self.entry = ttk.Entry(entry_row, textvariable=self.url, font=("Microsoft YaHei UI", 11))
        self.entry.pack(side="left", fill="x", expand=True, ipady=8)
        self.paste_button = ttk.Button(entry_row, text="粘贴", command=self.paste)
        self.paste_button.pack(side="right", padx=(8, 0))
        self.use_chrome = tk.BooleanVar(value=False)
        config = read_settings()
        self.rate_enabled = tk.BooleanVar(value=config.get('rate_limit_enabled', True))
        self.rate_mb = tk.StringVar(value=str(config.get('rate_limit_mb', '2')))
        rate_row = ttk.Frame(body)
        rate_row.pack(fill='x', pady=(10,0))
        self.rate_check = ttk.Checkbutton(rate_row, text='下载限速', variable=self.rate_enabled)
        self.rate_check.pack(side='left')
        self.rate_entry = ttk.Combobox(rate_row, textvariable=self.rate_mb, values=('0.5','1','2','5','10'), width=7)
        self.rate_entry.pack(side='left', padx=6)
        ttk.Label(rate_row, text='MB/s').pack(side='left')
        self.rate_apply = ttk.Button(rate_row, text='应用并记住', command=self.apply_rate)
        self.rate_apply.pack(side='left', padx=8)
        ttk.Label(rate_row, text='单个与合集下载共用；每次开始下载时生效。', style='Muted.TLabel').pack(side='left')
        self.chrome_checkbox = ttk.Checkbutton(body, text="尝试使用 Chrome 登录态（需要高清或会员权限时勾选）", variable=self.use_chrome)
        self.chrome_checkbox.pack(anchor="w", pady=(12, 3))
        ttk.Label(body, text="登录态读取失败时自动尝试公开画质；不会显示或保存 Cookie。", style="Muted.TLabel").pack(anchor="w")
        cookie_row = ttk.Frame(body)
        cookie_row.pack(fill="x", pady=(10, 0))
        self.cookie_button = ttk.Button(cookie_row, text="选择 Cookie 文件", command=self.choose_cookie)
        self.cookie_button.pack(side="left")
        self.clear_cookie_button = ttk.Button(cookie_row, text="取消选择", command=self.clear_cookie)
        self.clear_cookie_button.pack(side="left", padx=8)
        ttk.Button(cookie_row, text="登录帮助", command=self.login_help).pack(side="right")
        self.cookie_label = tk.StringVar(value="未选择文件；可选择已导出的本机 Netscape .txt 文件。")
        if self.cookie_file:
            prefix = '已自动选择：' if Path(self.cookie_file).is_file() else '记住的文件不存在，请重新选择：'
            self.cookie_label.set(prefix + Path(self.cookie_file).name + '（检查画质时验证登录态）')
        ttk.Label(body, textvariable=self.cookie_label, style="Muted.TLabel", wraplength=740).pack(anchor="w", pady=(5, 0))
        ttk.Label(body, text=f"保存到  {self.output_dir}", style="Muted.TLabel").pack(anchor="w", pady=(14, 12))
        actions = ttk.Frame(body)
        actions.pack(fill="x")
        self.quality_button = ttk.Button(actions, text="检查画质", command=self.check_quality)
        self.quality_button.pack(side="left", padx=(0,8))
        self.download_button = ttk.Button(actions, text="确认画质并下载", style="Primary.TButton", command=self.start, state='disabled')
        self.download_button.pack(side="left")
        self.batch_button = ttk.Button(actions, text="合集 / 批量下载", command=self.open_batch)
        self.batch_button.pack(side="left", padx=8)
        self.cancel_button = ttk.Button(actions, text="停止", command=self.cancel, state="disabled")
        self.cancel_button.pack(side="left", padx=8)
        ttk.Button(actions, text="打开保存目录", command=self.open_folder).pack(side="right")
        self.quality_label = tk.StringVar(value='待检查：先查看实际分辨率和帧率，再决定是否下载。')
        ttk.Label(body, textvariable=self.quality_label, wraplength=850,
                  font=('Microsoft YaHei UI',12,'bold'), foreground='#62439f').pack(anchor='w',pady=(14,0))
        self.status = tk.StringVar(value="准备好了，粘贴一个视频链接即可。")
        ttk.Label(body, textvariable=self.status, wraplength=690).pack(anchor="w", pady=(18, 8))
        self.bar = ttk.Progressbar(body, mode="determinate", maximum=100)
        self.bar.pack(fill="x")
        self.progress_text = tk.StringVar(value="")
        ttk.Label(body, textvariable=self.progress_text, style="Muted.TLabel").pack(anchor="w", pady=(6, 8))
        self.log = tk.Text(body, height=7, font=("Microsoft YaHei UI", 10), wrap="word", relief="flat",
                           background="white", foreground="#23334a", padx=12, pady=10, state="disabled")
        self.log.pack(fill="both", expand=True)
        self.add_log("支持 B 站以及下载组件支持的其他单条视频链接。")
        self.add_log("画质取决于视频源和账号权限；相同的完整文件会自动跳过。")
        root.bind("<Return>", lambda event: self.start())
        root.protocol("WM_DELETE_WINDOW", self.close)
        root.after(100, self.poll)
        self.entry.focus_set()
        self.url.trace_add('write', self.invalidate_quality)
        self.use_chrome.trace_add('write', self.invalidate_quality)

    def apply_rate(self):
        try:
            save_rate_limit(self.rate_enabled.get(), self.rate_mb.get())
            self.status.set(f'已保存限速：{self.rate_mb.get()} MB/s。' if self.rate_enabled.get() else '已关闭下载限速。')
            return True
        except (ValueError, OSError) as exc:
            self.status.set(str(exc) if isinstance(exc,ValueError) else '限速设置保存失败，请检查工具目录是否可写。')
            return False

    def invalidate_quality(self, *args):
        self.quality = None
        self.quality_label.set('待检查：链接或登录设置改变后需要重新检查画质。')
        self.download_button.configure(state='disabled')

    def check_quality(self):
        if self.running:
            return
        try:
            url = normalize_url(self.url.get())
        except ValueError as exc:
            self.status.set(str(exc))
            return
        if bangumi_id(url, 'ss'):
            self.status.set('已识别整季/电影链接，请在清单中选择要下载的版本或剧集。')
            self.open_batch()
            return
        self.invalidate_quality()
        self.set_running(True)
        self.stop_event.clear()
        chrome, cookie = self.use_chrome.get(), self.cookie_file
        self.status.set('正在检查可用画质，仅获取信息，不下载视频…')
        def worker():
            try:
                quality = download_video(url,self.output_dir,chrome,
                    lambda kind,value:self.events.put((kind,value)),self.stop_event,cookie,preview_only=True)
                self.events.put(('quality',quality))
            except Exception as exc:
                self.events.put(('failed',describe_error(exc)))
        threading.Thread(target=worker,daemon=True).start()

    def add_log(self, value):
        self.log.configure(state="normal")
        self.log.insert("end", clean_message(value) + "\n")
        self.log.see("end")
        self.log.configure(state="disabled")

    def open_batch(self):
        if self.running:
            return
        if not self.apply_rate():
            return
        from batch_download import BatchWindow
        panel = BatchWindow(self)
        try:
            if bangumi_id(self.url.get(), 'ss'):
                panel.input.delete('1.0','end')
                panel.input.insert('1.0', normalize_url(self.url.get()))
                panel.parse()
        except ValueError:
            pass

    def paste(self):
        try:
            self.url.set(self.root.clipboard_get().strip())
        except self.tk.TclError:
            self.status.set("剪贴板里没有可粘贴的文字。")

    def open_folder(self):
        self.output_dir.mkdir(parents=True, exist_ok=True)
        os.startfile(str(self.output_dir))

    def choose_cookie(self):
        from tkinter import filedialog
        path = filedialog.askopenfilename(parent=self.root, title="选择当前网站的 Cookie 文件（仅在本机读取）",
                                          filetypes=[("Netscape Cookie 文件", "*.txt"), ("所有文件", "*.*")])
        if path:
            try:
                remember_cookie_path(path)
            except OSError:
                self.messagebox.showerror('无法保存设置', '无法记住文件路径，请检查工具目录是否可写。')
                return
            self.cookie_file = path
            self.invalidate_quality()
            self.use_chrome.set(False)
            self.cookie_label.set("已记住，下次自动选择：" + Path(path).name + "（优先于 Chrome 自动读取）")

    def clear_cookie(self):
        try:
            remember_cookie_path(None)
        except OSError:
            self.messagebox.showerror('无法保存设置', '无法清除记住的路径，请检查工具目录是否可写。')
            return
        self.cookie_file = None
        self.invalidate_quality()
        self.cookie_label.set("未选择文件；可选择已导出的本机 Netscape .txt 文件。")

    def login_help(self):
        help_path = APP_DIR.parent / "Chrome 登录使用说明.txt"
        if help_path.is_file():
            os.startfile(str(help_path))
        else:
            self.messagebox.showinfo("登录帮助", "在已登录的浏览器中导出当前站点的 Netscape Cookie 文件，再点击“选择 Cookie 文件”。\n无需提供账号密码。")

    def set_running(self, value):
        self.running = value
        for control in (self.entry, self.paste_button, self.download_button, self.chrome_checkbox,
                        self.cookie_button, self.clear_cookie_button, self.batch_button, self.quality_button,
                        self.rate_check, self.rate_entry, self.rate_apply):
            control.configure(state="disabled" if value else "normal")
        self.cancel_button.configure(state="normal" if value else "disabled")
        self.download_button.configure(state='normal' if not value and self.quality else 'disabled')

    def start(self):
        if self.running:
            return
        try:
            url = normalize_url(self.url.get())
        except ValueError as exc:
            self.status.set(str(exc))
            return
        if not self.quality or self.quality.get('signature') != quality_signature(url,self.use_chrome.get(),self.cookie_file):
            self.invalidate_quality()
            self.status.set('请先点击“检查画质”，确认分辨率后再下载。')
            return
        if not self.apply_rate():
            return
        expected_quality = self.quality.copy()
        self.set_running(True)
        self.stop_event.clear()
        self.final_path = None
        self.bar["value"] = 0
        self.progress_text.set("")
        self.status.set("正在准备…")
        use_chrome = self.use_chrome.get()
        cookie_file = self.cookie_file

        def worker():
            try:
                result = download_video(url, self.output_dir, use_chrome,
                                        lambda kind, value: self.events.put((kind, value)), self.stop_event, cookie_file,
                                        expected_quality=expected_quality)
                self.events.put(("done", result))
            except Cancelled as exc:
                self.events.put(("cancelled", str(exc)))
            except Exception as exc:
                self.events.put(("failed", describe_error(exc)))
        threading.Thread(target=worker, daemon=True).start()

    def cancel(self):
        self.stop_event.set()
        self.cancel_button.configure(state="disabled")
        self.status.set("正在停止，将在当前网络请求或合并操作结束后完成…")

    def close(self):
        if self.running:
            if self.messagebox.askyesno("停止下载", "下载尚未完成。要停止并关闭窗口吗？\n已下载的片段会保留供重试。"):
                self.closing = True
                self.cancel()
        else:
            self.root.destroy()

    def poll(self):
        try:
            while True:
                kind, value = self.events.get_nowait()
                if kind == "status":
                    if not self.stop_event.is_set():
                        self.status.set(value)
                        if any(word in value for word in ('合并', '核验')):
                            self.progress_text.set('正在处理本地文件；剩余时间暂不可估算。')
                elif kind == 'quality':
                    self.quality = value
                    auth = 'Cookie 文件' if value['cookie_file_used'] else ('Chrome 登录态' if value['chrome_used'] else '公开画质（未使用登录态）')
                    self.quality_label.set(quality_text(value) + '\n本次来源：' + auth + ('；Chrome 读取失败，已回退公开画质' if value['fallback'] else ''))
                    self.set_running(False)
                    self.status.set('检查完成，尚未下载。画质符合要求再点击“确认画质并下载”。')
                    if self.closing:
                        self.root.destroy()
                        return
                elif kind == "log":
                    self.add_log(value)
                elif kind == "metadata":
                    self.add_log(value["title"])
                    if value.get("width") and value.get("height"):
                        self.add_log(f"当前可用画质：{value['width']} × {value['height']}")
                elif kind == "progress":
                    pct = value["percent"]
                    self.bar["value"] = pct or 0
                    label = f"{pct:.1f}%" if pct is not None else "大小待确认"
                    self.progress_text.set(progress_description(value))
                elif kind in ("done", "failed", "cancelled"):
                    self.set_running(False)
                    if kind == "done":
                        self.final_path = value["path"]
                        self.bar["value"] = 100
                        self.status.set("已找到完整文件，无需重复下载。" if value["reused"] else "下载完成，音视频核验通过。")
                        self.progress_text.set(f"{value['width']} × {value['height']} · {value['bytes']/1048576:.1f} MB")
                        self.add_log("保存位置：" + value["path"])
                    else:
                        self.status.set("下载未完成，请查看下方提示。" if kind == "failed" else "已停止。")
                        self.add_log(value)
                    if self.closing:
                        self.root.destroy()
                        return
        except queue.Empty:
            pass
        self.root.after(100, self.poll)


def main():
    parser = argparse.ArgumentParser(description="粘贴视频链接，下载到 F:\\BiliDownload")
    parser.add_argument("--url", help="命令行下载单个视频；不填则打开窗口")
    parser.add_argument("--chrome", action="store_true", help="明确授权本次尝试 Chrome 登录态")
    parser.add_argument("--cookie-file", type=Path, help="本次只读使用当前站点的本机 Netscape Cookie 文件")
    parser.add_argument("--prefill", help="打开窗口时预填视频链接，不自动下载")
    parser.add_argument("--batch", action="store_true", help="打开合集下载窗口，不自动下载")
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--gui-smoke-test", action="store_true")
    parser.add_argument('--login', action='store_true', help='打开主窗口的账号页，不自动请求登录')
    args = parser.parse_args()
    if args.url:
        last_progress = [0.0]

        def print_event(kind, value):
            if kind == "progress":
                now = time.monotonic()
                if now - last_progress[0] < 5:
                    return
                last_progress[0] = now
            print(json.dumps({"event": kind, "value": value}, ensure_ascii=False), flush=True)
        try:
            result = download_video(args.url, args.output, args.chrome, print_event, cookie_file=args.cookie_file)
            print(json.dumps({"event": "done", "value": result}, ensure_ascii=False), flush=True)
            return 0
        except Exception as exc:
            print(json.dumps({"event": "failed", "value": describe_error(exc)}, ensure_ascii=False), flush=True)
            return 1
    import tkinter as tk
    from tkinter import messagebox
    root = tk.Tk()
    if args.gui_smoke_test:
        root.withdraw()
    try:
        from unified_app import launch
        app = launch(root, initial_url=args.prefill or "")
        if args.login:
            app.show_account()
    except Exception as exc:
        root.withdraw()
        messagebox.showerror("无法启动视频下载工具", describe_error(exc))
        root.destroy()
        return 1
    if args.gui_smoke_test:
        app.input.delete('1.0','end')
        app.input.insert('1.0',"not a video URL")
        app.start()
        assert not app.running and "链接" in app.status.get()
        assert not any(isinstance(widget,tk.Toplevel) for widget in root.winfo_children())
        app.set_running(True)
        assert app.download_button.instate(["disabled"])
        app.set_running(False)
        root.update_idletasks()
        root.destroy()
        print("GUI_SMOKE_TEST_OK")
        return 0
    root.mainloop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
