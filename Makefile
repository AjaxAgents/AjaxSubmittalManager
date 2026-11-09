# Ajax Submittal Manager - developer commands
PY ?= python3
.PHONY: dev backend frontend ui test eval gate demo docker install exe

install:            ## install backend + frontend dependencies
	$(PY) -m pip install -r backend/requirements-dev.txt
	cd frontend && npm install

dev:                ## backend (8765) + Vite dev server with hot reload (5173)
	( cd backend && $(PY) -m asm.main --no-browser --reload ) & \
	( cd frontend && npm run dev )

backend:            ## backend only, serving the prebuilt UI on http://localhost:8765
	cd backend && $(PY) -m asm.main

frontend ui:        ## rebuild the UI into frontend/dist (committed so users need no Node)
	cd frontend && npm install && npm run build

test:               ## unit + end-to-end (offline) tests
	cd backend && $(PY) -m pytest -q tests

eval:               ## run the evaluation set in eval/cases (real API calls; costs money)
	$(PY) eval/run_eval.py

gate:               ## build the Bluebeam compatibility test PDF -> data/bluebeam_gate.pdf
	$(PY) scripts/make_bluebeam_gate.py

demo:               ## generate the fictional demo project -> data/demo
	$(PY) scripts/make_demo_project.py data/demo

docker:
	docker compose up --build

exe:                ## build dist/AjaxSubmittalManager.exe (run on Windows; CI does this on merge to main)
	$(PY) -m pip install pyinstaller
	pyinstaller packaging/AjaxSubmittalManager.spec --noconfirm
