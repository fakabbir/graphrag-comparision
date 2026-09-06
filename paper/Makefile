# Build the paper in both layouts.
# Requires tectonic (brew install tectonic) and python3.
#
#   make            -> regenerate assets, build both layouts
#   make onecol     -> build/main.pdf        (11pt, single column)
#   make twocol     -> build/main-2col.pdf   (10pt, two columns)
#   make assets     -> regenerate gen/*.tex from facts.json
#   make verify     -> assert the paper's numbers against facts.json
#   make clean

TECTONIC ?= tectonic
CACHE    ?= $(HOME)/.cache/tectonic
TFLAGS    = -X compile --outdir build --keep-logs

all: assets onecol twocol verify

assets:
	python3 build_assets.py

onecol: | build
	TECTONIC_CACHE_DIR=$(CACHE) $(TECTONIC) $(TFLAGS) main.tex
	@$(MAKE) --no-print-directory check DOC=main

twocol: | build
	TECTONIC_CACHE_DIR=$(CACHE) $(TECTONIC) $(TFLAGS) main-2col.tex
	@$(MAKE) --no-print-directory check DOC=main-2col

# A build that emits "??" for a reference still exits 0, so check explicitly.
check:
	@n=$$(grep -cE 'Reference .* undefined|Citation .* undefined' build/$(DOC).log || true); \
	 test "$$n" -eq 0 || { echo "$(DOC): $$n undefined references"; exit 1; }
	@r=$$(pdftotext build/$(DOC).pdf - 2>/dev/null | grep -c '^References' || true); \
	 test "$$r" -eq 1 || { echo "$(DOC): expected 1 reference list, found $$r"; exit 1; }
	@echo "$(DOC).pdf: $$(pdfinfo build/$(DOC).pdf | awk '/Pages/{print $$2}') pages, references resolved"

verify:
	python3 verify_numbers.py

build:
	@mkdir -p build

clean:
	rm -rf build shots

.PHONY: all assets onecol twocol check verify clean
