#!/usr/bin/env bash
#
# PULSE 통합 검증 스크립트 (macOS/Linux)
# run_full_validation.ps1 의 Mac/Linux 포팅 버전.
#
# 흐름: FE lint -> FE build -> Spring test -> Python pytest
#       -> FastAPI/Spring/Vite 서버 기동(필요 시) -> Playwright smoke E2E
#
# 사용법:
#   scripts/run_full_validation.sh [옵션]
# 옵션:
#   --frontend-url <url>   기본 http://127.0.0.1:4173
#   --spring-url <url>     기본 http://127.0.0.1:8080
#   --fastapi-url <url>    기본 http://127.0.0.1:8000
#   --keep-servers-running 검증 후 기동한 서버를 끄지 않고 유지
#   -h, --help             도움말
#
set -euo pipefail

# --- 루트 경로: 스크립트 위치 기준으로 자동 유도 (하드코딩 제거) ---
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_PATH="$(cd "$SCRIPT_DIR/.." && pwd)"

# --- 기본값 ---
FRONTEND_URL="http://127.0.0.1:4173"
SPRING_URL="http://127.0.0.1:8080"
FASTAPI_URL="http://127.0.0.1:8000"
KEEP_SERVERS_RUNNING=0

# --- 인자 파싱 ---
while [[ $# -gt 0 ]]; do
  case "$1" in
    --frontend-url) FRONTEND_URL="$2"; shift 2;;
    --spring-url)   SPRING_URL="$2"; shift 2;;
    --fastapi-url)  FASTAPI_URL="$2"; shift 2;;
    --keep-servers-running) KEEP_SERVERS_RUNNING=1; shift;;
    -h|--help)
      sed -n '2,17p' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//'
      exit 0;;
    *) echo "알 수 없는 옵션: $1" >&2; exit 2;;
  esac
done

# --- 색상 ---
if [[ -t 1 ]]; then
  CYAN='\033[36m'; GREEN='\033[32m'; YELLOW='\033[33m'; RED='\033[31m'; RESET='\033[0m'
else
  CYAN=''; GREEN=''; YELLOW=''; RED=''; RESET=''
fi

STARTED_PIDS=()

step() { printf "\n${CYAN}==> %s${RESET}\n" "$1"; }

# URL이 어떤 HTTP 응답(2xx~5xx)이든 주면 준비된 것으로 간주.
# 연결 자체가 안 되면 curl 이 http_code 000 을 반환 → not ready.
test_url_ready() {
  local url="$1" code
  code="$(curl -s -o /dev/null -w '%{http_code}' --max-time 5 "$url" 2>/dev/null)"
  [[ -n "$code" && "$code" != "000" ]]
}

wait_for_url() {
  local url="$1" name="$2" timeout="${3:-180}"
  local deadline=$(( SECONDS + timeout ))
  while (( SECONDS < deadline )); do
    if test_url_ready "$url"; then
      printf "   ${GREEN}%s ready: %s${RESET}\n" "$name" "$url"
      return 0
    fi
    sleep 2
  done
  printf "   ${RED}%s did not become ready within %ss: %s${RESET}\n" "$name" "$timeout" "$url" >&2
  return 1
}

# 백그라운드로 서버 기동 후 PID 기록.
# 각 커맨드는 `bash -c "... exec <server>"` 형태라 $! 가 곧 서버 PID 가 된다.
start_if_needed() {
  local name="$1" url="$2"; shift 2
  if test_url_ready "$url"; then
    printf "   ${YELLOW}%s already running: %s${RESET}\n" "$name" "$url"
    return 0
  fi
  printf "   ${YELLOW}Starting %s...${RESET}\n" "$name"
  "$@" &
  STARTED_PIDS+=("$!")
  wait_for_url "$url" "$name"
}

# 자식(gradle 데몬, java, node/vite 등)까지 재귀적으로 종료한다.
kill_tree() {
  local pid="$1" sig="${2:-TERM}" child
  for child in $(pgrep -P "$pid" 2>/dev/null); do
    kill_tree "$child" "$sig"
  done
  kill "-$sig" "$pid" 2>/dev/null || true
}

