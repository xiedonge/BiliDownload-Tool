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
$FfmpegUrl = 'https://github.com/GyanD/codexffmpeg/releases/download/9.0.2/ffmpeg-9.0.2-full_build.zip'
$FfmpegSha256 = '759D0A9831C436A0EB331AD36F236C06CB04AAA0005DA46571F0E9D3D9206F6B'

if (-not (Test-Path -LiteralPath $Payload -PathType Leaf)) { throw '安装包缺少随附的 Python 安装文件。请重新解压 ZIP。' }
[System.IO.Directory]::CreateDirectory($ToolDir) | Out-Null
[System.IO.Directory]::CreateDirectory($PythonDir) | Out-Null

foreach ($name in @('video_downloader.py','batch_download.py','unified_app.py','cache_manager.py')) {
  $from = Join-Path (Split-Path $PSScriptRoot -Parent) (Join-Path '工具' $name)
  if (-not (Test-Path -LiteralPath $from -PathType Leaf)) { throw "安装包缺少工具文件：$name" }
  [System.IO.File]::Copy($from,(Join-Path $ToolDir $name),$true)
}
foreach ($license in @($FfmpegLicense,$PythonLicense)) {
  if (-not (Test-Path -LiteralPath $license -PathType Leaf)) { throw '安装包缺少第三方许可说明。' }
  [System.IO.File]::Copy($license,(Join-Path $ToolDir (Split-Path $license -Leaf)),$true)
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
& $PythonExe -m pip install --disable-pip-version-check -r (Join-Path $PSScriptRoot 'requirements.txt')
if ($LASTEXITCODE -ne 0) { throw 'yt-dlp 安装失败。请检查网络后重新运行安装程序。' }

if (-not (Test-Path -LiteralPath (Join-Path $FfmpegDir 'ffmpeg.exe') -PathType Leaf) -or
    -not (Test-Path -LiteralPath (Join-Path $FfmpegDir 'ffprobe.exe') -PathType Leaf)) {
  $tempRoot = Join-Path ([System.IO.Path]::GetTempPath()) ('BiliDownload-FFmpeg-' + [guid]::NewGuid().ToString('N'))
  [System.IO.Directory]::CreateDirectory($tempRoot) | Out-Null
  $archive = Join-Path $tempRoot 'ffmpeg.zip'
  try {
    Write-Host '正在下载 FFmpeg/FFprobe（约 109 MB），并核对官方发布包校验值…'
    Invoke-WebRequest -Uri $FfmpegUrl -OutFile $archive
    $actualHash = (Get-FileHash -LiteralPath $archive -Algorithm SHA256).Hash
    if ($actualHash -ne $FfmpegSha256) { throw 'FFmpeg 压缩包校验失败，已停止安装。' }
    Add-Type -AssemblyName System.IO.Compression.FileSystem
    $zip = [System.IO.Compression.ZipFile]::OpenRead($archive)
    try {
      $expected = @{
        'ffmpeg-9.0.2-full_build/bin/ffmpeg.exe' = 'ffmpeg.exe'
        'ffmpeg-9.0.2-full_build/bin/ffprobe.exe' = 'ffprobe.exe'
      }
      [System.IO.Directory]::CreateDirectory($FfmpegDir) | Out-Null
      foreach ($entry in $zip.Entries) {
        $key = $entry.FullName.Replace('\','/')
        if ($expected.ContainsKey($key)) {
          $destination = Join-Path $FfmpegDir $expected[$key]
          $input = $entry.Open()
          try {
            $output = [System.IO.File]::Create($destination)
            try { $input.CopyTo($output) } finally { $output.Dispose() }
          } finally { $input.Dispose() }
        }
      }
    } finally { $zip.Dispose() }
    foreach ($name in @('ffmpeg.exe','ffprobe.exe')) {
      if (-not (Test-Path -LiteralPath (Join-Path $FfmpegDir $name) -PathType Leaf)) { throw "FFmpeg 包缺少 $name" }
    }
  } finally {
    if (Test-Path -LiteralPath $tempRoot -PathType Container) {
      $tempPrefix = [System.IO.Path]::GetFullPath([System.IO.Path]::GetTempPath())
      $resolvedTemp = [System.IO.Path]::GetFullPath($tempRoot)
      if (-not $resolvedTemp.StartsWith($tempPrefix,[System.StringComparison]::OrdinalIgnoreCase) -or
          [System.IO.Path]::GetFileName($resolvedTemp) -notlike 'BiliDownload-FFmpeg-*') {
        throw '临时下载目录位置异常，已停止清理。'
      }
      Get-ChildItem -LiteralPath $resolvedTemp -Force | Remove-Item -Force -Recurse
      Remove-Item -LiteralPath $resolvedTemp -Force
    }
  }
}

$launcher = Join-Path $InstallDir '下载视频.cmd'
$pythonw = Join-Path $PythonDir 'pythonw.exe'
$gui = Join-Path $ToolDir 'video_downloader.py'
$launcherBody = "@echo off`r`nsetlocal`r`nset `"PATH=$FfmpegDir;%PATH%`"`r`nstart `"`" `"$pythonw`" -X utf8 `"$gui`"`r`n"
[System.IO.File]::WriteAllText($launcher,$launcherBody,[System.Text.Encoding]::ASCII)

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
