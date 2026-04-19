PROJECT_ID  ?= $(shell gcloud config get-value project 2>/dev/null)
GCS_BUCKET   = benchmark-$(PROJECT_ID)
AACT_DIR     = "$(PWD)/data/aact"

.PHONY: infra data-download batch pipeline-image stream-up kestra-upload stream-backfill dbt-batch dbt-streaming dbt all clean help

KESTRA_URL  = http://localhost:8080
KESTRA_AUTH = admin@benchmark.io:Admin1234!

help:
	@echo "BenchMark — Clinical Trial Research Intelligence Platform"
	@echo ""
	@echo "Targets:"
	@echo "  infra            Provision GCP resources via Terraform"
	@echo "  data-download    Download AACT pipe-delimited export (~2.25 GB)"
	@echo "  batch            Run Spark job + dbt batch models (manual; Kestra runs this monthly)"
	@echo "  pipeline-image   Build benchmark-pipeline Docker image (PySpark + dbt + kafka deps)"
	@echo "  stream-up        Build pipeline image + start Docker Compose services"
	@echo "  kestra-upload    Upload flow YAMLs to Kestra via REST API"
	@echo "  stream-backfill  Backfill 14 days of streaming data for dashboard initial population"
	@echo "  dbt-batch        Run dbt batch models only"
	@echo "  dbt-streaming    Run dbt streaming models only"
	@echo "  dbt              Run all dbt models + tests"
	@echo "  all              Full setup: infra → download → batch → stream-up → backfill → dbt"
	@echo "  clean            Tear down Docker + Terraform + delete local data"

infra:
	@echo "Provisioning GCP infrastructure..."
	terraform -chdir=infra init
	terraform -chdir=infra apply -var="project_id=$(PROJECT_ID)" -auto-approve
	@mkdir -p credentials
	terraform -chdir=infra output -raw service_account_key | base64 -d > credentials/sa_key.json
	@echo "Service account key saved to credentials/sa_key.json"

data-download:
	bash data/download_aact.sh

# batch: manual trigger for development.
# In production, Kestra monthly_batch_flow handles scheduling.
# Requires: uv sync run once, and JAVA_HOME set (JDK 17 or 21).
batch: data-download
	@echo "Running Spark job..."
	GOOGLE_APPLICATION_CREDENTIALS="$(PWD)/credentials/sa_key.json" \
	uv run python spark/enrich_and_load.py ${AACT_DIR} ${PROJECT_ID}
	@$(MAKE) dbt-batch

pipeline-image:
	@echo "Building benchmark-pipeline Docker image..."
	docker build -t benchmark-pipeline:latest -f Dockerfile.pipeline .
	@echo "  ✓ benchmark-pipeline:latest ready"

stream-up: pipeline-image
	@echo "Starting Docker Compose services..."
	docker compose -p benchmark -f streaming/docker-compose.yml up -d --build
	@echo "Waiting for Kestra to be ready (up to 90s)..."
	@until curl -so /dev/null http://localhost:8080 2>/dev/null; do \
	  printf '.'; sleep 3; \
	done
	@echo ""
	@echo "Flink UI:  http://localhost:8081"
	@echo "Kestra UI: http://localhost:8080"
	@$(MAKE) kestra-upload

# Upload flow YAMLs to the running Kestra instance via its REST API.
# Kestra's PUT /api/v1/flows/{namespace}/{id} creates or updates a flow.
# The flow id must match the 'id:' field at the top of each YAML file.
kestra-upload:
	@echo "Uploading flows to Kestra..."
	@echo "--- monthly_batch ---"
	@$(call kestra_upsert,kestra/flows/monthly_batch_flow.yml,benchmark/monthly_batch)
	@echo "--- daily_streaming ---"
	@$(call kestra_upsert,kestra/flows/daily_streaming_flow.yml,benchmark/daily_streaming)
	@echo "Flows visible at: $(KESTRA_URL)/ui/flows"

