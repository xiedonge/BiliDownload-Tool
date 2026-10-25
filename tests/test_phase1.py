import http.cookiejar
import json
import os
from pathlib import Path
import sys
import tempfile
import threading
import unittest
from unittest.mock import patch
import tkinter as tk

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / '工具'))
import account_login as account
import batch_download as batch
import video_downloader as core
import unified_app as ui

URL = 'https://www.bilibili.com/video/BV13G41187Au'


def cookies(domain='.bilibili.com', expires=None):
    jar = http.cookiejar.CookieJar()
    jar.set_cookie(http.cookiejar.Cookie(0, 'SESSDATA', 'synthetic-test-session', None, False,
                   domain, True, True, '/', True, True, expires, False, None, None, {'HttpOnly': None}, False))
    return jar


class AccountTests(unittest.TestCase):
    def test_site_scope_and_expiry(self):
        self.assertEqual(len(list(account.site_cookies(cookies('.bilibili.com.evil.example')))), 0)
        self.assertEqual(len(list(account.site_cookies(cookies(expires=1)))), 0)
        self.assertEqual(len(list(account.site_cookies(cookies()))), 1)

    def test_generate_and_poll_states(self):
        client = account.LoginClient()
        with patch.object(client, 'request', return_value={'code': 0, 'data': {'url': 'https://account.bilibili.com/test', 'qrcode_key': 'test'}}):
            self.assertEqual(client.generate()['key'], 'test')
        for code, status in [(86101, 'waiting'), (86090, 'confirm'), (86038, 'expired')]:
            with patch.object(client, 'request', return_value={'code': 0, 'data': {'code': code}}):
                self.assertEqual(client.poll('test'), (status, None))
        with patch.object(client, 'request', return_value={'code': 0, 'data': {'url': 'https://evil.example/test', 'qrcode_key': 'test'}}):
            with self.assertRaises(account.LoginError): client.generate()

    def test_success_requires_verified_account(self):
        client = account.LoginClient()
        with patch.object(client, 'request', return_value={'code': 0, 'data': {'code': 0}}):
            with patch.object(client, 'verify', return_value={'logged_in': False}):
                with self.assertRaises(account.LoginError): client.poll('test')
            with patch.object(client, 'verify', return_value={'logged_in': True, 'name': 'test'}):
                self.assertEqual(client.poll('test')[0], 'success')

    def test_sensitive_error_redacted(self):
        client = account.LoginClient()
        with patch.object(client.opener, 'open', side_effect=RuntimeError('https://example.test/?qrcode_key=secret')):
            with self.assertRaises(account.LoginError) as raised: client.request(account.POLL_URL)
        self.assertNotIn('secret', str(raised.exception))

    @unittest.skipUnless(os.name == 'nt', 'Windows DPAPI')
    def test_encrypted_roundtrip_logout_and_download_adapter(self):
        with tempfile.TemporaryDirectory() as folder:
            store = account.SessionStore(folder)
            store.accept(cookies(), remember=True)
            self.assertNotIn(b'synthetic-test-session', store.path.read_bytes())
            restored = account.SessionStore(folder)
            self.assertEqual(list(restored.snapshot())[0].value, 'synthetic-test-session')
            with patch.object(account, 'STORE', restored), patch.object(account.LoginClient, 'verify', return_value={'logged_in': True}):
                old_signature = core.quality_signature(URL, cookie_file=account.SESSION_REF)
                jar = core.load_cookie_file(account.SESSION_REF, URL)
                self.assertEqual(list(jar)[0].name, 'SESSDATA')
                with self.assertRaises(account.LoginError): account.download_jar('https://example.org/')
                restored.logout()
                self.assertNotEqual(old_signature, core.quality_signature(URL, cookie_file=account.SESSION_REF))
                with self.assertRaises(account.LoginError): core.load_cookie_file(account.SESSION_REF, URL)
            self.assertFalse(store.path.exists())

    def test_memory_only_creates_no_file(self):
        with tempfile.TemporaryDirectory() as folder:
            store = account.SessionStore(folder)
            store.accept(cookies(), remember=False)
            self.assertFalse(store.path.exists())
            self.assertTrue(list(store.snapshot()))

    def test_revoked_session_rejected_before_download_and_validation_cached(self):
        with tempfile.TemporaryDirectory() as folder:
            store = account.SessionStore(folder)
            store.accept(cookies())
            with patch.object(account.LoginClient, 'verify', return_value={'logged_in': False}):
                with self.assertRaises(account.LoginError): store.validated_snapshot()
            with patch.object(account.LoginClient, 'verify', return_value={'logged_in': True}) as verify:
                store.validated_snapshot()
                store.validated_snapshot()
                verify.assert_called_once()


