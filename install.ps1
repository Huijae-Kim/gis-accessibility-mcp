#requires -Version 5.1
# =============================================================================
# install.ps1 — GIS Accessibility MCP 설치 스크립트 (Windows용)
# =============================================================================
# 실행 방법 (PowerShell에서):
#   Set-ExecutionPolicy -Scope CurrentUser -ExecutionPolicy RemoteSigned
#   Unblock-File .\install.ps1
#   .\install.ps1
#
# ✅ Windows에서 Claude Desktop이 MSIX(스토어/패키지)로 설치된 경우,
#    Claude 설정 파일이 아래 "가상화(LocalCache)" 경로에 생성/사용될 수 있습니다.
#    이 스크립트는 %APPDATA% 경로와 MSIX LocalCache 경로를 모두 탐지하여
#    가능한 경우 둘 다 업데이트합니다.
#
# ⚠️ 중요:
#   - 이 파일은 "UTF-8 with BOM" 인코딩으로 저장되어야 합니다.
# =============================================================================

$ErrorActionPreference = "Stop"
$ProgressPreference = "SilentlyContinue"

# ----------------------------------------------------------------------
# Paths
# ----------------------------------------------------------------------
$ScriptDir   = Split-Path -Parent $MyInvocation.MyCommand.Definition
$VenvDir     = Join-Path $ScriptDir ".venv"
$McpScript   = Join-Path $ScriptDir "gis_analysis_v7.py"
$PythonBin   = Join-Path $VenvDir "Scripts\python.exe"
$CommandsDir = Join-Path $env:USERPROFILE ".claude\commands"
$ResultsDir  = Join-Path $ScriptDir "_results"

Write-Host ""
Write-Host "======================================================"
Write-Host "  GIS Accessibility MCP 설치를 시작합니다 (Windows)"
Write-Host "======================================================"
Write-Host ""

# ----------------------------------------------------------------------
# Helper: Discover Claude Desktop config paths (classic + MSIX LocalCache)
# ----------------------------------------------------------------------
function Get-ClaudeDesktopConfigTargets {
    $targets = New-Object System.Collections.Generic.List[string]

    # 1) Classic path (docs often reference this)
    $classic = Join-Path $env:APPDATA "Claude\claude_desktop_config.json"
    $targets.Add($classic)

    # 2) MSIX / Store packaged app path (virtualized roaming under LocalCache)
    $packagesRoot = Join-Path $env:LOCALAPPDATA "Packages"
    if (Test-Path $packagesRoot) {
        $candidates = Get-ChildItem -Path $packagesRoot -Directory -ErrorAction SilentlyContinue |
            Where-Object {
                $_.Name -like "Anthropic.ClaudeDesktop_*" -or
                $_.Name -like "Claude_*"
            }

        foreach ($p in $candidates) {
            $roamingClaudeDir = Join-Path $p.FullName "LocalCache\Roaming\Claude"
            if (Test-Path $roamingClaudeDir) {
                $msixCfg = Join-Path $roamingClaudeDir "claude_desktop_config.json"
                $targets.Add($msixCfg)
            }
        }
    }

    # De-duplicate while preserving order
    $seen = @{}
    $uniq = New-Object System.Collections.Generic.List[string]
    foreach ($t in $targets) {
        if (-not $seen.ContainsKey($t)) {
            $seen[$t] = $true
            $uniq.Add($t)
        }
    }
    return $uniq
}

function Ensure-JsonFile {
    param([Parameter(Mandatory=$true)][string]$Path)

    $parent = Split-Path -Parent $Path
    if (-not (Test-Path $parent)) {
        New-Item -ItemType Directory -Force -Path $parent | Out-Null
    }
    if (-not (Test-Path $Path)) {
        Set-Content -Path $Path -Value "{}" -Encoding UTF8
    }
}

