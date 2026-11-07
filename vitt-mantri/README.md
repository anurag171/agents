# Vitta-Mantri

A research dashboard for Indian and global markets: it collects headlines from ~14 sources, maps them to affected stocks and assets, shows live prices, charts and IPOs, screens the whole market by fundamentals and price strength, and **logs every call to measure whether it was right**.

> **Status: unproven.** No edge has been demonstrated; the track record starts empty and results are gross of costs. Do not trade on it, or share its output with others, before reading `docs/CONTEXT_AND_APPROACH.md` (section 9) and finishing `TODO.md` section 3.

## Run
```bash
pip install -r requirements.txt
cp .env.example .env        # optional: USER_LLM_API_KEY enables headline interpretation
python app.py               # http://127.0.0.1:8000
```
Container: see `Dockerfile` (non-root, `/data` must be a persistent volume). Probes: `/health/live`, `/health/ready`.

## First run on a new machine
```bash
python -m engine.instruments    # does the live NSE stock list load?
python -m engine.scraper        # which news sources work?
```
Set `VM_ADMIN_TOKEN` and `VM_CONTACT`. Everything else has defaults; the full list is in `docs/CONTEXT_AND_APPROACH.md` section 7.

## Docs
- `docs/CONTEXT_AND_APPROACH.md` - what it is, how it works, why, what is verified, and a critical assessment
- `TODO.md` - everything left, with acceptance criteria and decisions needed
- `.opencode/skill/vitta-mantri-hardening/` - the original audit and implementation notes
