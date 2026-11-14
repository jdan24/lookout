# limit_compare parity fixtures

Made-up sample inputs (invented names and limits; exchange and product symbols are
public market data) and `expected_outputs.json`: what the original limit_compare
TypeScript code produces on them. The Python port in `checks/` is tested against
that file.

`ric_real_world` in the JSON was produced with TT's full public ExchangeProducts CSV,
which is not in this repo. Those tests run only when the file is available:

    LOOKOUT_TT_PRODUCTS_CSV=<path to the full TT CSV> uv run pytest

## Regenerating

With a checkout of limit_compare (its `npm install` done) and Node 22.18 or later,
from this folder:

    LIMIT_COMPARE_SPA=<checkout>/spa \
    LIMIT_COMPARE_RIC_TOOL=<RIC tool folder under src/tools> \
    LIMIT_COMPARE_UNIVERSE_TOOL=<product-universe tool folder under src/tools> \
      node --import ./regenerate/register.mjs regenerate/dump.mts <full TT CSV> expected_outputs.json

`regenerate/hooks.mjs` resolves the TypeScript `@/` imports. The script reads the
samples in this folder and only reads the limit_compare checkout; nothing there is
changed. The original's field keys are renamed to the port's neutral names in the
output (e.g. `supported`, `ticker`, `code`).

`ric_real_world_sample.tsv` lists public futures; its two "Expected" columns were
checked by hand against the product names in TT's full product list.
