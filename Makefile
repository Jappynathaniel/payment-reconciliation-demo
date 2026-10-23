.PHONY: test demo benchmark

test:
	python -m pytest -q

demo:
	python -m reconcile.walkthrough

benchmark:
	python -m reconcile.benchmark --orders 5000
