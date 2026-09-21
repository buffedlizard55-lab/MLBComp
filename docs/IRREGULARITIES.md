# MLBComp — Documented Irregularities Register

This document provides a permanent audit record of all identified data irregularities, upstream format errors, and environment constraints encountered in MLBComp.

---

### IRR-01: Primary Mirror Postseason Fabrication (sportsdataverse/baseballr-data)
- **Category:** `DATA_FABRICATION`
- **Severity:** `CRITICAL`
- **Impact:** Upstream schedule parquet contained fabricated postseason games for 2016–2024 (e.g. a "2016 World Series" game on 2016-10-25 between the Cubs and Indians that never occurred, and flipped World Series champions in 2020, 2021, and 2024).
- **Resolution:** The mirror postseason was completely quarantined and excluded from model inputs. Replaced with the verified postseason corpus (`po_corpus.parquet`): 2025 P1 from mirror (reality spot-checked) + 2019–2024 P2 reconstructed game winners from documented public results (`mlbcomp/data_recon/po_results.py`).

---

### IRR-02: Absence of Verified Postseason Market Odds
- **Category:** `MARKET_AVAILABILITY`
- **Severity:** `HIGH`
- **Impact:** The `cesar-dx/mlb-betting-ml` dataset ends at the regular season. No verified closing odds exist for 2019–2025 postseason games in any reachable open-source repository.
- **Resolution:** Postseason strategies are evaluated strictly via a **Fair-Coin Proxy** ($2\% \text{ flat at } +100 = 2 \times (\text{Win\%} - 50\%)$) as a metric of model predictive skill. No synthetic or invented market prices are claimed.

---

### IRR-03: 2020 COVID-Shortened 60-Game Season & Neutral-Site Bubble
- **Category:** `FORMAT_DISRUPTION`
- **Severity:** `MEDIUM`
- **Impact:** 2020 season featured regional 60-game schedules, 7-inning doubleheaders, expanded 16-team postseason, and neutral-site bubble rounds (Arlington / San Diego).
- **Resolution:** Isolated 2020 in rolling window calculations; doubleheaders handled with one-game-per-day logic; bubble games flagged for zero true home-field advantage.

---

### IRR-04: 2021 Wild Card & Division Series Reconstruction Exclusion
- **Category:** `PROVENANCE_LIMITATION`
- **Severity:** `MEDIUM`
- **Impact:** Insufficient independent per-game pattern evidence in sandbox to reconstruct game slot winners for 2021 Wild Card and Division Series with high confidence.
- **Resolution:** Excluded 2021 WC and DS from the reconstructed corpus rather than asserting uncertain game-by-game results.

---

### IRR-05: Offline Sandboxed Environment Network Isolation
- **Category:** `NETWORK_ISOLATION`
- **Severity:** `LOW`
- **Impact:** Direct endpoints to `statsapi.mlb.com` and `baseballsavant.mlb.com` are network-blocked.
- **Resolution:** All historical models utilize checked-in primary mirrors and NOAA gridded climatology; hypotheses requiring real-time Hawk-Eye feeds are flagged `DATA_UNAVAILABLE` rather than simulated with ungrounded heuristics.

---

### IRR-06: Ghost Runner Extra-Innings Rule Scoring Distortion
- **Category:** `RULE_CHANGE`
- **Severity:** `LOW`
- **Impact:** 2020-present rule placing an automatic runner on 2nd base in extra innings sharply elevates 10th-inning scoring rates.
- **Resolution:** Totals models calibrate strictly to 9-inning regulation scoring; ghost runner run expectancy is isolated as a distinct right-tail adjustment.
