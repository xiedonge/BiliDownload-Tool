import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]


@unittest.skipUnless(os.name == 'nt', 'Windows cmd.exe')
class LauncherTests(unittest.TestCase):
    def run_launcher(self, folder):
        env = dict(os.environ, LOCALAPPDATA=str(folder/'profile'))
        return subprocess.run(['cmd.exe', '/d', '/c', str(folder/'安装并启动.cmd')],
            input=b'\r\n', stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            env=env, timeout=30, creationflags=subprocess.CREATE_NO_WINDOW)

    def prepare(self, folder):
        folder.mkdir()
        shutil.copyfile(ROOT/'安装并启动.cmd', folder/'安装并启动.cmd')
        shutil.copyfile(ROOT/'setup.ps1', folder/'setup.ps1')

    def test_launcher_handles_success_and_failure_in_chinese_space_path(self):
        for failure in (False, True):
            with self.subTest(failure=failure), tempfile.TemporaryDirectory() as temp:
                folder=Path(temp)/'中文 安装目录 (测试)'
                self.prepare(folder)
                (folder/'安装程序').mkdir()
                script="param([switch]$NoLaunch)\nWrite-Host 'INSTALLER_REACHED'\n"
                script += "throw 'SIMULATED_FAILURE'" if failure else "Write-Host 'SUCCESS'"
                (folder/'安装程序'/'安装.ps1').write_text(script, encoding='utf-8-sig')
                result=self.run_launcher(folder)
                self.assertEqual(result.returncode, 1 if failure else 0, result.stdout)
                self.assertIn(b'INSTALLER_REACHED', result.stdout)
                self.assertIn(b'Press any key', result.stdout)
                logs=list((folder/'profile'/'BiliDownload'/'logs').glob('*.log'))
                self.assertEqual(len(logs),1)

    def test_zip_viewer_single_file_extraction_gets_clear_error(self):
        with tempfile.TemporaryDirectory() as temp:
            folder=Path(temp)
            shutil.copyfile(ROOT/'安装并启动.cmd',folder/'安装并启动.cmd')
            result=self.run_launcher(folder)
            self.assertEqual(result.returncode,2)
            self.assertIn(b'extract the ENTIRE ZIP',result.stdout)
            self.assertIn(b'Press any key',result.stdout)

    def test_wrapper_without_payload_creates_diagnostic_log(self):
        with tempfile.TemporaryDirectory() as temp:
            folder=Path(temp)/'incomplete';self.prepare(folder)
            result=self.run_launcher(folder)
            self.assertEqual(result.returncode,1)
            self.assertTrue(list((folder/'profile'/'BiliDownload'/'logs').glob('*.log')))

    def test_batch_files_have_windows_line_endings(self):
        for name in ['安装并启动.cmd','安装程序/安装并启动.cmd']:
            data=(ROOT/name).read_bytes()
            self.assertNotIn(b'\n',data.replace(b'\r\n',b''))
            self.assertFalse(data.startswith(b'\xef\xbb\xbf'))


if __name__ == '__main__':
    unittest.main()
