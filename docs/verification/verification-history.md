# Verification record

Build environment: Windows PowerShell workspace, 2026-10-18. Python regression verification passes (43 cases), and the updated frontend production build passes. All four browser cases now pass in the user terminal, including the new coverage-table case. Historical-data, model and deployment acceptance remain **pending**. Earlier environment failures below are retained as history.

| Check | Observed outcome |
|---|---|
| Existing workspace inspection | Only supplied requirements DOCX existed; no application/repository |
| Static repository integrity | Seven checks passed; see static-checks.json (not functional verification) |
| Node runtime | v22.20.0 executed successfully |
| Manifest/connectivity preflight | Ran successfully; 874 unique expected archive paths, 221,760 expected windows |
| Historical acquisition | User download acquired 874 archives; cached SHA256 verification passed. Native and invalid-value gaps block release |
| Python runtime | CPython 3.11.11 now runs from the user-provisioned virtual environment |
| Python provisioning | uv download denied with socket error 10013 |
| Python dependencies / imports / compilation / tests | Dependencies and ML imports passed in the user terminal; local compilation and all 38 regression cases now pass |
| npm dependency installation | Dependencies are now installed from the user terminal |
| TypeScript/Vite build | Passed in user-executed frontend verifier; production artifact inspected |
| Playwright/browser screenshots | Browser suite passed; paper audit screenshot saved and visually reviewed |
| Docker fallback | Docker daemon unavailable |
| Temporary runtime cleanup | Automatic approval review rejected cleanup because sandbox approval is disabled; ignored files remain |
| Public repository/deployment | Not created or deployed |
| Training / holdout / paper track record | No measured results; release intentionally blocked |

The checked-in tests cover causal features, swing confirmation, completed higher-timeframe alignment, timestamp availability, gap rejection, funding/fees/long-short accounting, margin/stop behavior, risk gates, delayed execution, replay prohibition, transactional idempotence/rollback, purged ML folds, frozen preprocessing, API bounds and official MCP stdio calls. Playwright covers four-view navigation, mobile overflow, missing-backend states and screenshots. These are **tests to run**, not claimed passing evidence.

Run `node scripts/verify.cjs` on a machine with Python 3.11, Node 22 and permitted package/provider access, or use the checked-in GitHub quality workflow. Preserve actual outputs and browser artifacts here before making a verified resume claim.

Known approximations are documented in README and the model card. Dependency lockfiles, source-definition inspection against downloaded archives, Supabase/PostgreSQL integration, provider limits, public hosting and the final recorded walkthrough remain release work requiring an executable, network-enabled environment.


## Continuation verification

The environment restrictions were rechecked: Python still exits with access denied; npm/provider requests still return EACCES. PowerShell also refused local script execution under its existing execution policy. No policy settings were changed. The portable Node runner executed the static checks and then failed at Python provisioning with socket error 10013; subsequent compilation, pytest, frontend and browser stages are marked not run in [latest-verification.json](../evidence/latest-verification.json).

Source review corrected these accounting/evaluation issues and added regression fixtures:

- Pause/resume no longer advances the market replay cursor or resets daily accounting.
- Future funding settlements cannot enter an earlier backtest; settlement/exit timestamp ties are explicit.
- Validation execution tails stop before the following split, including with longer custom holding limits.
- Passive benchmark fees, funding and drawdown use chronological shared accounting.
- Worker reconciliation checks candle continuity, marks and settlement coverage before account mutation.
- Bootstrap configuration is UTF-8 without BOM; model identity records installed package versions.

There are 34 Python test functions in source (including parametrized cases), plus three Playwright tests. These fixtures remain unexecuted here. Seven dependency-free structural checks and PowerShell parser validation succeeded; neither establishes functional acceptance. The original requirements DOCX remains untouched.

## Dependency installation follow-up

