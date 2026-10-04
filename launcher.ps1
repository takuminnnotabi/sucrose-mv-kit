$ErrorActionPreference = 'Stop'
Add-Type -AssemblyName System.Windows.Forms
[System.Windows.Forms.Application]::EnableVisualStyles()
$nl = [Environment]::NewLine
function Show-Notice([string]$message) {
    [System.Windows.Forms.MessageBox]::Show($message, '推し MV 壁紙のセットアップ', 'OK', 'Information') | Out-Null
}
function Ask-YesNo([string]$message) {
    return [System.Windows.Forms.MessageBox]::Show($message, '推し MV 壁紙のセットアップ', 'YesNo', 'Question') -eq 'Yes'
}
function Refresh-Path {
    $env:Path = [Environment]::GetEnvironmentVariable('Path', 'Machine') + ';' + [Environment]::GetEnvironmentVariable('Path', 'User')
}
function Find-Python {
    $pythonLauncher = Get-Command py -ErrorAction SilentlyContinue
    if ($pythonLauncher) {
        foreach ($selector in @('-3.13', '-3')) {
            try {
                $found = & $pythonLauncher.Source $selector -c 'import sys; print(sys.executable) if sys.version_info >= (3,10) else sys.exit(1)' 2>$null
                if ($LASTEXITCODE -eq 0 -and $found) { return $found.Trim() }
            } catch {}
        }
    }
    foreach ($name in @('python', 'python3')) {
        $candidate = Get-Command $name -ErrorAction SilentlyContinue
        if ($candidate -and $candidate.Source -notlike '*\Microsoft\WindowsApps\*') {
            try {
                $found = & $candidate.Source -c 'import sys; print(sys.executable) if sys.version_info >= (3,10) else sys.exit(1)' 2>$null
                if ($LASTEXITCODE -eq 0 -and $found) { return $found.Trim() }
            } catch {}
        }
    }
    $known = Join-Path $env:LOCALAPPDATA 'Programs\Python\Python313\python.exe'
    if (Test-Path -LiteralPath $known) { return $known }
    return $null
}
function Get-PlaylistCount {
    $library = Join-Path $env:APPDATA 'Sucrose\Library'
    if (!(Test-Path -LiteralPath $library)) { return 0 }
    $count = 0
    foreach ($folder in (Get-ChildItem -LiteralPath $library -Directory)) {
        $infoPath = Join-Path $folder.FullName 'SucroseInfo.json'
        if (!(Test-Path -LiteralPath $infoPath)) { continue }
        $info = Get-Content -LiteralPath $infoPath -Raw -Encoding UTF8 | ConvertFrom-Json
        if ($info.Type -eq 4 -and $info.Source -match '^https?://(www\.|m\.)?youtube\.com/.*[?&]list=[A-Za-z0-9_-]+') { $count++ }
        elseif ($info.Source -eq 'highest.m3u' -and (Test-Path -LiteralPath (Join-Path $folder.FullName 'SucroseMV.source.json'))) { $count++ }
    }
    return $count
}
$transcriptStarted = $false
$logPath = $null
try {
    $kitFiles = @('launcher.ps1', 'install.ps1', 'setup.py', 'refresh_playlists.py', 'wallpaper_control.py',
                  'keep_quality_ready.py', 'quality_proxy.py', 'README.md', 'セットアップを開始.cmd')
    foreach ($file in $kitFiles) {
        if (!(Test-Path -LiteralPath (Join-Path $PSScriptRoot $file))) {
            throw '必要なファイルが揃っていません。ZIP を右クリックして「すべて展開」し、展開したフォルダーの「セットアップを開始.cmd」を開いてください。'
        }
    }
    if (Get-CimInstance Win32_Process | Where-Object { $_.CommandLine -match '(?i)(keep_quality_ready|quality_proxy)\.py' }) {
        throw '既存の壁紙補助プログラムが動作しています。二重のセットアップはできません。普段は「壁紙プレイリストを更新」を使ってください。'
    }
    if (Get-Process -ErrorAction SilentlyContinue | Where-Object { $_.ProcessName -like 'Sucrose*' }) {
        Show-Notice ("Sucrose が起動しています。$nl$nl 登録済みなら、右下のタスクトレイで Sucrose のアイコンを右クリックし、「終了」を選んでください。画面の × だけでは終了しません。$nl$nl 終了したら、もう一度「セットアップを開始.cmd」を開いてください。")
        exit 1
    }
    $probe = New-Object System.Net.Sockets.TcpClient
    try {
        $connecting = $probe.BeginConnect('127.0.0.1', 18743, $null, $null)
        if ($connecting.AsyncWaitHandle.WaitOne(1000) -and $probe.Connected) {
            throw 'この PC では既存の壁紙補助プログラムが動作しています。二重の設定を防ぐため中止しました。普段はデスクトップの「壁紙プレイリストを更新」を使ってください。'
        }
    } finally { $probe.Close() }
    if ((Get-PlaylistCount) -eq 0) {
        Show-Notice ("まだ推しのプレイリストが登録されていません。$nl$nl 1. Sucrose を開く。$nl 2.「＋」→「YouTube」を選ぶ。$nl 3. YouTube のプレイリスト URL と好きなタイトルを入れて登録する。$nl 4. タスクトレイから Sucrose を終了する。$nl 5. もう一度「セットアップを開始.cmd」を開く。$nl$nl 何も変更せず終了します。")
        exit 1
    }
    $stable = Join-Path ([Environment]::GetFolderPath('MyDocuments')) 'sucrose-mv-kit'
    if ([IO.Path]::GetFullPath($PSScriptRoot).TrimEnd('\') -ne [IO.Path]::GetFullPath($stable).TrimEnd('\')) {
        if (!(Ask-YesNo ("推しの壁紙を設定します。$nl$nl 補助ファイルを「ドキュメント」の「sucrose-mv-kit」にコピーします。元の設定はバックアップし、再生補助を Windows 起動時に開始するショートカットを作ります。$nl$nl 続けますか？"))) { exit 0 }
        New-Item -ItemType Directory -Path $stable -Force | Out-Null
        foreach ($file in $kitFiles) { Copy-Item -LiteralPath (Join-Path $PSScriptRoot $file) -Destination (Join-Path $stable $file) -Force }
        & "$PSHOME\powershell.exe" -NoProfile -ExecutionPolicy Bypass -File (Join-Path $stable 'launcher.ps1')
        exit $LASTEXITCODE
    }
    $logPath = Join-Path $PSScriptRoot 'setup.log'
    Start-Transcript -LiteralPath $logPath -Append | Out-Null
    $transcriptStarted = $true
    $env:SUCROSE_MV_LOG_STARTED = '1'
    Refresh-Path
    $python = Find-Python
    $node = Get-Command node -ErrorAction SilentlyContinue
    if ($node) {
        $nodeVersion = & $node.Source --version
        if ($LASTEXITCODE -ne 0 -or $nodeVersion -notmatch '^v\d+\.') { $node = $null }
    }
    $missing = @()
    if (!$python) { $missing += 'Python 3.13' }
    if (!$node) { $missing += 'Node.js LTS' }
    if ($missing.Count -gt 0) {
        $winget = Get-Command winget -ErrorAction SilentlyContinue
        if (!$winget) {
            Show-Notice (("必要なソフトが見つかりません：" + ($missing -join '、')) + "$nl$nl 公式ダウンロードページを開きます。インストール後、このファイルをもう一度開いてください。")
            if (!$python) { Start-Process 'https://www.python.org/downloads/windows/' }
            if (!$node) { Start-Process 'https://nodejs.org/en/download' }
            exit 1
        }
        if (!(Ask-YesNo (("必要なソフト：" + ($missing -join '、')) + "$nl$nl Windows の winget で公式配布元から導入します。必要な利用条件に同意して続行します。Node.js の導入時は Windows の許可画面が出る場合があります。$nl$nl インストールしますか？"))) { exit 0 }
        if (!$python) {
            Write-Output 'Python 3.13 をインストールしています…'
            & $winget.Source install --id Python.Python.3.13 --exact --source winget --scope user --accept-source-agreements --accept-package-agreements
            if ($LASTEXITCODE -ne 0) { throw 'Python をインストールできませんでした。https://www.python.org/downloads/windows/ から Python 3 を導入し、もう一度お試しください。' }
        }
        if (!$node) {
            Write-Output 'Node.js LTS をインストールしています…'
            & $winget.Source install --id OpenJS.NodeJS.LTS --exact --source winget --scope machine --accept-source-agreements --accept-package-agreements
            if ($LASTEXITCODE -ne 0) { throw 'Node.js をインストールできませんでした。https://nodejs.org/en/download から LTS を導入し、もう一度お試しください。' }
        }
        Refresh-Path
        $python = Find-Python
        $node = Get-Command node -ErrorAction SilentlyContinue
        if (!$python -or !$node) { throw '導入後のソフトを見つけられません。Windows にサインインし直してから、もう一度このファイルを開いてください。' }
    }
    Write-Output '準備ができました。セットアップ中は、この画面を閉じないでください。'
    & (Join-Path $PSScriptRoot 'install.ps1') -PythonPath $python
    Show-Notice ("セットアップ完了！$nl$nl 次は Sucrose を開き、ライブラリで推しの壁紙を選び直してください。初回の再生には少し時間がかかる場合があります。$nl$nl デスクトップに「壁紙動画をシャッフル」と「壁紙プレイリストを更新」ができています。$nl$nl「ドキュメント」の「sucrose-mv-kit」は移動・削除しないでください。")
    exit 0
} catch {
    $message = "セットアップを完了できませんでした。$nl$nl" + $_.Exception.Message
    if ($logPath) { $message += "$nl$nl 詳しい記録：$nl" + $logPath }
    [System.Windows.Forms.MessageBox]::Show($message, '推し MV 壁紙のセットアップ', 'OK', 'Warning') | Out-Null
    exit 1
} finally {
    if ($transcriptStarted) { Stop-Transcript | Out-Null }
}

