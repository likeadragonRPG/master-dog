# 這台電腦的定時更新（由 Windows「工作排程器」在平日收盤後呼叫）。
# GitHub 的排程常延遲好幾小時，所以在本機準時跑 update.py，再推上 GitHub。
# 只在專用資料夾（例如 C:\Users\USER\master-dog-bot）執行：每次都會重設成 GitHub 上的最新版本，
# 所以不要在那個資料夾手動改東西。紀錄寫在 %LOCALAPPDATA%\master-dog\update.log。
$ErrorActionPreference = 'Continue'
$repo = Split-Path $PSScriptRoot -Parent
$log = Join-Path $env:LOCALAPPDATA 'master-dog\update.log'
New-Item -ItemType Directory -Force (Split-Path $log) | Out-Null
function Log($m) { "$(Get-Date -Format 'yyyy-MM-dd HH:mm:ss') $m" | Out-File $log -Append -Encoding utf8 }

$py = (Get-Command python -ErrorAction SilentlyContinue).Source
if (-not $py) { $py = Join-Path $env:LOCALAPPDATA 'Programs\Python\Python312\python.exe' }
$env:PYTHONIOENCODING = 'utf-8'
[Console]::OutputEncoding = [Text.Encoding]::UTF8  # 讀 Python 的 UTF-8 輸出，紀錄才不會變亂碼
Set-Location $repo
Log '開始更新'

for ($try = 1; $try -le 3; $try++) {
    git fetch -q origin main
    git reset -q --hard origin/main
    $out = & $py scripts/update.py 2>&1 | Out-String
    $code = $LASTEXITCODE
    ($out -split "`n" | Where-Object { $_ -match 'updated:|otc 20|error|warning|失敗' } | Select-Object -Last 8) | ForEach-Object { Log "  $($_.Trim())" }
    Log "update.py 結束碼 $code（非 0 代表有資料來源沒抓齊，已抓到的照樣保存）"

    git add data.json
    git add -A otc
    git diff --cached --quiet
    if ($LASTEXITCODE -eq 0) { Log '沒有新資料，不用推送'; break }
    git commit -q -m "更新股價 $(Get-Date -Format yyyy-MM-dd)（本機排程）"
    git push -q origin main
    if ($LASTEXITCODE -eq 0) { Log '已推上 GitHub'; break }
    Log "推送失敗（可能 GitHub 剛好有新版本），第 $try 次，重新來過"
    Start-Sleep -Seconds 20
}

# 紀錄只留最後 500 行
$lines = Get-Content $log -Encoding utf8
if ($lines.Count -gt 500) { $lines | Select-Object -Last 500 | Set-Content $log -Encoding utf8 }
