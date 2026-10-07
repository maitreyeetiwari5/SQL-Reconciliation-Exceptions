#!/usr/bin/env bash
# Rebuild everything from scratch. Deterministic: the data generator is seeded.
set -euo pipefail
python generate_data.py
python run_reconciliation.py
python validate_against_ground_truth.py
python build_dashboard.py
python export_excel.py
