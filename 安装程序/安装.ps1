$ErrorActionPreference = 'Stop'
$InstallDir = Join-Path $env:LOCALAPPDATA 'Programs\BiliDownload'
$ToolDir = Join-Path $InstallDir '工具'
$PythonDir = Join-Path $InstallDir 'runtime'
$PythonExe = Join-Path $PythonDir 'python.exe'
$FfmpegDir = Join-Path $ToolDir 'bin'
$Payload = Join-Path $PSScriptRoot 'python-3.14.8-amd64.exe'
$FfmpegLicense = Join-Path $PSScriptRoot 'FFmpeg 来源与许可.txt'
$PythonLicense = Join-Path $PSScriptRoot 'Python 来源与许可.txt'
$CookieExtensionSource = Join-Path (Split-Path $PSScriptRoot -Parent) '浏览器Cookie插件\src'
$FfmpegPayload = Join-Path $PSScriptRoot 'ffmpeg-9.0.2-full_build.zip'

if (-not (Test-Path -LiteralPath $Payload -PathType Leaf)) { throw '安装包缺少随附的 Python 安装文件。请重新解压 ZIP。' }
if (-not (Test-Path -LiteralPath $FfmpegPayload -PathType Leaf)) { throw '安装包缺少随附的 FFmpeg 文件。请完整解压离线安装包。' }
[System.IO.Directory]::CreateDirectory($ToolDir) | Out-Null
[System.IO.Directory]::CreateDirectory($PythonDir) | Out-Null

foreach ($name in @('video_downloader.py','batch_download.py','unified_app.py','cache_manager.py','workspace_view.py','account_login.py')) {
  $from = Join-Path (Split-Path $PSScriptRoot -Parent) (Join-Path '工具' $name)
  if (-not (Test-Path -LiteralPath $from -PathType Leaf)) { throw "安装包缺少工具文件：$name" }
  [System.IO.File]::Copy($from,(Join-Path $ToolDir $name),$true)
}
foreach ($license in @($FfmpegLicense,$PythonLicense)) {
  if (-not (Test-Path -LiteralPath $license -PathType Leaf)) { throw '安装包缺少第三方许可说明。' }
  [System.IO.File]::Copy($license,(Join-Path $ToolDir (Split-Path $license -Leaf)),$true)
}
$assets = Join-Path (Split-Path $PSScriptRoot -Parent) '工具\assets'
if (Test-Path -LiteralPath $assets -PathType Container) {
  $assetTarget = Join-Path $ToolDir 'assets'
  [System.IO.Directory]::CreateDirectory($assetTarget) | Out-Null
  Get-ChildItem -LiteralPath $assets -File | ForEach-Object {
    [System.IO.File]::Copy($_.FullName,(Join-Path $assetTarget $_.Name),$true)
  }
}
if (-not (Test-Path -LiteralPath (Join-Path $CookieExtensionSource 'manifest.json') -PathType Leaf)) {
  throw '安装包缺少随附的 Chrome Cookie 扩展源码。请重新解压 ZIP。'
}
$cookieExtensionDir = Join-Path $InstallDir '浏览器Cookie插件'
[System.IO.Directory]::CreateDirectory($cookieExtensionDir) | Out-Null
Copy-Item -LiteralPath $CookieExtensionSource -Destination (Join-Path $cookieExtensionDir 'src') -Recurse -Force
foreach ($name in @('安装说明.txt','打开扩展管理.cmd','LICENSE','README.md','privacy-policy.md','来源校验.txt')) {
  $source = Join-Path (Split-Path $CookieExtensionSource -Parent) $name
  if (Test-Path -LiteralPath $source -PathType Leaf) {
    [System.IO.File]::Copy($source,(Join-Path $cookieExtensionDir $name),$true)
  }
}

if (-not (Test-Path -LiteralPath $PythonExe -PathType Leaf)) {
  Write-Host '正在安装随包提供的 Python 3.14.8（仅安装到当前用户目录）…'
  $arguments = @('/quiet','InstallAllUsers=0',"TargetDir=$PythonDir",'Include_pip=1','Include_tcltk=1',
    'Include_test=0','Include_doc=0','Include_launcher=0','Include_tools=0','Shortcuts=0','PrependPath=0',
    'Include_exe=1','Include_dev=0')
  $process = Start-Process -FilePath $Payload -ArgumentList $arguments -Wait -PassThru -WindowStyle Hidden
  if ($process.ExitCode -notin @(0,3010)) { throw "Python 安装失败，代码：$($process.ExitCode)" }
}
if (-not (Test-Path -LiteralPath $PythonExe -PathType Leaf)) { throw 'Python 安装后没有找到运行文件。' }
& $PythonExe -c 'import tkinter, ssl' *> $null
if ($LASTEXITCODE -ne 0) { throw '随包 Python 缺少图形界面组件，请重试安装。' }

Write-Host '正在安装下载器依赖…'
$wheelhouse = Join-Path $PSScriptRoot 'wheels'
if (Test-Path -LiteralPath $wheelhouse -PathType Container) {
  & $PythonExe -m pip install --disable-pip-version-check --no-index --find-links $wheelhouse -r (Join-Path $PSScriptRoot 'requirements.txt')
} else {
  & $PythonExe -m pip install --disable-pip-version-check -r (Join-Path $PSScriptRoot 'requirements.txt')
}
if ($LASTEXITCODE -ne 0) { throw '下载器或二维码组件安装失败。请检查网络后重新运行安装程序。' }

Write-Host '正在校验并安装随包提供的 FFmpeg / FFprobe…'
& (Join-Path $PSScriptRoot '安装FFmpeg.ps1') -ArchivePath $FfmpegPayload -DestinationDir $FfmpegDir

$launcher = Join-Path $InstallDir '下载视频.cmd'
$pythonw = Join-Path $PythonDir 'pythonw.exe'
$gui = Join-Path $ToolDir 'video_downloader.py'
$launcherBody = "@echo off`r`nchcp 65001 >nul`r`nsetlocal`r`nset `"PATH=$FfmpegDir;%PATH%`"`r`nstart `"`" `"$pythonw`" -X utf8 `"$gui`"`r`n"
[System.IO.File]::WriteAllText($launcher,$launcherBody,(New-Object System.Text.UTF8Encoding($false)))

Write-Host '正在创建开始菜单快捷方式…'
$menu = Join-Path $env:APPDATA 'Microsoft\Windows\Start Menu\Programs'
[System.IO.Directory]::CreateDirectory($menu) | Out-Null
$shortcut = Join-Path $menu 'B 站视频下载工具.lnk'
$shell = New-Object -ComObject WScript.Shell
$link = $shell.CreateShortcut($shortcut)
$link.TargetPath = $launcher
$link.WorkingDirectory = $InstallDir
$link.Save()

Write-Host "安装完成：$InstallDir"
Write-Host '正在启动视频下载工具…'
Start-Process -FilePath $launcher
