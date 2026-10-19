"""Conservative cache cleanup: only redundant media with a verified complete final."""
import os
from pathlib import Path
import re
import video_downloader as core


def identity(path):
    match = re.search(r'\[(BV[0-9A-Za-z]{10}(?:_p\d+)?|ep\d+)\]', path.name)
    return match.group(1) if match else None


def safe_path(path, root):
    path, root = Path(path), Path(root).resolve()
    if not path.resolve().is_relative_to(root) or path.resolve() == root:
        return False
    for ancestor in [path, *path.parents]:
        if ancestor == root:
            break
        if ancestor.is_symlink() or ancestor.is_junction():
            return False
    return True


def scan(root, stop=None, emit=None):
    root=Path(root).resolve()
    emit=emit or (lambda kind,value:None)
    _,probe=core.find_media_tools()
    finals={}; cached=[]; groups={}
    for directory, dirs, files in os.walk(root,followlinks=False):
        dirs[:]=[name for name in dirs if name not in ('工具','历史版本','下载记录','视频分割')
                 and not (Path(directory)/name).is_junction() and not (Path(directory)/name).is_symlink()]
        for name in files:
            path=Path(directory)/name
            if not safe_path(path,root) or not identity(path):continue
            if '下载缓存' in path.relative_to(root).parts:
                cached.append(path)
            elif path.suffix.lower() in ('.mp4','.mkv','.webm','.mov') and not re.search(r'\.f\d+\.',name):
                finals.setdefault(identity(path),[]).append(path)
    probes={}
    for position,source in enumerate(cached,1):
        if stop and stop.is_set():raise core.Cancelled('缓存检查已停止。')
        if source.suffix.lower() not in ('.mp4','.webm','.mkv'):continue
        candidates=[p for identifier, paths in finals.items() if identifier in core.same_video_ids(identity(source)) for p in paths]
        if not candidates:continue
        emit('status',f'核对缓存 {position} / {len(cached)}，暂未删除文件…')
        try:original=core.probe_media(source,probe)
        except Exception:continue
        for final in candidates:
            try:
                if final not in probes:probes[final]=core.probe_media(final,probe)
                media=probes[final]
                if not core.duplicate_media(media,original|{'has_audio':True}) or media['fps']+0.15<original['fps']:continue
                group=(str(source.parent),identity(source))
                groups[group]={'directory':str(source.parent),'id':identity(source),'final':str(final),
                               'final_size':final.stat().st_size,'final_mtime':final.stat().st_mtime_ns,
                               'width':original['width'],'height':original['height'],'duration':original['duration']}
                break
            except (OSError,RuntimeError):continue
    plan=[]
    for source in cached:
        group=groups.get((str(source.parent),identity(source)))
        if not group or source.suffix.lower() not in ('.mp4','.m4a','.webm','.mkv'):continue
        # A .part, segment or incomplete file is deliberately never selected.
        stat=source.stat()
        plan.append(group|{'path':str(source),'size':stat.st_size,'mtime':stat.st_mtime_ns})
    return {'root':str(root),'files':plan,'bytes':sum(row['size'] for row in plan)}


def clean(plan):
    root=Path(plan['root']).resolve();removed=0;freed=0;skipped=0
    _,probe=core.find_media_tools();verified={}
    for row in plan['files']:
        source,final=Path(row['path']),Path(row['final'])
        try:
            if not safe_path(source,root) or not safe_path(final,root):raise ValueError()
            if '下载缓存' not in source.relative_to(root).parts or '下载缓存' in final.relative_to(root).parts:raise ValueError()
            if source.parent != Path(row['directory']) or identity(source)!=row['id']:raise ValueError()
            a,b=source.stat(),final.stat()
            if (a.st_size,a.st_mtime_ns)!=(row['size'],row['mtime']):raise ValueError()
            if (b.st_size,b.st_mtime_ns)!=(row['final_size'],row['final_mtime']):raise ValueError()
            if final not in verified:verified[final]=core.probe_media(final,probe)
            if not core.duplicate_media(verified[final],row|{'has_audio':True}):raise ValueError()
            source.unlink();removed+=1;freed+=row['size']
        except (OSError,ValueError,RuntimeError):skipped+=1
    return {'removed':removed,'bytes':freed,'skipped':skipped}
