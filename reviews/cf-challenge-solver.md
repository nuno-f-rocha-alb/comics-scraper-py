# cf-challenge-solver — gate record

**PASS.**

| Req | Status | Evidence |
|-----|--------|----------|
| R1 config env vars | MET | `FLARESOLVERR_URL` / `PROXY_URL` / `CF_SOLVER_TIMEOUT` in `config.py` |
| R2 cf_solver module | MET | `downloader/cf_solver.py`: `solve`/`get_page`/`clearance_for`/`_enabled`/`_clearance`; proxy-iff-set; failures → None + one WARNING |
| R3 page fetch via solver | MET | `get_comic_download_url` uses `cf_solver.get_page` when enabled; plain-requests+429 path when off |
| R4 download replays clearance | MET | `download_file` merges host cookies + solver UA before the stream; no-op when off |
| R5 compose sidecar | MET | `flaresolverr` service + `FLARESOLVERR_URL` env + `depends_on`; `PROXY_URL` commented |
| R6 ponytail ceilings | MET | `ponytail:` comments on cache lifetime, single-proxy, no rotation |

**Gate commands:**
- `pytest -q` (Docker test image) → **96 passed** (+7 new).
- Live-verify → `test_solve_posts_v1_payload_and_parses` asserts HTML/cookies/UA parsed from a real-shape
  FlareSolverr `solution`.
- CodeRabbit `-t uncommitted` → **0 findings** (code + compose infra).
