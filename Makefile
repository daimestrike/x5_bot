.PHONY: install init demo run check test validate seed package
install:
	python3 install.py --dev
init:
	python3 scripts/init_env.py
demo:
	BOT_MODE=demo ENABLE_DEV_CONSOLE=true python3 -m uvicorn app.main:create_app --factory --env-file .env --host 127.0.0.1 --port 8080 --workers 1 --limit-concurrency 32 --no-access-log
run:
	python3 run.py
check:
	python3 run.py --check
test:
	python3 -m pytest -q
validate:
	python3 scripts/validate_content.py
seed:
	set -a; . ./.env; set +a; python3 scripts/seed_demo.py
package:
	python3 scripts/package.py
