"""Bilibili QR login. Credentials stay in memory or current-user Windows DPAPI.

No browser database access, plaintext cookie files, passwords, or credential logs.
The QR endpoints belong to Bilibili's web login flow and can change independently.
"""
import copy
import ctypes
from ctypes import wintypes
import http.cookiejar
import json
import os
from pathlib import Path
import threading
import time
import urllib.parse
import urllib.request
import uuid

SESSION_REF = '@bili-session'
GENERATE_URL = 'https://passport.bilibili.com/x/passport-login/web/qrcode/generate'
POLL_URL = 'https://passport.bilibili.com/x/passport-login/web/qrcode/poll'
VERIFY_URL = 'https://api.bilibili.com/x/web-interface/nav'
USER_AGENT = 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/131.0.0.0 Safari/537.36'


class LoginError(RuntimeError):
    pass


def site_cookies(jar):
    """Copy only valid Bilibili cookies; never carry unrelated domains forward."""
    result = http.cookiejar.CookieJar()
    for cookie in jar:
        domain = cookie.domain.lstrip('.').lower()
        if (domain == 'bilibili.com' or domain.endswith('.bilibili.com')) and not cookie.is_expired():
            result.set_cookie(copy.copy(cookie))
    return result


class LoginClient:
    def __init__(self, jar=None, opener=None):
        self.jar = site_cookies(jar or [])
        self.opener = opener or urllib.request.build_opener(urllib.request.HTTPCookieProcessor(self.jar))

    def request(self, url):
        request = urllib.request.Request(url, headers={'User-Agent': USER_AGENT, 'Referer': 'https://www.bilibili.com/'})
        try:
            with self.opener.open(request, timeout=15) as response:
                data = json.loads(response.read(2 * 1024 * 1024).decode('utf-8'))
        except Exception:
            # urllib errors can contain a QR key in the URL. Do not expose them.
            raise LoginError('网络连接异常，请检查网络后重试。') from None
        if not isinstance(data, dict):
            raise LoginError('登录服务返回了无法识别的数据，请稍后重试。')
        return data

    def generate(self):
        result = self.request(GENERATE_URL)
        data = result.get('data') or {}
        url, key = data.get('url'), data.get('qrcode_key')
        if result.get('code') != 0 or not isinstance(url, str) or not isinstance(key, str):
            raise LoginError('无法生成登录二维码，请稍后刷新。')
        parts = urllib.parse.urlsplit(url)
        if parts.scheme != 'https' or parts.hostname not in ('passport.bilibili.com', 'account.bilibili.com'):
            raise LoginError('登录二维码来源无法确认，已停止。')
        return {'url': url, 'key': key}

    def poll(self, key):
        result = self.request(POLL_URL + '?' + urllib.parse.urlencode({'qrcode_key': key}))
        data = result.get('data') or {}
        if result.get('code') != 0:
            raise LoginError('扫码状态查询失败，请刷新二维码。')
        code = data.get('code')
        if code == 0:
            profile = self.verify()
            if not profile['logged_in']:
                raise LoginError('已扫码，但登录会话未通过验证，请重新登录。')
            return 'success', profile
        statuses = {86101: 'waiting', 86090: 'confirm', 86038: 'expired'}
        if code not in statuses:
            raise LoginError('登录需要额外验证，请在 B 站客户端处理后重试。')
        return statuses[code], None

    def verify(self):
        result = self.request(VERIFY_URL)
        data = result.get('data') or {}
        logged_in = result.get('code') == 0 and data.get('isLogin') is True
        return {'logged_in': logged_in,
                'name': str(data.get('uname') or '')[:80] if logged_in else '',
                'checked_at': time.strftime('%H:%M:%S')}


class Blob(ctypes.Structure):
    _fields_ = [('cbData', wintypes.DWORD), ('pbData', ctypes.POINTER(ctypes.c_ubyte))]


