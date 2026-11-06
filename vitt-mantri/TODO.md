# Leftovers

Everything below was left out on purpose (YAGNI) or could not be checked from the build sandbox. Each item says when it becomes worth doing.

## Do first, on your network (could not be verified here)
- [ ] `python -m engine.instruments` - does the live NSE stock list load? If not, follow the message it prints (allow the hosts, or drop the CSVs in `VM_UNIVERSE_DIR`).
- [ ] `python -m engine.scraper` - which news sources work, how fresh, how many have real timestamps. Remove sources that stay empty.
- [ ] Open the app and check Sources tab: `Market: NSE`, `Market: Yahoo`, `Assets: Yahoo`, `IPO: NSE` should show items. A source with `error` means its response format differs from what the code expects (formats were written from memory).
- [ ] Provide fundamentals: a Screener/Trendlyne-style CSV in `VM_FUNDAMENTALS_DIR` (most reliable) or let the Yahoo fill run.
- [ ] Set `VM_CONTACT` (shown in the user agent), `VM_ADMIN_TOKEN` (refresh / CSV export), and make `VM_DATA` a **persistent volume** (it holds the signals log).
- [ ] Revoke the GitHub token that was pasted in chat; open the PR for `feat/dynamic-universe-ipo-assets-ui`.

## Skipped on purpose - do it when the trigger happens
| Item | Trigger |
|---|---|
| Prometheus `/metrics` (stateless: read the shared snapshot + DB, ~30 lines) | you run Prometheus |
| OpenShift manifests (Deployment, PVC, Route; probes `/health/live` and `/health/ready`) | first deployment |
| Scale beyond one replica: needs a shared (RWX) volume or a Postgres adapter in `engine/store.py` | one replica is not enough |
| Source-health history table + auto-quarantine of dead feeds | the circuit breaker and `python -m engine.scraper` are not enough |
| GitHub Actions workflow running `pytest` | the branch is merged |
| Pinned requirements with hashes (`pip-compile --generate-hashes`) | your supply-chain policy asks for it |
| Auth on the read endpoints (`/api/briefing` etc. are open; only refresh/export/evaluate use the admin token) | the app is reachable outside a trusted network |

## Known limits (not bugs)
- The track record starts empty. Hit rates need 30 calls over 20 separate days; theme demotion needs 30 independent days; screen weight tuning needs about 3.5 years of weekly snapshots; news-weight tuning needs 300 calls over 60 days. Until then the screens say "not enough history".
- Screen PICK thresholds are not tuned (no honest way with this little data).
- Factor screens work over months and have long bad stretches; they find candidates, they do not predict.
- Several tuning/decision rules were calibrated by simulation, not on real outcomes.