The user ran the portable verifier in a regular terminal: CPython 3.11.11 started and dependency resolution completed, but installation failed while building Numba 0.47.0, selected transitively through SHAP with NumPy 2.4.6. Functional tests did not start. The project now requires `numba>=0.61.2,<0.62` and `numpy>=2.0,<2.3`, following the [Numba 0.61.2 compatibility table](https://numba.readthedocs.io/en/0.61.2/user/installing.html). Both verifier entry points and Python CI now import the ML dependencies before running tests to catch binary/version incompatibilities.

A local `uv lock` attempt could not refresh the existing lockfile because the NumPy metadata download was denied with socket error 10013. The existing lockfile has not been manually edited; `node scripts/verify.cjs` runs `uv sync --extra dev`, which re-resolves the changed project constraints and updates it on a network-enabled machine. No virtual environment or cache deletion is needed. Installation and regression results remain pending.

## Passing Python regression follow-up

The dependency correction installed successfully in the user terminal. That first functional run reported 34 passing cases and two failures: weekly support/resistance leakage and missing MCP structured content. Both defects have been corrected. Weekly levels now use fixed, right-closed seven-day bins anchored at UTC Monday midnight, preventing Monday afternoon observations from appearing at Monday midnight. All nine MCP tools explicitly require structured output and return `dict[str, Any]`, allowing the official SDK to publish object schemas and structured results.

The original mutation regression passes. Added boundary cases cover Monday and midweek dataset starts, and the official SDK stdio test now checks output schemas, structured content, provenance and service parity for all nine tools, plus invalid inputs. The complete suite passed locally: **38 passed, 0 failed, 1 deprecation warning in 180.34 seconds**. Python compilation and seven static checks also passed. See [regression evidence](../evidence/python-regression-verification.json) for source/report hashes. The warning concerns Starlette's deprecated httpx test client integration and did not fail a case.

The static checker no longer hardcodes an environment-blocked functional status: it directs readers to runtime evidence. The previous `.verification/result.json` reflects the user's earlier failing verifier run; the successful corrected Python run is recorded separately in `.verification/pytest-fixed.xml`. Frontend dependency installation was retried and still failed here with npm registry EACCES, so TypeScript, Vite and browser acceptance remain pending. Rerun `node scripts/verify.cjs` in the regular terminal to continue through those stages. Historical coverage, model release and deployment remain unverified.

## Frontend milestone attempt

The next milestone was attempted after the passing Python suite. Frontend dependencies are still absent; `npm install` failed with EACCES reaching the npm registry. The dependency-free provider preflight was also rerun: all six checksum/sentiment probes returned EACCES, so zero archives are verified. These are environment connectivity failures, not evidence of missing provider archives. No build, browser-test, screenshot, data-coverage or model-release success is claimed.

A focused continuation command is now available: `node scripts/verify.cjs --frontend`. It runs the static checks and four frontend stages without repeating Python tests, and records its scope/results separately in `.verification/result-frontend.json`. Run it in the regular terminal with network access, then run the documented historical audit command.

## Frontend milestone passed

The user executed `node scripts/verify.cjs --frontend` successfully. The saved result records every stage passing: static checks, dependency installation, TypeScript/Vite production build, Chromium installation and browser suite. The browser last-run record reports no failed tests. The suite covers four-view navigation, mobile overflow and backend failures. This validates preview and failure states; live provider ingestion and a released-model experience remain separate checks.

The production HTML and dependency lockfile exist. The [paper portfolio screenshot](../screenshots/paper-audit.png) was visually reviewed: the synthetic-preview disclosure, paused initial portfolio, empty ledger and stale-data state are visible and legible. [Frontend evidence](../evidence/frontend-milestone-status.json) preserves the verifier result and artifact hashes. Historical acquisition is the next milestone.

## Historical acquisition attempt

The actual Python audit executed after the frontend milestone. It wrote `data/coverage.json`: zero verified archives and zero observations in every mandatory family. All 874 archive requests and the sentiment request failed; see [acquisition evidence](../evidence/acquisition-audit.json). Model release remains blocked. The audit must be rerun in the regular network-enabled terminal; no data was fabricated or substituted.

## Real-data audit and development continuation ? 19 October 2026

The historical data is now present: all 874 Binance archives passed cached checksum validation. The audit identifies genuine full-day mark/index omissions, five absent positioning timestamps and 57 positioning rows with invalid numbers. [Coverage report](../research/coverage-report.md) lists exact times, column counts, dataset hashes and release blockers. Candles, funding and sentiment pass the automated audit. No full-period model release is authorized.

Corrected a false funding gap caused by millisecond settlement offsets: shared audit/worker checks allow a one-second interval tolerance, preserve exact settlement times and still reject missing settlements. Invalid latest observations now quarantine their prediction windows. Added native/invalid gap reporting, raw-cache and Parquet hash checks for offline audit, and an owner command: `quantpilot audit --as-of 2026-10-18 --offline`. Research UI reports source gaps and invalid rows separately.

Five new Python regression cases cover funding jitter versus missing settlements, invalid positioning, cached raw corruption, changed Parquet files and worker reconciliation. **All 43 cases pass**, with one pre-existing Starlette test-client deprecation warning. The final suite completed in 25.16 seconds after vectorizing contiguous-gap grouping (an earlier run with row-by-row grouping also passed). Python compilation, seven static checks, TypeScript and Vite build pass. The frontend bundle-size advisory remains.

The browser rerun could not execute assertions: all four cases failed at browser launch because the required Chromium headless shell executable (revision 1248) is unavailable here. Prior user-executed browser evidence remains valid for the earlier three cases; the new coverage-table case is pending. Run `node scripts/verify.cjs --frontend` in the regular terminal to install the matching browser and check all four cases. [Updated regression evidence](../evidence/python-regression-verification.json) records current hashes and limitations.

## Updated browser verification passed

The user reran `node scripts/verify.cjs --frontend` successfully after the data-quality UI changes. All four browser cases passed in 6.5 seconds: four-view audit navigation, mobile navigation/overflow, backend failure/retry state, and missing-versus-invalid coverage reporting. Static checks, dependency installation, TypeScript/Vite build and matching Chromium installation also passed. The supplied npm audit reported zero vulnerabilities. Vite reports a 798.38 kB main JavaScript chunk advisory; it did not fail the build. [Frontend evidence](../evidence/frontend-milestone-status.json) records the saved verifier result and current source hashes.

The current verified implementation has 43 passing Python cases and four passing browser cases. Model training remains blocked by the historical source gaps documented in the coverage report.

## Targeted recovery implementation

Added `quantpilot repair-data --as-of 2026-10-18`: corrected/monthly and alternate daily archive recovery, historical mark/index REST fallback, timestamp/availability checks, conflict rejection, raw evidence retention, hash-verified offline re-audit and fail-closed publication. Seven new regression cases test exact timestamp insertion, preservation of valid history, conflicting partially invalid rows, invalid/duplicate/future candidates, REST provenance/date bounds, missing supplemental evidence and successful/unsuccessful publication. **All 50 Python cases pass** in 21.92 seconds; compilation and seven static checks pass. Four frontend cases remain verified from the preceding user run; no frontend source changed in this recovery work.

The real recovery attempt made 13 bounded public-source requests. All were denied by socket permissions (WinError 10013), so no historical observations were repaired. Run `uv run quantpilot repair-data --as-of 2026-10-18` from the regular terminal to obtain actual provider responses. See [repair evidence](../evidence/repair-status.json). The source gaps and model release block remain in place.

## Network-enabled repair verified

The user recovery run repaired all 288 missing mark and 288 missing index observations from checksum-verified daily archives, with zero conflicts. A subsequent offline audit revalidated cached original/supplemental evidence and Parquet hashes. Candles, mark, index, funding and sentiment now pass automated coverage checks. Positioning still contains five absent timestamps and 57 invalid rows after refreshed archives supplied no replacements. Strict release remains blocked pending replacement data or an explicit coverage-policy decision.
