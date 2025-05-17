#!/bin/bash

cd "$( dirname "${BASH_SOURCE[0]}" )/../" || exit 1

if command -v uv > /dev/null 2>&1; then
  uv run src/ratingrelay.py
else
  echo "uv not installed. Attempting to use local venv."
  .venv/bin/python src/ratingrelay.py
fi
