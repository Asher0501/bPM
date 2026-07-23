@echo off
:: bePm — AI 辅助项目排期管理系统 · 启动脚本
:: 参考 Nexus 的 zig-cc.bat 风格

:: 设置控制台代码页为 UTF-8（解决中文乱码）
chcp 65001 >nul 2>&1

:: 设置 Python 输出编码为 UTF-8
set PYTHONIOENCODING=utf-8
set PYTHONUTF8=1

:: 如果用户未设置 ANTHROPIC_API_KEY，从 ~/.claude/settings.json 自动获取
:: （backend/config.py 会在启动时自动加载）

echo ============================================
echo   bePm - AI 辅助项目排期管理系统
echo   参考 Nexus 架构模式
echo ============================================
echo.
echo   前端: http://127.0.0.1:48090
echo   API:  http://127.0.0.1:48090/api/health
echo   WebSocket: ws://127.0.0.1:48090/ws/projects/{id}
echo.
echo   按 Ctrl+C 停止服务
echo ============================================
echo.

cd /d "%~dp0backend"
python main.py