function Update-ClaudeConfigWithPython {
    param(
        [Parameter(Mandatory=$true)][string]$ConfigPath,
        [Parameter(Mandatory=$true)][string]$PythonExe,
        [Parameter(Mandatory=$true)][string]$McpPy
    )

    # NOTE: We avoid double-quotes in the Python snippet to prevent Windows CLI quote stripping.
    $py = @"
import json

config_path = r'$ConfigPath'
mcp_script  = r'$McpPy'
python_bin  = r'$PythonExe'

try:
    with open(config_path, 'r', encoding='utf-8') as f:
        config = json.load(f)
except Exception:
    config = {}

config.setdefault('mcpServers', {})
config['mcpServers']['gis-accessibility'] = {
    'command': python_bin,
    'args': [mcp_script],
}

with open(config_path, 'w', encoding='utf-8') as f:
    json.dump(config, f, ensure_ascii=False, indent=2)

print('UPDATED:', config_path)
"@

    & $PythonExe -c $py
    if ($LASTEXITCODE -ne 0) {
        throw "Python config update failed (exit=$LASTEXITCODE)"
    }
}

# ----------------------------------------------------------------------
# 1) Python 확인
# ----------------------------------------------------------------------
Write-Host "[1/5] Python 버전 확인..."

$PythonCmd = $null
foreach ($cmd in @("python", "python3", "py")) {
    try {
        $ver = & $cmd --version 2>&1
        if ($ver -match "Python 3\.(\d+)") {
            $minor = [int]$Matches[1]
            if ($minor -ge 9) {
                $PythonCmd = $cmd
                Write-Host "   $ver 확인됨 (command: $cmd)"
                break
            }
        }
    } catch {
        # ignore
    }
}

if (-not $PythonCmd) {
    Write-Host "❌ Python 3.9 이상이 필요합니다."
    Write-Host "   https://www.python.org/downloads/ 에서 설치하세요."
    exit 1
}

# ----------------------------------------------------------------------
# 2) 가상환경 생성 및 패키지 설치
# ----------------------------------------------------------------------
Write-Host ""
Write-Host "[2/5] 가상환경 및 패키지 설치..."

if (-not (Test-Path $VenvDir)) {
    & $PythonCmd -m venv $VenvDir
    Write-Host "   .venv 가상환경 생성됨"
}

if (-not (Test-Path $PythonBin)) {
    Write-Host "❌ 가상환경 Python을 찾을 수 없습니다: $PythonBin"
    Write-Host "   .venv 폴더를 삭제한 뒤 다시 실행해보세요."
    exit 1
}

& $PythonBin -m pip install --upgrade pip
$ReqFile = Join-Path $ScriptDir "requirements.txt"
if (-not (Test-Path $ReqFile)) {
    Write-Host "❌ requirements.txt 파일이 없습니다: $ReqFile"
    exit 1
}
& $PythonBin -m pip install -r $ReqFile
Write-Host "   패키지 설치 완료"

# ----------------------------------------------------------------------
# 3) 데이터 다운로드(필요 시)
# ----------------------------------------------------------------------
Write-Host ""
Write-Host "[3/5] 데이터 확인..."

$DataDir = Join-Path $ScriptDir "_data"
$RequiredFiles = @(
    "인구_전처리.csv",
    "전국 병의원 현황_전처리.csv",
    "BND_ADM_DONG_PG.shp",
    "BND_SIGUNGU_PG.shp"
)

$Missing = $false
foreach ($f in $RequiredFiles) {
    if (-not (Test-Path (Join-Path $DataDir $f))) {
        $Missing = $true
        break
    }
}

if (-not $Missing) {
    Write-Host "   데이터 파일이 이미 존재합니다. 다운로드 생략."
} else {
    Write-Host "   데이터 파일이 없습니다. 다운로드를 시작합니다..."
    $Downloader = Join-Path $ScriptDir "download_data.py"
    if (-not (Test-Path $Downloader)) {
        Write-Host "❌ download_data.py 파일이 없습니다: $Downloader"
        exit 1
    }
    & $PythonBin $Downloader
}

# ----------------------------------------------------------------------
# 4) Claude Desktop MCP 등록 (classic + MSIX LocalCache auto-detect)
# ----------------------------------------------------------------------
Write-Host ""
Write-Host "[4/5] Claude Desktop MCP 등록..."

