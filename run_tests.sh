#!/bin/sh
set -e
black --check src/pygoattracker tests scripts
pylint src/pygoattracker tests scripts
pytest --cov=pygoattracker --cov-report=term-missing --cov-fail-under=85
