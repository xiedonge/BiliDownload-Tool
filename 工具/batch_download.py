"""Bounded playlist preview and resumable sequential downloads. No search or discovery."""
from __future__ import annotations

from datetime import datetime
import hashlib
import itertools
import json
from pathlib import Path
import queue
import re
import threading
from urllib.parse import urlsplit

import video_downloader as core


def collection_folder(root, title, reference):
    name = re.sub(r'[<>:"/\\|?*\x00-\x1f]', '_', title).strip(' .')[:65] or '批量下载'
    if name.upper() in {'CON', 'PRN', 'AUX', 'NUL', *(f'COM{i}' for i in range(10)), *(f'LPT{i}' for i in range(10))}:
        name = '_' + name
    key = hashlib.sha256(reference.encode()).hexdigest()[:8]
    return Path(root) / '合集' / f'{name} [{key}]'


def bangumi_collection(data, limit):
    """Only the explicitly requested season's main episodes; extras stay separate."""
    if data.get('code') != 0 or not isinstance(data.get('result'),dict):
        raise ValueError('无法读取这部番剧/电影的剧集清单，请确认链接及观看权限。')
    season = data['result']
    rows = season.get('episodes') or []
    entries, seen = [], set()
    for item in rows:
        identity = str(item.get('id') or '')
        if not identity.isdigit():
            raise ValueError('剧集清单缺少有效编号，未将其当作完整清单。')
        if identity in seen:
            continue
        seen.add(identity)
        label = ' '.join(str(item.get(key) or '').strip() for key in ('title','long_title')).strip()
        entries.append({'id':'ep'+identity,'url':'https://www.bilibili.com/bangumi/play/ep'+identity,
                        'title':str(season.get('title') or '番剧')+' · '+(label or 'EP'+identity)})
    if not entries or not str(season.get('season_id') or '').isdigit():
        raise ValueError('这部番剧/电影暂时没有可列出的正片剧集。')
    return {'title':season.get('title') or '番剧/电影','reference':'https://www.bilibili.com/bangumi/play/ss'+str(season['season_id']),
            'expected_count':len(entries),'truncated':len(entries)>limit,'entries':entries[:limit],
            'discovery_performed':False,'main_episodes_only':True}


