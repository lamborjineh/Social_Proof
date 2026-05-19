#!/usr/bin/env bash
# build.sh — Render build script
# Run: bash build.sh  (Render calls this via buildCommand)
set -e

echo "==> Installing Python dependencies..."
pip install --upgrade pip
pip install -r requirements.txt

echo "==> Downloading spaCy model..."
python -m spacy download en_core_web_sm

echo "==> Installing Playwright Chromium (no system deps — pre-installed on Render)..."
# --no-deps skips the sudo apt-get step that fails on Render's sandboxed build env.
# Render's Python runtime already ships the Chromium system libraries.
PLAYWRIGHT_BROWSERS_PATH=/opt/render/project/.playwright \
  playwright install --no-deps chromium

echo "==> Build complete."
