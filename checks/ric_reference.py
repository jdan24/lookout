"""Reference data for matching RIC-based limit sheets to TT products.

Ported verbatim from limit_compare's RIC tool (``knownMappings.ts`` and
``exchanges.ts``). Generic market data, not client rules.
"""

from __future__ import annotations

# Curated Refinitiv RIC root -> TT futures product, for well-known contracts where the
# RIC root differs from the TT symbol (or the exchange is ambiguous). An entry applies
# when the row's exchange is blank or resolves to the entry's TT exchange.
KNOWN_MAPPINGS: list[tuple[str, str, str]] = [
    ("TY", "CME", "ZN"),  # 10-Year T-Note
    ("FV", "CME", "ZF"),  # 5-Year T-Note
    ("TU", "CME", "ZT"),  # 2-Year T-Note
    ("US", "CME", "ZB"),  # T-Bond
    ("AUL", "CME", "UB"),  # Ultra T-Bond
    ("UB", "CME", "UB"),  # Ultra T-Bond
    ("TN", "CME", "TN"),  # Ultra 10-Year T-Note
    ("SRA", "CME", "SR3"),  # 3-Month SOFR
    ("SR3", "CME", "SR3"),  # 3-Month SOFR
    ("FF", "CME", "ZQ"),  # 30-Day Fed Funds
    ("C", "CME", "ZC"),  # Corn
    ("S", "CME", "ZS"),  # Soybeans
    ("W", "CME", "ZW"),  # Chicago SRW Wheat
    ("KW", "CME", "KE"),  # KC HRW Wheat
    ("BO", "CME", "ZL"),  # Soybean Oil
    ("SM", "CME", "ZM"),  # Soybean Meal
    ("O", "CME", "ZO"),  # Oats
    ("RR", "CME", "ZR"),  # Rough Rice
    ("LC", "CME", "LE"),  # Live Cattle
    ("FC", "CME", "GF"),  # Feeder Cattle
    ("LH", "CME", "HE"),  # Lean Hogs
    ("GC", "CME", "GC"),  # Gold
    ("SI", "CME", "SI"),  # Silver
    ("HG", "CME", "HG"),  # Copper
    ("PL", "CME", "PL"),  # Platinum
    ("PA", "CME", "PA"),  # Palladium
    ("CL", "CME", "CL"),  # WTI Crude Oil
    ("NG", "CME", "NG"),  # Henry Hub Natural Gas
    ("HO", "CME", "HO"),  # NY Harbor ULSD
    ("RB", "CME", "RB"),  # RBOB Gasoline
    ("ES", "CME", "ES"),  # E-mini S&P 500
    ("NQ", "CME", "NQ"),  # E-mini Nasdaq-100
    ("YM", "CME", "YM"),  # E-mini Dow
    ("RTY", "CME", "RTY"),  # E-mini Russell 2000
    ("EMD", "CME", "EMD"),  # E-mini S&P MidCap 400
    ("NIY", "CME", "NIY"),  # Nikkei 225 (Yen)
    ("NKD", "CME", "NKD"),  # Nikkei 225 (USD)
    ("AD", "CME", "6A"),  # Australian Dollar
    ("BP", "CME", "6B"),  # British Pound
    ("CD", "CME", "6C"),  # Canadian Dollar
    ("URO", "CME", "6E"),  # Euro FX
    ("EC", "CME", "6E"),  # Euro FX
    ("JY", "CME", "6J"),  # Japanese Yen
    ("SF", "CME", "6S"),  # Swiss Franc
    ("NE", "CME", "6N"),  # New Zealand Dollar
    ("MP", "CME", "6M"),  # Mexican Peso
    ("RA", "CME", "6Z"),  # South African Rand
    ("BR", "CME", "6L"),  # Brazilian Real
    ("VX", "CFE", "VX"),  # Cboe Volatility Index
    ("FDX", "Eurex", "FDAX"),  # DAX
    ("FDXM", "Eurex", "FDXM"),  # Mini-DAX
    ("STXE", "Eurex", "FESX"),  # EURO STOXX 50
    ("FESX", "Eurex", "FESX"),  # EURO STOXX 50
    ("FVS", "Eurex", "FVS"),  # VSTOXX
    ("FSMI", "Eurex", "FSMI"),  # SMI
    ("FESB", "Eurex", "FESB"),  # EURO STOXX Banks
    ("FXXP", "Eurex", "FXXP"),  # STOXX Europe 600
    ("FGBL", "Eurex", "FGBL"),  # Euro-Bund
    ("FGBM", "Eurex", "FGBM"),  # Euro-Bobl
    ("FGBS", "Eurex", "FGBS"),  # Euro-Schatz
    ("FGBX", "Eurex", "FGBX"),  # Euro-Buxl
    ("FBTP", "Eurex", "FBTP"),  # Long-Term Euro-BTP
    ("FBTS", "Eurex", "FBTS"),  # Short-Term Euro-BTP
    ("FOAT", "Eurex", "FOAT"),  # Euro-OAT
    ("LCO", "ICE", "BRN"),  # Brent Crude
    ("LGO", "ICE", "G"),  # Low Sulphur Gasoil
    ("WTCL", "ICE", "WBS"),  # WTI Crude (ICE)
    ("LCC", "ICE", "C"),  # London Cocoa
    ("LSU", "ICE", "W"),  # White Sugar
    ("LRC", "ICE", "RC"),  # Robusta Coffee
    ("TFMBM", "ICE", "TFM"),  # Dutch TTF Natural Gas
    ("KC", "ICE", "KC"),  # Coffee "C"
    ("SB", "ICE", "SB"),  # Sugar No. 11
    ("CT", "ICE", "CT"),  # Cotton No. 2
    ("CC", "ICE", "CC"),  # NY Cocoa
    ("DX", "ICE", "DX"),  # US Dollar Index
    ("MME", "ICE", "MME"),  # MSCI Emerging Markets
    ("FLG", "ICE_L", "R"),  # Long Gilt
    ("FMG", "ICE_L", "H"),  # Medium Gilt
    ("FSG", "ICE_L", "G"),  # Short Gilt
    ("FEI", "ICE_L", "I"),  # 3-Month Euribor
    ("SON3", "ICE_L", "SO3"),  # 3-Month SONIA
    ("FFI", "ICE_L", "Z"),  # FTSE 100
    ("HSI", "HKEX", "HSI"),  # Hang Seng Index
    ("HCEI", "HKEX", "HHI"),  # Hang Seng China Enterprises (H-shares)
    ("HHTI", "HKEX", "HTI"),  # Hang Seng TECH
    ("MHI", "HKEX", "MHI"),  # Mini Hang Seng
    ("JNI", "JPX", "225"),  # Nikkei 225
    ("JNM", "JPX", "225M"),  # Nikkei 225 Mini
    ("JTI", "JPX", "TPX"),  # TOPIX
    ("JGB", "JPX", "JBL"),  # 10-Year JGB
    ("SSI", "SGX", "NK"),  # SGX Nikkei 225
    ("SFC", "SGX", "CN"),  # FTSE China A50
    ("STW", "SGX", "TWN"),  # FTSE Taiwan
    ("YAP", "ASX", "AP"),  # SPI 200
    ("YT", "ASX", "YT"),  # 3-Year Treasury Bond
    ("XT", "ASX", "XT"),  # 10-Year Treasury Bond
    ("YBA", "ASX", "IR"),  # 90-Day Bank Bills
    ("CGB", "MX", "CGB"),  # 10-Year Canada Bond
    ("SXF", "MX", "SXF"),  # S&P/TSX 60
    ("CRA", "MX", "CRA"),  # 3-Month CORRA
    ("AEX", "Euronext", "kFTI"),  # AEX Index
    ("FCE", "Euronext", "jFCE"),  # CAC 40
    ("FMIB", "Euronext", "iFIB"),  # FTSE MIB
    ("EBM", "Euronext", "yEBM"),  # Milling Wheat
    ("KS", "KRX", "K2I"),  # KOSPI 200
]

