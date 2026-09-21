PY := .venv/bin/python
TB := toolbox run -c prism-dev
export HF_HUB_DISABLE_PROGRESS_BARS := 1

.PHONY: help venv build serve bench batch test eval clean

help:
	@grep -E '^[a-z-]+:.*##' $(MAKEFILE_LIST) | sed 's/:.*##/\t/' | column -t -s "$$(printf '\t')"

venv:  ## create the pinned 3.12 venv and install dependencies
	uv venv --python 3.12 .venv
	uv pip install --python $(PY) --torch-backend=cpu -r requirements.lock

build: ## compile skeletons -> artifacts/ (fails on any blocking gate)
	$(TB) $(PY) scripts/compile_plans.py

serve: ## run the REST API on :8000
	$(TB) $(PY) -m uvicorn api.main:app --host 0.0.0.0 --port 8000

bench: ## latency percentiles per execution path
	$(TB) $(PY) scripts/bench.py --n 40 --cold 11

batch: ## replay data/input.txt -> results.jsonl
	$(TB) $(PY) scripts/run_batch.py

test:  ## regression suite
	$(TB) $(PY) -m pytest tests/ -q

eval:  ## deeplink precision + cache hit rate
	@$(TB) $(PY) scripts/eval_deeplinks.py --dense
	@$(TB) $(PY) scripts/eval_deeplinks.py --dense --labels tests/fixtures/deeplink_labels_paraphrase.json
	@$(TB) $(PY) scripts/eval_cache.py

clean: ## remove build outputs
	rm -rf artifacts/*.json artifacts/*.npy results.jsonl .pytest_cache
