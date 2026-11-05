#!/bin/bash
set -e

DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" >/dev/null 2>&1 && pwd )"
cd "$DIR"

echo "=========================================================="
echo " Starting JanSetu Grievance Backend (FastAPI on Port 8000)"
echo " Native IndicTrans2 Neural Translation Engine (Apple Metal)"
echo "=========================================================="

source venv/bin/activate
exec uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload

