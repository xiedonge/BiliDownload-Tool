# B站视频下载工具（Windows）

Windows 10/11 x64 的桌面下载工具。安装包请从本仓库的 GitHub Releases 下载：`BiliDownload-Tool-Windows-x64.zip`。

## 安装

1. 下载并解压 Release 中的 ZIP。
2. 运行根目录的“安装并启动.cmd”。
3. 首次安装需要网络：安装器会安装随包提供的 Python，并下载 yt-dlp、FFmpeg/FFprobe；FFmpeg 压缩包会先校验 SHA-256。
4. 视频默认保存到当前 Windows 用户的 `Videos\BiliDownload`，也可以在主界面调整保存目录。

Cookie 导出扩展源码和安装说明随包附带。要获取账号可访问的画质，请在自己的 Chrome 中手动加载该扩展并自行导出 Cookie。Cookie 相当于登录凭据，不要提交到仓库或发给他人。

## 使用范围

请仅下载自己有权保存的内容，并遵守 Bilibili 服务条款及适用法律。画质与下载可用性取决于视频、账号权限和平台策略。

## 第三方组件

安装包内含 Python 3.14.8 Windows 安装程序，附带的 Chrome Cookie 扩展源码按其 MIT 许可分发。安装器会从 Gyan 下载 FFmpeg/FFprobe 并核对 SHA-256；yt-dlp 从 PyPI 安装。各组件的来源与许可说明均在安装包内。

## 校验下载

使用 Release 附件 `SHA256SUMS.txt` 校验 ZIP 完整性。SHA-256 用于校验传输完整性，不是数字签名。
