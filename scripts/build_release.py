"""Build a whitelisted Windows distribution; never copy installed user data."""
import argparse
import hashlib
from pathlib import Path
import shutil
import zipfile

ROOT = Path(__file__).resolve().parents[1]
parser = argparse.ArgumentParser()
parser.add_argument('--python-installer', type=Path, required=True)
parser.add_argument('--wheels', type=Path, required=True)
parser.add_argument('--output', type=Path, required=True)
args = parser.parse_args()
out = args.output.resolve()
if out.exists():
    raise SystemExit('Output directory already exists; choose a new build directory.')
if not args.python_installer.is_file() or not list(args.wheels.glob('*.whl')):
    raise SystemExit('Python installer or dependency wheels are missing.')
out.mkdir(parents=True)
for folder in ('安装程序', '工具'):
    (out/folder).mkdir()
for name in ('video_downloader.py', 'batch_download.py', 'unified_app.py', 'cache_manager.py', 'workspace_view.py', 'account_login.py'):
    shutil.copyfile(ROOT/'工具'/name, out/'工具'/name)
shutil.copytree(ROOT/'工具'/'assets', out/'工具'/'assets')
for path in (ROOT/'安装程序').iterdir():
    if path.is_file() and path.suffix in ('.ps1', '.cmd', '.txt'):
        shutil.copyfile(path, out/'安装程序'/path.name)
shutil.copyfile(args.python_installer, out/'安装程序'/'python-3.14.8-amd64.exe')
(out/'安装程序'/'wheels').mkdir()
for path in args.wheels.glob('*.whl'):
    shutil.copyfile(path, out/'安装程序'/'wheels'/path.name)
shutil.copytree(ROOT/'浏览器Cookie插件', out/'浏览器Cookie插件',
                ignore=shutil.ignore_patterns('__pycache__', '*.pyc', '*cookies*.txt', 'settings.json'))
for name in ('安装并启动.cmd', '首次运行说明.txt', '新版使用说明.md'):
    shutil.copyfile(ROOT/name, out/name)
files = sorted(p for p in out.rglob('*') if p.is_file())
for p in files:
    if p.name.lower() in ('cookies.txt', 'settings.json', 'session.bin', 'history.json') or p.suffix in ('.part', '.mp4', '.mkv', '.pyc'):
        raise SystemExit('Unexpected runtime file in package')
manifest = '\n'.join(hashlib.sha256(p.read_bytes()).hexdigest()+'  '+p.relative_to(out).as_posix() for p in files)
(out/'MANIFEST-SHA256.txt').write_text(manifest+'\n', encoding='utf-8')
archive = Path(str(out) + '.zip')
with zipfile.ZipFile(archive, 'w', zipfile.ZIP_DEFLATED, compresslevel=6) as bundle:
    for p in sorted(out.rglob('*')):
        if p.is_file():
            bundle.write(p, p.relative_to(out))
with zipfile.ZipFile(archive) as bundle:
    if bundle.testzip() is not None:
        raise SystemExit('Archive integrity check failed')
digest = hashlib.sha256(archive.read_bytes()).hexdigest()
archive.with_suffix('.sha256.txt').write_text(digest+'  '+archive.name+'\n', encoding='utf-8')
print(f'BUILD_OK files={len(files)+1} bytes={archive.stat().st_size} sha256={digest}')
print(archive)
