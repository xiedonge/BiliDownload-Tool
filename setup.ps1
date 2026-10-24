param([switch]$NoLaunch)
$ErrorActionPreference = 'Stop'
$env:PYTHONUTF8 = '1'
[Console]::OutputEncoding = New-Object System.Text.UTF8Encoding($false)
$transcribing = $false
$result = 1
try {
  $logDirectory = Join-Path $env:LOCALAPPDATA 'BiliDownload\logs'
  [IO.Directory]::CreateDirectory($logDirectory) | Out-Null
  $logPath = Join-Path $logDirectory ('install-' + (Get-Date -Format 'yyyyMMdd-HHmmss') + '.log')
  Start-Transcript -Path $logPath -Force | Out-Null
  $transcribing = $true
  Write-Host "安装日志：$logPath"
  $installer = Join-Path $PSScriptRoot '安装程序\安装.ps1'
  if (-not (Test-Path -LiteralPath $installer -PathType Leaf)) {
    throw '没有找到完整安装文件。请先右键 ZIP → 全部解压，再打开解压后的文件夹运行“安装并启动.cmd”。不要直接在压缩包内双击。'
  }
  & $installer -NoLaunch:$NoLaunch
  $result = 0
  Write-Host '安装已完成。可以从开始菜单打开“B 站视频下载工具”。' -ForegroundColor Green
} catch {
  Write-Host '安装未完成，窗口不会自动关闭。' -ForegroundColor Red
  Write-Host $_.Exception.Message -ForegroundColor Red
  Write-Host $_.InvocationInfo.PositionMessage
  if ($logPath) { Write-Host "请保留此日志用于排查：$logPath" }
} finally {
  if ($transcribing) { Stop-Transcript | Out-Null }
}
exit $result
