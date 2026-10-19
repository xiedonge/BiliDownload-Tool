# B站视频下载工具（Windows）

Windows 10/11 x64 桌面下载工具。仓库包含应用源码、安装脚本和 Chrome Cookie 插件源码；普通用户建议从 [GitHub Releases](https://github.com/xiedonge/BiliDownload-Tool/releases) 下载 Windows 一键安装包。

## 源码目录

- `工具/`：主界面、批量下载、视频下载与缓存管理 Python 源码。
- `安装程序/`：Windows 安装脚本、依赖清单和第三方组件来源/许可说明。
- `浏览器Cookie插件/`：Chrome 扩展源码及安装、隐私说明。
- `安装并启动.cmd`、`首次运行说明.txt`：Windows 启动入口和使用说明。

Python 安装程序体积较大，不重复存入 Git 历史；完整一键安装目录请从 Releases ZIP 获取。安装器会安装随包提供的 Python，并下载 yt-dlp、FFmpeg/FFprobe，下载后校验 FFmpeg 压缩包 SHA-256。

## 本地开发

安装 Python 3.14，再运行 `python -m pip install -r 安装程序/requirements.txt`。运行时还需要 FFmpeg/FFprobe。Release 一键安装包是受支持的普通用户安装方式。

## 隐私与使用范围

Cookie 是登录凭据。请只在自己的电脑上使用 Cookie 插件，不要把导出的 Cookie 提交到仓库或发给他人。仓库忽略 Cookie 文件、个人设置、历史记录、下载文件和未完成的临时文件。

请仅下载自己有权保存的内容，并遵守 Bilibili 服务条款及适用法律。可用画质取决于视频、账号权限和平台策略。

## 第三方组件与校验

安装包内的第三方组件来源和许可见 `安装程序/` 与插件目录中的说明。使用 Release 附件 `SHA256SUMS.txt` 校验 ZIP 完整性；SHA-256 用于校验传输完整性，不是数字签名。
