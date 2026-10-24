param(
  [Parameter(Mandatory=$true)][string]$ArchivePath,
  [Parameter(Mandatory=$true)][string]$DestinationDir
)
$ErrorActionPreference = 'Stop'
$expectedHash = '759D0A9831C436A0EB331AD36F236C06CB04AAA0005DA46571F0E9D3D9206F6B'
if (-not (Test-Path -LiteralPath $ArchivePath -PathType Leaf)) {
  throw '安装包缺少随附的 FFmpeg 压缩包，请完整解压离线安装包。'
}
if ((Get-FileHash -LiteralPath $ArchivePath -Algorithm SHA256).Hash -ne $expectedHash) {
  throw '随附 FFmpeg 压缩包校验失败，请重新获取完整安装包。'
}
Add-Type -AssemblyName System.IO.Compression.FileSystem
$zip = [System.IO.Compression.ZipFile]::OpenRead($ArchivePath)
try {
  $required = @('ffmpeg.exe','ffprobe.exe')
  foreach ($name in $required) {
    $entry = $zip.GetEntry('ffmpeg-9.0.2-full_build/bin/' + $name)
    if ($null -eq $entry -or $entry.Length -le 0) { throw "随附 FFmpeg 包缺少 $name" }
  }
  [IO.Directory]::CreateDirectory($DestinationDir) | Out-Null
  foreach ($name in $required) {
    $entry = $zip.GetEntry('ffmpeg-9.0.2-full_build/bin/' + $name)
    $destination = Join-Path $DestinationDir $name
    $temporary = Join-Path $DestinationDir ($name + '.' + [guid]::NewGuid().ToString('N') + '.tmp')
    try {
      [System.IO.Compression.ZipFileExtensions]::ExtractToFile($entry, $temporary)
      Move-Item -LiteralPath $temporary -Destination $destination -Force
    } finally {
      if (Test-Path -LiteralPath $temporary) { Remove-Item -LiteralPath $temporary -Force }
    }
  }
  foreach ($name in @('LICENSE','README.txt')) {
    $entry = $zip.GetEntry('ffmpeg-9.0.2-full_build/' + $name)
    if ($null -eq $entry) { throw "随附 FFmpeg 包缺少 $name" }
    [System.IO.Compression.ZipFileExtensions]::ExtractToFile($entry, (Join-Path $DestinationDir ('FFmpeg-' + $name)), $true)
  }
} finally { $zip.Dispose() }
foreach ($name in @('ffmpeg.exe','ffprobe.exe')) {
  $executable = Join-Path $DestinationDir $name
  & $executable -version *> $null
  if ($LASTEXITCODE -ne 0) { throw "$name 运行检查失败，请确认系统为 Windows 10/11 x64。" }
}
Write-Host 'FFmpeg / FFprobe 已从随包文件安装并通过运行检查，无需联网下载。'