def enumerate_collection(raw, limit=500, use_chrome=False, cookie_file=None, stop=None, emit=None):
    """Resolve a known BV's own collection, then enumerate using yt-dlp's collection backend."""
    import yt_dlp
    from yt_dlp.cookies import CookieLoadError
    from yt_dlp.extractor.bilibili import BiliBiliIE
    stop = stop or threading.Event()
    emit = emit or (lambda kind, value: None)
    limit = int(limit)
    if not 1 <= limit <= 2000:
        raise ValueError('读取上限应在 1 到 2000 之间。')
    urls = list(dict.fromkeys(core.normalize_url(line) for line in raw.splitlines() if line.strip()))
    if not urls:
        raise ValueError('请粘贴合集链接、其中一条视频链接，或每行一个视频链接。')
    if stop.is_set():
        raise core.Cancelled('已停止解析。')
    if len(urls) > 1:
        if len(urls) > limit:
            raise ValueError(f'输入了 {len(urls)} 条链接，超过读取上限 {limit}，请调整上限。')
        # Exact user-supplied list: do not expand every video's collection.
        return {'title': '链接清单', 'reference': '\n'.join(urls), 'expected_count': len(urls),
                'truncated': False, 'entries': [{'url': url, 'title': f'视频 {i}', 'id': str(i)}
                                             for i, url in enumerate(urls, 1)]}
    url = urls[0]
    host = urlsplit(url).hostname or ''
    path = urlsplit(url).path
    if host not in ('www.bilibili.com', 'bilibili.com', 'space.bilibili.com'):
        raise ValueError('合集解析目前支持 B 站的完整视频或合集链接；其他网站请每行粘贴一个明确的视频链接。')
    if host == 'space.bilibili.com' and not re.fullmatch(r'/\d+/(?:lists/\d+|channel/(?:collectiondetail|seriesdetail))/?', path):
        raise ValueError('请使用明确的合集链接，不支持从整个 UP 主主页批量展开。')
    if host != 'space.bilibili.com' and not re.fullmatch(r'/(?:video/(?:BV[0-9A-Za-z]{10}|av\d+)|bangumi/play/(?:ss|ep)\d+)/?', path):
        raise ValueError('请使用 B 站视频详情页或合集的完整链接。')
    options = {'quiet': True, 'cachedir': False, 'skip_download': True, 'extract_flat': 'in_playlist',
               'noplaylist': False, 'playlistend': limit + 1, 'lazy_playlist': True,
               'socket_timeout': 20, 'extractor_retries': 1, 'logger': core.DownloadLogger(emit)}
    if use_chrome and not cookie_file:
        options['cookiesfrombrowser'] = ('chrome',)

    def resolve(ydl):
        if cookie_file:
            ydl.cookiejar = core.load_cookie_file(cookie_file, url)
        bangumi = core.bangumi_id(url)
        if bangumi:
            query = {'season_id' if bangumi.startswith('ss') else 'ep_id':bangumi[2:]}
            data = BiliBiliIE(ydl)._download_json('https://api.bilibili.com/pgc/view/web/season',bangumi,
                query=query,headers={'Referer':'https://www.bilibili.com/'})
            if stop.is_set():
                raise core.Cancelled('已停止解析。')
            return bangumi_collection(data,limit)
        reference = url
        titles = {}
        expected_count = None
        known_title = None
        parsed = urlsplit(url)
        video_match = re.fullmatch(r'/video/(BV[0-9A-Za-z]{10}|av\d+)/?', parsed.path)
        if parsed.hostname in ('www.bilibili.com', 'bilibili.com') and video_match:
            video_id = video_match.group(1)
            query = {'bvid': video_id} if video_id.startswith('BV') else {'aid': video_id[2:]}
            # Read metadata for exactly the supplied video, never an uploader's feed.
            response = BiliBiliIE(ydl)._download_json(
                'https://api.bilibili.com/x/web-interface/view', video_id, query=query,
                headers={'Referer': 'https://www.bilibili.com/'})
            if response.get('code') != 0:
                raise ValueError('该视频信息暂不可读取，请在浏览器确认链接可播放后重试。')
            data = response.get('data') or {}
            season = data.get('ugc_season') or {}
            mid = season.get('mid') or (data.get('owner') or {}).get('mid')
            if season.get('id') and mid:
                reference = f"https://space.bilibili.com/{mid}/lists/{season['id']}?type=season"
                known_title = season.get('title')
                expected_count = season.get('ep_count')
                for section in season.get('sections', []):
                    for item in section.get('episodes', []):
                        bvid = item.get('bvid') or (item.get('arc') or {}).get('bvid')
                        title = item.get('title') or (item.get('arc') or {}).get('title')
                        if bvid and title:
                            titles[bvid] = title
                emit('status', f'已识别合集：{known_title}，正在读取清单…')
            elif len(data.get('pages') or []) > 1:
                reference = f'https://www.bilibili.com/video/{data.get("bvid", video_id)}'
                expected_count = len(data['pages'])
                known_title = data.get('title')
            else:
                return {'title': data.get('title') or video_id, 'reference': url,
                        'expected_count': 1, 'truncated': False,
                        'entries': [{'url': url, 'title': data.get('title') or video_id, 'id': video_id}]}
        if stop.is_set():
            raise core.Cancelled('已停止解析。')
        info = ydl.extract_info(reference, download=False)
        if not info:
            raise ValueError('未能取得视频清单。')
        raw_entries = info.get('entries') if info.get('_type') in ('playlist', 'multi_video') else [info]
        entries, seen = [], set()
        for item in itertools.islice(raw_entries or [], limit + 1):
            if stop.is_set():
                raise core.Cancelled('已停止解析。')
            if not item or item.get('_type') in ('playlist', 'multi_video'):
                raise ValueError('清单包含不可读取或嵌套的条目，未将其当作完整合集。')
            item_url = item.get('webpage_url') or item.get('url')
            item_id = str(item.get('id') or '')
            if item_url and not item_url.startswith(('http://', 'https://')) and item.get('ie_key') == 'Youtube':
                item_url = 'https://www.youtube.com/watch?v=' + item_url
            if not item_url:
                raise ValueError('清单中的条目缺少视频链接。')
            item_url = core.normalize_url(item_url)
            if item_url in seen:
                continue
            seen.add(item_url)
            entries.append({'url': item_url, 'id': item_id,
                            'title': titles.get(item_id) or item.get('title') or item_id or f'视频 {len(entries)+1}'})
        truncated = len(entries) > limit or bool(expected_count and expected_count > limit)
        entries = entries[:limit]
        if not entries:
            raise ValueError('合集没有可列出的视频。')
        if expected_count and not truncated and (len(entries) != expected_count or
                (len(titles) == expected_count and {item['id'] for item in entries} != set(titles))):
            # Some Bilibili page responses repeat or omit a page. The exact source video's
            # own season object may already contain the entire collection, with titles.
            if len(titles) == expected_count:
                entries = [{'id':bvid,'title':title,'url':'https://www.bilibili.com/video/'+bvid}
                           for bvid,title in titles.items()]
                emit('log','分页结果不完整，已用同一视频附带的完整合集清单核对补齐。')
            else:
                raise ValueError(f'合集标示 {expected_count} 条，但只取得 {len(entries)} 条；清单不完整，请稍后重试。')
        return {'title': known_title or info.get('title') or '视频合集', 'reference': reference,
                'expected_count': expected_count or info.get('playlist_count'),
                'truncated': truncated, 'entries': entries}

    try:
        with yt_dlp.YoutubeDL(options) as ydl:
            result = resolve(ydl)
    except (yt_dlp.utils.DownloadError, CookieLoadError):
        if not use_chrome or cookie_file or stop.is_set():
            raise
        options.pop('cookiesfrombrowser', None)
        emit('log', 'Chrome 登录态读取失败，改用公开信息解析合集。')
        with yt_dlp.YoutubeDL(options) as ydl:
            result = resolve(ydl)
    result['discovery_performed'] = False
    return result


def save_preview(collection, output_root):
    folder = collection_folder(output_root, collection['title'], collection['reference'])
    records = folder / '下载记录'
    records.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime('%Y%m%d-%H%M%S-%f')
    # Links are needed for an exact resumable list; no auth data is included.
    manifest = records / f'合集清单-{stamp}.json'
    with manifest.open('x', encoding='utf-8') as handle:
        json.dump(collection, handle, ensure_ascii=False, indent=2)
    with (records / f'链接清单-{stamp}.txt').open('x', encoding='utf-8') as handle:
        handle.write('\n'.join(item['url'] for item in collection['entries']) + '\n')
    return folder


