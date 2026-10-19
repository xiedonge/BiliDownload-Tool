@echo off
chcp 65001 >nul
start "" "chrome://extensions/"
echo.
echo Chrome 扩展管理页已打开。请开启“开发者模式”并点击“加载已解压的扩展程序”。
echo 在文件夹选择窗口中选择下面这个文件夹：
echo %~dp0src
echo.
pause
