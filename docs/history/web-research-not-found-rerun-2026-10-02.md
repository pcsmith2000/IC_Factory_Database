# Re-research of "not_found" plants in the building-review queue (2026-10-02)

Run `wr-buildings-1`. The building-review queue held 620 facilities whose earlier web research ended
`not_found`. Agents re-researched them in the browser, wrote one submission each (validated with
`pipeline.web_research.ingest.plan`) and inserted it into `web_research_submission`; the ingest workflow
has applied them.

**Done: 331 of 620** (batches 00–17; batches 13–17 partly, cut short when the Chrome extension
disconnected). Remaining: ~289 (batches 18–30 plus the unfinished tails of 13–17).

| verdict | count |
|---|---|
| not_found | 162 |
| not_ic | 58 |
| in_scope | 49 |
| closed | 35 |
| duplicate | 27 |

61 submissions move a record onto its real plant address (`plant_address_asserted`).
Every verdict, reason and source is in `web-research-not-found-rerun-2026-10-02.csv`.

Held back, not inserted: IC-20076 and IC-94546 (conflicting readings of 1230 SW 10th St, Ocala: Champion
retail lot vs Skyline plant), IC-96192 (Biszko, not_ic needs a second source).

## Needs a person
## Scope rulings needed
- Commodity mills (sawmill, OSB, plywood, veneer): agents treated as not_ic (IC-08130, IC-86459); Huber OSB rows IC-64577, IC-24088, IC-87883, IC-82083, IC-94576, IC-57876 tagged Mass Timber look wrong; Murphy Gold Hill IC-95375 plywood tagged CLT.
- Siding/cladding makers (Shakertown IC-90584/IC-90691, H&L half-log siding IC-91719).
- Sheds/pole barns/greenhouse kits/saunas (IC-75314 not_ic, IC-75122/75124 not_ic, IC-22186, IC-94727 Amish log/shed, IC-73421 Prospiant greenhouses, IC-13062/IC-48688 saunas not_ic).
- Log/timber-frame package makers (IC-95930 not_ic @0.55, IC-94658, IC-94892), framing kits (IC-12699), kit homes (Lindal IC-90308), hazmat storage buildings (IC-13798), generator enclosures (IC-94826), mobile-home chassis (IC-75771).
## Closures inferred from absence on MHI Oct-2024 plant list (left not_found)
- IC-64915, IC-66664, IC-66902 (+ probable merge with IC-65501), IC-74331 Beaver Buildings, IC-71198 Quackenbush, IC-54158 Gruen-Wald KY.
## Merge / entity reviews
- Addison AL Cavalier/Clayton/Southern Energy rows: IC-00489, IC-00516, IC-00958, IC-92649, IC-94984, IC-94742, IC-95118.
- Fairmont/Nappanee rows: IC-51865, IC-51867, IC-93715, IC-93805, IC-94749-51.
- Sauter Timber IC-95740 vs IC-94888/IC-94887; Engineered Wall Systems IC-87731 vs IC-87692.
- HELD (not inserted): IC-20076 (not_ic: Champion retail lot at 1230 SW 10th St Ocala) conflicts with IC-94546 (duplicate of IC-20076: Skyline Ocala plant at same address).
## Mis-linked source facts (entity resolution)
- IC-00958 (B.I.G. Enterprises CA facts), IC-00564, IC-00489/00499/00516 (fl_bcis names), IC-94549 (many unrelated sources).
## Capability errors seen
- IC-89199 HUD→trusses (website field wrong); IC-90308 HUD wrong; IC-95852 CLT wrong (drywall trim, closed); IC-95471 Closed LGS Panel→LGS components; IC-94680/IC-95980 likely Wood Volumetric not HUD.
## Possible missing plants
- Kalwall IC-68702 only; Woodbridge Glass San Leandro; DBS Prestress Dayton OH; Sto Panel affiliate fabricators; Elliott Presidio plant B; Prospiant.
- Utility/infrastructure-only precast: coordinator rule from chunk 13 on = not_ic with two pages (chunk11 already removed IC-95604, 95608, 95610, 95617; chunks 08–10 left ~20 as not_found: IC-21623, 31017, 47394, 48918, 49956, 52640, 56472, 56584, 56690, 56780, 68637, 68641, 55080, 70127, 75098, 95505, 95590, 13050, 19621, 20426, 21171, 21174, 95624). Owner to confirm.
- Metal roof/wall panel makers (IC-30924 IMETCO, IC-53716 Vicwest, IC-73908 Gideon), storm shelters IC-73943, steel fabricators IC-00579/IC-10212, Janus IC-10225, canopies IC-23341, ATC cabs IC-22762.
- Duplicates at 0.45 needing confirmation: IC-09141→IC-09134, IC-23482→IC-22864, IC-73423→IC-73417.
- American Buildings plants possibly closed: IC-00536 Eufaula, IC-94555 Atlantic IA, IC-70719 Carson City; Steel King Rome GA IC-95521 likely not_ic; IC-12781 Con-Fab "Needles" row suspect.
- Missing plants: Dukane Plainfield IL + Aurora 1875 Plain Ave; Palm Beach Stone / True Stone (if cast stone in scope).
- OSHA-sourced NAICS 332311 rows are often job sites/erectors (agents' suggestion: bulk rule). Ghosts to check: Frey-Moss IC-95971, IC-96098, IC-96262, IC-92823; HCI IC-90540, IC-90541; Whip IC-84379; Marshall Erdman IC-92999; Klover IC-95847; Trimak IC-96274.
- Scope: cleanroom panels IC-74439; steel fabricators IC-74999, IC-84575; General Shelters cabins IC-86152/85937/94119; MBCI roll-formers IC-86348/84102/87050/94568; e-house enclosures IC-96133; kit suppliers IC-95830; BFS lumber yards IC-21067/21826; Blackwater steel trusses IC-96033; Midwest Mfg IC-96073 PEMB vs truss.
- Missing plants: Benson/MiTek curtain wall Portland; MESCO 2218 Dawson Dr Chester SC.
- Held draft: nf/hold/IC-96192.json (Biszko, not_ic needs 2nd source).

