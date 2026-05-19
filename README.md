# Immigration Social Media Archive

A public, append-only archive of social-media posts (especially videos) published by U.S. federal agencies of the second Trump administration on the subject of immigration. The covered accounts are operated by DHS, ICE, CBP, USCIS, the White House, the Press Secretary, the President, and the Department of Labor.

This is journalism / public-records work. Capture happens via the publicly visible web only — no X Developer Agreement, no X API credentials. The repository itself is the database: structured tweet JSON is committed by a Firefox extension, ingested by GitHub Actions into per-account Parquet files, mirrored to GitHub Releases for media, and served as a static GitHub Pages viewer.

**[Open the searchable archive →](https://vidproject.github.io/x/)** _(live once Phase 3 completes)_

## How to use the viewer

The viewer is a static page that loads each account's Parquet file in your browser and lets you search, filter, and export. No login, no tracking. Pick one or more accounts from the **Database Downloads** menu in the header, then use the filter bar to narrow by date, account, media type, or free-text search. The URL updates as you filter, so any view can be bookmarked or shared.

## For contributors: the Firefox extension

The extension captures public X posts from the configured accounts as you browse and commits the structured JSON directly to this repository.

1. **Download [`extension.zip`](./extension.zip) and unzip it.**
2. In Firefox, open `about:debugging` → **This Firefox** → **Load Temporary Add-on…** → pick `manifest.json` from the unzipped folder.
3. Open the extension's sidebar (click its toolbar icon — the sidebar **stays open as you browse**), open **Settings**, and paste a GitHub PAT.
4. Visit a tracked account on `x.com`, e.g. <https://x.com/DHSgov>. Within a few seconds the sidebar's activity tail will show captures committing.

Firefox removes temporary add-ons when it closes — reinstalling takes about ten seconds.

If you reload the extension while X tabs are already open, those tabs keep their old content scripts; the new build only takes full effect on tabs opened after the reload. The extension does re-inject its page-hook into existing tabs on every wake, but the cleanest behaviour is to close X tabs before reloading and let `Capture now` open a fresh one.

### Generating the right PAT

Use a **fine-grained** Personal Access Token, not a classic one, with **only this repository** selected and only the minimum permissions:

| Permission            | Access         |
| --------------------- | -------------- |
| Repository → Contents | Read and write |
| Repository → Metadata | Read           |

Generate one at <https://github.com/settings/personal-access-tokens/new>. The PAT is stored in `browser.storage.local`; anyone with filesystem access to your Firefox profile can read it, so use a single-repo fine-grained token, never a classic `repo`-scoped one.

## Coverage

_Coverage tables are regenerated automatically by `scripts/update_readme.py` after each ingest run. The section below will populate once captures begin._

<!-- COVERAGE:START -->

| Handle | Label | Tweets | First post | Latest post | Latest capture | Media | Videos |
| ------ | ----- | -----: | ---------- | ----------- | -------------- | ----: | -----: |
| `@DHSgov` | Department of Homeland Security | 749 | 2025-04-09 | 2026-05-19 | 2026-05-19 | 462 | 189 |
| `@ICEgov` | U.S. Immigration and Customs Enforcement | 37 | 2025-04-16 | 2026-05-19 | 2026-05-19 | 29 | 8 |
| `@CBP` | U.S. Customs and Border Protection | 202 | 2025-06-04 | 2026-05-19 | 2026-05-19 | 93 | 20 |
| `@USCIS` | U.S. Citizenship and Immigration Services | 743 | 2025-04-04 | 2026-05-18 | 2026-05-19 | 469 | 83 |
| `@WhiteHouse` | The White House | 169 | 2025-06-09 | 2026-05-19 | 2026-05-19 | 157 | 37 |
| `@PressSec` | White House Press Secretary | 90 | 2025-09-17 | 2026-05-15 | 2026-05-19 | 10 | 3 |
| `@POTUS` | President of the United States | 81 | 2025-01-20 | 2026-05-15 | 2026-05-19 | 33 | 11 |
| `@USDOL` | U.S. Department of Labor | 297 | 2026-01-21 | 2026-05-19 | 2026-05-19 | 137 | 19 |
| `@10minutedrill` | 10minutedrill | 1 | 2026-04-28 | 2026-04-28 | 2026-05-19 | 1 | 1 |
| `@7NewsDC` | 7NewsDC | 2 | 2026-04-07 | 2026-05-15 | 2026-05-19 | 0 | 0 |
| `@ABC` | ABC | 1 | 2026-04-09 | 2026-04-09 | 2026-05-19 | 0 | 0 |
| `@ABC7` | ABC7 | 1 | 2026-04-10 | 2026-04-10 | 2026-05-19 | 1 | 1 |
| `@ACTIdgit` | ACTIdgit | 1 | 2026-04-05 | 2026-04-05 | 2026-05-19 | 0 | 0 |
| `@AEMAdvocacy` | AEMAdvocacy | 1 | 2026-02-09 | 2026-02-09 | 2026-05-19 | 3 | 0 |
| `@AGPamBondi` | AGPamBondi | 1 | 2026-03-26 | 2026-03-26 | 2026-05-19 | 0 | 0 |
| `@AJGuglielmi` | AJGuglielmi | 1 | 2026-04-26 | 2026-04-26 | 2026-05-19 | 1 | 0 |
| `@AJInvestigates` | AJInvestigates | 1 | 2026-04-28 | 2026-04-28 | 2026-05-19 | 2 | 0 |
| `@AZSun4Trump` | AZSun4Trump | 1 | 2026-04-04 | 2026-04-04 | 2026-05-19 | 0 | 0 |
| `@AdamsMorganNews` | AdamsMorganNews | 1 | 2026-05-15 | 2026-05-15 | 2026-05-19 | 1 | 1 |
| `@AnnaKelly47` | AnnaKelly47 | 1 | 2026-04-28 | 2026-04-28 | 2026-05-19 | 0 | 0 |
| `@AntiSatanist333` | AntiSatanist333 | 1 | 2026-04-04 | 2026-04-04 | 2026-05-19 | 0 | 0 |
| `@AquitaineH43797` | AquitaineH43797 | 1 | 2026-04-28 | 2026-04-28 | 2026-05-19 | 0 | 0 |
| `@BLaw` | BLaw | 1 | 2026-05-19 | 2026-05-19 | 2026-05-19 | 0 | 0 |
| `@BananaTweetsX` | BananaTweetsX | 1 | 2026-04-04 | 2026-04-04 | 2026-05-19 | 0 | 0 |
| `@Bear_Faced` | Bear_Faced | 1 | 2026-04-04 | 2026-04-04 | 2026-05-19 | 1 | 0 |
| `@BillMelugin_` | BillMelugin_ | 9 | 2026-03-22 | 2026-04-30 | 2026-05-19 | 8 | 1 |
| `@BorderHawkNews` | BorderHawkNews | 1 | 2026-05-08 | 2026-05-08 | 2026-05-19 | 0 | 0 |
| `@BrasfieldAshley` | BrasfieldAshley | 1 | 2026-02-13 | 2026-02-13 | 2026-05-19 | 1 | 0 |
| `@Breaking911` | Breaking911 | 1 | 2026-04-07 | 2026-04-07 | 2026-05-19 | 1 | 1 |
| `@Brooketaylortv` | Brooketaylortv | 1 | 2026-05-13 | 2026-05-13 | 2026-05-19 | 1 | 0 |
| `@CBPAMO` | CBPAMO | 11 | 2026-03-19 | 2026-05-12 | 2026-05-19 | 17 | 4 |
| `@CBPAMORegDirSE` | CBPAMORegDirSE | 1 | 2026-04-29 | 2026-04-29 | 2026-05-19 | 1 | 0 |
| `@CBPAMORegDirSW` | CBPAMORegDirSW | 4 | 2026-03-19 | 2026-04-29 | 2026-05-19 | 9 | 2 |
| `@CBPChicago` | CBPChicago | 2 | 2026-03-30 | 2026-04-03 | 2026-05-19 | 4 | 0 |
| `@CBPCommissioner` | CBPCommissioner | 10 | 2026-03-24 | 2026-05-15 | 2026-05-19 | 7 | 2 |
| `@CBPJobs` | CBPJobs | 1 | 2026-04-08 | 2026-04-08 | 2026-05-19 | 1 | 1 |
| `@CBPLSSAC` | CBPLSSAC | 1 | 2026-04-10 | 2026-04-10 | 2026-05-19 | 3 | 0 |
| `@CBPPortDirNOG` | CBPPortDirNOG | 1 | 2026-04-13 | 2026-04-13 | 2026-05-19 | 1 | 1 |
| `@CBPTradeGov` | CBPTradeGov | 2 | 2026-04-10 | 2026-05-13 | 2026-05-19 | 4 | 1 |
| `@CBSMornings` | CBSMornings | 1 | 2026-04-29 | 2026-04-29 | 2026-05-19 | 1 | 1 |
| `@CBSNews` | CBSNews | 3 | 2026-04-22 | 2026-04-30 | 2026-05-19 | 1 | 1 |
| `@CENTCOM` | CENTCOM | 1 | 2026-04-28 | 2026-04-28 | 2026-05-19 | 1 | 1 |
| `@Chaznolan` | Chaznolan | 1 | 2026-05-11 | 2026-05-11 | 2026-05-19 | 0 | 0 |
| `@ChrisVanHollen` | ChrisVanHollen | 1 | 2026-04-04 | 2026-04-04 | 2026-05-19 | 2 | 0 |
| `@Curt50542` | Curt50542 | 1 | 2026-04-04 | 2026-04-04 | 2026-05-19 | 0 | 0 |
| `@DFOBaltimore` | DFOBaltimore | 2 | 2026-04-06 | 2026-05-05 | 2026-05-19 | 2 | 1 |
| `@DFOChicago` | DFOChicago | 4 | 2026-03-23 | 2026-04-29 | 2026-05-19 | 14 | 0 |
| `@DFODetroit` | DFODetroit | 1 | 2026-04-27 | 2026-04-27 | 2026-05-19 | 1 | 1 |
| `@DFOLosAngeles` | DFOLosAngeles | 1 | 2026-05-15 | 2026-05-15 | 2026-05-19 | 1 | 1 |
| `@DFONewYork` | DFONewYork | 4 | 2026-04-02 | 2026-05-04 | 2026-05-19 | 6 | 1 |
| `@DFOSanDiegoCA` | DFOSanDiegoCA | 1 | 2026-03-20 | 2026-03-20 | 2026-05-19 | 1 | 1 |
| `@DOLOIG` | DOLOIG | 3 | 2026-05-02 | 2026-05-14 | 2026-05-19 | 3 | 3 |
| `@DOWResponse` | DOWResponse | 1 | 2026-05-19 | 2026-05-19 | 2026-05-19 | 1 | 0 |
| `@DailyCaller` | DailyCaller | 2 | 2026-04-08 | 2026-04-24 | 2026-05-19 | 0 | 0 |
| `@DeniseSkjerven` | DeniseSkjerven | 1 | 2026-04-05 | 2026-04-05 | 2026-05-19 | 0 | 0 |
| `@DepSec_Edgar` | DepSec_Edgar | 2 | 2025-05-30 | 2025-07-23 | 2026-05-19 | 4 | 0 |
| `@Dexerto` | Dexerto | 1 | 2026-04-27 | 2026-04-27 | 2026-05-19 | 2 | 0 |
| `@DliElectro19205` | DliElectro19205 | 1 | 2026-04-05 | 2026-04-05 | 2026-05-19 | 0 | 0 |
| `@DrOzCMS` | DrOzCMS | 1 | 2026-05-19 | 2026-05-19 | 2026-05-19 | 1 | 1 |
| `@DualityXrp` | DualityXrp | 1 | 2026-04-04 | 2026-04-04 | 2026-05-19 | 1 | 0 |
| `@EDSecMcMahon` | EDSecMcMahon | 2 | 2026-02-11 | 2026-04-21 | 2026-05-19 | 1 | 0 |
| `@EROBoston` | EROBoston | 6 | 2026-04-12 | 2026-05-13 | 2026-05-19 | 6 | 0 |
| `@ERODenver` | ERODenver | 5 | 2026-04-09 | 2026-05-07 | 2026-05-19 | 4 | 0 |
| `@EROLosAngeles` | EROLosAngeles | 3 | 2026-04-10 | 2026-04-29 | 2026-05-19 | 2 | 0 |
| `@ERONewOrleans` | ERONewOrleans | 4 | 2026-04-14 | 2026-05-13 | 2026-05-19 | 3 | 0 |
| `@ERONewark` | ERONewark | 3 | 2026-04-03 | 2026-05-11 | 2026-05-19 | 3 | 0 |
| `@EROPhiladelphia` | EROPhiladelphia | 2 | 2026-04-17 | 2026-05-11 | 2026-05-19 | 2 | 0 |
| `@EROSaltLakeCity` | EROSaltLakeCity | 1 | 2026-04-07 | 2026-04-07 | 2026-05-19 | 1 | 0 |
| `@EROSanDiego` | EROSanDiego | 1 | 2026-05-07 | 2026-05-07 | 2026-05-19 | 1 | 0 |
| `@EROSanFrancisco` | EROSanFrancisco | 7 | 2026-04-02 | 2026-05-12 | 2026-05-19 | 7 | 0 |
| `@EROSeattle` | EROSeattle | 2 | 2026-04-03 | 2026-04-16 | 2026-05-19 | 2 | 0 |
| `@EROWashington` | EROWashington | 1 | 2026-04-08 | 2026-04-08 | 2026-05-19 | 1 | 0 |
| `@EdWorkforceCmte` | EdWorkforceCmte | 2 | 2026-04-21 | 2026-04-29 | 2026-05-19 | 2 | 1 |
| `@EnvoyNoem` | EnvoyNoem | 3 | 2025-04-28 | 2025-12-05 | 2026-05-19 | 6 | 2 |
| `@EricTrump` | EricTrump | 1 | 2026-05-15 | 2026-05-15 | 2026-05-19 | 0 | 0 |
| `@FBITampa` | FBITampa | 1 | 2025-03-19 | 2025-03-19 | 2026-05-19 | 1 | 0 |
| `@FDRLST` | FDRLST | 2 | 2026-05-11 | 2026-05-12 | 2026-05-19 | 0 | 0 |
| `@FLACommerce` | FLACommerce | 1 | 2026-05-03 | 2026-05-03 | 2026-05-19 | 1 | 0 |
| `@FLOTUS` | FLOTUS | 1 | 2026-04-27 | 2026-04-27 | 2026-05-19 | 0 | 0 |
| `@FPSDHS` | FPSDHS | 1 | 2026-05-11 | 2026-05-11 | 2026-05-19 | 1 | 0 |
| `@Fashionably84` | Fashionably84 | 1 | 2026-04-04 | 2026-04-04 | 2026-05-19 | 0 | 0 |
| `@FoxNews` | FoxNews | 4 | 2026-04-10 | 2026-05-15 | 2026-05-19 | 7 | 2 |
| `@FrontlinesTPUSA` | FrontlinesTPUSA | 1 | 2025-09-30 | 2025-09-30 | 2026-05-19 | 1 | 1 |
| `@GovEvers` | GovEvers | 1 | 2026-04-02 | 2026-04-02 | 2026-05-19 | 0 | 0 |
| `@GovTimWalz` | GovTimWalz | 3 | 2026-01-06 | 2026-05-04 | 2026-05-19 | 2 | 1 |
| `@GuntherEagleman` | GuntherEagleman | 1 | 2026-04-30 | 2026-04-30 | 2026-05-19 | 1 | 1 |
| `@HSITampa` | HSITampa | 1 | 2025-05-02 | 2025-05-02 | 2026-05-19 | 1 | 0 |
| `@HSI_HQ` | HSI_HQ | 1 | 2026-04-28 | 2026-04-28 | 2026-05-19 | 1 | 0 |
| `@HarshThoughtful` | HarshThoughtful | 1 | 2026-04-04 | 2026-04-04 | 2026-05-19 | 0 | 0 |
| `@Independent` | Independent | 1 | 2026-05-13 | 2026-05-13 | 2026-05-19 | 1 | 0 |
| `@JDVance` | JDVance | 2 | 2026-04-28 | 2026-05-16 | 2026-05-19 | 1 | 1 |
| `@JSYKRobert` | JSYKRobert | 1 | 2026-04-04 | 2026-04-04 | 2026-05-19 | 0 | 0 |
| `@JamesBlairUSA` | JamesBlairUSA | 1 | 2026-04-28 | 2026-04-28 | 2026-05-19 | 1 | 1 |
| `@JennieSTaer` | JennieSTaer | 3 | 2026-04-07 | 2026-05-05 | 2026-05-19 | 1 | 0 |
| `@JoeyFAFO_JK` | JoeyFAFO_JK | 1 | 2026-04-04 | 2026-04-04 | 2026-05-19 | 1 | 0 |
| `@JonesOyl1776` | JonesOyl1776 | 1 | 2026-04-04 | 2026-04-04 | 2026-05-19 | 0 | 0 |
| `@JuliaDavisNews` | JuliaDavisNews | 1 | 2026-03-31 | 2026-03-31 | 2026-05-19 | 0 | 0 |
| `@Justadolfan` | Justadolfan | 1 | 2026-04-04 | 2026-04-04 | 2026-05-19 | 0 | 0 |
| `@Katelyn_Caralle` | Katelyn_Caralle | 1 | 2026-04-28 | 2026-04-28 | 2026-05-19 | 1 | 0 |
| `@Know2Protect` | Know2Protect | 1 | 2026-05-16 | 2026-05-16 | 2026-05-19 | 1 | 0 |
| `@KristinFisher` | KristinFisher | 1 | 2026-04-29 | 2026-04-29 | 2026-05-19 | 0 | 0 |
| `@KushDesai47` | KushDesai47 | 3 | 2026-04-29 | 2026-04-30 | 2026-05-19 | 0 | 0 |
| `@LeadingReport` | LeadingReport | 1 | 2026-04-06 | 2026-04-06 | 2026-05-19 | 0 | 0 |
| `@LizHuston33` | LizHuston33 | 2 | 2026-04-28 | 2026-04-29 | 2026-05-19 | 1 | 1 |
| `@LizHuston47` | LizHuston47 | 1 | 2026-04-28 | 2026-04-28 | 2026-05-19 | 0 | 0 |
| `@LongTimeHistory` | LongTimeHistory | 1 | 2026-05-06 | 2026-05-06 | 2026-05-19 | 1 | 1 |
| `@MSFarmBureau` | MSFarmBureau | 1 | 2026-02-07 | 2026-02-07 | 2026-05-19 | 4 | 0 |
| `@MargoMartin47` | MargoMartin47 | 1 | 2026-05-11 | 2026-05-11 | 2026-05-19 | 1 | 1 |
| `@MaryMargOlohan` | MaryMargOlohan | 1 | 2026-04-30 | 2026-04-30 | 2026-05-19 | 1 | 0 |
| `@MckagueKelli` | MckagueKelli | 1 | 2026-04-28 | 2026-04-28 | 2026-05-19 | 0 | 0 |
| `@MizellPreston` | MizellPreston | 1 | 2026-05-15 | 2026-05-15 | 2026-05-19 | 1 | 0 |
| `@MollieG65457693` | MollieG65457693 | 1 | 2026-04-04 | 2026-04-04 | 2026-05-19 | 0 | 0 |
| `@MoosesFelix` | MoosesFelix | 1 | 2026-04-04 | 2026-04-04 | 2026-05-19 | 1 | 0 |
| `@NASAAdmin` | NASAAdmin | 1 | 2026-04-29 | 2026-04-29 | 2026-05-19 | 0 | 0 |
| `@NBCNews` | NBCNews | 3 | 2026-03-31 | 2026-04-28 | 2026-05-19 | 0 | 0 |
| `@NCDOL` | NCDOL | 1 | 2026-05-06 | 2026-05-06 | 2026-05-19 | 3 | 0 |
| `@NEWSMAX` | NEWSMAX | 3 | 2026-04-15 | 2026-05-09 | 2026-05-19 | 2 | 1 |
| `@NWS` | NWS | 1 | 2026-01-23 | 2026-01-23 | 2026-05-19 | 1 | 0 |
| `@NickMinock` | NickMinock | 2 | 2026-03-31 | 2026-04-23 | 2026-05-19 | 1 | 1 |
| `@OANN` | OANN | 1 | 2026-05-11 | 2026-05-11 | 2026-05-19 | 1 | 1 |
| `@OFOEAC` | OFOEAC | 12 | 2026-03-18 | 2026-05-18 | 2026-05-19 | 21 | 6 |
| `@OSHA_DOL` | OSHA_DOL | 2 | 2026-01-23 | 2026-04-28 | 2026-05-19 | 1 | 0 |
| `@OffThePress1` | OffThePress1 | 1 | 2026-05-04 | 2026-05-04 | 2026-05-19 | 1 | 1 |
| `@PatAdams96` | PatAdams96 | 4 | 2026-04-27 | 2026-04-29 | 2026-05-19 | 4 | 3 |
| `@PattyMorin` | PattyMorin | 1 | 2026-05-09 | 2026-05-09 | 2026-05-19 | 1 | 0 |
| `@PeteHegseth` | PeteHegseth | 1 | 2026-04-29 | 2026-04-29 | 2026-05-19 | 1 | 0 |
| `@Plasticwasteguy` | Plasticwasteguy | 1 | 2026-04-04 | 2026-04-04 | 2026-05-19 | 0 | 0 |
| `@PolkCoSheriff` | PolkCoSheriff | 1 | 2025-04-18 | 2025-04-18 | 2026-05-19 | 1 | 0 |
| `@RapidResponse47` | RapidResponse47 | 82 | 2025-06-08 | 2026-05-19 | 2026-05-19 | 69 | 53 |
| `@RelentlesSheep` | RelentlesSheep | 1 | 2026-04-04 | 2026-04-04 | 2026-05-19 | 0 | 0 |
| `@RoKhanna` | RoKhanna | 1 | 2026-04-28 | 2026-04-28 | 2026-05-19 | 0 | 0 |
| `@SBA_Kelly` | SBA_Kelly | 1 | 2026-05-04 | 2026-05-04 | 2026-05-19 | 1 | 1 |
| `@SecDuffy` | SecDuffy | 1 | 2026-05-02 | 2026-05-02 | 2026-05-19 | 0 | 0 |
| `@SecMullinDHS` | SecMullinDHS | 45 | 2026-03-24 | 2026-05-19 | 2026-05-19 | 52 | 20 |
| `@SecRollins` | SecRollins | 1 | 2026-05-07 | 2026-05-07 | 2026-05-19 | 1 | 1 |
| `@SecRubio` | SecRubio | 2 | 2026-04-04 | 2026-04-11 | 2026-05-19 | 0 | 0 |
| `@SecScottBessent` | SecScottBessent | 2 | 2026-04-02 | 2026-04-29 | 2026-05-19 | 0 | 0 |
| `@SecretSvcSpox` | SecretSvcSpox | 1 | 2026-05-04 | 2026-05-04 | 2026-05-19 | 1 | 0 |
| `@SecretaryLCD` | SecretaryLCD | 50 | 2026-01-21 | 2026-04-20 | 2026-05-19 | 66 | 18 |
| `@SenEricSchmitt` | SenEricSchmitt | 1 | 2026-04-13 | 2026-04-13 | 2026-05-19 | 1 | 0 |
| `@SenWarren` | SenWarren | 1 | 2026-05-15 | 2026-05-15 | 2026-05-19 | 0 | 0 |
| `@SenateGOP` | SenateGOP | 1 | 2026-04-23 | 2026-04-23 | 2026-05-19 | 1 | 1 |
| `@Sonderling47` | Sonderling47 | 58 | 2026-01-22 | 2026-05-19 | 2026-05-19 | 49 | 22 |
| `@SonofKiese` | SonofKiese | 1 | 2026-04-04 | 2026-04-04 | 2026-05-19 | 0 | 0 |
| `@StacyAnnFlorida` | StacyAnnFlorida | 1 | 2026-04-04 | 2026-04-04 | 2026-05-19 | 0 | 0 |
| `@StateDept` | StateDept | 2 | 2026-04-07 | 2026-05-11 | 2026-05-19 | 1 | 0 |
| `@StateDeptDSS` | StateDeptDSS | 1 | 2026-04-22 | 2026-04-22 | 2026-05-19 | 1 | 0 |
| `@StevenCheung47` | StevenCheung47 | 1 | 2026-04-29 | 2026-04-29 | 2026-05-19 | 0 | 0 |
| `@SusieWiles47` | SusieWiles47 | 2 | 2026-04-28 | 2026-05-19 | 2026-05-19 | 1 | 0 |
| `@T4TEXAS2` | T4TEXAS2 | 1 | 2026-04-05 | 2026-04-05 | 2026-05-19 | 0 | 0 |
| `@TMZ` | TMZ | 1 | 2026-05-03 | 2026-05-03 | 2026-05-19 | 1 | 1 |
| `@TPsocialEXP` | TPsocialEXP | 1 | 2026-04-05 | 2026-04-05 | 2026-05-19 | 1 | 0 |
| `@TSA` | TSA | 3 | 2026-05-04 | 2026-05-11 | 2026-05-19 | 2 | 1 |
| `@TTim420` | TTim420 | 1 | 2026-04-04 | 2026-04-04 | 2026-05-19 | 0 | 0 |
| `@TXAG` | TXAG | 1 | 2025-06-17 | 2025-06-17 | 2026-05-19 | 0 | 0 |
| `@TaylorRogers47` | TaylorRogers47 | 2 | 2026-04-30 | 2026-04-30 | 2026-05-19 | 0 | 0 |
| `@TheBuffaloNews` | TheBuffaloNews | 1 | 2026-05-12 | 2026-05-12 | 2026-05-19 | 0 | 0 |
| `@TheJusticeDept` | TheJusticeDept | 2 | 2025-09-16 | 2026-04-30 | 2026-05-19 | 3 | 0 |
| `@TravelGov` | TravelGov | 1 | 2025-11-05 | 2025-11-05 | 2026-05-19 | 1 | 0 |
| `@USAO_SDFL` | USAO_SDFL | 1 | 2026-03-17 | 2026-03-17 | 2026-05-19 | 1 | 0 |
| `@USBPChief` | USBPChief | 7 | 2026-03-28 | 2026-05-01 | 2026-05-19 | 7 | 6 |
| `@USBPChiefDTM` | USBPChiefDTM | 5 | 2026-04-02 | 2026-05-19 | 2026-05-19 | 5 | 1 |
| `@USBPChiefELC` | USBPChiefELC | 2 | 2026-04-19 | 2026-05-18 | 2026-05-19 | 3 | 1 |
| `@USBPChiefEPT` | USBPChiefEPT | 3 | 2026-03-26 | 2026-04-27 | 2026-05-19 | 3 | 3 |
| `@USBPChiefLRT` | USBPChiefLRT | 1 | 2026-03-25 | 2026-03-25 | 2026-05-19 | 1 | 0 |
| `@USBPChiefRGV` | USBPChiefRGV | 1 | 2026-04-22 | 2026-04-22 | 2026-05-19 | 1 | 1 |
| `@USBPChiefSDC` | USBPChiefSDC | 3 | 2026-04-02 | 2026-04-27 | 2026-05-19 | 3 | 2 |
| `@USBPChiefSPW` | USBPChiefSPW | 3 | 2026-04-13 | 2026-05-11 | 2026-05-19 | 3 | 0 |
| `@USBPChiefYUM` | USBPChiefYUM | 1 | 2026-04-13 | 2026-04-13 | 2026-05-19 | 1 | 0 |
| `@USCG` | USCG | 4 | 2026-03-30 | 2026-05-14 | 2026-05-19 | 8 | 1 |
| `@USCGSoutheast` | USCGSoutheast | 4 | 2026-03-21 | 2026-05-12 | 2026-05-19 | 10 | 2 |
| `@USCISJoe` | USCISJoe | 23 | 2025-07-23 | 2026-05-09 | 2026-05-19 | 21 | 13 |
| `@USDOJ_Intl` | USDOJ_Intl | 1 | 2026-04-15 | 2026-04-15 | 2026-05-19 | 0 | 0 |
| `@USLaborIG` | USLaborIG | 4 | 2026-02-04 | 2026-05-19 | 2026-05-19 | 3 | 2 |
| `@USOPM` | USOPM | 1 | 2026-03-30 | 2026-03-30 | 2026-05-19 | 1 | 1 |
| `@USTradeRep` | USTradeRep | 1 | 2026-02-22 | 2026-02-22 | 2026-05-19 | 1 | 1 |
| `@UpdatingOnRome` | UpdatingOnRome | 1 | 2026-04-30 | 2026-04-30 | 2026-05-19 | 2 | 0 |
| `@VETS_DOL` | VETS_DOL | 2 | 2026-01-29 | 2026-04-28 | 2026-05-19 | 2 | 0 |
| `@VP` | VP | 2 | 2026-05-06 | 2026-05-18 | 2026-05-19 | 2 | 0 |
| `@Varneyco` | Varneyco | 1 | 2026-04-03 | 2026-04-03 | 2026-05-19 | 1 | 1 |
| `@WBAY` | WBAY | 1 | 2026-04-22 | 2026-04-22 | 2026-05-19 | 0 | 0 |
| `@WHTaskForceFIFA` | WHTaskForceFIFA | 1 | 2026-05-09 | 2026-05-09 | 2026-05-19 | 1 | 0 |
| `@WSJopinion` | WSJopinion | 1 | 2026-03-30 | 2026-03-30 | 2026-05-19 | 0 | 0 |
| `@WallStreetApes` | WallStreetApes | 1 | 2026-04-30 | 2026-04-30 | 2026-05-19 | 1 | 1 |
| `@WashTimes` | WashTimes | 1 | 2025-05-12 | 2025-05-12 | 2026-05-19 | 0 | 0 |
| `@WesAllenAlabama` | WesAllenAlabama | 2 | 2025-06-18 | 2025-06-18 | 2026-05-19 | 1 | 0 |
| `@alexbruesewitz` | alexbruesewitz | 1 | 2026-05-15 | 2026-05-15 | 2026-05-19 | 0 | 0 |
| `@angeldadjoe` | angeldadjoe | 1 | 2026-04-28 | 2026-04-28 | 2026-05-19 | 2 | 0 |
| `@axios` | axios | 2 | 2026-02-13 | 2026-03-24 | 2026-05-19 | 0 | 0 |
| `@bilignb` | bilignb | 1 | 2026-04-24 | 2026-04-24 | 2026-05-19 | 0 | 0 |
| `@californiapost` | californiapost | 1 | 2026-04-01 | 2026-04-01 | 2026-05-19 | 1 | 0 |
| `@cash_override` | cash_override | 1 | 2026-04-04 | 2026-04-04 | 2026-05-19 | 0 | 0 |
| `@chrisbradleyonX` | chrisbradleyonX | 1 | 2026-04-04 | 2026-04-04 | 2026-05-19 | 0 | 0 |
| `@denverpost` | denverpost | 1 | 2026-05-12 | 2026-05-12 | 2026-05-19 | 0 | 0 |
| `@dlippman` | dlippman | 1 | 2026-04-12 | 2026-04-12 | 2026-05-19 | 0 | 0 |
| `@genteastco` | genteastco | 1 | 2026-04-04 | 2026-04-04 | 2026-05-19 | 0 | 0 |
| `@glennbeck` | glennbeck | 1 | 2025-10-06 | 2025-10-06 | 2026-05-19 | 1 | 1 |
| `@johnnymaga` | johnnymaga | 1 | 2026-05-14 | 2026-05-14 | 2026-05-19 | 1 | 1 |
| `@jsolomonReports` | jsolomonReports | 1 | 2026-05-04 | 2026-05-04 | 2026-05-19 | 0 | 0 |
| `@karolineleavitt` | karolineleavitt | 1 | 2026-05-07 | 2026-05-07 | 2026-05-19 | 1 | 0 |
| `@lerafera` | lerafera | 1 | 2026-04-04 | 2026-04-04 | 2026-05-19 | 1 | 0 |
| `@matthatfield278` | matthatfield278 | 1 | 2026-04-04 | 2026-04-04 | 2026-05-19 | 0 | 0 |
| `@mrico18` | mrico18 | 1 | 2026-04-04 | 2026-04-04 | 2026-05-19 | 0 | 0 |
| `@necanet` | necanet | 1 | 2026-04-27 | 2026-04-27 | 2026-05-19 | 1 | 0 |
| `@news6wkmg` | news6wkmg | 1 | 2026-05-13 | 2026-05-13 | 2026-05-19 | 0 | 0 |
| `@nicksortor` | nicksortor | 1 | 2026-04-21 | 2026-04-21 | 2026-05-19 | 1 | 1 |
| `@nypost` | nypost | 7 | 2025-05-23 | 2026-05-04 | 2026-05-19 | 7 | 0 |
| `@politico` | politico | 3 | 2026-04-15 | 2026-05-12 | 2026-05-19 | 0 | 0 |
| `@priscialva` | priscialva | 1 | 2026-05-15 | 2026-05-15 | 2026-05-19 | 0 | 0 |
| `@recoverbritain` | recoverbritain | 1 | 2026-04-04 | 2026-04-04 | 2026-05-19 | 0 | 0 |
| `@ryer25785` | ryer25785 | 1 | 2026-04-04 | 2026-04-04 | 2026-05-19 | 0 | 0 |
| `@scotus_wire` | scotus_wire | 1 | 2026-03-04 | 2026-03-04 | 2026-05-19 | 1 | 0 |
| `@sfj8888` | sfj8888 | 1 | 2026-04-04 | 2026-04-04 | 2026-05-19 | 1 | 0 |
| `@theblaze` | theblaze | 2 | 2025-04-09 | 2026-04-29 | 2026-05-19 | 2 | 0 |
| `@unlimited_ls` | unlimited_ls | 1 | 2026-04-07 | 2026-04-07 | 2026-05-19 | 1 | 1 |
| `@uscgfmsg` | uscgfmsg | 1 | 2026-04-07 | 2026-04-07 | 2026-05-19 | 4 | 0 |
| `@usedgov` | usedgov | 2 | 2026-05-11 | 2026-05-18 | 2026-05-19 | 1 | 0 |
| `@usembassytokyo` | usembassytokyo | 1 | 2026-03-26 | 2026-03-26 | 2026-05-19 | 1 | 0 |
| `@vinekpr` | vinekpr | 1 | 2026-04-04 | 2026-04-04 | 2026-05-19 | 0 | 0 |
| `@vlast98230` | vlast98230 | 1 | 2026-04-05 | 2026-04-05 | 2026-05-19 | 1 | 0 |
| `@washingtonpost` | washingtonpost | 3 | 2026-05-08 | 2026-05-13 | 2026-05-19 | 1 | 1 |

_Generated 2026-05-19T19:15:22Z._

<!-- COVERAGE:END -->

## Architecture

```
Firefox extension  ──HTTPS PAT──▶  GitHub repo  ──push──▶  GitHub Actions  ──commit──▶  GitHub Pages
   page-hook                          raw/*.json              ingest.py                   index.html
   normalize                          config/accounts.yaml    archive_media.py            viewer/
   sidebar                            data/*.parquet          submit_wayback.py
   github client                      data/manifest.json      detect_deletions.py
                                      Releases (media)        update_readme.py
```

See the project specification for a fuller diagram.

## Documentation

- [Data schema](docs/SCHEMA.md)

## Operating principles

- **Determinism over cleverness.** Boring, explicit code.
- **Never silently drop data.** Parse failures are quarantined to `raw/_quarantine/` and surfaced in the sidebar.
- **Atomicity.** Parquet rewrites are `tmp.parquet` → `os.rename`; Release uploads must succeed before the Parquet row records the asset URL.
- **Don't store credentials in the repo.** PAT lives in `browser.storage.local`; Internet Archive keys live in GitHub Actions secrets.
- **Capture honestly.** Tweets in `data/` mirror what X served the browser at capture time. No massaging, no relevance filtering, no political characterization. Annotation is a separate, downstream concern.

## License

Property of the University of California.
