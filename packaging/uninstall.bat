@echo off
chcp 65001 >nul
title Vermes
echo  正在停止 Vermes...
"%~dp0vermes.exe" gateway stop 2>nul
echo  Vermes 已停止。
