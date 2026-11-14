PY ?= /Users/gillianr/.dsh/dsh-runtimes/dsh-primary-runtime/dependencies/python/bin/python3

.PHONY: run verify demo fixtures clean

run:
	$(PY) run.py

verify:
	$(PY) verify.py $(or $(M),all)

demo:
	$(PY) demo.py

fixtures:
	$(PY) -c "from tests.make_fixtures import build_all; print(build_all(force=True))"

clean:
	rm -rf vault eval __pycache__ wb/__pycache__ wb/*/__pycache__ tests/__pycache__