def run_batch(entries, folder, use_chrome=False, cookie_file=None, stop=None, emit=None, download=None):
    stop = stop or threading.Event()
    emit = emit or (lambda kind, value: None)
    download = download or core.download_video
    result = {'success': 0, 'skipped': 0, 'failed': 0, 'pending': len(entries), 'items': []}
    records = Path(folder) / '下载记录'
    records.mkdir(parents=True, exist_ok=True)
    report = records / f'批量结果-{datetime.now():%Y%m%d-%H%M%S-%f}.jsonl'
    chrome_for_remaining = use_chrome
    for position, entry in enumerate(entries, 1):
        if stop.is_set():
            break
        emit('item', {'key': entry['key'], 'state': '正在下载', 'position': position, 'total': len(entries)})
        try:
            kwargs = {'expected_quality':entry['quality']} if entry.get('quality') else {}
            media = download(entry['url'], folder, chrome_for_remaining, emit, stop, cookie_file, **kwargs)
            state = '已存在' if media.get('reused') else '已完成'
            result['skipped' if media.get('reused') else 'success'] += 1
            if media.get('fallback') and not entry.get('quality'):
                chrome_for_remaining = False
            item = {'key': entry['key'], 'status': state, 'path': media['path'],
                    'width': media.get('width'), 'height': media.get('height'), 'fps': media.get('fps')}
        except core.Cancelled:
            emit('item', {'key': entry['key'], 'state': '待续传'})
            break
        except Exception as exc:
            result['failed'] += 1
            item = {'key': entry['key'], 'status': '失败', 'error': core.describe_error(exc),
                    'retryable': getattr(exc, 'retryable', True), 'error_code': getattr(exc, 'code', None)}
        result['pending'] -= 1
        result['items'].append(item)
        # A flushed per-item receipt survives stopping or a later failed entry.
        with report.open('a', encoding='utf-8') as handle:
            handle.write(json.dumps(item, ensure_ascii=False) + '\n')
        emit('item', {**item, 'state': item['status']})
        emit('batch_progress', {k: v for k, v in result.items() if k != 'items'})
        if stop.wait(0.6):
            break
    result['report'] = str(report)
    return result


def run_until_complete(entries, folder, use_chrome=False, cookie_file=None, stop=None, emit=None, download=None, retry_now=None):
    """Finish each round, then retry only failures; user cancellation always wins."""
    stop = stop or threading.Event()
    emit = emit or (lambda kind, value: None)
    remaining = list(entries)
    latest = {}
    reports = []
    round_number = 0
    while remaining and not stop.is_set():
        round_number += 1
        emit('status', f'第 {round_number} 轮：处理 {len(remaining)} 条，已完成的视频不再重试。')
        if retry_now is not None:
            retry_now.clear()
        current = run_batch(remaining, folder, use_chrome, cookie_file, stop, emit, download)
        reports.append(current['report'])
        latest.update({item['key']: item for item in current['items']})
        if stop.is_set() or current['pending']:
            break
        blocked = [item for item in current['items'] if item['status'] == '失败' and not item.get('retryable', True)]
        if blocked:
            emit('log', f'{len(blocked)} 条片源受保护，已退出自动重试；其他条目继续处理。')
        remaining = [entry for entry in remaining if latest[entry['key']]['status'] == '失败'
                     and latest[entry['key']].get('retryable', True)]
        if remaining:
            delay = min(300, 30 * (2 ** min(round_number - 1, 4)))
            error = latest[remaining[0]['key']].get('error', '')
            emit('status', f'第 {round_number} 轮结束，剩余 {len(remaining)} 条失败；{delay} 秒后自动重试，可随时停止。最近错误：{error}')
            wait_for_retry(delay, round_number, len(remaining), stop, emit, retry_now)
            if stop.is_set():
                break
    return {'success': sum(x['status'] == '已完成' for x in latest.values()),
            'skipped': sum(x['status'] == '已存在' for x in latest.values()),
            'failed': sum(x['status'] == '失败' for x in latest.values()),
            'blocked': sum(x['status'] == '失败' and not x.get('retryable', True) for x in latest.values()),
            'pending': len(entries) - len(latest), 'items': list(latest.values()),
            'reports': reports, 'rounds': round_number, 'stopped': stop.is_set()}



def wait_for_retry(seconds, round_number, count, stop, emit, retry_now=None):
    # One-second interruptible ticks keep cancellation responsive and show a live countdown.
    for left in range(seconds, 0, -1):
        if stop.is_set() or (retry_now is not None and retry_now.is_set()):
            break
        emit('retry_wait', {'seconds': left, 'round': round_number + 1, 'count': count})
        if stop.wait(1):
            break
    emit('retry_wait', {'seconds': 0, 'round': round_number + 1, 'count': count})


def failure_help(error):
    text = core.clean_message(error)
    low = text.lower()
    rules = [
        (('drm',), '片源受保护', '当前工具无法下载，此项不自动重试。打开播放页，或查看官方客户端是否提供缓存。'),
        (('no space', 'disk full', 'errno 28', 'winerror 112', '空间不足'), '磁盘空间不足', '清理空间或更换保存位置后重试。'),
        (('画质', 'format is not available', 'format not available'), '画质需要重新确认', '停止任务，重新检查该视频画质并确认后再下载。'),
        (('cookie', '登录', 'dpapi', 'sign in', 'log in', 'login'), '登录信息需要检查', '停止任务，重新导出或选择 Cookie 文件，再检查画质。'),
        (('vip', 'premium', '会员', 'permission', '权限'), '访问权限不足', '在浏览器确认账号能播放该画质，再更新登录信息。'),
        (('404', 'not found', 'removed', 'deleted', '下架', '不存在'), '视频可能不可用', '在浏览器打开视频，确认是否仍可播放。'),
        (('403', '412', '429', '拒绝', 'too many'), '网站暂时限制请求', '等待后重试；持续失败时在浏览器确认视频可播放。'),
        (('timeout', 'timed out', 'network', 'connection', 'resolve', 'ssl', '网络', '超时'), '网络连接异常', '检查网络，工具会在本轮结束后自动重试。'),
    ]
    for needles, category, advice in rules:
        if any(word in low for word in needles):
            return {'category': category, 'advice': advice, 'detail': text}
    return {'category': '下载未成功', 'advice': '工具会自动重试；持续失败时查看详细原因。', 'detail': text}


