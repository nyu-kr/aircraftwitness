PYTHON ?= python3

.PHONY: eval check locate studies

eval:
	@test -n "$(ANSWERS)" || { echo "usage: make eval ANSWERS=answers.jsonl"; exit 2; }
	@$(PYTHON) -I tools/score_answers.py "$(ANSWERS)"

check:
	$(PYTHON) -I tests/test_answers.py
	$(PYTHON) -I tools/check_squares.py
	$(PYTHON) -I tests/test_labels.py
	$(PYTHON) -I tests/test_detection.py
	$(PYTHON) -I tools/validate_tables.py

locate:
	@test -n "$(AUDIO)" -a -n "$(ADSB)" -a -n "$(BOX)" -a -n "$(T0)" -a -n "$(OUT)" || { echo "usage: make locate AUDIO=clip.flac ADSB=flights.parquet BOX=W,S,E,N T0=UNIX OUT=dir/name [DEM=glo90.tif] [SLACK=60]"; exit 2; }
	@mkdir -p "$(dir $(OUT))"
	@$(PYTHON) -I -W ignore locator/locate.py --audio "$(AUDIO)" --adsb "$(ADSB)" --box="$(BOX)" --t0 "$(T0)" --slack $(or $(SLACK),60) --out "$(OUT)" --jobs 3 $(if $(DEM),--dem "$(DEM)")

studies:
	$(PYTHON) -I studies/e2e_score.py results/e2e_rows.csv results/e2e_results.json results/e2e_source_aliases.json
	$(PYTHON) -I studies/fresh_score.py results/fresh_rows.csv results/fresh_results.json
