param([string]$PythonPath)
$ErrorActionPreference = 'Stop'
Set-Location -LiteralPath $PSScriptRoot
$env:PYTHONIOENCODING = 'utf-8'
$env:PYTHONUTF8 = '1'
$ownTranscript = $false
try {
    if (!$env:SUCROSE_MV_LOG_STARTED) {
        Start-Transcript -LiteralPath (Join-Path $PSScriptRoot 'setup.log') -Append | Out-Null
        $ownTranscript = $true
    }
    if ((Get-Process -ErrorAction SilentlyContinue | Where-Object { $_.ProcessName -like 'Sucrose*' })) {
        throw 'Sucrose が起動中です。タスクトレイから Sucrose を終了し、もう一度「セットアップを開始.cmd」を開いてください。'
    }
    if (Get-CimInstance Win32_Process | Where-Object { $_.CommandLine -match '(?i)(keep_quality_ready|quality_proxy)\.py' }) {
        throw '既存の壁紙補助プログラムが動作しています。このキットを重ねて設定しないでください。'
    }
    $probe = New-Object System.Net.Sockets.TcpClient
    try {
        $connecting = $probe.BeginConnect('127.0.0.1', 18743, $null, $null)
        if ($connecting.AsyncWaitHandle.WaitOne(1000) -and $probe.Connected) {
            throw '既存の壁紙補助プログラムが動作しています。このキットを重ねて設定しないでください。'
        }
    } finally { $probe.Close() }
    if (!(Get-Command node -ErrorAction SilentlyContinue)) {
        throw 'Node.js が見つかりません。「セットアップを開始.cmd」からやり直してください。'
    }
    if (!$PythonPath) {
        $PythonPath = & py -3 -c 'import sys; print(sys.executable)'
        if ($LASTEXITCODE -ne 0 -or !$PythonPath) { throw 'Python が見つかりません。「セットアップを開始.cmd」からやり直してください。' }
        $PythonPath = $PythonPath.Trim()
    }
    if (!(Test-Path -LiteralPath '.venv\Scripts\python.exe')) {
        Write-Output '壁紙専用の実行環境を準備しています…'
        & $PythonPath -m venv .venv
        if ($LASTEXITCODE -ne 0) { throw '実行環境を作れませんでした。空き容量とフォルダーの書き込み権限を確認してください。' }
    }
    $python = Join-Path $PSScriptRoot '.venv\Scripts\python.exe'
    $pythonw = Join-Path $PSScriptRoot '.venv\Scripts\pythonw.exe'
    Write-Output '必要な動画取得ツールを準備しています。数分かかる場合があります…'
    & $python -m pip install --upgrade 'yt-dlp[default]'
    if ($LASTEXITCODE -ne 0) { throw '動画取得ツールを準備できませんでした。ネット接続を確認し、もう一度お試しください。' }
    Write-Output '登録済みプレイリストを確認しています。動画本体を保存する処理ではありません…'
    & $python setup.py
    if ($LASTEXITCODE -ne 0) { throw 'プレイリストの設定を完了できませんでした。上の日本語メッセージを確認してください。' }
    $ws = New-Object -ComObject WScript.Shell
    function Add-MVShortcut($destination, $file, $argument, $description) {
        $shortcut = $ws.CreateShortcut($destination)
        $shortcut.TargetPath = $pythonw
        $shortcut.Arguments = '"' + (Join-Path $PSScriptRoot $file) + '" ' + $argument
        $shortcut.WorkingDirectory = $PSScriptRoot
        $shortcut.WindowStyle = 7
        $shortcut.Description = $description
        $shortcut.Save()
    }
    Add-MVShortcut (Join-Path ([Environment]::GetFolderPath('Startup')) 'Sucrose MV Helper.lnk') 'keep_quality_ready.py' '' '壁紙の再生補助とプレイリストの定期確認'
    Add-MVShortcut (Join-Path ([Environment]::GetFolderPath('Desktop')) '壁紙動画をシャッフル.lnk') 'wallpaper_control.py' 'shuffle' '別の壁紙動画へシャッフル'
    Add-MVShortcut (Join-Path ([Environment]::GetFolderPath('Desktop')) '壁紙プレイリストを更新.lnk') 'refresh_playlists.py' '--show-result' '登録済みの YouTube プレイリストを確認'
    Start-Process -FilePath $pythonw -ArgumentList ('"' + (Join-Path $PSScriptRoot 'keep_quality_ready.py') + '"') -WindowStyle Hidden
    $ready = $false
    for ($attempt = 0; $attempt -lt 10; $attempt++) {
        try {
            $health = Invoke-WebRequest -Uri 'http://127.0.0.1:18743/health' -UseBasicParsing -TimeoutSec 2
            if ($health.Content -eq 'sucrose-mv-stream-v1') { $ready = $true; break }
        } catch {}
        Start-Sleep -Seconds 1
    }
    if (!$ready) { throw '壁紙の設定は保存しましたが、再生補助を起動できませんでした。setup.log を確認してください。' }
    Write-Output 'セットアップが完了しました！'
    Write-Output 'Sucrose を開き直し、ライブラリで登録した推しの壁紙を選び直してください。'
    Write-Output '「ドキュメント」の sucrose-mv-kit フォルダーは、移動・削除しないでください。'
} finally {
    if ($ownTranscript) { Stop-Transcript | Out-Null }
}

