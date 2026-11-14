import { readFileSync, writeFileSync } from "node:fs";
import { createRequire } from "node:module";
// LIMIT_COMPARE_SPA: the limit_compare "spa" folder; LIMIT_COMPARE_RIC_TOOL and
// LIMIT_COMPARE_UNIVERSE_TOOL: its two tool folders under src/tools. The sample
// inputs are read from this fixtures folder.
const SPA = process.env.LIMIT_COMPARE_SPA!.replace(/\/?$/, "/");
const RIC = "tools/" + process.env.LIMIT_COMPARE_RIC_TOOL! + "/";
const UNI = "tools/" + process.env.LIMIT_COMPARE_UNIVERSE_TOOL! + "/";
const FIX = new URL("../", import.meta.url).pathname;
const req = createRequire(SPA + "package.json");
const XLSX = req("xlsx-js-style");
const Papa = req("papaparse");
const m = (p: string) => import(SPA + "src/" + p + ".ts");
const { autoDetect } = await m("shared/parsers/autoDetect");
const RIC_FIELDS = Object.values(await m(RIC + "fields")).find(Array.isArray); // its one export
const { parsePastedTable } = await m(RIC + "paste");
const { buildTtIndex, parseTtCsv } = await m(RIC + "tt");
const { enrich, outputColumns } = await m(RIC + "enrich");
const { tokens, nameScore, rankByDescription } = await m(RIC + "describe");
const { ttExchangesFor } = await m(RIC + "exchanges");
const { parseMappingsCsv } = await m(RIC + "mappingCsv");
const { CLIENT_FIELDS, SYSTEM_FIELDS } = await m(UNI + "fields");
const { normalizeClient, normalizeSystem, parseLimit } = await m(UNI + "limits/normalize");
const { computeDiff } = await m(UNI + "limits/diff");

const fullCsv = process.argv[2];
const out: Record<string, unknown> = {};
const rowOut = (r: any) => ({ ricRoot: r.ricRoot, exchange: r.exchange, status: r.status,
  ttExchange: r.ttExchange, ttSymbol: r.ttSymbol,
  candidates: r.candidates.map((p: any) => `${p.exchange} ${p.symbol}`) });
const enrichOut = (res: any) => ({ columns: res.columns, counts: res.counts, rows: res.rows.map(rowOut) });

// --- RIC mapping ---
const raw = parsePastedTable(readFileSync(FIX + "ric_paste_sample.tsv", "utf8"));
const cols = autoDetect(RIC_FIELDS, raw.headers).mapping;
const sample = parseTtCsv(readFileSync(FIX + "tt_products_sample.csv", "utf8"));
const tt = buildTtIndex(sample);
out.ric_paste = { headers: raw.headers, rows: raw.rows, mapping: cols };
out.ric_sample_products = sample;
const userMaps = [
  { ricRoot: "FLG", exchange: "ICE Europe", ttExchange: "ICE_L", ttSymbol: "R" },
  { ricRoot: "G", exchange: "", ttExchange: "ICE_L", ttSymbol: "G" },
  { ricRoot: "TY", exchange: "CBOT", ttExchange: "CME", ttSymbol: "TN" },
];
out.ric_enrich = {
  default: enrichOut(enrich(raw, cols, tt, [])),
  no_known: enrichOut(enrich(raw, cols, tt, [], { known: false })),
  mapped: enrichOut(enrich(raw, cols, tt, userMaps)),
  no_tt: enrichOut(enrich(raw, cols, null, [])),
};
out.ric_user_mappings = userMaps;
const descs = [...raw.rows.map((r: any) => r["Product Description"]),
  "US 10yr Note", "3 Month SOFR", "Low Sulphur Gasoil", "Hard Red Winter Wheat", "E-mini S&P 500",
  "Euro-Bund", "AUD/USD", "Nikkei 225 (Yen)", "T-Bond 30 yrs", "Two Year U.S. T-Notes", "...", ""];
out.ric_tokens = descs.map((d) => ({ text: d, tokens: tokens(d) }));
out.ric_name_scores = descs.flatMap((d) => sample.map((p: any) => ({ text: d, exchange: p.exchange, symbol: p.symbol, score: nameScore(new Set(tokens(d)), p) })));
out.ric_rank = descs.map((d) => ({ text: d, ranked: rankByDescription(d, "", sample, 5).map((s: any) => ({ p: `${s.product.exchange} ${s.product.symbol}`, name: s.name, total: s.total })) }));
const exNames = ["CBOT", "ICE Europe", "ICE", "SFE", "ME", "EUREX", "IEU", "Endex", "cboe", "", "Nowhere", "ASX", "Eurex", "c.b.o.t"];
const exSet = new Set(sample.map((p: any) => p.exchange));
out.ric_exchanges = exNames.map((e) => ({ exchange: e, tt: ttExchangesFor(e, exSet) }));
out.ric_output_columns = [
  { headers: ["A", "Max Order", "B", "Max Net"], cols: { maxOrder: "Max Order", maxNet: "Max Net" } },
  { headers: ["A", "B"], cols: {} },
  { headers: ["A", "TT Exchange", "Max Net", "TT Product Symbol"], cols: { maxNet: "Max Net" } },
].map((c) => ({ ...c, out: outputColumns(c.headers, c.cols) }));
out.ric_mapping_csv = parseMappingsCsv("Ric Root,Exchange,TT Exchange,TT Symbol\r\nFLG,\"ICE, Europe\",ICE_L,R\r\nX,,,\r\n");

