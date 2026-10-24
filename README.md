# B站视频下载工具（Windows）

Windows 10/11 x64 桌面下载工具。仓库包含应用源码、安装脚本和 Chrome Cookie 插件源码；普通用户建议从 [GitHub Releases](https://github.com/xiedonge/BiliDownload-Tool/releases) 下载 Windows 一键安装包。

## 1.1.1

新增工具内 B 站扫码登录、下载前确认预览、独立账号/历史/设置页面、小屏布局、阶段进度，以及按失败原因处理重试。使用方式见[新版使用说明](新版使用说明.md)。扫码使用 B 站网页登录接口，不需要插件；该接口由网站维护，可能随平台变化。

本版支持工具内扫码登录并默认加密记住登录，登录成功时显示账号、验证时间和明显的绿色提示。标签页增加悬停反馈、滑动指示条与键盘切换。用户已在本机扫码并确认工具显示“已登录：昵称”；登录后最高画质与实际下载仍取决于视频和账号权限，尚未完成本版真实下载验收。

## 源码目录

- `工具/`：主界面、批量下载、视频下载与缓存管理 Python 源码。
- `安装程序/`：Windows 安装脚本、依赖清单和第三方组件来源/许可说明。
- `浏览器Cookie插件/`：Chrome 扩展源码及安装、隐私说明。
- `安装并启动.cmd`、`首次运行说明.txt`：Windows 启动入口和使用说明。
- `tests/`：登录、界面状态与下载核心回归测试。
- `scripts/`：无凭据的登录服务探测和临时示例界面截图脚本。

Python 安装程序和依赖轮子不重复存入 Git 历史。完整安装包携带 Python 安装程序、Python 依赖轮子以及 FFmpeg/FFprobe 原始发布包。安装阶段无需再下载这些组件，FFmpeg 安装前会校验固定 SHA-256。源码构建需要自行准备打包脚本指定的依赖文件。

## 本地开发

安装 Python 3.14，再运行 `python -m pip install -r 安装程序/requirements.txt`。运行时还需要 FFmpeg/FFprobe。Release 一键安装包是受支持的普通用户安装方式。

在仓库目录运行 `python -X utf8 -m unittest discover -s tests -v`。Windows 图形测试需要可用桌面。`python -X utf8 scripts/check_login_service.py` 是显式联网探测，不会输出二维码密钥或凭据，也不代表真实账号登录验收。

## 隐私与使用范围

Cookie 是登录凭据。“记住登录”默认勾选，扫码成功后用 Windows 当前用户 DPAPI 加密保存到 `%LOCALAPPDATA%\BiliDownload\account\session.bin`；扫码前取消勾选则只在本次运行内使用。不读取浏览器数据库，不保存明文 Cookie。兼容导入只读本机指定文件。仓库忽略会话、个人设置、历史记录、下载文件和临时文件。

请仅下载自己有权保存的内容，并遵守 Bilibili 服务条款及适用法律。可用画质取决于视频、账号权限和平台策略。

## 第三方组件与校验

安装包内的第三方组件来源和许可见 `安装程序/` 与插件目录中的说明。使用 Release 附件 `SHA256SUMS.txt` 校验 ZIP 完整性；SHA-256 用于校验传输完整性，不是数字签名。
