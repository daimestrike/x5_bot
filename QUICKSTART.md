# Быстрый запуск без Docker

Инструкция рассчитана на архив, скачанный из GitHub или GitLab. Нужен **64-разрядный Python 3.12**.

## Linux: внутренний PyPI

Откройте терминал в распакованной папке проекта и вставьте блок целиком. Замените адрес зеркала на адрес внутри X5:

```bash
chmod +x setup.sh start.sh
./setup.sh --index-url "https://ВНУТРЕННИЙ-PYPI/simple"
./start.sh
```

После строки `Rooms-бот запущен` откройте:

- диалог: <http://127.0.0.1:8080/dev/chat>
- метрики: <http://127.0.0.1:8080/metrics/dashboard>
- содержание: <http://127.0.0.1:8080/content/>

Остановить бот: нажмите `Ctrl+C` в этом терминале.

## Linux: полностью без сети

Этот вариант работает, только если в `wheels/` заранее положен полный набор `.whl` для Linux, архитектуры сервера и Python 3.12:

```bash
chmod +x setup.sh start.sh
./setup.sh --offline
./start.sh
```

## Windows

Откройте PowerShell или `cmd` в распакованной папке проекта.

Через внутренний PyPI:

```bat
setup.cmd --index-url "https://ВНУТРЕННИЙ-PYPI/simple"
start.cmd
```

Полностью без сети при наличии `.whl` в `wheels\`:

```bat
setup.cmd --offline
start.cmd
```

## Повторный запуск

Установка выполняется один раз. В следующие разы достаточно:

```bash
./start.sh
```

На Windows:

```bat
start.cmd
```

## Быстрая проверка без запуска сервера

Linux:

```bash
./start.sh --check
```

Windows:

```bat
start.cmd --check
```

Успешный результат выглядит так:

```text
Конфигурация корректна: mode=demo, cards=35
```

## Запуск на другом порту

```bash
./start.sh --port 8090
```

Тогда адрес диалога будет `http://127.0.0.1:8090/dev/chat`.

## Если команда не запускается

- `Нужен Python 3.12` — установите 64-разрядный Python 3.12 и повторите установку.
- `В wheels/ нет .whl файлов` — используйте внутренний PyPI или добавьте полный wheelhouse.
- `Окружение не подготовлено` — сначала выполните `setup.sh` или `setup.cmd`.
- Порт занят — запустите `./start.sh --port 8090`.

## Перевод в рабочий режим Rooms

Демо-режим не подключается к Rooms. Перед рабочим запуском заполните `.env` реальными параметрами Rooms, установите `BOT_MODE=production`, подтвердите контракт `ROOMS_CONTRACT_CONFIRMED=true` и выполните:

```bash
./start.sh --check
./start.sh
```

Параметры интеграции перечислены в [docs/ROOMS-INTEGRATION.md](docs/ROOMS-INTEGRATION.md).