# Pasted "Exchange" values (names or Reuters codes, canonicalised) -> TT exchange names.
# TT groups CBOT/NYMEX/COMEX under CME and splits ICE into ICE (US) and ICE_L (Europe).
EXCHANGE_ALIASES: dict[str, list[str]] = {
    "cme": ["CME"],
    "cmegroup": ["CME"],
    "globex": ["CME"],
    "cbot": ["CME"],
    "cbotd": ["CME"],
    "cbt": ["CME"],
    "nymex": ["CME"],
    "nym": ["CME"],
    "comex": ["CME"],
    "cmx": ["CME"],
    "ice": ["ICE", "ICE_L"],
    "iceus": ["ICE"],
    "iceusa": ["ICE"],
    "icefuturesus": ["ICE"],
    "nybot": ["ICE"],
    "iceeu": ["ICE_L", "ICE"],
    "iceeurope": ["ICE_L", "ICE"],
    "iceu": ["ICE_L", "ICE"],
    "icel": ["ICE_L", "ICE"],
    "icefutureseurope": ["ICE_L", "ICE"],
    "ife": ["ICE_L", "ICE"],
    "liffe": ["ICE_L", "ICE"],
    "icelondon": ["ICE_L", "ICE"],
    "eurex": ["Eurex"],
    "dtb": ["Eurex"],
    "euronext": ["Euronext"],
    "matif": ["Euronext"],
    "monep": ["Euronext"],
    "liffeparis": ["Euronext"],
    "aex": ["Euronext"],
    "meff": ["MEFF"],
    "idem": ["Euronext"],
    "eex": ["EEX"],
    "lme": ["LME", "LME_NTP"],
    "moex": ["MOEX"],
    "wse": ["WSE"],
    "athex": ["ATHEX"],
    "bist": ["BIST"],
    "nasdaqnordic": ["NASDAQ_NED"],
    "omx": ["NASDAQ_NED"],
    "asx": ["ASX"],
    "asx24": ["ASX"],
    "sfe": ["ASX"],
    "sydneyfuturesexchange": ["ASX"],
    "jpx": ["JPX"],
    "ose": ["JPX"],
    "osaka": ["JPX"],
    "tse": ["JPX"],
    "tocom": ["JPX"],
    "tfx": ["TFX"],
    "sgx": ["SGX", "SGX_GIFT"],
    "simex": ["SGX"],
    "hkex": ["HKEX"],
    "hkfe": ["HKEX"],
    "krx": ["KRX"],
    "kfe": ["KRX"],
    "taifex": ["TAIFEX"],
    "tfex": ["TFEX"],
    "nse": ["NSE"],
    "shfe": ["SHFE"],
    "dce": ["DCE"],
    "czce": ["CZCE"],
    "ine": ["INE"],
    "cffex": ["CCFX"],
    "nzx": ["NZX"],
    "me": ["MX"],
    "mx": ["MX"],
    "mse": ["MX"],
    "montreal": ["MX"],
    "montrealexchange": ["MX"],
    "cfe": ["CFE"],
    "cboe": ["CFE"],
    "b3": ["B3"],
    "bmf": ["B3"],
    "bmfbovespa": ["B3"],
    "mexder": ["MEXDER"],
    "jse": ["JSE"],
    "safex": ["JSE"],
    "dgcx": ["DGCX"],
    "nodal": ["NODAL"],
    "nfx": ["NFX"],
    "imm": ["CME"],
    "iom": ["CME"],
    "gem": ["CME"],
    "cec": ["CME"],
    "cbf": ["CFE"],
    "eux": ["Eurex"],
    "ieu": ["ICE_L", "ICE"],
    "ifeu": ["ICE_L", "ICE"],
    "ifl": ["ICE_L", "ICE"],
    "nyb": ["ICE"],
    "nyf": ["ICE"],
    "ius": ["ICE"],
    "ifus": ["ICE"],
    "edx": ["ICE", "ICE_L"],
    "endex": ["ICE", "ICE_L"],
    "hfe": ["HKEX"],
    "mon": ["MX"],
    "mfx": ["MEFF"],
    "mef": ["MEFF"],
    "mil": ["Euronext"],
    "par": ["Euronext"],
    "bru": ["Euronext"],
    "lis": ["Euronext"],
    "osl": ["Euronext"],
    "tai": ["TAIFEX"],
    "twf": ["TAIFEX"],
    "saf": ["JSE"],
    "shf": ["SHFE"],
    "zce": ["CZCE"],
    "ccf": ["CCFX"],
    "mdx": ["CME"],
    "nlx": ["CurveGlobal"],
}
