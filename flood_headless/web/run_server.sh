#!/usr/bin/env bash
# 도림천 콘솔 웹 서버:  web/run_server.sh start|stop|status|reset|prep  [포트, 기본 8765]
#   prep = 녹화 준비 (일지 비우기, 시계 03:55, ×10분/초, 자동 실행 켬)
HERE="$(cd "$(dirname "$0")/.." && pwd)"; PORT="${2:-8765}"; PID="$HERE/web/.server_$PORT.pid"; LOG="$HERE/web/server_$PORT.log"
PY="${FLOOD_PY:-$HOME/miniforge3/envs/floodagent/bin/python}"
case "${1:-start}" in
  start) [ -f "$PID" ] && kill -0 "$(cat "$PID")" 2>/dev/null && { echo "이미 실행 중 (pid $(cat "$PID"))"; exit 0; }
         cd "$HERE"; nohup "$PY" -W ignore web/server.py --port "$PORT" > "$LOG" 2>&1 &
         echo $! > "$PID"; sleep 3; head -1 "$LOG";;
  stop)  [ -f "$PID" ] && kill "$(cat "$PID")" 2>/dev/null; rm -f "$PID"; echo "정지";;
  reset) curl -s -X POST "http://127.0.0.1:$PORT/api/reset" >/dev/null && echo "일지 비우고 시계 03:50 으로";;
  prep)  curl -s -X POST "http://127.0.0.1:$PORT/api/reset" >/dev/null
         curl -s -X POST "http://127.0.0.1:$PORT/api/clock" -d '{"action":"set","now":"2022-08-08 03:55","speed":10}' >/dev/null
         curl -s -X POST "http://127.0.0.1:$PORT/api/auto" -d '{"on":true}' >/dev/null; echo "녹화 준비: 일지 비움, 03:55, ×10, 자동 실행 켬";;
  status) [ -f "$PID" ] && kill -0 "$(cat "$PID")" 2>/dev/null && echo "실행 중 (pid $(cat "$PID"))" || echo "꺼짐";;
esac
