#!/usr/bin/env bash
# 자연어 질문 하나로 총괄 에이전트를 깨운다. 총괄이 관측·예측 하위 에이전트에게 나눠 맡기고 브리핑을 합친다. Claude Code 헤드리스(claude -p), API 키 불필요.
# 에이전트가 질문에서 날짜·관측/예보를 판단하고, status 로 가능 여부를 본 뒤 도구를 부른다.
#
#   ./ask.sh "오늘 도림천 침수 위험 어때?"
#   ./ask.sh --now "2022-08-08 05:00" "오늘 침수 위험 예보해줘"         # 과거 시점 재현
#   ./ask.sh --now "2022-08-09 09:00" "어제 예보가 실제랑 얼마나 맞았어?"
#
# 모델: 기본 sonnet. FLOOD_MODEL=opus ./ask.sh "..." 처럼 바꿀 수 있다.
# 결과: outputs/queries/<시각>.md (답변), <시각>.json (최종 응답), <시각>.jsonl (에이전트 실행 기록). 답변은 stdout 에도 출력.
set -euo pipefail

if [ "${1:-}" = "--now" ]; then export FLOOD_NOW="${2:?--now \"YYYY-MM-DD HH:MM\"}"; shift 2; fi
QUERY="${1:?사용법: ask.sh [--now \"YYYY-MM-DD HH:MM\"] \"질문\"}"

HERE="$(cd "$(dirname "$0")" && pwd)"
TOOL="$HERE/bin/flood_tool"
OUT="$HERE/outputs"
CLAUDE_BIN="${CLAUDE_BIN:-$(command -v claude || echo "$HOME/.nvm/versions/node/v22.23.3/bin/claude")}"
NOW="${FLOOD_NOW:-$(date '+%Y-%m-%d %H:%M')}"
MODEL="${FLOOD_MODEL:-sonnet}"            # claude 모델 (별칭 또는 전체 ID)
export FLOOD_NOW="$NOW"                     # 도구도 같은 기준 시각을 쓴다 (에이전트가 바꿀 수 없음)

fill() { sed -e "s#{TOOL}#$TOOL#g" -e "s#{OUT}#$OUT#g" -e "s#{NOW}#$NOW#g" "$HERE/prompts/$1"; }
SYS="$(fill lead.md)"
AGENTS="$(mktemp)"; trap 'rm -f "$AGENTS"' EXIT
# 하위 에이전트 2개: 관측(rain·sewer)과 예측(status·blend·sensitivity). 프롬프트는 prompts/ 에서 읽는다.
jq -n --arg obs "$(fill obs_agent.md)" --arg fc "$(fill forecast_agent.md)" --arg model "$MODEL" '{
  "obs-agent":      {description: "관측 에이전트: 지금까지 내린 비(레이더·AWS)와 하수관 수위계로 서울시 침수 예·경보 기준 도달과 현장 이상을 판단", prompt: $obs, tools: ["Bash"], model: $model},
  "forecast-agent": {description: "예측 에이전트: 관측+최신 예보 블렌딩으로 오늘 위험 분구를 고르고 건물 정적 위험지도로 우선 확인 건물을 집음", prompt: $fc, tools: ["Bash"], model: $model}
}' > "$AGENTS"
QDIR="$OUT/queries"; mkdir -p "$QDIR"
STAMP="${FLOOD_RUN_ID:-$(date '+%Y%m%d_%H%M%S')}"

cd "$HERE"
"$CLAUDE_BIN" -p "$QUERY" \
  --model "$MODEL" \
  --append-system-prompt "$SYS" \
  --agents "$AGENTS" \
  --allowedTools "Bash($TOOL:*)" "Task" "Agent" \
  --disallowedTools "Read" "Glob" "Grep" "Write" "Edit" "WebFetch" "WebSearch" \
  --max-turns 30 \
  --output-format stream-json --verbose < /dev/null > "$QDIR/$STAMP.jsonl"
# 실행 기록(.jsonl: 총괄·하위 에이전트의 판단과 도구 호출 전부)에서 최종 결과만 .json 으로 뽑는다
jq -c 'select(.type=="result")' "$QDIR/$STAMP.jsonl" | tail -1 > "$QDIR/$STAMP.json"

{ printf '> 질문 (%s 기준): %s\n\n' "$NOW" "$QUERY"; jq -r '.result // empty' "$QDIR/$STAMP.json"; } > "$QDIR/$STAMP.md"
cat "$QDIR/$STAMP.md"
echo; echo "[저장] $QDIR/$STAMP.md"