class PolicyTests(unittest.TestCase):
    def test_actionable_errors_are_not_retried_but_network_is(self):
        for error, action in [('Cookie expired', 'login'), ('VIP permission', 'permission'),
                              ('No space left on device', 'space'), ('video removed', 'source'), ('画质已变化', 'quality')]:
            policy = batch.failure_policy(error)
            self.assertFalse(policy['retryable'])
            self.assertEqual(policy['action'], action)
        self.assertTrue(batch.failure_policy('connection timed out')['retryable'])
        self.assertTrue(batch.failure_policy('429 too many requests')['retryable'])

    def test_login_failure_does_not_block_other_items(self):
        calls = []
        def download(url, *args, **kwargs):
            calls.append(url)
            if url == 'a': raise RuntimeError('Cookie expired')
            return {'path': 'test.mp4'}
        with tempfile.TemporaryDirectory() as folder:
            result = batch.run_until_complete([{'key': x, 'url': x} for x in 'ab'], folder, download=download)
        self.assertEqual(calls, ['a', 'b'])
        self.assertEqual((result['success'], result['blocked']), (1, 1))

    def test_space_failure_stops_before_next_video(self):
        calls = []
        def download(url, *args, **kwargs):
            calls.append(url)
            raise OSError('No space left on device')
        with tempfile.TemporaryDirectory() as folder:
            result = batch.run_until_complete([{'key': x, 'url': x} for x in 'ab'], folder, download=download)
        self.assertEqual(calls, ['a'])
        self.assertTrue(result['stopped'])
        self.assertEqual(result['pending'], 1)


class WorkspaceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.path = Path(self.temp.name)
        (self.path / 'settings.json').write_text(json.dumps({'output_dir': str(self.path / 'out'), 'auto_check': False}))
        self.patcher = patch.object(core, 'APP_DIR', self.path)
        self.patcher.start()
        self.root = tk.Tk()
        self.root.withdraw()
        self.app = ui.UnifiedApp(self.root, URL, history_dir=self.path / 'history')

    def tearDown(self):
        self.app.stop.set()
        self.root.destroy()
        self.patcher.stop()
        self.temp.cleanup()

    def parsed(self):
        self.app.pending_raw = URL
        self.app.emit('parsed', {'title': '测试合集', 'reference': URL, 'truncated': False,
            'entries': [{'url': URL, 'title': '视频 A', 'id': 'BV13G41187Au'},
                        {'url': URL+'?p=2', 'title': '视频 B', 'id': 'BV13G41187Au_p2'}]})
        self.app.poll()
        self.root.update_idletasks()

    def test_one_window_and_small_screen(self):
        self.root.geometry('820x620')
        self.root.deiconify()
        self.root.update()
        self.assertFalse(any(isinstance(w, tk.Toplevel) for w in self.root.winfo_children()))
        for widget in [self.app.download_button, self.app.bar, self.app.progress_label, self.app.table]:
            self.assertGreater(widget.winfo_height(), 8)
            self.assertLessEqual(widget.winfo_rooty() + widget.winfo_height(), self.root.winfo_rooty() + self.root.winfo_height())
        self.parsed()
        self.app.show_preview()
        self.root.update()
        self.assertLessEqual(self.app.confirm_button.winfo_rooty()+self.app.confirm_button.winfo_height(), self.root.winfo_rooty()+self.root.winfo_height())

    def test_tab_rapid_switch_and_programmatic_navigation_stay_in_sync(self):
        self.root.deiconify()
        self.root.update()
        for index in (1, 3, 0, 2, 1):
            self.app.tabbar.choose(index, focus=True)
            self.root.update()
        self.assertEqual(self.app.pages.select(), str(self.app.account_page))
        self.assertEqual(self.app.tabbar.active, 1)
        self.app.pages.select(self.app.download_page)
        self.root.update()
        self.assertEqual(self.app.tabbar.active, 0)
        self.app.tabbar.sync(animate=False)
        self.assertIsNone(self.app.tabbar.timer)
        self.assertTrue(self.app.remember_login.get())

    def test_preview_no_download_until_confirm_and_unknown_allowed(self):
        self.parsed()
        with patch.object(self.app, 'worker') as worker:
            self.app.start()
            worker.assert_not_called()
            self.assertIn('画质未确认：2', self.app.preview_text.get())
            self.app.commit_preview()
            worker.assert_called_once()

    def test_scope_change_invalidates_preview(self):
        self.parsed()
        self.app.start()
        self.app.scope_text.set('展开所在合集')
        self.app.scope_changed()
        self.assertFalse(self.app.list_is_current())
        self.assertFalse(self.app.preview_keys)

    def test_audio_mode_resets_video_completion_and_restores_separate_history(self):
        self.parsed()
        original_folder=self.app.folder
        self.app.download_states['0']='已完成'
        self.app.start()
        self.app.download_mode.set('仅音频（原始音质）')
        self.app.mode_changed()
        self.assertFalse(self.app.preview_keys)
        self.assertEqual(self.app.folder, original_folder/'仅音频')
        self.assertFalse(self.app.download_states)
        self.app.show_preview()
        self.assertIn('仅音频',self.app.preview_text.get())
        self.assertIn('不下载视频流',self.app.preview_text.get())
        self.app.persist_task()
        self.app.configure_download_mode(False)
        self.app.restore_task(force=True)
        self.assertTrue(self.app.audio_mode())
        self.assertEqual(self.app.folder,original_folder/'仅音频')

    def test_batch_statistics_do_not_follow_selection_after_start(self):
        self.parsed()
        self.app.task_keys = ['0', '1']
        self.app.batch_locked = True
        self.app.download_states['0'] = '已完成'
        self.app.table.selection_set('1')
        self.app.selection_changed()
        self.assertEqual(self.app.task_keys, ['0', '1'])
        self.app.update_total()
        self.assertIn('已完成 1 / 2', self.app.total_text.get())

    def test_stages_and_progress_keep_metrics(self):
        self.parsed()
        self.app.emit('progress', {'percent': 42.5, 'downloaded': 425, 'total': 1000, 'speed': 100, 'eta': 6, 'part': '视频'})
        self.app.poll()
        self.assertIn('42.5%', self.app.file_progress.get())
        self.assertIn('预计剩余', self.app.file_progress.get())
        self.app.emit('stage', '合并 / 处理音视频')
        self.app.poll()
        self.assertEqual(str(self.app.bar.cget('mode')), 'indeterminate')
        self.app.emit('item', {'key': '0', 'state': '已完成'})
        self.app.poll()
        self.assertEqual(self.app.bar['value'], 100)

    def test_qr_is_rendered_in_account_page_and_cancelled_result_discarded(self):
        self.app.workspace_event('account_qr', 'https://account.bilibili.com/test')
        self.assertIsNotNone(self.app.qr_label.image)
        self.app.stop.set()
        with patch.object(account.STORE, 'accept') as accept:
            self.app.workspace_event('account_done', {'jar': cookies(), 'profile': {'logged_in': True}})
            accept.assert_not_called()
        self.assertIsNone(self.app.qr_label.image)

    def test_successful_login_reaches_downloader_without_cookie_file(self):
        store = account.SessionStore(self.path/'account')
        profile = {'logged_in': True, 'name': 'Test Account', 'checked_at': '12:00:00'}
        self.app.login_remember_requested = False
        with patch.object(account, 'STORE', store), patch.object(account, 'LoginClient') as client:
            client.return_value.generate.return_value = {'url': 'https://account.bilibili.com/test', 'key': 'synthetic-key'}
            client.return_value.poll.return_value = ('success', profile)
            client.return_value.jar = cookies()
            with patch.object(self.app.stop, 'wait', return_value=False):
                result = self.app.login_flow()
            self.app.workspace_event('account_done', result)
            self.assertEqual(self.app.cookie_file, account.SESSION_REF)
            self.assertIn('已登录', self.app.cookie_label.get())
            self.assertEqual(self.app.login_state, 'success')
            self.assertIn('登录成功', self.app.login_heading.get())
            self.assertIn('Test Account', self.app.login_text.get())
            self.assertEqual(self.app.return_download_button.winfo_manager(), 'pack')
            self.assertIsNone(self.app.qr_label.image)
            jar = core.load_cookie_file(self.app.cookie_file, URL)
            self.assertEqual(list(jar)[0].name, 'SESSDATA')
            self.assertFalse(store.path.exists())
            self.assertIsNone(core.read_settings()['cookie_file'])
            self.assertNotIn('synthetic-test-session', (self.path/'settings.json').read_text())

    def test_login_failure_never_leaves_success_card_or_return_button(self):
        self.app.set_account_profile({'logged_in': True, 'name': 'Demo', 'checked_at': '12:00'})
        self.app.operation = 'verify_login'
        self.app.workspace_event('error', '网络连接异常')
        self.assertEqual(self.app.login_state, 'error')
        self.assertIn('尚未验证成功', self.app.login_heading.get())
        self.assertFalse(self.app.return_download_button.winfo_manager())
        self.app.set_account_profile({'logged_in': False})
        self.assertEqual(self.app.login_state, 'expired')
        self.app.workspace_event('account_done', {'expired': True})
        self.assertEqual(self.app.login_heading.get(), '二维码已过期')

    def test_scan_confirm_and_success_visible_on_small_screen(self):
        self.root.geometry('820x620')
        self.root.deiconify()
        self.app.show_account()
        self.app.workspace_event('account_qr', 'https://account.bilibili.com/test')
        self.app.workspace_event('account_wait', '已扫码，请在手机上确认登录。')
        self.assertEqual(self.app.login_state, 'confirm')
        self.assertIsNotNone(self.app.qr_label.image)
        self.root.update()
        for widget in (self.app.qr_label, self.app.login_button, self.app.cookie_button):
            self.assertLessEqual(widget.winfo_rooty()+widget.winfo_height(), self.root.winfo_rooty()+self.root.winfo_height())
        self.app.set_account_profile({'logged_in': True, 'name': 'Demo', 'checked_at': '12:00'})
        self.root.update()
        self.assertTrue(self.app.return_download_button.winfo_ismapped())
        self.app.return_download_button.invoke()
        self.assertEqual(self.app.pages.select(), str(self.app.download_page))


if __name__ == '__main__':
    unittest.main(verbosity=2)
