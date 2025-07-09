#!/usr/bin/env bash
# exit on error
set -o errexit

pip install -r requirements.txt

# Run the Python script to create database tables and admin user
python init_db.py