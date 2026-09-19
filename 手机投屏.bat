@echo off
chcp 65001 >nul
title 手机实时投屏 - scrcpy
cd /d "%~dp0"
echo 正在启动手机实时投屏...
scrcpy --always-on-top --stay-awake --window-title "手机实时投屏"
pause
