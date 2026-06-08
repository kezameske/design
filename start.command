#!/bin/bash
# Double-click this file in Finder to launch the poster automation app.
# It activates the virtualenv and starts the Streamlit server.

set -e

# Always run from this script's directory (works regardless of how it's launched)
cd "$(dirname "$0")"

echo "============================================================"
echo "  포스터 자동화 v1 — 서버 시작"
echo "============================================================"
echo ""
echo "프로젝트 폴더: $(pwd)"
echo ""

# Check venv exists
if [ ! -d ".venv" ]; then
    echo "❌ .venv 폴더가 없습니다. 먼저 가상환경을 생성해야 합니다:"
    echo ""
    echo "  python3 -m venv .venv"
    echo "  source .venv/bin/activate"
    echo "  pip install -r requirements.txt"
    echo ""
    read -p "엔터를 누르면 창이 닫힙니다..."
    exit 1
fi

# Check .env exists
if [ ! -f ".env" ]; then
    echo "❌ .env 파일이 없습니다. .env.example을 복사하고 키를 입력하세요:"
    echo ""
    echo "  cp .env.example .env"
    echo "  open -e .env"
    echo ""
    read -p "엔터를 누르면 창이 닫힙니다..."
    exit 1
fi

# Activate venv
source .venv/bin/activate
echo "✓ 가상환경 활성화 완료"
echo "✓ Python: $(which python3)"
echo "✓ Streamlit: $(streamlit --version 2>/dev/null || echo 'NOT INSTALLED')"
echo ""

# Check streamlit is installed
if ! command -v streamlit &> /dev/null; then
    echo "❌ streamlit이 설치되어 있지 않습니다. 설치:"
    echo ""
    echo "  pip install -r requirements.txt"
    echo ""
    read -p "엔터를 누르면 창이 닫힙니다..."
    exit 1
fi

# Detect local IP for network access (try en0 wifi, then en1)
LOCAL_IP=$(ipconfig getifaddr en0 2>/dev/null || ipconfig getifaddr en1 2>/dev/null || echo "")

echo "============================================================"
echo "  Streamlit 서버 시작 중..."
echo "============================================================"
echo ""
echo "  로컬 접속:    http://localhost:8501"
if [ -n "$LOCAL_IP" ]; then
    echo "  네트워크 접속: http://${LOCAL_IP}:8501"
    echo "                (같은 Wi-Fi의 동료가 위 주소로 접속 가능)"
else
    echo "  네트워크: IP 감지 실패 — Wi-Fi 연결 확인"
fi
echo ""
echo "  종료: Ctrl+C 또는 이 창 닫기"
echo "============================================================"
echo ""
echo "※ Mac이 \"네트워크 연결 허용\"을 물어보면 \"허용\" 클릭"
echo ""

# Launch streamlit bound to 0.0.0.0 so LAN devices can reach it
streamlit run app.py \
    --server.address=0.0.0.0 \
    --server.port=8501 \
    --browser.gatherUsageStats=false

# Keep window open if streamlit exits with error
echo ""
read -p "Streamlit이 종료되었습니다. 엔터를 누르면 창이 닫힙니다..."
