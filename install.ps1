# =============================================================================
# install.ps1 — GIS Accessibility MCP 설치 스크립트 (Windows용)
# =============================================================================
# 실행 방법 (PowerShell에서):
#   Set-ExecutionPolicy -Scope CurrentUser -ExecutionPolicy RemoteSigned
#   .\install.ps1
# =============================================================================

$ErrorActionPreference = "Stop"

$ScriptDir  = Split-Path -Parent $MyInvocation.MyCommand.Definition
$VenvDir    = Join-Path $ScriptDir ".venv"
$McpScript  = Join-Path $ScriptDir "gis_analysis_v7.py"
$PythonBin  = Join-Path $VenvDir "Scripts\python.exe"
$CommandsDir = Join-Path $env:USERPROFILE ".claude\commands"

Write-Host ""
Write-Host "======================================================"
Write-Host "  GIS Accessibility MCP 설치를 시작합니다 (Windows)"
Write-Host "======================================================"
Write-Host ""

# ------------------------------------------------------------------------------
# 1. Python 확인
# ------------------------------------------------------------------------------
Write-Host "[1/5] Python 버전 확인..."

$PythonCmd = $null
foreach ($cmd in @("python", "python3", "py")) {
    try {
        $ver = & $cmd --version 2>&1
        if ($ver -match "Python 3\.(\d+)") {
            $minor = [int]$Matches[1]
            if ($minor -ge 9) {
                $PythonCmd = $cmd
                Write-Host "   $ver 확인됨"
                break
            }
        }
    } catch {}
}

if (-not $PythonCmd) {
    Write-Host "❌ Python 3.9 이상이 필요합니다."
    Write-Host "   https://www.python.org/downloads/ 에서 설치하세요."
    exit 1
}

# ------------------------------------------------------------------------------
# 2. 가상환경 생성 및 패키지 설치
# ------------------------------------------------------------------------------
Write-Host ""
Write-Host "[2/5] 가상환경 및 패키지 설치..."

if (-not (Test-Path $VenvDir)) {
    & $PythonCmd -m venv $VenvDir
    Write-Host "   .venv 가상환경 생성됨"
}

& $PythonBin -m pip install --upgrade pip --quiet
& $PythonBin -m pip install -r (Join-Path $ScriptDir "requirements.txt") --quiet
Write-Host "   패키지 설치 완료"

# ------------------------------------------------------------------------------
# 3. 데이터 다운로드
# ------------------------------------------------------------------------------
Write-Host ""
Write-Host "[3/5] 데이터 확인..."

$DataDir = Join-Path $ScriptDir "_data"
$RequiredFiles = @("인구_전처리.csv", "전국 병의원 현황_전처리.csv", "BND_ADM_DONG_PG.shp", "BND_SIGUNGU_PG.shp")
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
    & $PythonBin (Join-Path $ScriptDir "download_data.py")
}

# ------------------------------------------------------------------------------
# 4. Claude Desktop MCP 등록
# ------------------------------------------------------------------------------
Write-Host ""
Write-Host "[4/5] Claude Desktop MCP 등록..."

$ClaudeConfig = Join-Path $env:APPDATA "Claude\claude_desktop_config.json"

if (-not (Test-Path $ClaudeConfig)) {
    Write-Host "   ℹ️  Claude Desktop 설정 파일이 없습니다 (Claude Desktop 미설치 시 무시)."
    Write-Host "      경로: $ClaudeConfig"
} else {
    $ConfigJson = @"
import json, sys
config_path = r"""$ClaudeConfig"""
mcp_script  = r"""$McpScript"""
python_bin  = r"""$PythonBin"""

with open(config_path, 'r', encoding='utf-8') as f:
    config = json.load(f)

config.setdefault('mcpServers', {})
config['mcpServers']['gis-accessibility'] = {
    'command': python_bin,
    'args': [mcp_script],
}

with open(config_path, 'w', encoding='utf-8') as f:
    json.dump(config, f, ensure_ascii=False, indent=2)

print("   Claude Desktop에 'gis-accessibility' MCP가 등록되었습니다.")
"@
    & $PythonBin -c $ConfigJson
}

# ------------------------------------------------------------------------------
# 5. Claude Code(CLI) MCP 및 Skill 등록
# ------------------------------------------------------------------------------
Write-Host ""
Write-Host "[5/5] Claude Code MCP 및 Skill 등록..."

$ClaudeCliPath = Get-Command claude -ErrorAction SilentlyContinue
if ($ClaudeCliPath) {
    claude mcp remove gis-accessibility 2>$null
    claude mcp add gis-accessibility $PythonBin $McpScript
    Write-Host "   Claude Code에 'gis-accessibility' MCP가 등록되었습니다."
} else {
    Write-Host "   ℹ️  Claude Code CLI가 설치되지 않았습니다. Claude Desktop만 설정됩니다."
}

# Claude Code CLI용 Skill 설치
$SkillZip = Join-Path $ScriptDir "GIS-ACCESSIBILITY-SKILL.zip"
if (Test-Path $SkillZip) {
    $TmpDir = Join-Path $env:TEMP "gis-skill-tmp"
    Expand-Archive -Path $SkillZip -DestinationPath $TmpDir -Force
    New-Item -ItemType Directory -Force -Path $CommandsDir | Out-Null
    Copy-Item (Join-Path $TmpDir "GIS-ACCESSIBILITY-SKILL\SKILL.md") `
              (Join-Path $CommandsDir "gis-accessibility.md") -Force
    Remove-Item $TmpDir -Recurse -Force
    Write-Host "   [Claude Code] Skill 설치됨: $CommandsDir\gis-accessibility.md"
    Write-Host "   → Claude Code에서 /gis-accessibility 로 사용하세요."
}

# ------------------------------------------------------------------------------
# 완료
# ------------------------------------------------------------------------------
Write-Host ""
Write-Host "======================================================"
Write-Host "  ✅ 설치 완료!"
Write-Host "======================================================"
Write-Host ""
Write-Host "  다음 단계:"
Write-Host "  1. Claude Desktop을 재시작하세요."
Write-Host "  2. [Claude Desktop] Skill 등록 방법:"
Write-Host "     Claude Desktop 상단 메뉴 → 사용자지정 → 스킬 → + → 스킬 업로드"
Write-Host "     → GIS-ACCESSIBILITY-SKILL.zip 선택"
Write-Host "     파일 위치: $ScriptDir\GIS-ACCESSIBILITY-SKILL.zip"
Write-Host "  3. [Claude Code CLI] /gis-accessibility 명령어로 Skill을 바로 사용할 수 있습니다."
Write-Host ""
Write-Host "  결과 파일 저장 위치: $ScriptDir\_results\"
Write-Host ""
