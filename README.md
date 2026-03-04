# GIS Accessibility MCP

한국 읍면동 단위의 **의료·시설 접근성 분석**을 Claude에서 대화하듯 수행할 수 있는 MCP(Model Context Protocol) 서버입니다.

> 이 서버는 Claude Desktop 및 Claude Code(CLI)와 함께 동작합니다.

---

## 지원 분석 지표

| 방법 | 설명 |
|------|------|
| `MIN` | 최근접 시설까지의 최소 거리 |
| `K_AVG` | k개 최근접 시설까지의 평균 거리 |
| `COM` | 누적 기회 지표 (threshold 내 시설 수) |
| `GRAVITY` | 중력 모형 기반 접근성 |
| `2SFCA` | 두 단계 부동 집수 구역법 |
| `E2SFCA` | 향상된 2SFCA (거리 감쇠 적용) |
| `PPR` | 인구 대비 공급자 비율 |

---

## 빠른 시작 (Quick Start)

### 0. 사전 요구사항

- **Python 3.9 이상** ([다운로드](https://www.python.org/downloads/))
- **Claude Desktop** ([다운로드](https://claude.ai/download)) 또는 **Claude Code CLI** (`npm install -g @anthropic-ai/claude-code`)

### 1. 저장소 클론

```bash
git clone https://github.com/Huijae-Kim/gis-accessibility.git
cd gis-accessibility
```

### 2. 데이터 다운로드

`_data/` 폴더에 필요한 데이터 파일(약 260MB)을 받습니다.

**방법 A: 자동 다운로드 (권장)**

`download_data.py` 파일을 열고 `GDRIVE_FILE_ID` 값을 실제 공유 ID로 수정한 뒤:

```bash
python download_data.py
```

**방법 B: 수동 다운로드**

[여기](https://drive.google.com/drive/folders/YOUR_FOLDER_LINK)에서 `data.zip`을 다운받아 압축 해제하면 `_data/` 폴더가 생성됩니다.

> **필요한 파일 목록:**
> - `인구_전처리.csv`
> - `전국 병의원 현황_전처리.csv`
> - `BND_ADM_DONG_PG.shp` (및 관련 파일)
> - `BND_SIGUNGU_PG.shp` (및 관련 파일)

### 3. 한 번에 설치

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
2. Claude Desktop 설정 파일에 MCP 서버 등록
3. Claude Code CLI에 MCP 서버 등록
4. Claude Code Skill(`/gis-accessibility`) 설치

### 4. Claude Desktop 재시작

설치 후 Claude Desktop을 완전히 종료하고 다시 실행하세요.

---

## 수동 설치 (선택 사항)

자동 설치(`install.sh`)가 실패하거나 직접 설정하고 싶은 경우:

### 패키지 설치

```bash
pip install -r requirements.txt
```

### Claude Desktop MCP 수동 등록

`~/Library/Application Support/Claude/claude_desktop_config.json` 파일을 열고 아래 내용을 추가:

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

> Windows: `%APPDATA%\Claude\claude_desktop_config.json`

### Claude Code CLI MCP 수동 등록

```bash
claude mcp add gis-accessibility /절대경로/.venv/bin/python3 /절대경로/gis_analysis_v7.py
```

### Skill 수동 설치

```bash
mkdir -p ~/.claude/commands
unzip -p GIS-ACCESSIBILITY-SKILL.zip "GIS-ACCESSIBILITY-SKILL/SKILL.md" > ~/.claude/commands/gis-accessibility.md
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
대전광역시 내과 접근성을 지도로 보여줘
```

결과 파일(지도 이미지, CSV)은 `_results/` 폴더에 저장됩니다.

---

## 파일 구조

```
gis-accessibility/
├── gis_analysis_v7.py        # MCP 서버 메인 코드
├── requirements.txt           # Python 패키지 목록
├── download_data.py           # 데이터 자동 다운로드 스크립트
├── install.sh                 # 원클릭 설치 스크립트
├── GIS-ACCESSIBILITY-SKILL.zip # Claude Code Skill 파일
├── _data/                     # 데이터 폴더 (Git 제외, 별도 다운로드)
│   ├── 인구_전처리.csv
│   ├── 전국 병의원 현황_전처리.csv
│   ├── BND_ADM_DONG_PG.shp
│   └── BND_SIGUNGU_PG.shp
└── _results/                  # 분석 결과 저장 (자동 생성)
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

Ahn et al. (2026). *A Conceptual Framework for Spatial Accessibility*. (preprint)

---

## 라이선스

MIT License
