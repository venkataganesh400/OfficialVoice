#!/usr/bin/env bash
# exit on error
set -o errexit

echo "--- Installing Python dependencies ---"
pip install -r requirements.txt

echo "--- Running database initialization ---"
# The 'init_db.py' script will create tables and the default admin user.
python init_db.py

echo "--- Build finished successfully! ---"