$targets = Get-ClaudeDesktopConfigTargets
$updatedPaths = New-Object System.Collections.Generic.List[string]

foreach ($cfg in $targets) {
    try {
        Ensure-JsonFile -Path $cfg
        Update-ClaudeConfigWithPython -ConfigPath $cfg -PythonExe $PythonBin -McpPy $McpScript
        $updatedPaths.Add($cfg)
    } catch {
        Write-Host "   ⚠️  업데이트 실패(무시 가능): $cfg"
        Write-Host "      $($_.Exception.Message)"
    }
}

if ($updatedPaths.Count -gt 0) {
    Write-Host "   ✅ Claude Desktop 설정 파일 업데이트 완료:"
    foreach ($p in $updatedPaths) { Write-Host "      - $p" }
} else {
    Write-Host "   ❌ Claude Desktop 설정 파일을 찾지 못했거나 업데이트할 수 없습니다."
    Write-Host "      - Claude Desktop이 설치되어 있는지 확인 후 다시 실행해보세요."
    Write-Host "      - classic 예상 경로: $(Join-Path $env:APPDATA 'Claude\claude_desktop_config.json')"
    Write-Host "      - MSIX 예상 경로:    $env:LOCALAPPDATA\Packages\Anthropic.ClaudeDesktop_*\LocalCache\Roaming\Claude\claude_desktop_config.json"
}

# ----------------------------------------------------------------------
# 5) Claude Code(CLI) MCP 및 Skill 등록
# ----------------------------------------------------------------------
Write-Host ""
Write-Host "[5/5] Claude Code MCP 및 Skill 등록..."

$ClaudeCliPath = Get-Command claude -ErrorAction SilentlyContinue
if ($ClaudeCliPath) {
    try { claude mcp remove gis-accessibility 2>$null } catch { }
    claude mcp add gis-accessibility $PythonBin $McpScript
    Write-Host "   Claude Code에 'gis-accessibility' MCP가 등록되었습니다."
} else {
    Write-Host "   ℹ️  Claude Code CLI가 설치되지 않았습니다. Claude Desktop만 설정됩니다."
}

$SkillZip = Join-Path $ScriptDir "GIS-ACCESSIBILITY-SKILL.zip"
if (Test-Path $SkillZip) {
    $TmpDir = Join-Path $env:TEMP "gis-skill-tmp"
    if (Test-Path $TmpDir) { Remove-Item $TmpDir -Recurse -Force }
    Expand-Archive -Path $SkillZip -DestinationPath $TmpDir -Force

    New-Item -ItemType Directory -Force -Path $CommandsDir | Out-Null

    $SkillMd = Join-Path $TmpDir "GIS-ACCESSIBILITY-SKILL\SKILL.md"
    if (Test-Path $SkillMd) {
        Copy-Item -Path $SkillMd -Destination (Join-Path $CommandsDir "gis-accessibility.md") -Force
        Write-Host "   [Claude Code] Skill 설치됨: $(Join-Path $CommandsDir "gis-accessibility.md")"
        Write-Host "   → Claude Code에서 /gis-accessibility 로 사용하세요."
    } else {
        Write-Host "   ⚠️  SKILL.md를 찾지 못했습니다. ZIP 내부 폴더 구조를 확인하세요."
    }

    Remove-Item $TmpDir -Recurse -Force
}

# ----------------------------------------------------------------------
# 완료
# ----------------------------------------------------------------------
New-Item -ItemType Directory -Force -Path $ResultsDir | Out-Null

Write-Host ""
Write-Host "======================================================"
Write-Host "  ✅ 설치 완료!"
Write-Host "======================================================"
Write-Host ""
Write-Host "  다음 단계:"
Write-Host "  1. Claude Desktop을 완전히 종료한 뒤 다시 실행하세요."
Write-Host "  2. [Claude Desktop] MCP 서버 목록에서 'gis-accessibility'가 보이는지 확인하세요."
Write-Host "  3. [Claude Code CLI] /gis-accessibility 명령어로 Skill을 바로 사용할 수 있습니다."
Write-Host ""
Write-Host "  결과 파일 저장 위치: $ResultsDir"
Write-Host ""