# POST to create; if 409 or 422 (already exists) use PUT to update.
define kestra_upsert
  HTTP=$$(curl -s -o /tmp/kestra_resp -w "%{http_code}" -X POST $(KESTRA_URL)/api/v1/flows \
    -u $(KESTRA_AUTH) -H "Content-Type: application/x-yaml" --data-binary @$(1)); \
  if [ "$$HTTP" = "409" ] || [ "$$HTTP" = "422" ]; then \
    curl -s -w "\nHTTP %{http_code}\n" -X PUT $(KESTRA_URL)/api/v1/flows/$(2) \
      -u $(KESTRA_AUTH) -H "Content-Type: application/x-yaml" --data-binary @$(1); \
  else \
    cat /tmp/kestra_resp; echo "\nHTTP $$HTTP"; \
  fi
endef

# Backfill the last 14 days of CT.gov updates to populate the temporal dashboard tile.
# This fetches REAL data from the CT.gov API for each of the past 14 dates.
stream-backfill:
	@echo "Backfilling 14 days of streaming data..."
	@echo "Resetting Kafka topic and consumer group for clean backfill..."
	@docker exec $$(docker ps -qf name=benchmark-redpanda-1) \
	  rpk topic delete trial-updates 2>/dev/null || true
	@docker exec $$(docker ps -qf name=benchmark-redpanda-1) \
	  rpk topic create trial-updates --partitions 1
	@docker exec $$(docker ps -qf name=benchmark-redpanda-1) \
	  rpk group delete pulse-consumer 2>/dev/null || true
	@for i in $$(seq 13 -1 0); do \
	  d=$$(date -d "-$$i days" +%Y-%m-%d 2>/dev/null || date -v-$${i}d +%Y-%m-%d); \
	  echo "--- Backfilling $$d ---"; \
	  uv run python streaming/producers/producer.py --date $$d; \
	  docker exec $$(docker ps -qf name=benchmark-jobmanager-1) \
	    flink run -py /opt/jobs/registration_pulse_job.py; \
	  GCP_PROJECT_ID=$(PROJECT_ID) \
	  GOOGLE_APPLICATION_CREDENTIALS="$(PWD)/credentials/sa_key.json" \
	    uv run python streaming/sync/pg_to_bq.py; \
	done
	@$(MAKE) dbt-streaming
	@echo "Backfill complete."

dbt-batch:
	cd dbt/benchmark && \
	  GCP_PROJECT_ID=$(PROJECT_ID) uv run dbt deps && \
	  GCP_PROJECT_ID=$(PROJECT_ID) uv run dbt run --select stg_trials mart_trial_landscape \
	  --profiles-dir . --target prod

dbt-streaming:
	cd dbt/benchmark && \
	  GCP_PROJECT_ID=$(PROJECT_ID) uv run dbt deps && \
	  GCP_PROJECT_ID=$(PROJECT_ID) uv run dbt run --select stg_daily_activity mart_activity_trends \
	  --profiles-dir . --target prod

dbt:
	cd dbt/benchmark && \
	  GCP_PROJECT_ID=$(PROJECT_ID) uv run dbt deps && \
	  GCP_PROJECT_ID=$(PROJECT_ID) uv run dbt run --profiles-dir . --target prod && \
	  GCP_PROJECT_ID=$(PROJECT_ID) uv run dbt test --profiles-dir . --target prod

all: infra batch stream-up stream-backfill
	@echo "Setup complete."
	@echo "  Kestra UI (flows + execution history): http://localhost:8080"
	@echo "  Flink UI (job monitor):                http://localhost:8081"

clean:
	docker compose -p benchmark -f streaming/docker-compose.yml down -v
	rm -rf data/aact credentials/sa_key.json
	@echo "To destroy GCP resources: terraform -chdir=infra destroy -var=project_id=$(PROJECT_ID)"