cleanup() {
  if (( KEEP_SERVERS_RUNNING == 1 )); then
    printf "\n${YELLOW}--keep-servers-running: 기동한 서버를 유지합니다.${RESET}\n"
    if (( ${#STARTED_PIDS[@]} > 0 )); then
      printf "   PIDs: %s\n" "${STARTED_PIDS[*]}"
    fi
    return
  fi
  if (( ${#STARTED_PIDS[@]} == 0 )); then return; fi
  printf "\n${YELLOW}기동한 서버 종료 중...${RESET}\n"
  local pid
  for pid in "${STARTED_PIDS[@]}"; do
    kill_tree "$pid" TERM
  done
  sleep 2
  for pid in "${STARTED_PIDS[@]}"; do
    kill_tree "$pid" KILL
  done
}
trap cleanup EXIT INT TERM

# ===================== 검증 단계 =====================

step "Frontend lint"
( cd "$ROOT_PATH/pulse_FE" && npm run lint )

step "Frontend build"
( cd "$ROOT_PATH/pulse_FE" && npm run build )

step "Spring tests"
( cd "$ROOT_PATH/pulse_spring" && ./gradlew test )

step "Python pytest"
PY_BIN="$ROOT_PATH/pulse_python/.venv/bin/python"
if [[ ! -x "$PY_BIN" ]]; then
  printf "   ${RED}Python venv 가 없습니다: %s${RESET}\n" "$PY_BIN" >&2
  printf "   먼저 생성하세요: cd pulse_python && python3 -m venv .venv && ./.venv/bin/pip install -r requirements.txt\n" >&2
  exit 1
fi
( cd "$ROOT_PATH/pulse_python" && "$PY_BIN" -m pytest -q )

step "Ensure FastAPI, Spring, and Vite servers are running"
start_if_needed "FastAPI" "$FASTAPI_URL/" \
  bash -c "cd '$ROOT_PATH/pulse_python' && exec '$PY_BIN' run_server.py"

start_if_needed "Spring Boot" "$SPRING_URL/api/auth/login" \
  bash -c "cd '$ROOT_PATH/pulse_spring' && exec ./gradlew bootRun --console=plain"

start_if_needed "Vite frontend" "$FRONTEND_URL/" \
  bash -c "cd '$ROOT_PATH/pulse_FE' && exec npm run dev -- --host 127.0.0.1 --port 4173"

step "Playwright smoke E2E"
# 러너(playwright-smoke.mjs)는 시스템 브라우저 경로가 필요한데 기본 후보가 Windows 경로뿐이다.
# Mac/Linux 브라우저를 자동 탐지해 PULSE_PLAYWRIGHT_BROWSER_PATH 로 주입한다.
if [[ -z "${PULSE_PLAYWRIGHT_BROWSER_PATH:-}" ]]; then
  for cand in \
    "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome" \
    "/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge" \
    "/Applications/Chromium.app/Contents/MacOS/Chromium" \
    "$(command -v google-chrome 2>/dev/null || true)" \
    "$(command -v chromium 2>/dev/null || true)"; do
    if [[ -n "$cand" && -x "$cand" ]]; then
      export PULSE_PLAYWRIGHT_BROWSER_PATH="$cand"
      break
    fi
  done
fi
if [[ -z "${PULSE_PLAYWRIGHT_BROWSER_PATH:-}" ]]; then
  printf "   ${YELLOW}경고: 시스템 Chrome/Edge 를 찾지 못했습니다. PULSE_PLAYWRIGHT_BROWSER_PATH 를 직접 지정하세요.${RESET}\n" >&2
fi

ARTIFACTS_DIR="$ROOT_PATH/output/playwright"
mkdir -p "$ARTIFACTS_DIR"
(
  cd "$ROOT_PATH/pulse_FE"
  export PULSE_FRONTEND_URL="$FRONTEND_URL"
  export PULSE_SPRING_URL="$SPRING_URL"
  export PULSE_FASTAPI_URL="$FASTAPI_URL"
  export PULSE_PLAYWRIGHT_ARTIFACTS_DIR="$ARTIFACTS_DIR"
  npm run e2e:smoke -- \
    --frontend-url "$FRONTEND_URL" \
    --spring-url "$SPRING_URL" \
    --fastapi-url "$FASTAPI_URL" \
    --artifacts-dir "$ARTIFACTS_DIR"
)

printf "\n${GREEN}✅ 전체 검증 완료${RESET}\n"
