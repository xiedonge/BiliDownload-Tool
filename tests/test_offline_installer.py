"""Offline installer must fail safely before changing existing media tools."""
import os
from pathlib import Path
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / '安装程序' / '安装FFmpeg.ps1'


@unittest.skipUnless(os.name == 'nt', 'Windows PowerShell installer')
class OfflineInstallerTests(unittest.TestCase):
    def invoke(self, archive, destination):
        return subprocess.run(['powershell.exe', '-NoProfile', '-ExecutionPolicy', 'Bypass',
                               '-File', str(SCRIPT), '-ArchivePath', str(archive),
                               '-DestinationDir', str(destination)],
                              stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=30,
                              creationflags=subprocess.CREATE_NO_WINDOW)

    def test_missing_payload_does_not_create_installation(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            result = self.invoke(root/'missing.zip', root/'installed')
            self.assertNotEqual(result.returncode, 0)
            self.assertFalse((root/'installed').exists())

    def test_corrupt_payload_preserves_existing_tool(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            (root/'corrupt.zip').write_bytes(b'incomplete download')
            (root/'installed').mkdir()
            old = root/'installed'/'ffmpeg.exe'
            old.write_bytes(b'previous installed tool')
            result = self.invoke(root/'corrupt.zip', old.parent)
            self.assertNotEqual(result.returncode, 0)
            self.assertEqual(old.read_bytes(), b'previous installed tool')

    def test_installers_parse_in_windows_powershell(self):
        for script in (SCRIPT, SCRIPT.parent/'安装.ps1'):
            self.assertTrue(script.read_bytes().startswith(b'\xef\xbb\xbf'))
            code = "$e=$null;$t=$null;[System.Management.Automation.Language.Parser]::ParseFile($args[0],[ref]$t,[ref]$e)>$null;if($e.Count){exit 1}"
            # Use an explicit quoted literal for the known repository path.
            code = code.replace('$args[0]', "'"+str(script).replace("'", "''")+"'")
            result = subprocess.run(['powershell.exe', '-NoProfile', '-Command', code],
                stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=30,
                creationflags=subprocess.CREATE_NO_WINDOW)
            self.assertEqual(result.returncode, 0)


if __name__ == '__main__':
    unittest.main()
