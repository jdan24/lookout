# Lookout

Read-only verification of Trading Technologies (TT) trading setups. Lookout pulls
setup data through the TT REST API, saves every pull as a snapshot, and checks the
snapshot against expected rules. It never writes to TT Live. The one exception is
the Clone page, which works in TT UAT only (see "Clone" below).

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
  optional companion-account and legacy-group rules), the Limits page (the Limits
  app's Account limit upload and Risk and periodic audit tools), credential storage, Windows launcher, and the
  Clone page (UAT only; not yet run against TT UAT).
- Pulling: the Setup Verification page has a "Pull a new snapshot from TT" panel. It
  needs stored credentials and a ticked confirmation, shows progress, and selects the
  new snapshot when done.
- Not done: product-family names from `ttpds`.
- To confirm on the first supervised pull: the request conventions in
  `core/tt_client.py` (headers, token form fields; the `requestId` form
  `<app>-<company>--<guid>` came from TT's own reply on the first pull), the path
  `/ttaccount/ext_prod_live/account/{id}/users`, any `start_id` marked
  `id_verified = false` in a profile; and which `productTypeId` values besides 34 (Future) occur.

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

## Limits

The Limits page holds the two tools of the Limits app (`jdan24/limit_compare`), one per
tab:

- **Account limit upload**: paste the Client Limit Table and match each row to a TT
  exchange and product symbol. The result, "Client Limit Table with TT Symbols", can
  be copied (for Excel / Outlook) or exported as an Excel file.
  - **New setup (no risk system export yet):** type the IsvTeName once in the table's
    IsvTeName column and click **Export to risk system (new setup)**. Products on CFE,
    CME, Eurex, ICE, ICE_L, FMX and MIAX_FUT_CH go in `Risk_Upload_<IsvTeName>_<date>.csv`
    with that IsvTeName; every other exchange goes in `Risk_Upload_CB-<IsvTeName>_<date>.csv`
    with `CB-` in front. The files use the risk export's layout (Header = Data,
    IsvInstance = TTRest01, IsvTeType = Account, margin 100%, trading allowed True).
    Unavailable products, products without a confirmed TT match and repeats are left
    out. The IsvTeName column is the table's first column, and Copy table and Export
    Excel include it.
  - **Unavailable products:** TT products clients ask for that we don't offer. Mark
    them from the table ("mark unavailable") or import a CSV (`TT Exchange,TT Product
    Symbol`); the list is kept in the browser and can be exported. They are greyed
    and struck through on screen, in Copy table and in the Excel export, and are never
    uploaded.
  - Then drop the Risk System Export: every file for the account (e.g. the CB and the
    non-CB markets file). The risk system splits one account across several
    IsvTeNames, so every loaded row is checked as that one account. The check flags
    limits that differ, products missing from the export (or Not Set there),
    products under more than one IsvTeName (conflict, never uploaded), unavailable
    products, products in the export that were not requested, and rows with no
    confirmed TT product (left out).
  - **Export risk-tool upload** writes one CSV per IsvTeName, in the layout of its
    file, with only the changed and new products. A new product goes to the
    IsvTeName that already has its exchange. Max Order goes to "Max order quantity"
    and "Spreads: Max order quantity", Max Net to "Max position product (net)".
    Exchanges are matched case-free, with CBOT / NYMEX / COMEX counted as CME and JPX
    as OSE (TT's names).
  - **Both risk-system uploads** (new setup and account upload) use the column names
    "Max long position (gross)" / "Max short position (gross)" (renamed from "... per
    contract" when a loaded export still has the old names), and set four columns on
    every row as the last four columns, in this order: Wholesale Spreads: Max order
    quantity (Not Set), Wholesale Spreads: Trading allowed (False), Wholesale: Max
    order quantity (Not Set), Wholesale: Trading allowed (False). Every true/false
    value in these files is written True / False.
- **Sorting and filtering:** in both tables, click a header to sort (again to
  reverse, a third time to clear) and use its ▾ to filter by value, as in Excel.
  Copy table and Export Excel take the client table as shown; the risk-tool upload
  is never filtered.
- **Team lists:** the TT product list, symbol mappings and unavailable products can
  come from a shared folder, so every user starts with the same ones. A banner at
  the top of the tab says which team lists are in use. Put TT's product list there as
  `tt_exchange_products.csv` (as downloaded from TT; Lookout keeps only the futures).
  A user can still drop their own TT file, which wins in their browser until they
  click "Use the team list". Mappings and unavailable products marked in the tab are
  kept in that user's browser. To share them with everyone, set `[limits] shared_folder` in
  each user's `config.toml` to a folder all users can read (single quotes for a
  network path: `shared_folder = '\\server\share\Lookout'`). On that PC, click
  **Export mapping CSV** and **Export unavailable CSV** in the tab and put the two
  files in the folder, named `tt_symbol_mappings.csv` and `unavailable_products.csv`.
  - Lookout reads them once per session; **Reload team lists** reads them again. Each
    user's own additions stay on top, and team items show as "shared".
  - **Saved mappings** shows two lists: *Team mappings* (from the shared file) and
    *Your mappings* (saved in this browser only, with a note when one overrides a team
    mapping). **commit to team** (one mapping) or **Commit all to the team file** saves
    them into `tt_symbol_mappings.csv` on the share, so every user gets them. This
    needs write access to the folder. Lookout merges them into the current file
    (yours replace a team mapping for the same RIC root and exchange) under a lock
    file, so two users can't overwrite each other, and keeps the previous version in
    `history/` (last 30). This is the only file Lookout ever writes in that folder.
  - Unavailable products are still shared by exporting them and replacing
    `unavailable_products.csv`.
- **Risk and periodic audit**: a client's whole product universe against the limits
  in place (the risk system's exports), with the risk team's Excel review, approvals
  and the upload CSV back into the risk tool.

It is the app's compiled file, copied into `vendor/limits_app/index.html` and never
edited here; `vendor/limits_app/README.md` says how to refresh it. It runs in the
browser only: no TT calls. Saved mappings and approvals live in the browser's storage
for Lookout's address; a loaded risk export is kept for the session only.

## Clone (UAT only)

The Clone page copies an account, or a group with the accounts below it, into new
accounts. It is the only part of Lookout that changes TT, and only in UAT: in Live the
page shows a notice and nothing else. Its calls go through `core/write_http.py`, a
separate guard that allows only these, and only in `ext_uat_cert`:

- `POST /ttaccount/ext_uat_cert/account/clone`: clone one account
- `POST /ttsetup/ext_uat_cert/ordertagdefaults`: create Order Tag Defaults

How it works:

1. Enter the source account (name or id).
2. Paste the new accounts (straight from Excel, or one per line), or choose a CSV file.
   Columns, in order: new account (needed), exchange and clearing account override
   (optional, but together). An account may have several rows, one per exchange.
   Exchanges are TT's market names (CME, ICE_L, Eurex...). Spaces around each cell are
   removed, blank rows are dropped and a header row (`Account`, `Name`...) is skipped.
3. Optionally tick the clearing account override (the "Clearing Account" Order Tag
   Default). A row's value is used for its account on its exchange; on the source's
   other exchanges the value is either the source account's value or the new
   account's name. A row may name an exchange the source has no Clearing Account
   on: the source's entry is then copied with the exchange switched (not possible
   when the source's entries are set per product). Clearing values without the box
   ticked stop the plan.
4. Press **Next**. Lookout reads the source from TT UAT and lists every account and
   Clearing Account value it would create, with any problem (e.g. a name that
   already exists).
5. Edit the two tables in place if needed: rename a new account or change a New
   value, or select rows and delete them (deleting an account also drops the
   accounts cloned below it; deleting a clearing row means that exchange gets no
   override). The plan is checked again after every edit, without reading TT again;
   **Undo all edits** goes back to the pasted plan.
6. Tick "I have checked this list" (again after any edit) and press **Create**.

For a group, Lookout clones each account itself, top down. Each copy's name swaps the
source's name for the new one: cloning POD1 as POD7 turns POD1-A into POD7-A. An account
below the source whose name does not contain the source's name stops the plan. A row's
clearing value goes on the new top account only; the accounts below it get the
clearing value from the page's choice.

TT copies none of the source's other settings: every switch on the clone call (Order
Tag Defaults, algo, exchange, credit/SOD, counterparties, connections, FIX sessions,
limits, users) is sent as off. TT's clone call can't set an account description, so
the Clone page doesn't take one. A run
stops at the first failure and never undoes what it created. Every run is recorded in
`%LOCALAPPDATA%\Lookout\uat\clone_log`.

TT's reference says the clone reply lists the new account with its id. The first real
UAT clone's reply did not give the id in that form, so Lookout now accepts the id
wherever it is in the reply. If the reply has no id, Lookout finds the new account by
name in TT's accounts list. Only if both fail does it stop, and it then shows TT's reply.

To confirm on the next UAT clone: the reply shape of the clone call, the form of its
on/off switches (`"true"`/`"false"`; `cloneChildren` is sent as `"False"`), and the Order
Tag Default bodies. These follow TT's public ttaccount and ttsetup references for UAT.

## API log

Every TT call is written to a local log for troubleshooting: one JSON line per call,
one file per day (`api_YYYYMMDD.jsonl`), in `%LOCALAPPDATA%\Lookout\api_log` (Live)
or `%LOCALAPPDATA%\Lookout\uat\api_log` (UAT). Each line holds the time, method, path,
query (with the `requestId` TT support asks for), status, duration, the body of a POST
and TT's reply, cut at 200,000 characters. Headers are never logged, so the API key
and the token never appear. The sign-in call logs no bodies. The log holds account
data, like a snapshot: keep it on the PC.

## Client profiles

Setup Verification reads one TOML profile per client from the profiles folder
(`[clients] directory` in `config.toml`; default `%LOCALAPPDATA%\Lookout\clients`).
A profile names the accounts each client tree starts from (a path from a top-level
account), the levels below it by depth, and the expected values at each level. See
`docs/example_client.toml`.

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
