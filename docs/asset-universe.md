<!-- GENERATED FROM backend/archimedes/data/synthetic_universe.json BY scripts/gen_asset_universe_doc.py — DO NOT EDIT BY HAND; run: PYTHONPATH=backend python scripts/gen_asset_universe_doc.py -->

# Archimedes asset universe

What Archimedes prices and trades on-chain. This page is **generated from the single source of truth** (`backend/archimedes/data/synthetic_universe.json`) so it can never drift from the actual universe — a CI test fails if it's stale. Regenerate: `PYTHONPATH=backend python scripts/gen_asset_universe_doc.py`.

- **On-chain deploy-eligible synths:** 281 across 31 asset classes
- **Oracle coverage (per source):** 203 with a real Pyth Hermes feed · 78 Pyth-null · 0 Stork · 0 Chainlink (of 281)
- **Expected oracle tiers (hint):** admin, single_checked
- **Single-name equity synths held back (backtest-only, compliance):** 59

**Oracle honesty (T1.5b / #759):** `pyth_feed_id` is a REAL Pyth Hermes id or null (never fabricated); `stork_asset_id` and `chainlink_feed` are null for now — no public Stork catalog to resolve yet, and Chainlink price Data Feeds are NOT yet deployed on Arc testnet (only CCIP as of 2026-06-30). `oracle_tier` is a HINT from the count of configured feeds (`quorum` ≥2, `single_checked` =1, `admin` =0); the AUTHORITATIVE tier is on-chain via `QuorumPriceOracle.priceWithProvenance()` (#840).

**Parity invariant:** every on-chain synth is also backtestable (`on-chain ⊆ GLOBAL_ASSETS`); every backtestable-but-not-on-chain symbol is an explained compliance-flagged single stock; and **no on-chain synth is compliance-held** (`on-chain ∩ compliance-held = ∅`). Enforced by `backend/tests/test_universe_parity.py`.

## On-chain deploy-eligible universe

All 281 synths below are **on-chain-eligible** (priced on the live path).

| symbol | name | asset_class | price_usd | pyth | stork | chainlink_feed | oracle_tier |
|---|---|---|---:|:---:|:---:|:---:|:---:|
| sCORN | Synthetic CORN (corn ETF) | agri_etf | $19.30 | no | no | null | admin |
| sMOO | Synthetic MOO (agribusiness) | agri_etf | $68.10 | no | no | null | admin |
| sSOYB | Synthetic SOYB (soybean ETF) | agri_etf | $22.10 | no | no | null | admin |
| sWEAT | Synthetic WEAT (wheat ETF) | agri_etf | $4.80 | no | no | null | admin |
| sASHR | Synthetic ASHR (China A-shares) | asia_equity_etf | $27.80 | no | no | null | admin |
| sDXJ | Synthetic DXJ (Japan hedged) | asia_equity_etf | $108.40 | no | no | null | admin |
| sEIDO | Synthetic EIDO (Indonesia) | asia_equity_etf | $19.30 | no | no | null | admin |
| sENZL | Synthetic ENZL (New Zealand) | asia_equity_etf | $47.20 | no | no | null | admin |
| sEPI | Synthetic EPI (India) | asia_equity_etf | $47.30 | no | no | null | admin |
| sEWA | Synthetic EWA (Australia) | asia_equity_etf | $25.10 | no | no | null | admin |
| sEWH | Synthetic EWH (Hong Kong) | asia_equity_etf | $18.40 | yes | no | null | single_checked |
| sEWJ | Synthetic EWJ (Japan) | asia_equity_etf | $73.20 | yes | no | null | single_checked |
| sEWM | Synthetic EWM (Malaysia) | asia_equity_etf | $23.10 | no | no | null | admin |
| sEWS | Synthetic EWS (Singapore) | asia_equity_etf | $22.60 | no | no | null | admin |
| sEWT | Synthetic EWT (Taiwan) | asia_equity_etf | $52.10 | no | no | null | admin |
| sEWY | Synthetic EWY (Korea) | asia_equity_etf | $58.30 | yes | no | null | single_checked |
| sFXI | Synthetic FXI (China large) | asia_equity_etf | $31.20 | no | no | null | admin |
| sINDA | Synthetic INDA (India) | asia_equity_etf | $55.80 | yes | no | null | single_checked |
| sKWEB | Synthetic KWEB (China tech) | asia_equity_etf | $33.60 | yes | no | null | single_checked |
| sMCHI | Synthetic MCHI (China) | asia_equity_etf | $51.40 | yes | no | null | single_checked |
| sTHD | Synthetic THD (Thailand) | asia_equity_etf | $68.40 | no | no | null | admin |
| sVNM | Synthetic VNM (Vietnam) | asia_equity_etf | $12.40 | yes | no | null | single_checked |
| sDBA | Synthetic DBA (agriculture) | commodity_etf | $25.60 | no | no | null | admin |
| sDBB | Synthetic DBB (base metals) | commodity_etf | $19.70 | no | no | null | admin |
| sDBC | Synthetic DBC (broad commod) | commodity_etf | $22.40 | no | no | null | admin |
| sGSG | Synthetic GSG (broad commod) | commodity_etf | $21.30 | no | no | null | admin |
| sICLN | Synthetic ICLN (clean energy) | commodity_etf | $13.40 | no | no | null | admin |
| sLIT | Synthetic LIT (lithium/battery) | commodity_etf | $42.80 | no | no | null | admin |
| sPDBC | Synthetic PDBC (broad commod) | commodity_etf | $13.80 | no | no | null | admin |
| sTAN | Synthetic TAN (solar) | commodity_etf | $32.60 | no | no | null | admin |
| sURA | Synthetic URA (uranium) | commodity_etf | $31.20 | no | no | null | admin |
| sWOOD | Synthetic WOOD (timber) | commodity_etf | $82.40 | no | no | null | admin |
| sANGL | Synthetic ANGL (fallen angels) | credit_hy | $29.40 | no | no | null | admin |
| sBKLN | Synthetic BKLN (leveraged loans) | credit_hy | $21.10 | no | no | null | admin |
| sHYG | Synthetic HYG (HY credit) | credit_hy | $79.40 | yes | no | null | single_checked |
| sJNK | Synthetic JNK (HY credit) | credit_hy | $96.80 | no | no | null | admin |
| sFLOT | Synthetic FLOT (floating rate) | credit_ig | $50.70 | yes | no | null | single_checked |
| sLQD | Synthetic LQD (IG credit) | credit_ig | $110.30 | yes | no | null | single_checked |
| sPFF | Synthetic PFF (preferred) | credit_ig | $32.40 | no | no | null | admin |
| sVCIT | Synthetic VCIT (IG credit) | credit_ig | $80.40 | yes | no | null | single_checked |
| sVCSH | Synthetic VCSH (short IG) | credit_ig | $76.90 | yes | no | null | single_checked |
| sAAVE | Synthetic AAVE | crypto | $340.20 | yes | no | null | single_checked |
| sADA | Synthetic ADA | crypto | $1.05 | yes | no | null | single_checked |
| sALGO | Synthetic ALGO | crypto | $0.42 | yes | no | null | single_checked |
| sAPE | Synthetic APE | crypto | $1.20 | yes | no | null | single_checked |
| sAPT | Synthetic APT | crypto | $11.40 | yes | no | null | single_checked |
| sAR | Synthetic AR | crypto | $12.80 | yes | no | null | single_checked |
| sARB | Synthetic ARB | crypto | $0.92 | yes | no | null | single_checked |
| sATOM | Synthetic ATOM | crypto | $8.90 | yes | no | null | single_checked |
| sAVAX | Synthetic AVAX | crypto | $42.30 | yes | no | null | single_checked |
| sAXS | Synthetic AXS | crypto | $8.40 | yes | no | null | single_checked |
| sBCH | Synthetic BCH | crypto | $512.30 | yes | no | null | single_checked |
| sBNB | Synthetic BNB | crypto | $685.00 | yes | no | null | single_checked |
| sBTC | Synthetic BTC | crypto | $104,500.00 | yes | no | null | single_checked |
| sCHZ | Synthetic CHZ | crypto | $0.088 | yes | no | null | single_checked |
| sCRV | Synthetic CRV | crypto | $1.10 | yes | no | null | single_checked |
| sDASH | Synthetic DASH | crypto | $38.10 | yes | no | null | single_checked |
| sDOGE | Synthetic DOGE | crypto | $0.38 | yes | no | null | single_checked |
| sDOT | Synthetic DOT | crypto | $8.20 | yes | no | null | single_checked |
| sDYDX | Synthetic DYDX | crypto | $1.40 | yes | no | null | single_checked |
| sEGLD | Synthetic EGLD | crypto | $32.40 | yes | no | null | single_checked |
| sENA | Synthetic ENA | crypto | $0.98 | yes | no | null | single_checked |
| sENJ | Synthetic ENJ | crypto | $0.24 | yes | no | null | single_checked |
| sETC | Synthetic ETC | crypto | $32.10 | yes | no | null | single_checked |
| sETH | Synthetic ETH | crypto | $3,850.00 | yes | no | null | single_checked |
| sFET | Synthetic FET | crypto | $1.30 | yes | no | null | single_checked |
| sFIL | Synthetic FIL | crypto | $6.20 | yes | no | null | single_checked |
| sFLOW | Synthetic FLOW | crypto | $1.20 | yes | no | null | single_checked |
| sGALA | Synthetic GALA | crypto | $0.036 | yes | no | null | single_checked |
| sGMX | Synthetic GMX | crypto | $24.10 | yes | no | null | single_checked |
| sGRT | Synthetic GRT | crypto | $0.28 | yes | no | null | single_checked |
| sHBAR | Synthetic HBAR | crypto | $0.31 | yes | no | null | single_checked |
| sICP | Synthetic ICP | crypto | $12.30 | yes | no | null | single_checked |
| sIMX | Synthetic IMX | crypto | $1.85 | yes | no | null | single_checked |
| sINJ | Synthetic INJ | crypto | $26.80 | yes | no | null | single_checked |
| sIOTA | Synthetic IOTA | crypto | $0.28 | yes | no | null | single_checked |
| sJTO | Synthetic JTO | crypto | $3.10 | yes | no | null | single_checked |
| sJUP | Synthetic JUP | crypto | $0.88 | yes | no | null | single_checked |
| sKAS | Synthetic KAS | crypto | $0.12 | yes | no | null | single_checked |
| sKAVA | Synthetic KAVA | crypto | $0.46 | yes | no | null | single_checked |
| sKSM | Synthetic KSM | crypto | $42.10 | yes | no | null | single_checked |
| sLDO | Synthetic LDO | crypto | $2.60 | yes | no | null | single_checked |
| sLINK | Synthetic LINK | crypto | $24.60 | yes | no | null | single_checked |
| sLTC | Synthetic LTC | crypto | $108.40 | yes | no | null | single_checked |
| sMINA | Synthetic MINA | crypto | $0.62 | yes | no | null | single_checked |
| sNEAR | Synthetic NEAR | crypto | $6.30 | yes | no | null | single_checked |
| sONDO | Synthetic ONDO | crypto | $1.40 | yes | no | null | single_checked |
| sOP | Synthetic OP | crypto | $2.10 | yes | no | null | single_checked |
| sPOL | Synthetic POL | crypto | $0.52 | yes | no | null | single_checked |
| sPYTH | Synthetic PYTH | crypto | $0.38 | yes | no | null | single_checked |
| sQNT | Synthetic QNT | crypto | $108.20 | yes | no | null | single_checked |
| sRENDER | Synthetic RENDER | crypto | $8.60 | yes | no | null | single_checked |
| sROSE | Synthetic ROSE | crypto | $0.098 | yes | no | null | single_checked |
| sRUNE | Synthetic RUNE | crypto | $6.10 | yes | no | null | single_checked |
| sSAND | Synthetic SAND | crypto | $0.68 | yes | no | null | single_checked |
| sSEI | Synthetic SEI | crypto | $0.48 | yes | no | null | single_checked |
| sSNX | Synthetic SNX | crypto | $3.20 | yes | no | null | single_checked |
| sSOL | Synthetic SOL | crypto | $215.00 | yes | no | null | single_checked |
| sSTX | Synthetic STX | crypto | $2.30 | yes | no | null | single_checked |
| sSUI | Synthetic SUI | crypto | $4.30 | yes | no | null | single_checked |
| sTHETA | Synthetic THETA | crypto | $2.40 | yes | no | null | single_checked |
| sTIA | Synthetic TIA | crypto | $6.70 | yes | no | null | single_checked |
| sTON | Synthetic TON | crypto | $5.40 | yes | no | null | single_checked |
| sTRX | Synthetic TRX | crypto | $0.26 | yes | no | null | single_checked |
| sUNI | Synthetic UNI | crypto | $14.20 | yes | no | null | single_checked |
| sVET | Synthetic VET | crypto | $0.058 | yes | no | null | single_checked |
| sWLD | Synthetic WLD | crypto | $2.60 | yes | no | null | single_checked |
| sXLM | Synthetic XLM | crypto | $0.44 | yes | no | null | single_checked |
| sXRP | Synthetic XRP | crypto | $2.35 | yes | no | null | single_checked |
| sXTZ | Synthetic XTZ | crypto | $1.05 | yes | no | null | single_checked |
| sYFI | Synthetic YFI | crypto | $8,600.00 | yes | no | null | single_checked |
| sZEC | Synthetic ZEC | crypto | $52.30 | yes | no | null | single_checked |
| sEMB | Synthetic EMB (EM bond) | em_bond | $91.80 | yes | no | null | single_checked |
| sEMLC | Synthetic EMLC (EM local bond) | em_bond | $23.60 | no | no | null | admin |
| sEEM | Synthetic EEM | em_equity_etf | $44.10 | yes | no | null | single_checked |
| sIEMG | Synthetic IEMG | em_equity_etf | $56.30 | yes | no | null | single_checked |
| sVWO | Synthetic VWO | em_equity_etf | $46.30 | yes | no | null | single_checked |
| sEIRL | Synthetic EIRL (Ireland) | eu_equity_etf | $68.10 | no | no | null | admin |
| sEPOL | Synthetic EPOL (Poland) | eu_equity_etf | $27.30 | no | no | null | admin |
| sEWD | Synthetic EWD (Sweden) | eu_equity_etf | $41.80 | no | no | null | admin |
| sEWG | Synthetic EWG (Germany) | eu_equity_etf | $35.40 | no | no | null | admin |
| sEWI | Synthetic EWI (Italy) | eu_equity_etf | $41.30 | no | no | null | admin |
| sEWK | Synthetic EWK (Belgium) | eu_equity_etf | $21.80 | no | no | null | admin |
| sEWL | Synthetic EWL (Switzerland) | eu_equity_etf | $52.10 | no | no | null | admin |
| sEWN | Synthetic EWN (Netherlands) | eu_equity_etf | $62.40 | no | no | null | admin |
| sEWO | Synthetic EWO (Austria) | eu_equity_etf | $23.40 | no | no | null | admin |
| sEWP | Synthetic EWP (Spain) | eu_equity_etf | $34.60 | no | no | null | admin |
| sEWQ | Synthetic EWQ (France) | eu_equity_etf | $43.90 | no | no | null | admin |
| sEWU | Synthetic EWU (UK) | eu_equity_etf | $38.20 | no | no | null | admin |
| sEZU | Synthetic EZU (Eurozone) | eu_equity_etf | $53.70 | no | no | null | admin |
| sBNO | Synthetic BNO (Brent oil ETF) | energy_etf | $28.40 | no | no | null | admin |
| sUNG | Synthetic UNG (natgas ETF) | energy_etf | $14.20 | no | no | null | admin |
| sUSO | Synthetic USO (oil ETF) | energy_etf | $78.50 | yes | no | null | single_checked |
| sAUDJPY | Synthetic AUD/JPY | fx | $98.30 | yes | no | null | single_checked |
| sAUDUSD | Synthetic AUD/USD | fx | $0.625 | yes | no | null | single_checked |
| sEURCHF | Synthetic EUR/CHF | fx | $0.982 | yes | no | null | single_checked |
| sEURGBP | Synthetic EUR/GBP | fx | $0.854 | yes | no | null | single_checked |
| sEURJPY | Synthetic EUR/JPY | fx | $170.60 | yes | no | null | single_checked |
| sEURNOK | Synthetic EUR/NOK | fx | $12.35 | yes | no | null | single_checked |
| sEURSEK | Synthetic EUR/SEK | fx | $11.99 | yes | no | null | single_checked |
| sEURUSD | Synthetic EUR/USD | fx | $1.085 | yes | no | null | single_checked |
| sGBPJPY | Synthetic GBP/JPY | fx | $199.60 | yes | no | null | single_checked |
| sGBPUSD | Synthetic GBP/USD | fx | $1.27 | yes | no | null | single_checked |
| sNZDUSD | Synthetic NZD/USD | fx | $0.565 | yes | no | null | single_checked |
| sUSDBRL | Synthetic USD/BRL | fx | $6.15 | yes | no | null | single_checked |
| sUSDCAD | Synthetic USD/CAD | fx | $1.435 | yes | no | null | single_checked |
| sUSDCHF | Synthetic USD/CHF | fx | $0.905 | yes | no | null | single_checked |
| sUSDCNH | Synthetic USD/CNH | fx | $7.31 | yes | no | null | single_checked |
| sUSDCZK | Synthetic USD/CZK | fx | $24.30 | yes | no | null | single_checked |
| sUSDHKD | Synthetic USD/HKD | fx | $7.78 | yes | no | null | single_checked |
| sUSDHUF | Synthetic USD/HUF | fx | $397.00 | yes | no | null | single_checked |
| sUSDILS | Synthetic USD/ILS | fx | $3.64 | yes | no | null | single_checked |
| sUSDINR | Synthetic USD/INR | fx | $85.60 | yes | no | null | single_checked |
| sUSDJPY | Synthetic USD/JPY | fx | $157.20 | yes | no | null | single_checked |
| sUSDKRW | Synthetic USD/KRW | fx | $1,445.00 | yes | no | null | single_checked |
| sUSDMXN | Synthetic USD/MXN | fx | $20.40 | yes | no | null | single_checked |
| sUSDNOK | Synthetic USD/NOK | fx | $11.38 | yes | no | null | single_checked |
| sUSDPLN | Synthetic USD/PLN | fx | $4.12 | yes | no | null | single_checked |
| sUSDSEK | Synthetic USD/SEK | fx | $11.05 | yes | no | null | single_checked |
| sUSDSGD | Synthetic USD/SGD | fx | $1.36 | yes | no | null | single_checked |
| sUSDTHB | Synthetic USD/THB | fx | $34.50 | yes | no | null | single_checked |
| sUSDTRY | Synthetic USD/TRY | fx | $39.10 | yes | no | null | single_checked |
| sUSDZAR | Synthetic USD/ZAR | fx | $18.70 | yes | no | null | single_checked |
| sBNDX | Synthetic BNDX (intl bond) | intl_bond | $48.60 | yes | no | null | single_checked |
| sBWX | Synthetic BWX (intl Treasury) | intl_bond | $22.10 | no | no | null | admin |
| sACWI | Synthetic ACWI | intl_equity_etf | $118.70 | no | no | null | admin |
| sEFA | Synthetic EFA | intl_equity_etf | $84.20 | yes | no | null | single_checked |
| sEWC | Synthetic EWC (Canada) | intl_equity_etf | $43.80 | no | no | null | admin |
| sEZA | Synthetic EZA (South Africa) | intl_equity_etf | $44.60 | no | no | null | admin |
| sIEFA | Synthetic IEFA | intl_equity_etf | $75.60 | yes | no | null | single_checked |
| sVEA | Synthetic VEA | intl_equity_etf | $52.10 | yes | no | null | single_checked |
| sVT | Synthetic VT | intl_equity_etf | $122.30 | yes | no | null | single_checked |
| sVXUS | Synthetic VXUS | intl_equity_etf | $66.40 | yes | no | null | single_checked |
| sARGT | Synthetic ARGT (Argentina) | latam_equity_etf | $78.20 | no | no | null | admin |
| sECH | Synthetic ECH (Chile) | latam_equity_etf | $26.10 | no | no | null | admin |
| sEWW | Synthetic EWW (Mexico) | latam_equity_etf | $51.20 | no | no | null | admin |
| sEWZ | Synthetic EWZ (Brazil) | latam_equity_etf | $28.40 | yes | no | null | single_checked |
| sGXG | Synthetic GXG (Colombia) | latam_equity_etf | $26.10 | no | no | null | admin |
| sILF | Synthetic ILF (LatAm) | latam_equity_etf | $22.30 | no | no | null | admin |
| sGLD | Synthetic GLD (gold ETF) | metal_etf | $300.50 | yes | no | null | single_checked |
| sIAU | Synthetic IAU (gold ETF) | metal_etf | $61.20 | yes | no | null | single_checked |
| sPALL | Synthetic PALL (palladium ETF) | metal_etf | $92.10 | yes | no | null | single_checked |
| sPPLT | Synthetic PPLT (platinum ETF) | metal_etf | $96.30 | yes | no | null | single_checked |
| sSLV | Synthetic SLV (silver ETF) | metal_etf | $30.20 | yes | no | null | single_checked |
| sXAG | Synthetic Silver (spot) | metal_spot | $32.40 | yes | no | null | single_checked |
| sXAU | Synthetic Gold (spot) | metal_spot | $3,250.00 | yes | no | null | single_checked |
| sXCU | Synthetic Copper (spot) | metal_spot | $4.30 | yes | no | null | single_checked |
| sXPD | Synthetic Palladium (spot) | metal_spot | $1,010.00 | yes | no | null | single_checked |
| sXPT | Synthetic Platinum (spot) | metal_spot | $980.00 | yes | no | null | single_checked |
| sCOPX | Synthetic COPX (copper miners) | metal_eq_etf | $41.30 | no | no | null | admin |
| sGDX | Synthetic GDX (gold miners) | metal_eq_etf | $40.80 | no | no | null | admin |
| sGDXJ | Synthetic GDXJ (jr gold miners) | metal_eq_etf | $51.60 | no | no | null | admin |
| sSIL | Synthetic SIL (silver miners) | metal_eq_etf | $38.10 | no | no | null | admin |
| sXME | Synthetic XME (metals & mining) | metal_eq_etf | $68.40 | no | no | null | admin |
| sIYR | Synthetic IYR | reit_etf | $98.40 | yes | no | null | single_checked |
| sSCHH | Synthetic SCHH | reit_etf | $21.30 | no | no | null | admin |
| sVNQ | Synthetic VNQ | reit_etf | $92.10 | no | no | null | admin |
| sVNQI | Synthetic VNQI (intl REIT) | reit_etf | $43.60 | no | no | null | admin |
| sDGRO | Synthetic DGRO | factor_etf | $63.10 | yes | no | null | single_checked |
| sIWD | Synthetic IWD (Value) | factor_etf | $189.40 | yes | no | null | single_checked |
| sIWF | Synthetic IWF (Growth) | factor_etf | $418.20 | yes | no | null | single_checked |
| sMTUM | Synthetic MTUM | factor_etf | $224.60 | yes | no | null | single_checked |
| sNOBL | Synthetic NOBL | factor_etf | $102.40 | yes | no | null | single_checked |
| sQUAL | Synthetic QUAL | factor_etf | $178.20 | yes | no | null | single_checked |
| sSCHD | Synthetic SCHD | factor_etf | $27.80 | yes | no | null | single_checked |
| sSPLV | Synthetic SPLV | factor_etf | $73.40 | no | no | null | admin |
| sUSMV | Synthetic USMV | factor_etf | $88.40 | yes | no | null | single_checked |
| sVIG | Synthetic VIG | factor_etf | $197.30 | yes | no | null | single_checked |
| sVLUE | Synthetic VLUE | factor_etf | $108.90 | yes | no | null | single_checked |
| sVTV | Synthetic VTV | factor_etf | $178.30 | yes | no | null | single_checked |
| sVUG | Synthetic VUG | factor_etf | $428.10 | yes | no | null | single_checked |
| sVYM | Synthetic VYM | factor_etf | $128.60 | yes | no | null | single_checked |
| sTUR | Synthetic TUR (Turkey) | tr_equity_etf | $28.70 | no | no | null | admin |
| sBIL | Synthetic BIL (T-Bills) | us_bond_tbill | $91.60 | yes | no | null | single_checked |
| sMINT | Synthetic MINT (ultrashort) | us_bond_tbill | $100.30 | yes | no | null | single_checked |
| sSHV | Synthetic SHV (short T) | us_bond_tbill | $110.30 | yes | no | null | single_checked |
| sTIP | Synthetic TIP (TIPS) | us_bond_tips | $108.70 | no | no | null | admin |
| sVTIP | Synthetic VTIP (short TIPS) | us_bond_tips | $48.10 | no | no | null | admin |
| sAGG | Synthetic AGG (Aggregate) | us_bond_agg | $98.20 | yes | no | null | single_checked |
| sBND | Synthetic BND (Aggregate) | us_bond_agg | $72.80 | yes | no | null | single_checked |
| sGOVT | Synthetic GOVT (Treasuries) | us_bond_agg | $22.60 | yes | no | null | single_checked |
| sIUSB | Synthetic IUSB (total bond) | us_bond_agg | $44.30 | yes | no | null | single_checked |
| sMBB | Synthetic MBB (MBS) | us_bond_agg | $93.10 | yes | no | null | single_checked |
| sIEF | Synthetic IEF (7-10yr) | us_bond_mid | $95.10 | yes | no | null | single_checked |
| sVGIT | Synthetic VGIT (interm Treasury) | us_bond_mid | $58.60 | yes | no | null | single_checked |
| sTLT | Synthetic TLT (20+yr) | us_bond_long | $92.30 | yes | no | null | single_checked |
| sVGLT | Synthetic VGLT (long Treasury) | us_bond_long | $56.40 | no | no | null | admin |
| sSHY | Synthetic SHY (1-3yr) | us_bond_short | $82.40 | yes | no | null | single_checked |
| sVGSH | Synthetic VGSH (short Treasury) | us_bond_short | $58.20 | no | no | null | admin |
| sDIA | Synthetic DIA | us_equity_etf | $437.10 | yes | no | null | single_checked |
| sIJH | Synthetic IJH | us_equity_etf | $64.80 | yes | no | null | single_checked |
| sIJR | Synthetic IJR | us_equity_etf | $118.90 | yes | no | null | single_checked |
| sITOT | Synthetic ITOT | us_equity_etf | $132.60 | yes | no | null | single_checked |
| sIVV | Synthetic IVV | us_equity_etf | $596.20 | yes | no | null | single_checked |
| sIWM | Synthetic IWM | us_equity_etf | $228.70 | yes | no | null | single_checked |
| sMDY | Synthetic MDY | us_equity_etf | $588.10 | no | no | null | admin |
| sQQQ | Synthetic QQQ | us_equity_etf | $512.30 | yes | no | null | single_checked |
| sRSP | Synthetic RSP | us_equity_etf | $182.30 | yes | no | null | single_checked |
| sSCHB | Synthetic SCHB | us_equity_etf | $24.10 | yes | no | null | single_checked |
| sSPY | Synthetic SPY | us_equity_etf | $592.40 | yes | no | null | single_checked |
| sVB | Synthetic VB | us_equity_etf | $245.20 | yes | no | null | single_checked |
| sVO | Synthetic VO | us_equity_etf | $268.40 | yes | no | null | single_checked |
| sVOO | Synthetic VOO | us_equity_etf | $545.10 | yes | no | null | single_checked |
| sVTI | Synthetic VTI | us_equity_etf | $293.40 | yes | no | null | single_checked |
| sMUB | Synthetic MUB (US muni) | us_muni | $106.50 | yes | no | null | single_checked |
| sVTEB | Synthetic VTEB (US muni) | us_muni | $50.20 | yes | no | null | single_checked |
| sXLB | Synthetic XLB | us_sector_etf | $89.30 | yes | no | null | single_checked |
| sXLC | Synthetic XLC | us_sector_etf | $104.70 | yes | no | null | single_checked |
| sXLE | Synthetic XLE | us_sector_etf | $91.80 | yes | no | null | single_checked |
| sXLF | Synthetic XLF | us_sector_etf | $49.60 | yes | no | null | single_checked |
| sXLI | Synthetic XLI | us_sector_etf | $138.90 | yes | no | null | single_checked |
| sXLK | Synthetic XLK | us_sector_etf | $235.40 | yes | no | null | single_checked |
| sXLP | Synthetic XLP | us_sector_etf | $81.20 | yes | no | null | single_checked |
| sXLRE | Synthetic XLRE | us_sector_etf | $42.10 | no | no | null | admin |
| sXLU | Synthetic XLU | us_sector_etf | $78.40 | yes | no | null | single_checked |
| sXLV | Synthetic XLV | us_sector_etf | $146.20 | yes | no | null | single_checked |
| sXLY | Synthetic XLY | us_sector_etf | $218.60 | yes | no | null | single_checked |
| sARKG | Synthetic ARKG | us_thematic_etf | $27.80 | yes | no | null | single_checked |
| sARKK | Synthetic ARKK | us_thematic_etf | $62.10 | yes | no | null | single_checked |
| sBOTZ | Synthetic BOTZ | us_thematic_etf | $33.10 | yes | no | null | single_checked |
| sCIBR | Synthetic CIBR (cybersecurity) | us_thematic_etf | $68.30 | no | no | null | admin |
| sFDN | Synthetic FDN (internet) | us_thematic_etf | $268.40 | no | no | null | admin |
| sHACK | Synthetic HACK (cybersecurity) | us_thematic_etf | $78.10 | no | no | null | admin |
| sIBB | Synthetic IBB (biotech) | us_thematic_etf | $132.10 | no | no | null | admin |
| sIFRA | Synthetic IFRA (infrastructure) | us_thematic_etf | $51.30 | yes | no | null | single_checked |
| sIGV | Synthetic IGV | us_thematic_etf | $98.70 | yes | no | null | single_checked |
| sITB | Synthetic ITB | us_thematic_etf | $108.40 | yes | no | null | single_checked |
| sJETS | Synthetic JETS (airlines) | us_thematic_etf | $24.30 | no | no | null | admin |
| sKRE | Synthetic KRE | us_thematic_etf | $62.30 | yes | no | null | single_checked |
| sOIH | Synthetic OIH (oil services) | us_thematic_etf | $288.60 | no | no | null | admin |
| sPAVE | Synthetic PAVE (infrastructure) | us_thematic_etf | $42.10 | yes | no | null | single_checked |
| sROBO | Synthetic ROBO (robotics) | us_thematic_etf | $58.40 | no | no | null | admin |
| sSKYY | Synthetic SKYY (cloud) | us_thematic_etf | $138.20 | no | no | null | admin |
| sSMH | Synthetic SMH | us_thematic_etf | $258.40 | yes | no | null | single_checked |
| sSOXX | Synthetic SOXX | us_thematic_etf | $228.90 | yes | no | null | single_checked |
| sVGT | Synthetic VGT | us_thematic_etf | $618.30 | yes | no | null | single_checked |
| sXBI | Synthetic XBI | us_thematic_etf | $92.60 | yes | no | null | single_checked |
| sXOP | Synthetic XOP | us_thematic_etf | $138.20 | yes | no | null | single_checked |
| sSVXY | Synthetic SVXY (short VIX) | volatility_etf | $44.60 | yes | no | null | single_checked |
| sUVXY | Synthetic UVXY (2x VIX) | volatility_etf | $22.10 | yes | no | null | single_checked |
| sVXX | Synthetic VXX (VIX futures) | volatility_etf | $48.30 | yes | no | null | single_checked |

## Held back — single-name equities (backtest-only)

**Compliance status (verbatim from the SSOT):** FLAGGED — DO NOT add to the live on-chain path without compliance sign-off

> Single-stock synthetics (sTSLA, sNVDA, sAAPL, sMSFT, ... and the EU/Asian/Turkish single names in GLOBAL_ASSETS) reference individual securities. Minting an on-chain synthetic that tracks a single registered security raises securities-law / derivatives-registration questions that broad-index, FX, crypto, and commodity synths do not. These remain backtest-only (present in GLOBAL_ASSETS) until legal review. The two single-stock names that were on the legacy live list (sTSLA, sNVDA) are intentionally REMOVED from the live on-chain universe by this SSOT.

The 59 single-name equity synths below are present in the backtest universe but **NOT** on the live on-chain path. Do not add any to the SSOT `synthetics` without sign-off.

`sAAPL` `sAKBNK` `sAMD` `sAMZN` `sASELS` `sASML` `sAVGO` `sAZN`
`sBABA` `sBAC` `sBIMAS` `sBP` `sBRK-B` `sCOIN` `sCOP` `sCOST`
`sCRM` `sCVX` `sEREGL` `sGARAN` `sGOOGL` `sGS` `sHD` `sHSBC`
`sJNJ` `sJPM` `sKCHOL` `sLLY` `sLVMH` `sMA` `sMETA` `sMRK`
`sMSFT` `sMSTR` `sNESN` `sNFLX` `sNOVO` `sNVDA` `sORCL` `sPFE`
`sPG` `sPLTR` `sRHM` `sSAHOL` `sSAP` `sSE` `sSHEL` `sSIE`
`sSONY` `sTCEHY` `sTHYAO` `sTM` `sTSLA` `sTSM` `sTTE` `sUNH`
`sV` `sWMT` `sXOM`

