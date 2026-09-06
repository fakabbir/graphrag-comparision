# Build the paper. Requires tectonic (brew install tectonic) and python3.
#
#   make          -> regenerate tables/figures from data, then compile
#   make assets   -> regenerate gen/*.tex only
#   make verify   -> assert every number in the tables matches facts.json
#   make clean

TECTONIC ?= tectonic
CACHE    ?= $(HOME)/.cache/tectonic

all: assets paper

assets:
	python3 build_assets.py

paper:
	mkdir -p build
	TECTONIC_CACHE_DIR=$(CACHE) $(TECTONIC) -X compile main.tex --outdir build --keep-logs
	@echo
	@grep -cE "Reference .* undefined|Citation .* undefined" build/main.log \
	  | xargs -I{} sh -c 'test {} -eq 0 || (echo "undefined references: {}" && false)'
	@echo "built build/main.pdf"

verify:
	python3 verify_numbers.py

clean:
	rm -rf build shots

.PHONY: all assets paper verify clean