def save_last_task(root, collection, selected, states, errors, raw, limit, paths=None):
    path = Path(root) / '下载记录' / '上次批量任务.json'
    path.parent.mkdir(parents=True, exist_ok=True)
    data = {'version': 1, 'collection': collection, 'selected': list(selected),
            'states': states, 'errors': errors, 'raw': raw, 'limit': str(limit), 'paths': paths or {}}
    temporary = path.with_suffix('.json.tmp')
    temporary.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding='utf-8')
    temporary.replace(path)


def load_last_task(root):
    path = Path(root) / '下载记录' / '上次批量任务.json'
    if not path.exists():
        return None
    data = json.loads(path.read_text(encoding='utf-8'))
    entries = data['collection']['entries']
    if data.get('version') != 1 or not isinstance(entries, list) or not 1 <= len(entries) <= 2000:
        raise ValueError('上次任务记录格式不正确，请重新解析链接。')
    for entry in entries:
        entry['url'] = core.normalize_url(entry['url'])
    return data

def valid_quality(quality, url, use_chrome=False, cookie_file=None):
    return bool(quality and (quality.get('width') or 0)>0 and (quality.get('height') or 0)>0
                and quality.get('format_id') and quality.get('signature') ==
                core.quality_signature(url,use_chrome,cookie_file))


def signature_hash(url,use_chrome=False,cookie_file=None):
    value=json.dumps(core.quality_signature(url,use_chrome,cookie_file),ensure_ascii=False)
    return hashlib.sha256(value.encode()).hexdigest()


def save_quality_cache(folder,url,quality,use_chrome=False,cookie_file=None):
    if not valid_quality(quality,url,use_chrome,cookie_file):
        return
    records=Path(folder)/'下载记录'
    records.mkdir(parents=True,exist_ok=True)
    row={'url':core.normalize_url(url),'signature_hash':signature_hash(url,use_chrome,cookie_file),
         'quality':{k:v for k,v in quality.items() if k!='signature'}}
    with (records/'画质检查缓存.jsonl').open('a',encoding='utf-8') as handle:
        handle.write(json.dumps(row,ensure_ascii=False)+'\n')


def load_quality_cache(folder,entries,use_chrome=False,cookie_file=None):
    cached={}
    path=Path(folder)/'下载记录'/'画质检查缓存.jsonl'
    if path.is_file():
        with path.open(encoding='utf-8') as handle:
            for line in handle:
                try:
                    row=json.loads(line)
                    cached[(row['url'],row['signature_hash'])]=row['quality']
                except (ValueError,KeyError,TypeError):
                    continue
    result={}
    for i,entry in enumerate(entries):
        url=core.normalize_url(entry['url'])
        q=cached.get((url,signature_hash(url,use_chrome,cookie_file)))
        if isinstance(q,dict):
            q=q | {'signature':core.quality_signature(url,use_chrome,cookie_file)}
            if valid_quality(q,url,use_chrome,cookie_file):
                result[str(i)]=q
    return result


