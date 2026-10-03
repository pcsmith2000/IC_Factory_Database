# Contact pass: wood and steel volumetric plants (run `wr-contacts-1`, 2026-10-03)

Scope: every golden row labelled Wood or Steel Volumetric Modular that lacked a website, phone or
email (328 rows). Each row was researched in the browser in this order: **is it really an IC plant
at this location → is the category right → fill and check website, phone, email (and address)**.
Each result is a `web_research_submission` validated against `docs/web-research-agent.md` §4–§6.

**Researched: 328 of 328.** Submitted: 289. Held for a person: 39.

| verdict | count |
|---|---|
| not_found | 65 |
| in_scope | 63 |
| closed | 63 |
| duplicate | 50 |
| not_ic | 48 |

Fields asserted (rows with at least one sourced value): website 107, phone 122,
email 63, street address 107, capability_leaf 85 (confirmations and corrections).

## What the gap list turned out to be
- About a third of the "wood volumetric" gap rows are entries copied from Florida's
  floridabuilding.org manufactured-buildings organisation list: denied or expired applications from around
  2000, school districts, third-party inspection agencies, trade associations, and firms dissolved in
  the 1980s–2010s. They have no contact data because they are not plants. Removals cite the
  registry's own org type or a Sunbiz/GA SoS dissolution filing.
- Many duplicates are old brand names of plants that changed owners (Destiny → Cavco Moultrie,
  Whitley → Sunbelt, Beaman → American Modular Technologies, Fleetwood → Champion).
- Category corrections: HUD-code plants labelled wood volumetric, steel-module plants labelled wood,
  relocatable/commercial plants, plus a few pods and panel plants.
- Contact claims taken only from the 20–30-year-old Florida registry were **removed** from rows that
  are not confirmed operating (24 drafts replaced before ingest), because an empty cell beats an old guess.
- Every row in the CSV keeps its sources. `submitted = no` means held for a ruling.

