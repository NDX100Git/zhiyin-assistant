#!/usr/bin/env bash
# 智印助手单机演示：一条命令起停 MySQL / MinIO 容器、后端、学生端、老师端。
#
#   bash backend/scripts/dev_up.sh            # 起全套（已起的不重复起）
#   bash backend/scripts/dev_up.sh --stop     # 停掉本脚本起的进程与进程组
#   bash backend/scripts/dev_up.sh --status   # 看本脚本起的进程还在不在
#
# 日志与 pid 固定在 .runtime/demo/ 下：
#   .runtime/demo/logs/{backend,student,teacher}.log
#   .runtime/demo/{backend,student,teacher}.pid
#
# 三条安全规则：
# - 只操作自己起的进程：pid 文件存在、进程还活着、且它的工作目录在本工作树内才动手；
# - 端口被别的进程占着就报出端口与占用者后退出，不强杀；
# - MySQL / MinIO 容器已在跑就跳过，不重启、不重建别人的容器。

set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
BACKEND_DIR="$ROOT/backend"
STUDENT_DIR="$ROOT/student-app"
TEACHER_DIR="$ROOT/teacher-web"
RUN_DIR="$ROOT/.runtime/demo"
LOG_DIR="$RUN_DIR/logs"

PY="${PY:-/home/twilightrain/WorkZone/Temp/zhiyin-venv/bin/python}"
NPM="${NPM:-$(command -v npm || true)}"
BACKEND_PORT="${BACKEND_PORT:-8208}"
STUDENT_PORT="${STUDENT_PORT:-3207}"
TEACHER_PORT="${TEACHER_PORT:-3208}"

info() { printf '%s\n' "$*"; }
warn() { printf '!! %s\n' "$*" >&2; }
fail() { printf '!! %s\n' "$*" >&2; exit 1; }

pid_file() { printf '%s/%s.pid' "$RUN_DIR" "$1"; }

# 端口有人监听就返回 0
port_listening() {
  [ -n "$(ss -lntH "sport = :$1" 2>/dev/null)" ]
}

