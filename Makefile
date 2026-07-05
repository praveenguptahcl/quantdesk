.PHONY: dev test stack-up stack-down backup
dev:        ## run the zero-dependency backend + GUI on 127.0.0.1:8700
	python3 api/server.py
test:       ## run the test suite (stdlib unittest)
	python3 -m unittest discover -s tests -v
stack-up:   ## M3+: full docker stack
	docker compose -f infra/docker-compose.yml --env-file .env up -d --wait
stack-down:
	docker compose -f infra/docker-compose.yml down
backup:
	python3 scripts/backup.py
