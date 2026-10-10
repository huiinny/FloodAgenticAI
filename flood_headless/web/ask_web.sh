#!/usr/bin/env bash
# 터미널에서 질문하면 켜져 있는 상황판(웹)에 바로 나타난다:  web/ask_web.sh "신대방 쪽 지금 어때?"  [포트]
# 질문 시각은 상황판의 가상 시계 시각이다. 진행 상황은 상황판 오른쪽 '에이전트 일지'에 실시간으로 쌓인다.
Q="${1:?사용법: ask_web.sh \"질문\" [포트]}"; PORT="${2:-8765}"
curl -s -X POST "http://127.0.0.1:$PORT/api/ask" -H 'Content-Type: application/json' \
  -d "$(jq -n --arg q "$Q" '{q:$q}')" >/dev/null && echo "질문을 보냈습니다. 상황판 오른쪽 '에이전트 일지'를 보세요."
