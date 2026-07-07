#!/usr/bin/env bash
# 전체 테스트 일괄 실행. 하나라도 실패하면 즉시 비정상 종료.
# 로컬: bash run_tests.sh
# CI:   .github/workflows/test.yml이 push마다 실행
set -e
cd "$(dirname "$0")"
for t in tests/test_*.py; do
  echo "=== $t"
  python "$t"
done
echo
echo "✓ 전체 테스트 통과"