class BatchWindow:
    def __init__(self, parent, embedded=False):
        import tkinter as tk
        from tkinter import ttk, filedialog, messagebox
        self.parent, self.tk, self.ttk = parent, tk, ttk
        self.filedialog, self.messagebox = filedialog, messagebox
        self.root = parent.root if embedded else tk.Toplevel(parent.root)
        self.root.title('视频下载工具 · 统一主界面' if embedded else '合集 / 批量下载 · 补查与去重')
        self.root.geometry(f'1120x{min(970,self.root.winfo_screenheight()-100)}')
        self.root.minsize(920, 820)
        self.background = core.apply_anime_background(self.root)
        if not embedded:
            self.root.grab_set()
        self.running = False
        self.closing = False
        self.stop = threading.Event()
        self.retry_now = threading.Event()
        self.download_states = {}
        self.download_paths = {}
        self.errors = {}
        self.task_keys = []
        self.restoring = False
        self.events = queue.Queue()
        self.collection = None
        self.parsed_raw = ''
        self.pending_raw = ''
        self.qualities = {}
        self.folder = None
        body = ttk.Frame(self.root, padding=18)
        body.pack(fill='both', expand=True, padx=20, pady=(85,20) if self.background else 12)
        ttk.Label(body, text='视频下载工具' if embedded else '一个链接，下载整个合集', style='Title.TLabel').pack(anchor='w')
        if hasattr(self,'build_settings'):
            self.build_settings(body)
        ttk.Label(body, text='粘贴视频、合集、番剧或电影链接；也可每行粘贴一个视频链接。').pack(anchor='w', pady=(6, 8))
        self.input = tk.Text(body, height=3, wrap='word', font=('Microsoft YaHei UI', 10))
        self.input.pack(fill='x')
        self.input.insert('1.0', parent.url.get())
        controls = ttk.Frame(body)
        controls.pack(fill='x', pady=8)
        self.parse_button = ttk.Button(controls, text='解析清单', command=self.parse)
        self.parse_button.pack(side='left')
        self.import_button = ttk.Button(controls, text='导入链接 TXT', command=self.import_links)
        self.import_button.pack(side='left', padx=8)
        self.restore_button = ttk.Button(controls, text='恢复上次任务', command=lambda:self.restore_task(force=True))
        self.restore_button.pack(side='left', padx=(0,8))
        self.refresh_quality_button = ttk.Button(controls,text='强制刷新选中画质',command=lambda:self.check_qualities(force=True))
        self.refresh_quality_button.pack(side='right')
        ttk.Label(controls, text='最多读取').pack(side='left')
        self.limit = tk.StringVar(value='500')
        self.limit_box = ttk.Spinbox(controls, from_=1, to=2000, textvariable=self.limit, width=6)
        self.limit_box.pack(side='left', padx=6)
        self.status = tk.StringVar(value='先解析清单，不会立即下载视频。')
        ttk.Label(body, textvariable=self.status, wraplength=960).pack(anchor='w', pady=(4, 8))
        footer = ttk.Frame(body)
        footer.pack(side='bottom', fill='x')
        table_frame = ttk.Frame(body)
        table_frame.pack(fill='both', expand=True)
        self.table = ttk.Treeview(table_frame, columns=('number','title','resolution','fps','status'), show='headings', selectmode='extended')
        for column, label, width in [('number','序号',50),('title','视频标题',440),('resolution','下载分辨率',160),('fps','帧率',75),('status','状态',130)]:
            self.table.heading(column, text=label)
            self.table.column(column, width=width, minwidth=50, stretch=column == 'title')
        scroll = ttk.Scrollbar(table_frame, orient='vertical', command=self.table.yview)
        self.table.configure(yscrollcommand=scroll.set)
        scroll.pack(side='right', fill='y')
        self.table.pack(fill='both', expand=True)
        selection_row = ttk.Frame(footer)
        selection_row.pack(fill='x', pady=8)
        self.all_button = ttk.Button(selection_row, text='全选', command=lambda:self.table.selection_set(self.table.get_children()))
        self.all_button.pack(side='left')
        self.none_button = ttk.Button(selection_row, text='取消选择', command=lambda:self.table.selection_remove(self.table.selection()))
        self.none_button.pack(side='left', padx=6)
        self.selection_text = tk.StringVar(value='已选 0 条；可按 Ctrl / Shift 多选')
        ttk.Label(selection_row, textvariable=self.selection_text).pack(side='left', padx=6)
        self.table.bind('<<TreeviewSelect>>', self.selection_changed)
        if hasattr(self,'build_task_actions'):
            self.build_task_actions(footer)
        actions = ttk.Frame(footer)
        actions.pack(fill='x')
        self.quality_button = ttk.Button(actions,text='补查失败 / 未识别',command=self.check_qualities)
        self.quality_button.pack(side='left',padx=(0,6))
        self.only4k_button = ttk.Button(selection_row,text='只选已检查的 4K',command=self.select_4k)
        self.only4k_button.pack(side='right')
        self.download_button = ttk.Button(actions, text='下载选中视频', style='Primary.TButton', command=self.start)
        self.download_button.pack(side='left')
        self.retry_button = ttk.Button(actions, text='重试失败项', command=self.retry)
        self.retry_button.pack(side='left', padx=6)
        self.stop_button = ttk.Button(actions, text='停止', command=self.cancel, state='disabled')
        self.stop_button.pack(side='left')
        self.now_button = ttk.Button(actions, text='立即重试', command=self.retry_now.set, state='disabled')
        self.now_button.pack(side='left', padx=6)
        ttk.Button(actions, text='打开合集目录', command=self.open_folder).pack(side='right')
        auth = '所选 Cookie 文件' if parent.cookie_file else ('Chrome 登录态（失败回退公开画质）' if parent.use_chrome.get() else '公开可用画质')
        rate = core.configured_rate_limit()
        rate_text = f'{rate / 1048576:g} MB/s' if rate else '不限速'
        if not embedded:
            ttk.Label(footer, text='本次使用：' + auth + '；下载限速：' + rate_text + '（每个任务）。如需更换，请关闭此窗口，在主窗口设置。', wraplength=960).pack(anchor='w', pady=(10,4))
        self.detail = tk.StringVar(value='每轮结束自动重试可重试的失败项；受保护片源提示处理，已完成跳过，可随时停止。')
        ttk.Label(footer, textvariable=self.detail, wraplength=960).pack(anchor='w', pady=(4,6))
        self.total_text = tk.StringVar(value='总进度：尚未开始下载')
        ttk.Label(footer, textvariable=self.total_text, font=('Microsoft YaHei UI', 11, 'bold')).pack(anchor='w', pady=(6,4))
        self.total_bar = ttk.Progressbar(footer, maximum=100)
        self.total_bar.pack(fill='x', pady=(0,6))
        self.retry_text = tk.StringVar(value='自动重试：等待任务开始')
        ttk.Label(footer, textvariable=self.retry_text).pack(anchor='w')
        self.error_text = tk.StringVar(value='选择失败的视频，可查看原因和处理建议。')
        ttk.Label(footer, textvariable=self.error_text, wraplength=1020, foreground='#9c3527').pack(anchor='w', pady=(4,6))
        ttk.Label(footer, text='当前视频进度').pack(anchor='w')
        self.bar = ttk.Progressbar(footer, maximum=100)
        self.bar.pack(fill='x')
        self.file_progress = tk.StringVar(value='当前文件：等待开始下载')
        ttk.Label(footer, textvariable=self.file_progress, wraplength=1020).pack(anchor='w', pady=(5,0))
        self.use_chrome = parent.use_chrome.get()
        self.cookie_file = parent.cookie_file
        self.root.protocol('WM_DELETE_WINDOW', self.close)
        self.restore_task()
        self.root.after(100, self.poll)

    def persist_task(self):
        if self.collection and not self.restoring:
            try:
                save_last_task(self.parent.output_dir, self.collection, self.table.selection(),
                               self.download_states, self.errors, self.parsed_raw or self.collection['reference'], self.limit.get(), self.download_paths)
            except OSError:
                self.detail.set('任务恢复记录未能保存，请检查保存目录是否可写。')

    def restore_task(self, force=False):
        if self.running:
            return
        self.restoring = True
        try:
            saved = load_last_task(self.parent.output_dir)
            if not saved:
                return
            incoming = self.input.get('1.0','end').strip()
            previous = saved.get('raw') or saved['collection']['reference']
            def normalized_lines(value):
                try:
                    return [core.normalize_url(line) for line in value.splitlines() if line.strip()]
                except ValueError:
                    return [value.strip()]
            if not force and incoming and normalized_lines(incoming) != normalized_lines(previous):
                self.status.set('已保留新链接，请点击“解析清单”；需要旧清单时可点“恢复上次任务”。')
                return
            self.table.delete(*self.table.get_children())
            self.collection = saved['collection']
            self.parsed_raw = previous
            self.folder = collection_folder(self.parent.output_dir, self.collection['title'], self.collection['reference'])
            self.qualities = load_quality_cache(self.folder, self.collection['entries'], self.use_chrome, self.cookie_file)
            self.errors = saved.get('errors', {})
            self.download_states = saved.get('states', {})
            self.download_paths = saved.get('paths', {})
            self.input.delete('1.0','end')
            self.input.insert('1.0',saved.get('raw') or self.collection['reference'])
            self.limit.set(saved.get('limit','500'))
            for i, entry in enumerate(self.collection['entries']):
                key = str(i)
                q = self.qualities.get(key, {})
                state = self.download_states.get(key, '待下载')
                if state == '正在下载':
                    state = '待续传'
                    self.download_states[key] = state
                resolution = f"{q['width']}×{q['height']}" if q else '待检查'
                self.table.insert('', 'end', iid=key, values=(i+1, entry['title'], resolution, q.get('fps') or '—', state))
            selected = [key for key in saved.get('selected',[]) if self.table.exists(key)]
            self.table.selection_set(selected)
            self.task_keys = selected
            self.update_total()
            self.download_button.configure(text='继续选中任务')
            self.status.set(f"已恢复上次任务：{self.collection['title']}。点击继续；不会自动开始下载。")
            self.detail.set('已恢复选择、画质及历史状态；继续时仍会核验实际文件，缺失的文件会重新下载。')
        except (OSError, ValueError, KeyError, TypeError):
            self.collection = None
            self.table.delete(*self.table.get_children())
            self.status.set('上次任务记录无法恢复，请重新解析链接。')
        finally:
            self.restoring = False

    def selection_changed(self, event=None):
        keys = self.table.selection()
        self.selection_text.set(f'已选 {len(keys)} / {len(self.table.get_children())} 条；可按 Ctrl / Shift 多选')
        key = self.table.focus()
        if key not in keys:
            key = keys[0] if keys else ''
        error = self.errors.get(key)
        if error:
            help_info = failure_help(error)
            self.error_text.set(f"{help_info['category']}：{help_info['advice']}  详细：{help_info['detail'][:320]}")
        else:
            self.error_text.set('选择失败的视频，可查看原因和处理建议。')
        if not self.running:
            self.task_keys = list(keys)
            self.update_total()
        self.persist_task()

    def update_total(self):
        states = [self.download_states.get(key, '待下载') for key in self.task_keys]
        done = sum(state in ('已完成','已存在') for state in states)
        active = states.count('正在下载')
        failed = states.count('失败')
        blocked = sum(self.download_states.get(key) == '失败' and
                      failure_help(self.errors.get(key, ''))['category'] == '片源受保护' for key in self.task_keys)
        pending = len(states) - done - active - failed
        self.total_text.set(f'总进度：已完成 {done} / {len(states)}（含跳过 {states.count("已存在")}） · 下载中 {active} · 待下载 {pending} · 待重试 {failed-blocked} · 受保护 {blocked}')
        self.total_bar['value'] = 100 * done / len(states) if states else 0

    def set_running(self, running):
        self.running = running
        for control in (self.parse_button, self.import_button, self.limit_box, self.download_button,
                        self.retry_button, self.all_button, self.none_button, self.input, self.quality_button,self.only4k_button,self.refresh_quality_button,self.restore_button):
            control.configure(state='disabled' if running else 'normal')
        self.stop_button.configure(state='normal' if running else 'disabled')
        if not running:
            self.now_button.configure(state='disabled')

    def worker(self, function, completion):
        self.set_running(True)
        self.stop.clear()
        def run():
            try:
                self.events.put((completion, function()))
            except Exception as exc:
                self.events.put(('error', core.describe_error(exc)))
        threading.Thread(target=run, daemon=True).start()

    def import_links(self):
        path = self.filedialog.askopenfilename(parent=self.root, filetypes=[('链接清单','*.txt')])
        if path:
            try:
                value = Path(path).read_text(encoding='utf-8-sig')
                self.input.delete('1.0','end')
                self.input.insert('1.0',value)
            except (OSError, UnicodeError):
                self.status.set('无法读取文件，请使用 UTF-8 编码的链接 TXT。')

    def parse(self):
        if self.running:
            return
        try:
            limit = int(self.limit.get())
        except ValueError:
            self.status.set('读取上限请输入数字。')
            return
        text = self.input.get('1.0','end').strip()
        self.pending_raw = text
        self.collection = None
        self.qualities = {}
        self.download_states = {}
        self.download_paths = {}
        self.errors = {}
        self.task_keys = []
        self.update_total()
        self.table.delete(*self.table.get_children())
        self.status.set('正在解析合集清单…')
        self.worker(lambda: enumerate_collection(text,limit,self.use_chrome,self.cookie_file,self.stop,self.emit), 'parsed')

    def emit(self, kind, value):
        self.events.put((kind,value))

    def start(self):
        if self.running:
            return
        keys = set(self.table.selection())
        if not self.collection or not keys:
            self.status.set('请先解析清单，并选择要下载的视频。')
            return
        if self.parsed_raw and self.input.get('1.0','end').strip() != self.parsed_raw.strip():
            self.status.set('链接已修改，请先点击“解析清单”，避免下载旧合集。')
            return
        stale = [key for key in keys if self.qualities.get(key)
                 and (self.qualities[key].get('width') or 0) > 0
                 and (self.qualities[key].get('height') or 0) > 0
                 and not valid_quality(self.qualities[key],self.collection['entries'][int(key)]['url'],
                                       self.use_chrome,self.cookie_file)]
        if stale:
            self.status.set(f'选中的 {len(stale)} 条已确认画质对应的登录设置发生变化，请先重新检查画质。')
            return
        entries = []
        unknown = 0
        for i, entry in enumerate(self.collection['entries']):
            key = str(i)
            if key not in keys:
                continue
            item = {**entry, 'key': key}
            quality = self.qualities.get(key)
            if valid_quality(quality,entry['url'],self.use_chrome,self.cookie_file):
                item['quality'] = quality.copy()
            else:
                unknown += 1
            entries.append(item)
        self.task_keys = [entry['key'] for entry in entries]
        for key in self.task_keys:
            self.download_states[key] = '待下载'
        self.retry_now.clear()
        self.update_total()
        self.persist_task()
        self.status.set(f'准备下载 {len(entries)} 条，其中 {unknown} 条未识别，将尝试当前可用最高画质。')
        self.worker(lambda:run_until_complete(entries,self.folder,self.use_chrome,self.cookie_file,self.stop,self.emit,
                    download=getattr(self,'download_handler',None),retry_now=self.retry_now), 'finished')

    def select_4k(self):
        self.table.selection_set([key for key,q in self.qualities.items() if (q.get('height') or 0) >= 2160])

    def check_qualities(self, force=False):
        if self.running or not self.collection:
            return
        keys = [key for key in self.table.get_children() if key in self.table.selection()]
        if not keys:
            self.status.set('请先选择要检查的视频。')
            return
        selected_count=len(keys)
        if not force:
            keys=[key for key in keys if not valid_quality(self.qualities.get(key),
                    self.collection['entries'][int(key)]['url'],self.use_chrome,self.cookie_file)]
        preserved=selected_count-len(keys)
        if not keys:
            self.status.set(f'选中的 {preserved} 条画质已成功识别，已保留结果，不重复检查。')
            return
        self.status.set(f'保留 {preserved} 条成功结果，仅检查剩余 {len(keys)} 条。')
        for key in keys:
            self.qualities.pop(key,None)
            self.table.set(key,'resolution','检查中')
            self.table.set(key,'fps','—')
            self.table.set(key,'status','待检查')
        def check():
            passed = failed = 0
            for position,key in enumerate(keys,1):
                if self.stop.is_set():
                    break
                self.emit('quality_status',f'仅检查画质，不下载视频：{position} / {len(keys)}')
                try:
                    q = core.download_video(self.collection['entries'][int(key)]['url'],self.folder,
                            self.use_chrome,self.emit,self.stop,self.cookie_file,preview_only=True)
                    if not valid_quality(q,self.collection['entries'][int(key)]['url'],self.use_chrome,self.cookie_file):
                        raise ValueError('没有识别到有效分辨率，下次点击会继续补查。')
                    self.emit('quality_row',{'key':key,'quality':q})
                    passed += 1
                except core.Cancelled:
                    break
                except Exception as exc:
                    self.emit('quality_row',{'key':key,'error':core.describe_error(exc)})
                    failed += 1
                if self.stop.wait(0.4):
                    break
            return {'checked':passed,'failed':failed,'pending':len(keys)-passed-failed,'preserved':preserved}
        self.worker(check,'quality_finished')

    def retry(self):
        failed = [key for key in self.table.get_children() if self.table.set(key,'status') == '失败']
        if not failed:
            self.status.set('没有需要重试的失败项。')
            return
        self.table.selection_set(failed)
        self.start()

    def cancel(self):
        self.stop.set()
        self.status.set('正在停止，等待当前网络请求或合并操作结束…')
        self.stop_button.configure(state='disabled')
        self.now_button.configure(state='disabled')
        self.retry_text.set('自动重试：正在停止')

    def close(self):
        if self.running:
            if self.messagebox.askyesno('停止批量任务','停止当前任务并关闭窗口？已完成视频及下载片段会保留。',parent=self.root):
                self.closing = True
                self.cancel()
        else:
            self.persist_task()
            self.root.destroy()

    def open_folder(self):
        import os
        folder = self.folder or self.parent.output_dir
        folder.mkdir(parents=True,exist_ok=True)
        os.startfile(str(folder))

    def poll(self):
        try:
            while True:
                kind,value = self.events.get_nowait()
                if hasattr(self,'on_event'):
                    self.on_event(kind,value)
                if kind == 'parsed':
                    self.set_running(False)
                    self.collection = value
                    self.parsed_raw = self.pending_raw or value['reference']
                    try:
                        self.folder = save_preview(value,self.parent.output_dir)
                    except OSError as exc:
                        self.collection = None
                        self.status.set('无法保存清单：' + core.clean_message(exc))
                        continue
                    for i,entry in enumerate(value['entries']):
                        self.table.insert('','end',iid=str(i),values=(i+1,entry['title'],'未检查','—','待检查画质'))
                    try:
                        restored=load_quality_cache(self.folder,value['entries'],self.use_chrome,self.cookie_file)
                    except OSError:
                        restored={}
                    for key,q in restored.items():
                        self.qualities[key]=q
                        self.table.set(key,'resolution',f"{q['width']}×{q['height']}")
                        self.table.set(key,'fps',str(q.get('fps') or '?'))
                        self.table.set(key,'status','已检查 · 已恢复')
                    self.table.selection_set(self.table.get_children())
                    warning = '（受读取上限限制，只列出部分条目）' if value['truncated'] else ''
                    self.status.set(f"{value['title']}：已读取 {len(value['entries'])} 条{warning}。先检查选中画质，再决定是否下载。")
                    self.detail.set(f'已恢复 {len(restored)} 条成功画质记录。保存到：' + str(self.folder))
                    self.persist_task()
                elif kind == 'quality_status':
                    self.status.set(value)
                elif kind == 'quality_row':
                    key = value['key']
                    if 'quality' in value:
                        q = value['quality']
                        self.qualities[key] = q
                        self.errors.pop(key, None)
                        self.table.set(key,'resolution',f"{q.get('width') or '?'}×{q.get('height') or '?'}")
                        self.table.set(key,'fps',str(q.get('fps') or '?'))
                        self.table.set(key,'status','已检查 · 登录态' if q['cookie_file_used'] or q['chrome_used'] else '已检查 · 公开')
                        try:
                            if self.folder:
                                save_quality_cache(self.folder,self.collection['entries'][int(key)]['url'],q,self.use_chrome,self.cookie_file)
                        except OSError:
                            self.detail.set('画质已识别，但本地缓存未保存；当前窗口仍保留检查结果。')
                    else:
                        protected = failure_help(value['error'])['category'] == '片源受保护'
                        self.qualities.pop(key, None)
                        self.table.set(key,'resolution','受保护' if protected else '检查失败')
                        self.table.set(key,'status','受保护 · 无法下载' if protected else '检查失败')
                        self.errors[key] = value['error']
                        self.detail.set(value['error'])
                    self.persist_task()
                elif kind == 'quality_finished':
                    self.set_running(False)
                    for key in self.table.get_children():
                        if key not in self.qualities and self.table.set(key,'resolution') == '检查中':
                            self.table.set(key,'resolution','未检查')
                            self.table.set(key,'status','待检查画质')
                    self.status.set(f"保留 {value.get('preserved',0)} 条成功结果；本次成功 {value['checked']}，失败 {value['failed']}，未检查 {value['pending']}。尚未下载视频。")
                    self.detail.set('可点击“只选已检查的 4K”，或手动选择符合要求的视频，再点击下载。')
                elif kind == 'finished':
                    self.set_running(False)
                    self.status.set(f"任务结束：下载完成 {value['success']}，已存在 {value['skipped']}，失败 {value['failed']}，未处理 {value['pending']}。")
                    self.detail.set('已停止自动重试；下次下载会核验并跳过已有文件。' if value.get('stopped') else
                                    (f"{value['blocked']} 条片源受保护，无法下载且不自动重试；选中后可打开播放页。" if value.get('blocked') else '本次处理已结束。'))
                    self.retry_text.set('自动重试：已停止' if value.get('stopped') else '自动重试：本次已结束')
                    self.persist_task()
                elif kind == 'error':
                    self.set_running(False)
                    self.status.set(value)
                elif kind == 'retry_wait':
                    left = value['seconds']
                    self.retry_text.set(f"第 {value['round']} 轮重试 · 剩余 {value['count']} 条 · {left} 秒后开始" if left else '重试等待结束')
                    self.now_button.configure(state='normal' if left and not self.stop.is_set() else 'disabled')
                elif kind == 'item':
                    key = value['key']
                    if value.get('path'):
                        self.download_paths[key] = value['path']
                    if value.get('width') and value.get('height'):
                        self.table.set(key,'resolution',f"{value['width']}×{value['height']}")
                        self.table.set(key,'fps',str(value.get('fps') or '?'))
                    self.download_states[key] = value['state']
                    if value.get('error'):
                        self.errors[key] = value['error']
                        info = failure_help(value['error'])
                        if info['category'] == '片源受保护':
                            self.qualities.pop(key, None)
                            self.table.set(key,'resolution','受保护')
                        self.error_text.set(f"{info['category']}：{info['advice']}  详细：{info['detail'][:320]}")
                    elif value['state'] in ('已完成', '已存在'):
                        self.errors.pop(key, None)
                    self.update_total()
                    self.persist_task()
                    self.table.set(value['key'],'status',value['state'])
                    self.table.see(value['key'])
                    if 'position' in value:
                        self.bar['value'] = 0
                        self.file_progress.set('当前文件：正在获取大小与下载速度…')
                        self.retry_text.set('正在下载；本轮结束后自动重试失败项')
                        self.status.set(f"正在处理第 {value['position']} / {value['total']} 条…")
                elif kind == 'progress':
                    self.bar['value'] = value['percent'] or 0
                    self.file_progress.set(core.progress_description(value))
                elif kind == 'metadata':
                    self.detail.set(value['title'])
                elif kind == 'log':
                    self.detail.set(core.clean_message(value))
                elif kind == 'status' and not self.stop.is_set():
                    self.detail.set(value)
                    if any(word in value for word in ('合并', '核验')):
                        self.file_progress.set('下载阶段结束，正在处理本地文件；剩余时间暂不可估算。')
                if self.closing and not self.running:
                    self.root.destroy()
                    return
        except queue.Empty:
            pass
        self.root.after(100,self.poll)
