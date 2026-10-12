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
echo [开始] CRISPR-dCas 靶向 DNA 甲基化与去甲基化编辑 --max 10
echo.

py -u lit_agent.py discover --profile dna-methylation-editing --max 10

if errorlevel 1 (
    echo.
    echo [失败] 脚本异常退出，上方是错误信息。
) else (
    echo.
    echo [完成] 搜完了。
)

pause
