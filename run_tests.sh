#!/bin/sh
set -e
black --check src/pygoattracker tests
pylint src/pygoattracker tests
pytest --cov=pygoattracker --cov-report=term-missing --cov-fail-under=85
