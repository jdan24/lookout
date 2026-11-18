# AGENTS.md — conventions for any AI agent working on Lookout

Lookout is a local, **read-only** Streamlit tool that checks Trading Technologies
(TT) trading setups against expected rules.

The project brief and the TT data handoff hold real client details, so they are
kept **outside** this repo (the brief is git-ignored if present locally). Ask the
user when you need them.

## Ground rules (non-negotiable)

1. **Read-only, with one UAT exception.** Only HTTP `GET` to TT data services, plus
   the single token `POST` to `/ttid/<environment>/token`. This is enforced in
   `core/readonly_http.py`. **Never loosen the guard, never widen its allow-list,
   never add a write call.** If a task seems to need one, stop and ask the user.
   The exception, added at the user's request: the Clone page may send the two
   `POST`s listed in `core/write_http.py`, in `ext_uat_cert` only, through that
   module's own guard. **Never add Live, another path or another method to it
   without the user's explicit say-so.**
   Two environments are allowed, `ext_prod_live` (Live) and `ext_uat_cert` (UAT, added
   at the user's request). A client is tied to one when created and cannot reach the
   other. Live and UAT also keep separate credentials, profiles and snapshots; never
   mix them.
2. **All TT access goes through** `core/tt_client.py` → `core/readonly_http.py`
   (reads) or `core/tt_writer.py` → `core/write_http.py` (Clone, UAT only). No other
   module may import an HTTP library. A test enforces this.
3. **No live TT calls during development or in tests.** Tests run on fixtures;
   `tests/conftest.py` blocks real sockets.
4. **Never touch** the team's live TT job scripts on the shared drive.
5. **Secrets** live only in Windows Credential Manager via `keyring`. Never put
   them in code, config, the repo, logs, snapshots or test output.
6. **Never commit** snapshots, client profiles or any real client data: no real
   account names or ids, company ids, client names or expected values, in code,
   tests, docs or commit messages. `tests/fixtures/` holds invented data only.
   `tests/test_no_real_data.py` checks tracked files against the local profiles.
7. **Scope-limited pulls.** Read only the client's subtrees, never the whole TT
   setup.
8. **Offline first.** Every pull is saved as a snapshot; rules run on snapshots,
   not on live responses.

## Where code goes

| Path | Contents | Streamlit allowed |
|---|---|---|
| `app.py` | Discovers `reports/`, builds landing page and navigation. Thin. | yes |
| `reports/` | One file per report (UI only). Files starting with `_` are skipped. | yes |
| `ui/` | Shared UI components. | yes |
| `core/` | Generic logic: HTTP guard, TT client, snapshots, hierarchy, results. | **no** |
| `checks/` | Generic check logic as pure functions (`checks/setup/`: profile-driven setup rules). | **no** |
| `scripts/` | Launcher and one-time setup scripts. | no |
| `vendor/` | Compiled files copied from sister projects (the Limits app). Never edit here; see each folder's README. | — |
| `tests/` | pytest; `tests/fixtures/` for data. | — |

- New reports go in `reports/` and follow the report contract (`TITLE`,
  `DESCRIPTION`, `ICON`, `CATEGORY`, `render()`). **Never edit `app.py` to add a
  report.**
- Rules are generic and data-driven. Each client is described by a **profile**
  (TOML) in the local profiles folder (`[clients] directory` in `config.toml`,
  default `%LOCALAPPDATA%\Lookout\clients`), never in the repo. The format is in
  `docs/example_client.toml`, which matches the synthetic fixture.
- A new rule type is added to `checks/setup/` with a profile option to switch it
  on; nothing client-specific goes in code.
- All reports use `ui/components.py` and the result model in `core/results.py`.

## Data conventions

- **Ids are strings everywhere.** `0`, `""` and `None` all mean "no id". Product
  and family ids are 19-digit strings; never parse them as floats (pandas:
  `dtype=str`).
- TT booleans arrive as `0/1` integers or `"0"/"1"`/`"true"/"false"` strings.
  Normalise them through the one helper in `core/normalize.py`.
- `riskLimitSettings.noLimits` is the **reverse** of the "Apply Limits" checkbox.
- A JSON body whose `status` is not `"Ok"` is an error, even on HTTP 200.

## Working practice

- Python 3.12 or newer, whichever `python` the PC already has. Type hints everywhere.
  Keep files small.
- Packages are listed in `requirements.txt` (`requirements-dev.txt` adds pytest and
  ruff). The launcher and `scripts/check_environment.py` only **check** them: nothing
  in this repo may install or download Python or packages by itself.
- Run `python -m pytest` and `python -m ruff check` before considering any change done.
- Small steps: each change ends with passing tests and one commit.
- Do not push; the user decides when to push.
- The mode (Live or UAT) comes only from the launcher, through `LOOKOUT_ENVIRONMENT`.
  Do not add an in-app switch.
- Target is Windows 11. Keep paths, keyring and certificate handling
  cross-platform; do not assume a POSIX shell at runtime.
- No PyInstaller, no `.exe`, no scheduler, no database.
