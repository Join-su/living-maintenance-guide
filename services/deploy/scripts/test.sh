#!/bin/sh
# 시험: 가짜 provider + 파일 backend. 네트워크·실제 provider 없음.
exec "$(dirname "$0")/py" -m pytest -p no:cacheprovider -q "$@"
