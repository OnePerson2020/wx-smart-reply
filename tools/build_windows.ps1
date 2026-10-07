# Windows 打包脚本：在 PowerShell 里执行
#   powershell -ExecutionPolicy Bypass -File tools\build_windows.ps1
#
# 产物：dist\WxReply\WxReply.exe（免安装，双击即用；拷走整个 dist\WxReply 文件夹即可分发）

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
Set-Location $root

Write-Host "== 1/5 检查 Python" -ForegroundColor Cyan
python --version
if ($LASTEXITCODE -ne 0) { throw "没有找到 python，请先装 Python 3.10+（勾选 Add to PATH）" }

Write-Host "== 2/5 建虚拟环境并安装依赖" -ForegroundColor Cyan
if (-not (Test-Path ".venv")) { python -m venv .venv }
& .\.venv\Scripts\python.exe -m pip install --upgrade pip
& .\.venv\Scripts\python.exe -m pip install -r requirements.txt pytest pyinstaller

Write-Host "== 3/5 跑测试" -ForegroundColor Cyan
& .\.venv\Scripts\python.exe -m pytest -q

Write-Host "== 4/5 生成图标 + 自检" -ForegroundColor Cyan
& .\.venv\Scripts\python.exe tools\make_icon.py
& .\.venv\Scripts\python.exe -m wxreply selftest

Write-Host "== 5/5 打包" -ForegroundColor Cyan
& .\.venv\Scripts\python.exe -m PyInstaller --noconfirm --clean wxreply.spec

Write-Host ""
Write-Host "完成：$root\dist\WxReply\WxReply.exe" -ForegroundColor Green
Write-Host "第一次运行请先在「监控」页填：已解密数据库目录（或 微信数据目录 + keys.json），然后点「保存并应用」。" -ForegroundColor Yellow