## Needs a person
- IC-95444: All Star Storage & Container Sales yard; nothing ties American Manufacturing & Building Corp to it. Remove as container yard?
- IC-16954 LIM Living: panelized ADU kits; site looks dead. Panel leaf or closed?
- IC-94468 BOSS Homes ("Built On Site Systems"): probably a panel leaf; material unconfirmed.
- IC-94876 Wilkins: commercial modular offices/classrooms. Relocatable Modular instead of Wood Volumetric?
- IC-93518/IC-94485 FreeForm: also sells modified shipping-container units (survivor row scope).
- IC-95194 Rocky Mountain Prefabs: plant in Red Deer County, AB (Canada); row address is the registered office.
- IC-96149 Atom Modular: lists FRAMECAD (LGS framing) as partner; leaf may be steel.
- IC-94944 / IC-94522: two S2A rows in Patterson, CA; not checked as duplicates.
- IC-94938 Plant Prefab Rialto: contact page lists only Arvin (IC-94516); no closure source. Keep or close?
- IC-92727 Cubicco: at Raymond Building Supply / Deco Truss plant (IC-96657); dormant. Duplicate or closed?
- IC-92767 EcoTainer: container branding, says light-steel-frame homes. not_ic, or Steel Volumetric / Open LGS Panel?
- IC-93054 Modtech Holdings: same address as IC-10675 Silver Creek (2830 Barrett Ave, Perris); likely predecessor → duplicate.
- IC-92630 Buildit Shell: shell contractor "or work with a factory", but holds FL factory-built plan 40516. Remove or keep?
- IC-93002 Master Garage Builders: garage contractor that pre-builds walls and makes own trusses. Remove or relabel panel/truss?
- IC-93856/IC-93865 Atelier7: container homes (excluded) but MBI calls it full volumetric modular manufacturer.
- IC-93140 Performance Concrete Structures: precast/ICF shelters; Precast Concrete Panel or SIP/ICF? Likely same as IC-93141 (Newnan).
- IC-94893 ProBox: MBI says it specializes in container modification → may be not IC.
- IC-92872 Heartland Building Systems: GA company of that name reported dissolved; needs GA SoS check.
- IC-23839 Modular Engineering Mfg: 280 Stapleton Rd was SteelCell's site until 2009 (now IC-37641, Baldwin GA).
- IC-92750 / IC-94734 Design Space (Homerville): look like one plant; IC-92750 has the street address.
- IC-93993 Appalachian Enterprises = Deer Run Cabins: pre-built cabins + pre-cut SIP cabin kits. not_ic (cabins) or SIP/ICF?
- Creative Modular Construction, Springfield MO: ~7 rows (IC-93491..93495, IC-95004, IC-96155); OSHA confirms only 2738 E Kearney (IC-93494). Consolidate.
- IC-94722 Contempri: IL SoS "Dissolved" (Bizapedia snippet only) + parked domain. Confirm on apps.ilsos.gov before removal.
- IC-48437 KIT Home Builders West (1124 Garber St): likely same plant as IC-93914.
- IC-94140 Modular Builders Plant #2 (2756 Fort Wayne Rd): Sunbelt lists only one Rochester plant (IC-94139). Inactive?
- IC-94705 / IC-92628 Builder Modules: 8002 SR 59 may be Home Nation's dealer lot only → possibly not_ic.
- IC-94879 (Winalta Linton, closed) appears to be the plant Advance Building Concepts now runs (IC-94687).
- Parent-level emails (Sunbelt marketing@) asserted at low confidence for IC-93482, IC-94140 — keep or drop?
- IC-93279 Southern Building Solutions: plumbing/residential contractor at 250 Ball Rd, Ecru; FL phone + cragroup.us email suggest a FL plan listing. Proposed not_ic (directories only).
- IC-95047 Davie Construction: commercial GC with no plant, but Active NCDOI modular licence 47453. Remove?
- IC-95114 Shae Enterprises: Active NC commercial modular licence; fire-protection supplier, likely fire-pump houses → Specialty Volumetric MEP?
- IC-71358 / IC-93392 United Structures (NY): same company/phone, two addresses, one Steel one Wood; neither confirmed.
- IC-93448 "Xtreme Cubes Co." duplicates IC-93646 (same address/phone) → merge.
- IC-95126 VFP Duffield VA: precast/steel telecom equipment shelters, not wood volumetric. Specialty Volumetric MEP, Precast Concrete Panel, or not IC?
- Morgan Buildings IC-94143 (Hallettsville TX, survivor of 2 dups): mostly portable storage buildings/sheds, but also commercial modular. In scope? Is FM 318 the factory or a sales lot?
- IC-94900 Arshan: likely office/developer; only one source.
- IC-92625 Brodie Modular: site down; possibly defunct.
- IC-76263 Pine Grove Manufactured Homes (2 Pleasant Valley Rd) next to Pleasant Valley Homes — check.
- Canadian plants: IC-92971/IC-92972 Bonneville (Beloeil QC), IC-95194 Rocky Mountain Prefabs (AB). Remove as out-of-country?
- IC-92897 Horton Eatonton: plant now Legacy Housing → duplicate of IC-94988 or relabel?
- IC-95676 nVent Trachte (Oregon WI): steel control buildings/E-houses; sibling IC-91520 is PEMB. Steel Volumetric, PEMB or Specialty MEP?
- Leaf conflicts: IC-93482 C&B (Steel → Relocatable asserted); IC-94039 ProMod (Steel, frame unstated).
- Rows at sites found closed, need same check: IC-93086 (Nationwide Homes, Arabi GA), IC-94841 (Ruf-Neck, Aubrey TX).
- IC-93938 / IC-93965: Oregon licence test records ("100 TEST RD", "10 TEST RD") — delete (operator ruling).
- IC-74360 MODS PDX: HUD Modular leaf looks wrong (custom modular buildings).
- IC-75953 MBSP Plant 1 (72 E Market St) is an office; second plant address unknown (only IC-93549 known).
- IC-73178 Superior Kraft Homes: probably builder/sales lot; directories only.
- IC-95097 Pengrove → PennKraft Building Systems at 1 Mauro Ave. Rename or close?
- IC-95255 Modern Living Solutions: steel vs wood unconfirmed.
- IC-95660 MODLOGIQ Seville relabelled Open LGS Panel (they also advertise LGS volumetric).
- Out-of-country/test rows held: IC-94214 (Hunter Buildings UAE office; US plant IC-85336), IC-92970 + IC-92969 (Quebec renovation contractor), IC-92640 (Ontario cart maker), IC-92692 CNTNR (container modules made in Monterrey MX), IC-92615 "botest" test row.
- IC-62172 Butler, 13500 Botts Rd, Grandview MO: BlueScope lists it as "Research", not manufacturing.
- IC-65098 Liberty Homes Statesville = same closed plant as IC-92976 → close.
- IC-93291 / IC-94050 Specialized Structures: Wood vs Steel leaf disagree.
- More dups: IC-93429 & IC-94864 at 3089 Fort Wayne Rd (survivor IC-94139); IC-22917 GMH #4 (2885 Fulford Rd) maybe ScotBilt.
- IC-92649 Cavalier Addison 2 marked not_ic: site now Clayton Park Models (park-model RVs). Confirm.
- Closed verdicts resting mainly on corporate dissolution (1990s FL/GA firms): IC-92664, 92665, 92668, 92707, 92734, 92735, 92764, 92595, 93443, 92601, 92605.
- IC-92738 D&F Construction: probably a GC; directories only.
- IC-92732 Custom Mobile Builders may be the Sardis Rd site of IC-00516 (Cavalier Plant 3).
- KPS Global (IC-95546 → IC-92952; also IC-93540 Piney Flats with bad leaf/website): insulated cooler/freezer panels. Exterior Envelope Panels or not_ic?
- IC-93262 Sheds America: proposed not_ic, evidence weak (directories, name).
- Safe Buildings (IC-93235 / IC-94694): prefab hazmat storage modules; Quincy plant unconfirmed; leaf PEMB vs Wood; IC?
- IC-23806 name says "inactive" but Cavco Moultrie is operating (target of 4+ Destiny dups).
- IC-94046 Rolling Acres "Factory 2" (no address) may be IC-93226 (Leland's KY).
- IC-93054 Modtech Holdings - TX: Perris CA address with Glen Rose TX phone.
- IC-94695 American Modular Technologies: HUD leaf looks wrong (commercial modular); site footer "Copyright 2008".
- Blanket ruling wanted: dead Florida manufactured-building applications (~2000, Denied/Expired/Inactive) never confirmed as plants — remove all, or leave as not_found?
- Out-of-country more: IC-93100 Nova Deko (Foshan, China), IC-93039 ModDsys (Dubai), IC-92969 Marcoux (Quebec, dup of IC-92970).
- IC-93198 Radva (Radford VA) now Alleguard EPS foam plant (also AMVIC ICF blocks). not_ic or SIP/ICF?
- IC-93250 "School Board": blank placeholder record in FL registry → not_ic/delete.
- IC-93266 / IC-93267 old Skyline Ocala divisions: possible dups of IC-94546 / IC-95671.
- Sagebrush/Ventaire Tulsa: IC-93241 (→IC-93603), IC-93242, IC-93602, IC-93603 — consolidate.
- IC-93056 Modulaire: email field holds a FL DBPR staff address (sandi.curlee@dbpr...) — needs manual removal (monitor_fix can't blank).
- IC-92992 Manning Quick Wall: probably concrete tilt-wall panels, not wood volumetric.
- IC-92922 International Systems Inc: may be ISI Detention (Mobile AL) steel cells; needs AL SoS check.
- School-district rows (FL registry): most removed as not_ic; IC-92997 Marion County SD is in Mississippi.