def protect_bytes(data, decrypt=False):
    if os.name != 'nt':
        raise LoginError('记住登录仅支持当前 Windows 用户；可以取消勾选后使用。')
    buffer = ctypes.create_string_buffer(data)
    source = Blob(len(data), ctypes.cast(buffer, ctypes.POINTER(ctypes.c_ubyte)))
    output = Blob()
    crypt = ctypes.WinDLL('crypt32', use_last_error=True)
    kernel = ctypes.WinDLL('kernel32', use_last_error=True)
    function = crypt.CryptUnprotectData if decrypt else crypt.CryptProtectData
    function.argtypes = [ctypes.POINTER(Blob), ctypes.c_void_p, ctypes.c_void_p,
                         ctypes.c_void_p, ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(Blob)]
    function.restype = wintypes.BOOL
    kernel.LocalFree.argtypes = [ctypes.c_void_p]
    kernel.LocalFree.restype = ctypes.c_void_p
    if not function(ctypes.byref(source), None, None, None, None, 1, ctypes.byref(output)):
        raise LoginError('本机登录资料无法解锁或保存，请重新扫码登录。')
    try:
        return ctypes.string_at(output.pbData, output.cbData)
    finally:
        kernel.LocalFree(output.pbData)
        ctypes.memset(buffer, 0, len(data))


class SessionStore:
    def __init__(self, directory=None):
        directory = directory or Path(os.environ.get('LOCALAPPDATA', Path.home())) / 'BiliDownload' / 'account'
        self.path = Path(directory) / 'session.bin'
        self.lock = threading.RLock()
        self.jar = http.cookiejar.CookieJar()
        self.revision = uuid.uuid4().hex
        self.loaded = False
        self.verified_at = 0.0

    def restore(self):
        with self.lock:
            if self.loaded:
                return
            self.loaded = True
            if not self.path.is_file():
                return
            if self.path.stat().st_size > 1024 * 1024:
                raise LoginError('本机登录资料异常，请重新登录。')
            try:
                payload = json.loads(protect_bytes(self.path.read_bytes(), decrypt=True))
                jar = http.cookiejar.CookieJar()
                for row in payload['cookies']:
                    jar.set_cookie(http.cookiejar.Cookie(**row))
                self.jar = site_cookies(jar)
                self.revision = str(payload['revision'])
            except Exception:
                raise LoginError('本机登录资料不可用，请重新登录。') from None

    def snapshot(self):
        self.restore()
        with self.lock:
            return site_cookies(self.jar)

    def signature(self):
        self.restore()
        return ('account-session', self.revision)

    def accept(self, jar, remember=False):
        filtered = site_cookies(jar)
        if not any(c.name == 'SESSDATA' for c in filtered):
            raise LoginError('登录会话不完整，请重新扫码。')
        revision = uuid.uuid4().hex
        fields = ('version', 'name', 'value', 'port', 'port_specified', 'domain', 'domain_specified',
                  'domain_initial_dot', 'path', 'path_specified', 'secure', 'expires', 'discard',
                  'comment', 'comment_url', 'rfc2109')
        with self.lock:
            if remember:
                rows = [dict({key: getattr(c, key) for key in fields}, rest=c._rest) for c in filtered]
                encrypted = protect_bytes(json.dumps({'revision': revision, 'cookies': rows}).encode('utf-8'))
                self.path.parent.mkdir(parents=True, exist_ok=True)
                temp = self.path.with_suffix('.tmp')
                temp.write_bytes(encrypted)
                temp.replace(self.path)
            else:
                self.path.unlink(missing_ok=True)
            self.jar, self.revision, self.loaded = filtered, revision, True
            self.verified_at = 0.0

    def logout(self):
        with self.lock:
            # Clear memory even if disk cleanup is denied. Surface deletion failures.
            self.jar.clear()
            self.revision = uuid.uuid4().hex
            self.verified_at = 0.0
            self.loaded = True
            self.path.unlink(missing_ok=True)
            self.path.with_suffix('.tmp').unlink(missing_ok=True)

    def validated_snapshot(self):
        jar = self.snapshot()
        if not any(c.name == 'SESSDATA' for c in jar):
            raise LoginError('登录已失效，请重新登录 B 站。')
        with self.lock:
            revision, checked = self.revision, self.verified_at
        if time.monotonic() - checked > 300 or not checked:
            if not LoginClient(jar).verify()['logged_in']:
                raise LoginError('登录已失效，请重新登录 B 站。')
            with self.lock:
                if revision != self.revision:
                    raise LoginError('登录来源已改变，请重新检查画质。')
                self.verified_at = time.monotonic()
        return jar


STORE = SessionStore()


def download_jar(target_url):
    from yt_dlp.cookies import YoutubeDLCookieJar
    host = urllib.parse.urlsplit(target_url).hostname or ''
    if not (host == 'b23.tv' or host == 'bilibili.com' or host.endswith('.bilibili.com')):
        raise LoginError('本机 B 站登录态不能用于其他网站。')
    cookies = STORE.validated_snapshot()
    jar = YoutubeDLCookieJar()
    for cookie in cookies:
        jar.set_cookie(cookie)
    return jar
