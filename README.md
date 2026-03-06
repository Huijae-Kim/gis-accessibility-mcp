# GIS Accessibility MCP

한국 읍면동 단위의 **의료·시설 접근성 분석**을 Claude에서 대화하듯 수행할 수 있는 MCP(Model Context Protocol) 서버입니다.

> 이 서버는 Claude Desktop 및 Claude Code(CLI)와 함께 동작합니다.

---

## 지원 분석 지표

| 방법 | 설명 | 논문 분류 |
|------|------|-----------|
| `MIN` | 최근접 시설까지의 최소 거리 | Proximity-based |
| `K_AVG` | k개 최근접 시설까지의 평균 거리 | Proximity-based |
| `COM` | 누적 기회 지표 (threshold 내 시설 수) | Opportunity-based (threshold) |
| `GRAVITY` | 중력 모형 기반 접근성 | Opportunity-based (distance-decay) |
| `PPR` | 공급자 대비 인구 비율 (비공간 기준선) | Opportunity-based (non-spatial baseline) |
| `2SFCA` | 두 단계 부동 집수 구역법 | Competition-adjusted |
| `E2SFCA` | 향상된 2SFCA (거리 감쇠 적용) | Competition-adjusted |

> 지표 선택 기준은 [Ahn et al. (2026)](#관련-논문) 논문 및 `GIS-ACCESSIBILITY-SKILL.zip` 내 `references/methodology.md` 참조.

---

## 빠른 시작 (Quick Start)

### 0. 사전 요구사항

아래 소프트웨어가 모두 설치되어 있어야 합니다.

| 소프트웨어 | 설명 | 다운로드 |
|---|---|---|
| **Python 3.9 이상** | 분석 엔진 | [python.org](https://www.python.org/downloads/) |
| **Git** | 저장소 클론 | [git-scm.com](https://git-scm.com/downloads) |
| **Claude Desktop** | Claude GUI 클라이언트 *(둘 중 하나)* | [claude.ai/download](https://claude.ai/download) |
| **Claude Code CLI** | 터미널 Claude 클라이언트 *(둘 중 하나)* | `npm install -g @anthropic-ai/claude-code` |

> Claude Desktop과 Claude Code CLI 중 **하나만** 있어도 됩니다.

**Git 설치 확인 방법:**

```bash
git --version
# 출력 예: git version 2.x.x
```

Git이 없다면 [git-scm.com/downloads](https://git-scm.com/downloads) 에서 설치 후 터미널(또는 PowerShell)을 **새로 열어** 다시 확인하세요.

---

### 1. 저장소 클론

**Mac / Linux — Terminal에서:**

```bash
git clone https://github.com/Huijae-Kim/gis-accessibility-mcp.git
cd gis-accessibility-mcp
```

**Windows — PowerShell에서:**

> ⚠️ Windows 사용자는 이후 `install.ps1` 실행도 **PowerShell**에서 진행해야 합니다. 처음부터 PowerShell을 사용하세요.

```powershell
git clone https://github.com/Huijae-Kim/gis-accessibility-mcp.git
cd gis-accessibility-mcp
```

> Git이 설치되어 있으면 PowerShell에서 바로 `git` 명령을 사용할 수 있습니다.
> 설치 직후에는 PowerShell을 **새로 열어야** 명령이 인식됩니다.

---

### 2. 한 번에 설치

**Mac / Linux:**

```bash
bash install.sh
```

**Windows (PowerShell):**

```powershell
# 최초 1회만 실행 (스크립트 실행 권한 허용)
Set-ExecutionPolicy -Scope CurrentUser -ExecutionPolicy RemoteSigned

.\install.ps1
```

이 스크립트가 자동으로:
1. Python 가상환경(`.venv`) 생성 및 패키지 설치
2. 데이터 다운로드
3. Claude Desktop 설정 파일에 MCP 서버 등록
4. Claude Code CLI에 MCP 서버 등록
5. Skill 파일 준비 (`GIS-ACCESSIBILITY-SKILL.zip`, `~/.claude/commands/gis-accessibility.md`)

---

### 3. Claude Desktop 재시작 및 Skill 활성화

설치 후 Claude Desktop을 완전히 종료하고 다시 실행하세요.

**Skill 활성화 방법 (환경별 상이):**

| 환경 | 방법 |
|------|------|
| **Claude Desktop** | 상단 메뉴 → 사용자지정 → 스킬 → **+** → 스킬 업로드 → `GIS-ACCESSIBILITY-SKILL.zip` 선택 |
| **Claude Code CLI** | 터미널에서 `claude` 실행 → `/gis-accessibility` 입력 (자동 설치됨) |

---

## 수동 설치 (선택 사항)

자동 설치(`install.sh` / `install.ps1`)가 실패하거나 직접 설정하고 싶은 경우:

### 패키지 설치

```bash
pip install -r requirements.txt
```

### 데이터 다운로드

`_data/` 폴더에 필요한 데이터 파일(약 260MB)을 받습니다.

**방법 A: 자동 다운로드 (권장)**

```bash
python download_data.py
```

**방법 B: 수동 다운로드**

[여기](https://drive.google.com/file/d/1yQ97HCBKR1W_G_R2-a2iP_O4jcWTNsrz/view?usp=sharing)에서 `data.zip`을 다운받아 압축 해제하면 `_data/` 폴더가 생성됩니다. `_data/` 폴더를 `gis-accessibility` 폴더로 이동해주세요.

> **필요한 파일 목록:**
> - `POPULATION_DONG_FINAL.csv`
> - `HOSPITALS_FINAL.csv`
> - `BND_ADM_DONG_PG.shp` (및 관련 파일)
> - `BND_SIGUNGU_PG.shp` (및 관련 파일)

### Claude Desktop MCP 수동 등록

설정 파일 위치:
- **Mac:** `~/Library/Application Support/Claude/claude_desktop_config.json`
- **Windows:** `%APPDATA%\Claude\claude_desktop_config.json`

아래 내용을 추가하세요:

```json
{
  "mcpServers": {
    "gis-accessibility": {
      "command": "/절대경로/gis-accessibility/.venv/bin/python3",
      "args": ["/절대경로/gis-accessibility/gis_analysis_v7.py"]
    }
  }
}
```

> Windows 경로 예시: `"command": "C:\\Users\\사용자명\\gis-accessibility\\.venv\\Scripts\\python.exe"`

### Claude Code CLI MCP 수동 등록

```bash
claude mcp add gis-accessibility /절대경로/.venv/bin/python3 /절대경로/gis_analysis_v7.py
```

### Skill 수동 설치

**Mac / Linux:**

```bash
mkdir -p ~/.claude/commands
unzip -p GIS-ACCESSIBILITY-SKILL.zip "GIS-ACCESSIBILITY-SKILL/SKILL.md" > ~/.claude/commands/gis-accessibility.md
```

**Windows (PowerShell):**

```powershell
$dest = "$env:USERPROFILE\.claude\commands"
New-Item -ItemType Directory -Force -Path $dest | Out-Null
$tmp = "$env:TEMP\gis-skill-tmp"
Expand-Archive -Path GIS-ACCESSIBILITY-SKILL.zip -DestinationPath $tmp -Force
Copy-Item "$tmp\GIS-ACCESSIBILITY-SKILL\SKILL.md" "$dest\gis-accessibility.md" -Force
Remove-Item $tmp -Recurse -Force
```

---

## 사용 예시

Claude Desktop 또는 Claude Code에서 자연어로 대화:

```
서울특별시 소아과 접근성을 2SFCA 방법으로 분석해줘
```

```
충청권 정신건강의학과 의료 공백 지역이 어디야?
```

```
대전광역시 내과 접근성을 PPR 지표로 빠르게 확인해줘
```

결과 파일(지도 이미지, CSV)은 `_results/` 폴더에 저장됩니다.

---

## 파일 구조

```
gis-accessibility/
├── gis_analysis_v7.py          # MCP 서버 메인 코드
├── requirements.txt             # Python 패키지 목록
├── download_data.py             # 데이터 자동 다운로드 스크립트
├── install.sh                   # 원클릭 설치 스크립트 (Mac/Linux)
├── install.ps1                  # 원클릭 설치 스크립트 (Windows PowerShell)
├── GIS-ACCESSIBILITY-SKILL.zip  # Claude Skill 파일 (Desktop + Code)
│   ├── SKILL.md                 #   Claude Code CLI용 slash command
│   ├── gis-accessibility.skill  #   Claude Desktop용 skill 파일
│   └── references/
│       └── methodology.md       #   방법론 참고 자료
├── _data/                       # 데이터 폴더 (Git 제외, 별도 다운로드)
│   ├── POPULATION_DONG_FINAL.csv
│   ├── HOSPITALS_FINAL.csv
│   ├── BND_ADM_DONG_PG.shp
│   └── BND_SIGUNGU_PG.shp
└── _results/                    # 분석 결과 저장 (자동 생성)
    ├── result_2SFCA_서울특별시_산부인과.png
    └── result_2SFCA_서울특별시_산부인과.csv
```

---

## 데이터 출처

- 행정구역 경계: 통계지리정보서비스(SGIS)
- 전국 병의원 현황: 건강보험심사평가원(HIRA) 공공데이터
- 인구 데이터: 행정안전부 주민등록 인구 통계

---

## 관련 논문

Ahn, J., Kim, H., & Lee, T. (2026). *A Conceptual Framework for Spatial Accessibility: Aligning Metrics with Urban Service Determinants*. KAIST ISysE Preprint.

---

## 라이선스

This project is licensed under the [CC BY-NC 4.0 License](https://creativecommons.org/licenses/by-nc/4.0/).
You are free to use and modify this work for **non-commercial purposes** with proper attribution.
Commercial use is **prohibited** without prior written permission.

이 프로젝트는 [CC BY-NC 4.0 라이선스](https://creativecommons.org/licenses/by-nc/4.0/) 하에 배포됩니다.
출처를 밝히는 경우 **비상업적 목적**에 한해 자유롭게 사용 및 수정 가능합니다.
**상업적 이용은 사전 서면 허가 없이 금지**됩니다.

---

## 사사

This research was supported by the **Center for Advanced Urban Systems (CAUS)** of Korea Advanced Institute of Science and Technology (KAIST), funded by **GS E&C**.

본 연구는 **GS건설**이 지원하는 한국과학기술원(KAIST) **CAUS**의 지원을 받아 수행되었습니다.
