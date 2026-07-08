@echo off
chcp 65001 >nul
cd /d "%~dp0"

echo [检查] 当前目录: %cd%
echo.

:: 检查 .env 是否存在
if not exist ".env" (
    echo [错误] 缺少 .env 文件，请先配置 API Key！
    pause
    exit /b 1
)

:: 检查 Python 是否可用
py --version >nul 2>&1
if errorlevel 1 (
    echo [错误] 找不到 py 启动器，请确认已安装 Python！
    pause
    exit /b 1
)

set PYTHONUTF8=1
echo [开始] 甲基转移酶 x 基因编辑/代谢工程 --max 10
echo.

py -u lit_agent.py discover --profile methyl-edit --max 10

if errorlevel 1 (
    echo.
    echo [失败] 脚本异常退出，上方是错误信息。
) else (
    echo.
    echo [完成] 搜完了。
)

pause
