// Resolve the limit_compare "@/..." alias and extensionless .ts imports, read-only.
import { existsSync } from "node:fs";
import { pathToFileURL, fileURLToPath } from "node:url";
const SRC = process.env.LIMIT_COMPARE_SPA.replace(/\/?$/, "/") + "src/";
export async function resolve(spec, ctx, next) {
  let s = spec;
  if (s.startsWith("@/")) s = pathToFileURL(SRC + s.slice(2)).href;
  else if ((s.startsWith("./") || s.startsWith("../")) && ctx.parentURL?.startsWith("file:"))
    s = new URL(s, ctx.parentURL).href;
  if (s.startsWith("file:")) {
    const p = fileURLToPath(s);
    for (const cand of [p, p + ".ts", p + "/index.ts"]) if (existsSync(cand) && !cand.endsWith("/")) {
      try { if (!require_isDir(cand)) return next(pathToFileURL(cand).href, ctx); } catch {}
    }
  }
  return next(spec, ctx);
}
import { statSync } from "node:fs";
function require_isDir(p) { return statSync(p).isDirectory(); }
