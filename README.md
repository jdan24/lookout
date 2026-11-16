# Lookout

Read-only verification of Trading Technologies (TT) trading setups. Lookout pulls
setup data through the TT REST API, saves every pull as a snapshot, and checks the
snapshot against expected rules. It never writes to TT.

- Conventions and ground rules: `AGENTS.md`
- Client profile format: `docs/example_client.toml`

## Development

Lookout uses the Python already on the PC (3.12 or newer). Once, in this folder:

```
python -m venv .venv
.venv\Scripts\activate          (Linux/macOS: source .venv/bin/activate)
pip install -r requirements-dev.txt
```

Then, before every commit:

```
python -m pytest
python -m ruff check
```

Tests never call TT; real sockets are blocked in `tests/conftest.py`.

## Status

Built and tested on a synthetic setup (`tests/fixtures/synthetic.py`). The first
production pull signed in but TT refused the first data call over the `requestId` form,
since fixed; no full pull has completed yet.

- Done: read-only guard, TT client, pull -> snapshot -> rebuild, account tree, result
  model, landing page with report discovery, Setup Verification (profile-driven rules:
  levels, Apply Limits / Restrictions, markets, naming, required users on the lowest
  level, Order Tag Defaults (only on markets that have limits), and
  optional companion-account and legacy-group rules, plus display-only views), Limit
  Verification (ported from limit_compare, with parity tests in
  `tests/fixtures/limit_compare/`), credential storage, Windows launcher.
- Pulling: the Setup Verification page has a "Pull a new snapshot from TT" panel. It
  needs stored credentials and a ticked confirmation, shows progress, and selects the
  new snapshot when done.
- Not done: product-family names from `ttpds`.
- To confirm on the first supervised pull: the request conventions in
  `core/tt_client.py` (headers, token form fields; the `requestId` form
  `<app>-<company>--<guid>` came from TT's own reply on the first pull), the path
  `/ttaccount/ext_prod_live/account/{id}/users`, any `start_id` marked
  `id_verified = false` in a profile; that TT's market names from `ttpds` match the
  exchange names in TT's product list (e.g. `ICE_L`), which Limit Verification relies
  on; and which `productTypeId` values besides 34 (Future) occur.

## Live and UAT

Lookout runs against one TT environment per session, chosen by the launcher:

| | Live | UAT |
|---|---|---|
| Start with | `scripts\Start-Lookout.bat` | `scripts\Start-Lookout-UAT.bat` |
| TT environment | `ext_prod_live` | `ext_uat_cert` |
| Address | http://localhost:8501 | http://localhost:8502 |
| Credentials | `python scripts\set_credentials.py` | `python scripts\set_credentials.py --uat` |
| Profiles | `%LOCALAPPDATA%\Lookout\clients` | `%LOCALAPPDATA%\Lookout\uat\clients` |
| Snapshots | `%LOCALAPPDATA%\Lookout\snapshots` | `%LOCALAPPDATA%\Lookout\uat\snapshots` |

UAT shows an orange "UAT MODE" banner on every page and in the sidebar; Live shows a
green "LIVE" label. Both can be open at the same time. The read-only guard ties each
session to its environment, and a snapshot pulled from one is refused by the other. If
`[snapshots]` or `[clients] directory` is set in `config.toml`, UAT uses its `uat`
subfolder. Keep the two `.bat` files in the same folder. Account ids differ in UAT, so
UAT needs its own profiles.

Without a launcher (development), set the mode yourself before starting:
`LOOKOUT_ENVIRONMENT=ext_uat_cert python -m streamlit run app.py`.

## Client profiles

Setup Verification reads one TOML profile per client from the profiles folder
(`[clients] directory` in `config.toml`; default `%LOCALAPPDATA%\Lookout\clients`).
A profile names the accounts each client tree starts from (a path from a top-level
account), the levels below it by depth, and the expected values at each level. See
`docs/example_client.toml`; the Levels tab of the report shows where each account
landed, to help tune a profile.

Profiles hold real client data: they are **never** committed. Copy them to each PC
(or point `[clients] directory` at a shared folder). With your profiles in place,
`python -m pytest tests/test_no_real_data.py` checks that none of their names, ids or
values appear in the repo.

## Things to do when deploying to production

- [ ] Set `RELEASE_SHARE` in `scripts/Start-Lookout.bat` to the network share that holds
      the released copy (currently `\\CHANGE-ME\Lookout\current`).
- [ ] On each user's PC, make sure `python` starts Python 3.12 or newer and that it has
      the packages in `requirements.txt`, installed through the company's own process.
      `python scripts\check_environment.py` lists anything missing or too old. The
      launcher runs the same check on every start, then starts anyway; it never installs
      or downloads anything.
- [ ] Set `request_id_prefix` in `config.toml` to `Lookout-<YourCompany>` (letters and
      digits only, no spaces). TT rejects calls without the company name, and Lookout
      will not pull while it still says `CHANGEME`. The launcher copies `config.toml`
      from the release share on every start, so make the edit in the share's copy.
- [ ] On each user's PC, store the TT credentials once:
      `python scripts\set_credentials.py`.
- [ ] On each user's PC, copy the client profiles (`*.toml`) into
      `%LOCALAPPDATA%\Lookout\clients` (they are not in the release).
- [ ] Run a first supervised pull (Setup Verification → "Pull a new snapshot from TT") and confirm the request conventions in
      `core/tt_client.py`, the `/account/{id}/users` path, and any `start_id` marked
      `id_verified = false` in a profile (then set it to true).
- [ ] Check `Start-Lookout.bat` on a Windows 11 PC, including behind the corporate proxy.
- [ ] For UAT: store the UAT credentials (`python scripts\set_credentials.py --uat`), put
      UAT profiles in `%LOCALAPPDATA%\Lookout\uat\clients`, start `Start-Lookout-UAT.bat`
      and run a first supervised UAT pull. To confirm there: that UAT is served from the
      same host with `/ext_uat_cert/` in the path, and accepts the same `requestId` form.