# 本脚本起的进程：pid 文件齐全、进程活着、工作目录在本工作树内
running_pid() {
  local file pid cwd
  file="$(pid_file "$1")"
  [ -f "$file" ] || return 1
  pid="$(cat "$file" 2>/dev/null || true)"
  [ -n "$pid" ] || return 1
  [ -d "/proc/$pid" ] || return 1
  cwd="$(readlink -f "/proc/$pid/cwd" 2>/dev/null || true)"
  [ -n "$cwd" ] || return 1
  case "$cwd" in
    "$ROOT" | "$ROOT"/*) printf '%s' "$pid" ;;
    *) return 1 ;;
  esac
}

# 端口是不是本脚本起的那个进程组在听：npm 会把 vite 派生到子进程，监听者不是 pid 本身，
# 但同一个 setsid 会话里 pgid == 记录的 pid，按进程组认领才不会把别人的端口当成自己的
port_owned_by_group() {
  local lpid
  lpid="$(ss -lntpH "sport = :$1" 2>/dev/null | grep -o 'pid=[0-9]*' | head -1 | cut -d= -f2)"
  [ -n "$lpid" ] || return 1
  [ "$(ps -o pgid= -p "$lpid" 2>/dev/null | tr -d ' ')" = "$2" ]
}

wait_port() {
  local port=$1 label=$2 timeout=${3:-60} i=0
  while [ "$i" -lt "$timeout" ]; do
    if port_listening "$port"; then
      return 0
    fi
    sleep 1
    i=$((i + 1))
  done
  fail "$label 端口 $port 在 ${timeout} 秒内没起来"
}

# 等一个 HTTP 地址就绪：可选传入要在响应里看到的字符串
wait_http() {
  local url=$1 label=$2 timeout=${3:-120} expect=${4:-} i=0 body
  while [ "$i" -lt "$timeout" ]; do
    body="$(curl -fsS --max-time 3 "$url" 2>/dev/null || true)"
    if [ -n "$body" ]; then
      if [ -z "$expect" ] || printf '%s' "$body" | grep -q "$expect"; then
        return 0
      fi
    fi
    sleep 1
    i=$((i + 1))
  done
  fail "$label 健康检查没过：$url 在 ${timeout} 秒内没返回预期内容（日志在 $LOG_DIR）"
}

precheck_ports() {
  local bad=0 entry label port name pid
  for entry in "后端:$BACKEND_PORT:backend" "学生端:$STUDENT_PORT:student" \
    "老师端:$TEACHER_PORT:teacher"; do
    IFS=: read -r label port name <<< "$entry"
    port_listening "$port" || continue
    if pid="$(running_pid "$name")" && port_owned_by_group "$port" "$pid"; then
      info "  $label :$port 已经由本脚本起的进程（pid $pid）占着，不重复启动"
      continue
    fi
    bad=1
    warn "$label 端口 $port 被别的进程占用，占用者："
    ss -lntpH "sport = :$port" >&2 || true
  done
  if [ "$bad" -ne 0 ]; then
    fail "端口被别的进程占着，本脚本不强杀；请先确认占用者是谁，或改用 BACKEND_PORT / STUDENT_PORT / TEACHER_PORT 换端口"
  fi
}

start_containers() {
  local name state
  command -v docker >/dev/null 2>&1 || fail "没有 docker 命令，起不了 MySQL / MinIO 容器"
  for name in zhiyin-mysql zhiyin-minio; do
    state="$(docker inspect -f '{{.State.Running}}' "$name" 2>/dev/null || true)"
    if [ "$state" = "true" ]; then
      info "  容器 $name 已在运行，跳过（不重启别人的容器）"
      continue
    fi
    if [ "$state" != "false" ]; then
      fail "容器 $name 不存在：请先在 backend 目录执行 docker compose up -d 建一次（本脚本不创建容器）"
    fi
    info "  启动容器 $name"
    docker start "$name" >/dev/null
  done
  # 起过才等；已在跑的两三秒内就能连上
  wait_port 3306 "MySQL" 90
  wait_port 9000 "MinIO" 90
}

start_backend() {
  if port_listening "$BACKEND_PORT"; then
    info "  后端 :$BACKEND_PORT 已在运行，跳过"
    return 0
  fi
  info "  启动后端 :$BACKEND_PORT"
  # setsid 另起一个会话：--stop 时按进程组收拾，npm 派生的子进程一起停
  setsid bash -c "cd '$BACKEND_DIR' && exec '$PY' -m uvicorn app.main:app --host 0.0.0.0 --port $BACKEND_PORT" \
    >>"$LOG_DIR/backend.log" 2>&1 &
  echo $! >"$(pid_file backend)"
  wait_http "http://127.0.0.1:$BACKEND_PORT/api/health" "后端" 120 '"code":0'
}

start_student() {
  if port_listening "$STUDENT_PORT"; then
    info "  学生端 :$STUDENT_PORT 已在运行，跳过"
    return 0
  fi
  info "  启动学生端 H5 :$STUDENT_PORT"
  setsid bash -c "cd '$STUDENT_DIR' && exec '$NPM' run dev:h5 -- --port $STUDENT_PORT --host 0.0.0.0" \
    >>"$LOG_DIR/student.log" 2>&1 &
  echo $! >"$(pid_file student)"
  wait_http "http://127.0.0.1:$STUDENT_PORT/" "学生端" 240 ''
}

start_teacher() {
  if port_listening "$TEACHER_PORT"; then
    info "  老师端 :$TEACHER_PORT 已在运行，跳过"
    return 0
  fi
  info "  启动老师端 :$TEACHER_PORT"
  setsid bash -c "cd '$TEACHER_DIR' && exec '$NPM' run dev -- --port $TEACHER_PORT --host 0.0.0.0" \
    >>"$LOG_DIR/teacher.log" 2>&1 &
  echo $! >"$(pid_file teacher)"
  wait_http "http://127.0.0.1:$TEACHER_PORT/" "老师端" 180 ''
}

stop_one() {
  local name=$1 label=$2 pid pgid i=0
  if ! pid="$(running_pid "$name")"; then
    info "  $label 没在运行（不是本脚本起的，或已经停了）"
    rm -f "$(pid_file "$name")"
    return 0
  fi
  pgid="$(ps -o pgid= -p "$pid" 2>/dev/null | tr -d ' ' || true)"
  if [ "$pgid" = "$pid" ]; then
    kill -TERM -- "-$pid" 2>/dev/null || true
  else
    kill -TERM "$pid" 2>/dev/null || true
  fi
  while [ "$i" -lt 15 ]; do
    kill -0 "$pid" 2>/dev/null || break
    sleep 1
    i=$((i + 1))
  done
  if kill -0 "$pid" 2>/dev/null; then
    info "  $label 15 秒没退出，强制结束本脚本起的进程组（pid $pid）"
    if [ "$pgid" = "$pid" ]; then
      kill -KILL -- "-$pid" 2>/dev/null || true
    else
      kill -KILL "$pid" 2>/dev/null || true
    fi
  fi
  rm -f "$(pid_file "$name")"
  info "  已停 $label（pid $pid）"
}

show_status() {
  local entry label name pid
  for entry in "后端:backend" "学生端:student" "老师端:teacher"; do
    IFS=: read -r label name <<< "$entry"
    if pid="$(running_pid "$name")"; then
      info "  $label 在跑（pid $pid）"
    else
      info "  $label 没在跑"
    fi
  done
}

start_all() {
  [ -x "$PY" ] || fail "找不到 Python：$PY（可用 PY=... 指定）"
  [ -n "$NPM" ] || fail "找不到 npm 命令（可用 NPM=... 指定）"
  mkdir -p "$LOG_DIR"

  info "== 检查端口 =="
  precheck_ports

  info "== 起 MySQL / MinIO 容器 =="
  start_containers

  info "== 起服务 =="
  start_backend
  start_student
  start_teacher

  local lan_ip
  lan_ip="$(hostname -I 2>/dev/null | awk '{print $1}')"
  info ""
  info "全部就绪："
  info "  后端      http://127.0.0.1:$BACKEND_PORT/docs （局域网 http://$lan_ip:$BACKEND_PORT）"
  info "  学生端    http://127.0.0.1:$STUDENT_PORT"
  info "  老师端    http://127.0.0.1:$TEACHER_PORT"
  info "  日志      $LOG_DIR/{backend,student,teacher}.log"
  info "  停服务    bash backend/scripts/dev_up.sh --stop"
}

stop_all() {
  info "== 停本脚本起的进程（MySQL / MinIO 容器不动）=="
  stop_one teacher "老师端"
  stop_one student "学生端"
  stop_one backend "后端"
}

usage() {
  cat <<'EOF'
用法：
  bash backend/scripts/dev_up.sh            起全套（MySQL / MinIO、后端 :8208、学生端 :3207、老师端 :3208）
  bash backend/scripts/dev_up.sh --stop     停掉本脚本起的进程
  bash backend/scripts/dev_up.sh --status   看本脚本起的进程状态

可用环境变量换端口：BACKEND_PORT / STUDENT_PORT / TEACHER_PORT；换 Python：PY=...；换 npm：NPM=...
EOF
}

case "${1:-}" in
  "") start_all ;;
  --stop) stop_all ;;
  --status) show_status ;;
  -h | --help) usage ;;
  *) usage; exit 1 ;;
esac
