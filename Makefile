.DEFAULT:
	@echo "No such command (or you passed two or more targets to make). List of possible commands: make help"
	@exit 2

.DEFAULT_GOAL := help

##@ Help

.PHONY: help
help: ## Show this help
	@awk 'BEGIN {FS = ":.*##"; printf "\nUsage:\n  make \033[36m<target> <arg=value>\033[0m\n"} /^[a-zA-Z0-9._-]+:.*?##/ { printf "  \033[36m%-25s\033[0m %s\n", $$1, $$2 } /^##@/ { printf "\n\033[1m  %s\033[0m\n\n", substr($$0, 5) } ' $(MAKEFILE_LIST)

##@ Local development

.PHONY: run_infra
run_infra: ## Run S3 compatible docker container
	@docker compose -f docker-compose.yml up -d minio

.PHONY: lint
lint: ## Run linters
	@uv run ruff check .
	@uv run mypy src

.PHONY: format
format: ## Run formatters
	@uv run ruff format .
	@uv run ruff check --fix .

.PHONY: test
test: ## Run tests
	@uv run pytest

.PHONY: example_collectstatic
example_collectstatic: ## Run the example app's collectstatic against local MinIO
	@uv run python example_app/probe.py ensure-bucket
	@uv run python example_app/manage.py collectstatic --noinput
	@uv run python example_app/probe.py list
