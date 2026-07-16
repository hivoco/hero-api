#!/bin/bash

# Start the Hero Destini FastAPI server with the correct virtual environment.

cd "$(dirname "$0")"

.venv/bin/uvicorn app.main:app --reload --host 127.0.0.1 --port 8000
