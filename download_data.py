"""
download_data.py
----------------
_data/ 폴더에 필요한 데이터 파일을 Google Drive에서 자동으로 다운로드합니다.

사용법:
    python download_data.py

준비 사항:
    pip install gdown
"""

import os
import sys
import zipfile

# ============================================================
# ★ 여기에 Google Drive 공유 파일 ID를 입력하세요 ★
#
# 방법:
# 1. Google Drive에 data.zip을 업로드
# 2. 우클릭 → '링크 공유' → '링크가 있는 모든 사용자' 로 변경
# 3. 공유 URL 예시:
#    https://drive.google.com/file/d/1aBcDeFgHiJkLmNoPqRsTuVwXyZ/view?usp=sharing
#                                    ^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
#                                    이 부분이 FILE_ID 입니다
# ============================================================
GDRIVE_FILE_ID = "1yQ97HCBKR1W_G_R2-a2iP_O4jcWTNsrz"

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(SCRIPT_DIR, "_data")
ZIP_PATH = os.path.join(SCRIPT_DIR, "data.zip")


def main():
    if GDRIVE_FILE_ID == "YOUR_GOOGLE_DRIVE_FILE_ID_HERE":
        print("❌ GDRIVE_FILE_ID가 설정되지 않았습니다.")
        print("   download_data.py 파일을 열고 GDRIVE_FILE_ID를 실제 Google Drive 파일 ID로 수정하세요.")
        sys.exit(1)

    try:
        import gdown
    except ImportError:
        print("❌ gdown이 설치되지 않았습니다. 아래 명령어로 설치하세요:")
        print("   pip install gdown")
        sys.exit(1)

    os.makedirs(DATA_DIR, exist_ok=True)

    print("📥 Google Drive에서 데이터를 다운로드합니다...")
    url = f"https://drive.google.com/uc?id={GDRIVE_FILE_ID}"
    gdown.download(url, ZIP_PATH, quiet=False)

    print("📦 압축을 해제합니다...")
    with zipfile.ZipFile(ZIP_PATH, "r") as z:
        z.extractall(SCRIPT_DIR)

    os.remove(ZIP_PATH)
    print(f"✅ 데이터 다운로드 완료! → {DATA_DIR}")


if __name__ == "__main__":
    main()