if (fullCsv) {
  const full = buildTtIndex(parseTtCsv(readFileSync(fullCsv, "utf8")));
  const ap = parsePastedTable(readFileSync(FIX + "ric_real_world_sample.tsv", "utf8"));
  const apCols = autoDetect(RIC_FIELDS, ap.headers).mapping;
  out.ric_real_world = {
    rows: ap.rows, mapping: apCols, futures: full.products.length,
    default: enrichOut(enrich(ap, apCols, full, [])),
    no_known: enrichOut(enrich(ap, apCols, full, [], { known: false })),
    paste_sample_full: enrichOut(enrich(raw, cols, full, [])),
  };
}

// --- Product universe ---
const wb = XLSX.read(readFileSync(FIX + "client_sample.xlsx"), { type: "buffer", cellDates: true });
const arrays = XLSX.utils.sheet_to_json(wb.Sheets[wb.SheetNames[0]], { header: 1, defval: "", raw: false });
const headers = (arrays[0] as unknown[]).map((h, i) => String(h ?? "").trim() || `Column ${i + 1}`);
const crow: Record<string, string>[] = [];
for (const arr of arrays.slice(1) as unknown[][]) {
  const row: Record<string, string> = {}; let has = false;
  headers.forEach((h, j) => { const v = String(arr[j] ?? "").trim(); row[h] = v; if (v) has = true; });
  if (has) crow.push(row);
}
const clientRaw = { headers, rows: crow, fileType: "xlsx", fileName: "client_sample.xlsx" };
const sysText = readFileSync(FIX + "system_sample.csv", "utf8");
const sp = Papa.parse(sysText, { header: true, skipEmptyLines: true, transformHeader: (h: string) => h.trim() });
const sysRaw = { headers: sp.meta.fields.map((h: string) => h.trim()), rows: sp.data, fileType: "csv", fileName: "system_sample.csv" };
const cMap = autoDetect(CLIENT_FIELDS, clientRaw.headers).mapping;
const sMap = autoDetect(SYSTEM_FIELDS, sysRaw.headers).mapping;
const clients = normalizeClient(clientRaw, cMap);
const systems = normalizeSystem(sysRaw, sMap);
const cell = (c: any) => ({ current: c.current, requested: c.requested, category: c.category });
const diffOut = (d: any) => ({ counts: d.counts, unmatched: d.unmatched.map((r: any) => r.key),
  rows: d.rows.map((r: any) => ({ key: r.key, category: r.category, matchMethod: r.matchMethod,
    tsTicker: r.client?.tsTicker ?? null, family: r.system?.family ?? null, account: r.account,
    maxPosition: cell(r.maxPosition), maxOrderQtyOutright: cell(r.maxOrderQtyOutright),
    maxOrderQtySpreads: cell(r.maxOrderQtySpreads) })) });
out.universe_client_raw = clientRaw;
out.universe_system_raw = { headers: sysRaw.headers, rows: sysRaw.rows };
out.universe_mappings = { client: cMap, system: sMap };
out.universe_clients = clients.map(({ raw, ...rest }: any) => rest);
out.universe_systems = systems.map(({ raw, ...rest }: any) => rest);
out.universe_diff = {
  no_map: diffOut(computeDiff(clients, systems, [])),
  with_map: diffOut(computeDiff(clients, systems, [{ clientTicker: "GBL", type: "Future", systemFamily: "FGBL" }])),
};
const limits = ["", " ", "Not Set", "n/a", "NA", "-", "—", "1,000", " 2 000 ", "$500", "50%", "1.5", "-3", "abc", "1e3", "0", "Infinity", "0x10"];
out.universe_parse_limit = limits.map((t) => ({ text: t, value: parseLimit(t) }));

// The original names its "supported" column after a firm; the port uses a neutral name.
// The original's field keys are renamed to the port's neutral names.
const RENAME: [string, string][] = [["wfSupported", "supported"], ["qCode", "code"],
  ["tsTicker", "ticker"], ["bbgTicker", "bloombergTicker"]];
let json = JSON.stringify(out, null, 1);
for (const [from, to] of RENAME) json = json.replaceAll(`"${from}"`, `"${to}"`);
writeFileSync(process.argv[3], json);
console.log("written", Object.keys(out).join(", "));
