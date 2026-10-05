#!/bin/sh
# Установить бота как службу systemd — подставляет каталог, пользователя и системный python3.
#
#   sudo ./deploy/install-service.sh            # служба от имени того, кто вызвал sudo
#   sudo ./deploy/install-service.sh botuser    # от имени другого пользователя
#
# Повторный запуск безопасен: юнит перезаписывается, служба перезапускается.
set -eu

if [ "$(id -u)" -ne 0 ]; then
    echo "Запустите через sudo: sudo $0 $*" >&2
    exit 1
fi

DIR=$(cd "$(dirname "$0")/.." && pwd)
RUN_USER=${1:-${SUDO_USER:-root}}
RUN_GROUP=$(id -gn "$RUN_USER")
PYTHON=$(command -v python3 || true)
NAME=${ROOMS_SERVICE:-rooms-bot}
UNIT=/etc/systemd/system/$NAME.service

[ -n "$PYTHON" ] || { echo "python3 не найден" >&2; exit 1; }
[ -f "$DIR/run.py" ] || { echo "Не похоже на каталог бота: $DIR" >&2; exit 1; }
[ -f "$DIR/.env" ] || { echo "Нет $DIR/.env — сначала выполните ./setup.sh" >&2; exit 1; }

# Каталог в /home закрыт для служб при ProtectHome=true — в этом случае защиту не включаем.
case "$DIR" in
    /home/*|/root/*) PROTECT_HOME=false ;;
    *) PROTECT_HOME=true ;;
esac

mkdir -p "$DIR/data" "$DIR/backups"
chown -R "$RUN_USER:$RUN_GROUP" "$DIR/data" "$DIR/content" "$DIR/backups" "$DIR/.env"

# Проверка конфигурации от имени службы до того, как её включать
if ! su -s /bin/sh "$RUN_USER" -c "cd '$DIR' && '$PYTHON' run.py --check"; then
    echo "Проверка конфигурации не прошла — служба не установлена." >&2
    exit 1
fi

cat > "$UNIT" <<UNIT_EOF
[Unit]
Description=Справочник Аватара — бот Rooms
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=$RUN_USER
Group=$RUN_GROUP
WorkingDirectory=$DIR
ExecStart=$PYTHON $DIR/run.py
Restart=on-failure
RestartSec=5
UMask=0077
NoNewPrivileges=true
PrivateTmp=true
ProtectSystem=full
ProtectHome=$PROTECT_HOME

[Install]
WantedBy=multi-user.target
UNIT_EOF

systemctl daemon-reload
systemctl enable "$NAME" >/dev/null
systemctl restart "$NAME"
sleep 2
if systemctl is-active --quiet "$NAME"; then
    echo "Служба $NAME запущена от $RUN_USER ($PYTHON)."
    echo "Логи:      sudo journalctl -u $NAME -f"
    echo "Обновлять: ./update.sh <архив> --service $NAME"
else
    echo "Служба не поднялась. Последние строки журнала:" >&2
    journalctl -u "$NAME" -n 30 --no-pager >&2
    exit 1
fi
