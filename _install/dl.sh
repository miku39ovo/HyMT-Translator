#!/usr/bin/env bash
# curl 多线程分片下载（自动续传/重试），用于不稳定的国内 CDN
# 用法: dl.sh <url> <out> [线程数]
set -u
URL="$1"; OUT="$2"; N="${3:-12}"
DIR="$(dirname "$OUT")"; BASE="$(basename "$OUT")"
mkdir -p "$DIR"

full_size() {
  curl -sL --noproxy '*' -r 0-0 -D - -o /dev/null "$URL" \
    | tr -d '\r' | grep -i '^content-range:' | tail -1 | sed 's#.*/##' | tr -dc '0-9'
}

SIZE=$(full_size)
if [ -z "$SIZE" ]; then echo "ERR: 无法获取大小"; exit 1; fi
echo "总大小: $SIZE 字节, 线程: $N"

# 已有文件可作为第 0 片的前缀，节省流量
if [ -f "$OUT" ] && [ -s "$OUT" ]; then
  echo "复用已下载: $(stat -c %s "$OUT") 字节"
  mv -f "$OUT" "$DIR/$BASE.part000"
fi

STEP=$(( (SIZE + N - 1) / N ))

part_file() { printf "%s/%s.part%03d" "$DIR" "$BASE" "$1"; }

worker() {
  local i=$1
  local s=$(( i * STEP ))
  local e=$(( s + STEP - 1 ))
  [ $e -ge $SIZE ] && e=$(( SIZE - 1 ))
  local want=$(( e - s + 1 ))
  local pf; pf=$(part_file "$i")
  local tries=0
  while :; do
    local have=0
    [ -f "$pf" ] && have=$(stat -c %s "$pf")
    [ "$have" -ge "$want" ] && break
    tries=$((tries+1))
    [ $tries -gt 60 ] && { echo "part$i 放弃"; return 1; }
    local rs=$(( s + have ))
    curl -sL --noproxy '*' --max-time 600 -r "${rs}-${e}" "$URL" >> "$pf" 2>/dev/null
    local now=0; [ -f "$pf" ] && now=$(stat -c %s "$pf")
    if [ "$now" -le "$have" ]; then sleep 2; fi
    # 超过片长则截断（个别 CDN 忽略 Range）
    if [ "$now" -gt "$want" ]; then head -c "$want" "$pf" > "$pf.tmp" && mv -f "$pf.tmp" "$pf"; fi
  done
}

for ((i=0;i<N;i++)); do
  worker $i &
done

while :; do
  sleep 5
  alive=$(jobs -rp | wc -l)
  total=0
  for ((i=0;i<N;i++)); do
    pf=$(part_file $i)
    [ -f "$pf" ] && total=$(( total + $(stat -c %s "$pf") ))
  done
  echo "  进度 $(( total * 100 / SIZE ))%  ($(( total / 1048576 )) / $(( SIZE / 1048576 )) MB)  活跃线程 $alive"
  [ "$alive" -eq 0 ] && break
done
wait

total=0
for ((i=0;i<N;i++)); do
  pf=$(part_file $i)
  [ -f "$pf" ] && total=$(( total + $(stat -c %s "$pf") ))
done
if [ "$total" -ne "$SIZE" ]; then echo "ERR: 不完整 $total/$SIZE"; exit 2; fi

: > "$OUT"
for ((i=0;i<N;i++)); do cat "$(part_file $i)" >> "$OUT"; done
rm -f "$DIR/$BASE".part[0-9][0-9][0-9]
echo "完成: $OUT ($(stat -c %s "$OUT") 字节)"
