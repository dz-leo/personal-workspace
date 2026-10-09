#!/bin/bash
# 个人账本 —— 数据库备份（双击运行）
# 备份到 项目目录/backups/ledger-日期_时间.sql.gz，自动清理 30 天前的旧备份
# 数据库账号口令从 ~/.ledger.conf 读（见 server.py --init-conf），脚本里不写明文

cd "$(dirname "$0")" || exit 1
MYSQL=/opt/homebrew/opt/mysql@8.0/bin/mysql
DUMP=/opt/homebrew/opt/mysql@8.0/bin/mysqldump
STAMP=$(date +%Y-%m-%d_%H%M)
OUT="backups/ledger-$STAMP.sql"

CONF="${LEDGER_CONF:-$HOME/.ledger.conf}"
DB_USER="root"
DB_PASS=""
DB_NAME="ledger"
if [ -f "$CONF" ]; then
  DB_USER=$(sed -n 's/^db_user=//p' "$CONF" | tail -1)
  DB_PASS=$(sed -n 's/^db_password=//p' "$CONF" | tail -1)
  DB_NAME=$(sed -n 's/^db_name=//p' "$CONF" | tail -1)
  [ -n "$DB_USER" ] || DB_USER="root"
  [ -n "$DB_NAME" ] || DB_NAME="ledger"
fi
# 用环境变量传口令：避免出现在命令行里（ps 能看到）、也不会触发 mysql 的明文告警
export MYSQL_PWD="$DB_PASS"

if [ -z "$DB_PASS" ] && [ -f "$CONF" ]; then
  echo "[错误] $CONF 里 db_password 是空的，先填上再备份"
  exit 1
fi

mkdir -p backups

# 先确认 MySQL 活着
if ! "$MYSQL" -u"$DB_USER" -e "SELECT 1" >/dev/null 2>&1; then
  echo "[错误] 连不上 MySQL（用户 $DB_USER），先执行： brew services start mysql@8.0"
  echo "       若装了 MySQL 但用户名不同，改 ~/.ledger.conf 里的 db_user / db_password"
  exit 1
fi

echo "正在备份数据库 $DB_NAME ..."
if "$DUMP" -u"$DB_USER" --default-character-set=utf8mb4 --skip-lock-tables "$DB_NAME" > "$OUT" 2>/dev/null; then
  if [ -s "$OUT" ]; then
    gzip -f "$OUT"
    SIZE=$(du -h "$OUT.gz" | cut -f1)
    echo "[完成] $OUT.gz  ($SIZE)"
  else
    echo "[错误] 备份文件为空，已丢弃"
    rm -f "$OUT"
    exit 1
  fi
else
  echo "[错误] mysqldump 执行失败，检查 $CONF 里的 db_user / db_password"
  rm -f "$OUT"
  exit 1
fi

# 清理 30 天前的旧备份
find backups -name "ledger-*.sql.gz" -mtime +30 -delete 2>/dev/null

echo ""
echo "当前备份（最多列 10 份）："
ls -lh backups/ledger-*.sql.gz 2>/dev/null | tail -10 | awk '{print "  " $NF "  " $5}'
echo ""
echo "恢复方法： gunzip -c backups/ledger-xxx.sql.gz | mysql -u$DB_USER $DB_NAME"
echo "        （mysql 会提示输入密码）"