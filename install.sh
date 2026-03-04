#!/bin/bash
# =============================================================================
# install.sh — GIS Accessibility MCP 설치 스크립트
# =============================================================================
# 이 스크립트가 하는 일:
#   1. Python 가상환경(.venv) 생성 및 패키지 설치
#   2. 데이터 다운로드 (Google Drive)
#   3. Claude Desktop에 MCP 서버 등록
#   4. Claude Code(CLI)에 MCP 서버 등록
#   5. Claude Code Skill 설치
# =============================================================================

set -e  # 오류 발생 시 즉시 종료

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
VENV_DIR="$SCRIPT_DIR/.venv"
MCP_SCRIPT="$SCRIPT_DIR/gis_analysis_v7.py"
SKILL_FILE="$SCRIPT_DIR/GIS-ACCESSIBILITY-SKILL/SKILL.md"
COMMANDS_DIR="$HOME/.claude/commands"

echo ""
echo "======================================================"
echo "  GIS Accessibility MCP 설치를 시작합니다"
echo "======================================================"
echo ""

# ------------------------------------------------------------------------------
# 1. Python 확인
# ------------------------------------------------------------------------------
echo "▶ [1/5] Python 버전 확인..."
if ! command -v python3 &> /dev/null; then
    echo "❌ python3를 찾을 수 없습니다. Python 3.9 이상을 설치하세요."
    echo "   https://www.python.org/downloads/"
    exit 1
fi

PYTHON_VERSION=$(python3 -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')")
echo "   Python $PYTHON_VERSION 확인됨"

# ------------------------------------------------------------------------------
# 2. 가상환경 생성 및 패키지 설치
# ------------------------------------------------------------------------------
echo ""
echo "▶ [2/5] 가상환경 및 패키지 설치..."

if [ ! -d "$VENV_DIR" ]; then
    python3 -m venv "$VENV_DIR"
    echo "   .venv 가상환경 생성됨"
fi

"$VENV_DIR/bin/pip" install --upgrade pip --quiet
"$VENV_DIR/bin/pip" install -r "$SCRIPT_DIR/requirements.txt" --quiet
echo "   패키지 설치 완료"

PYTHON_BIN="$VENV_DIR/bin/python3"

# ------------------------------------------------------------------------------
# 3. 데이터 다운로드
# ------------------------------------------------------------------------------
echo ""
echo "▶ [3/5] 데이터 확인..."

DATA_DIR="$SCRIPT_DIR/_data"
REQUIRED_FILES=(
    "인구_전처리.csv"
    "전국 병의원 현황_전처리.csv"
    "BND_ADM_DONG_PG.shp"
    "BND_SIGUNGU_PG.shp"
)

MISSING=0
for f in "${REQUIRED_FILES[@]}"; do
    if [ ! -f "$DATA_DIR/$f" ]; then
        MISSING=1
        break
    fi
done

if [ $MISSING -eq 0 ]; then
    echo "   데이터 파일이 이미 존재합니다. 다운로드 생략."
else
    echo "   데이터 파일이 없습니다. 다운로드를 시작합니다..."
    "$PYTHON_BIN" "$SCRIPT_DIR/download_data.py"
fi

# ------------------------------------------------------------------------------
# 4. Claude Desktop MCP 등록
# ------------------------------------------------------------------------------
echo ""
echo "▶ [4/5] Claude Desktop MCP 등록..."

# OS별 Claude Desktop 설정 파일 경로
if [[ "$OSTYPE" == "darwin"* ]]; then
    CLAUDE_CONFIG="$HOME/Library/Application Support/Claude/claude_desktop_config.json"
elif [[ "$OSTYPE" == "msys" || "$OSTYPE" == "win32" ]]; then
    CLAUDE_CONFIG="$APPDATA/Claude/claude_desktop_config.json"
else
    CLAUDE_CONFIG="$HOME/.config/Claude/claude_desktop_config.json"
fi

if [ ! -f "$CLAUDE_CONFIG" ]; then
    echo "   ℹ️  Claude Desktop 설정 파일이 없습니다 (Claude Desktop이 설치되지 않은 경우 무시)."
    echo "      경로: $CLAUDE_CONFIG"
else
    # Python으로 JSON 수정 (jq 없어도 동작)
    "$PYTHON_BIN" - <<PYEOF
import json, os, sys

config_path = """$CLAUDE_CONFIG"""
mcp_script  = """$MCP_SCRIPT"""
python_bin  = """$PYTHON_BIN"""

with open(config_path, "r", encoding="utf-8") as f:
    config = json.load(f)

config.setdefault("mcpServers", {})
config["mcpServers"]["gis-accessibility"] = {
    "command": python_bin,
    "args": [mcp_script],
}

with open(config_path, "w", encoding="utf-8") as f:
    json.dump(config, f, ensure_ascii=False, indent=2)

print("   Claude Desktop에 'gis-accessibility' MCP가 등록되었습니다.")
PYEOF
fi

# ------------------------------------------------------------------------------
# 5. Claude Code(CLI) MCP 등록
# ------------------------------------------------------------------------------
echo ""
echo "▶ [5/5] Claude Code MCP 및 Skill 등록..."

if command -v claude &> /dev/null; then
    # 이미 등록된 경우 제거 후 재등록
    claude mcp remove gis-accessibility 2>/dev/null || true
    claude mcp add gis-accessibility "$PYTHON_BIN" "$MCP_SCRIPT"
    echo "   Claude Code에 'gis-accessibility' MCP가 등록되었습니다."
else
    echo "   ℹ️  Claude Code CLI가 설치되지 않았습니다. Claude Desktop만 설정됩니다."
fi

# Skill 설치
if [ -f "$SCRIPT_DIR/GIS-ACCESSIBILITY-SKILL.zip" ]; then
    # zip에서 SKILL.md 추출
    TMP_DIR=$(mktemp -d)
    unzip -q "$SCRIPT_DIR/GIS-ACCESSIBILITY-SKILL.zip" -d "$TMP_DIR"
    mkdir -p "$COMMANDS_DIR"
    cp "$TMP_DIR/GIS-ACCESSIBILITY-SKILL/SKILL.md" "$COMMANDS_DIR/gis-accessibility.md"
    rm -rf "$TMP_DIR"
    echo "   Skill이 설치되었습니다: $COMMANDS_DIR/gis-accessibility.md"
    echo "   Claude Code에서 /gis-accessibility 로 사용하세요."
fi

# ------------------------------------------------------------------------------
# 완료
# ------------------------------------------------------------------------------
echo ""
echo "======================================================"
echo "  ✅ 설치 완료!"
echo "======================================================"
echo ""
echo "  다음 단계:"
echo "  1. Claude Desktop을 재시작하세요."
echo "  2. Claude에서 '서울시 소아과 접근성 분석해줘' 라고 물어보세요."
echo ""
echo "  결과 파일 저장 위치: $SCRIPT_DIR/_results/"
echo ""
