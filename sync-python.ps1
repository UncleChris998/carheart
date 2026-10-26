# 把 carheart 的 python 包同步进 App 资源目录。
# 用法：在本目录下执行   powershell -File sync-python.ps1

$ErrorActionPreference = 'Stop'

$src = Join-Path $PSScriptRoot '..\car-heart\carheart'
$dst = Join-Path $PSScriptRoot 'Resources\python\carheart'

if (-not (Test-Path $src)) { throw "找不到源目录：$src" }

if (Test-Path $dst) { Remove-Item $dst -Recurse -Force }
New-Item -ItemType Directory -Path $dst -Force | Out-Null

Copy-Item -Path (Join-Path $src '*') -Destination $dst -Recurse -Force
Get-ChildItem $dst -Recurse -Directory -Filter '__pycache__' |
    Remove-Item -Recurse -Force -ErrorAction SilentlyContinue
Get-ChildItem $dst -Recurse -File -Filter '*.pyc' |
    Remove-Item -Force -ErrorAction SilentlyContinue

$n = (Get-ChildItem $dst -Recurse -File -Filter '*.py' | Measure-Object).Count
$mb = [math]::Round(((Get-ChildItem $dst -Recurse -File | Measure-Object Length -Sum).Sum / 1MB), 2)
Write-Host "已同步 $n 个 .py 文件，共 $mb MB -> $dst"
