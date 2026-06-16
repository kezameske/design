#!/bin/bash
# Double-click this file in Finder to launch the poster automation app.
# It activates the virtualenv and starts the Streamlit server.

set -e

# Always run from this script's directory (works regardless of how it's launched)
cd "$(dirname "$0")"

echo "============================================================"
echo "  Poster Automation v1 — starting server"
echo "============================================================"
echo ""
echo "Project folder: $(pwd)"
echo ""

# Check venv exists
if [ ! -d ".venv" ]; then
    echo "❌ No .venv folder. Create the virtualenv first:"
    echo ""
    echo "  python3 -m venv .venv"
    echo "  source .venv/bin/activate"
    echo "  pip install -r requirements.txt"
    echo ""
    read -p "Press Enter to close this window..."
    exit 1
fi

# Check .env exists
if [ ! -f ".env" ]; then
    echo "❌ No .env file. Copy .env.example and enter your key:"
    echo ""
    echo "  cp .env.example .env"
    echo "  open -e .env"
    echo ""
    read -p "Press Enter to close this window..."
    exit 1
fi

# Activate venv
source .venv/bin/activate
echo "✓ Virtualenv activated"
echo "✓ Python: $(which python3)"
echo "✓ Streamlit: $(streamlit --version 2>/dev/null || echo 'NOT INSTALLED')"
echo ""

# Check streamlit is installed
if ! command -v streamlit &> /dev/null; then
    echo "❌ streamlit is not installed. Install it with:"
    echo ""
    echo "  pip install -r requirements.txt"
    echo ""
    read -p "Press Enter to close this window..."
    exit 1
fi

# Detect local IP for network access (try en0 wifi, then en1)
LOCAL_IP=$(ipconfig getifaddr en0 2>/dev/null || ipconfig getifaddr en1 2>/dev/null || echo "")

echo "============================================================"
echo "  Starting Streamlit server..."
echo "============================================================"
echo ""
echo "  Local:    http://localhost:8501"
if [ -n "$LOCAL_IP" ]; then
    echo "  Network:  http://${LOCAL_IP}:8501"
    echo "            (teammates on the same Wi-Fi can use the address above)"
else
    echo "  Network: IP detection failed — check your Wi-Fi connection"
fi
echo ""
echo "  Quit: Ctrl+C or close this window"
echo "============================================================"
echo ""
echo "※ If macOS asks to \"allow network connections\", click \"Allow\""
echo ""

# Launch streamlit bound to 0.0.0.0 so LAN devices can reach it
streamlit run app.py \
    --server.address=0.0.0.0 \
    --server.port=8501 \
    --browser.gatherUsageStats=false

# Keep window open if streamlit exits with error
echo ""
read -p "Streamlit has stopped. Press Enter to close this window..."
