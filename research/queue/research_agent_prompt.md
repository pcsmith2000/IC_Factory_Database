You are verifying US factory records for a database of plants that make industrialized-construction (IC)
products. Attached is `research_queue_2026-09-29.csv`: 283 records that earlier research could not settle.
For each one, work out whether a plant making IC products operates at that place today, and if so, what it
makes. Use web search and page fetches.

## The rule that matters most
Never guess. Every verdict must rest on a source you found and can quote. If the evidence is thin or
conflicting, the answer is UNCLEAR. An empty answer is better than a plausible wrong one.

## What counts as IC (the owner's rulings)
IC means factory-built building products:
- HUD-code manufactured homes;
- wood, steel or relocatable volumetric modular buildings (steel includes shipping-container buildings);
- bathroom pods, and MEP skids and racks;
- open or closed wall, floor and roof panels, in wood or light-gauge steel;
- wood trusses and other structural components (joists, I-joists, glulam), and mass timber (CLT);
- pre-engineered metal building systems;
- precast concrete building products (wall panels, foundation walls, building components);
- SIP and ICF panels, exterior envelope panel systems, and cleanroom modules.

NOT IC:
- sheds, barns, garages, carports, gazebos and portable storage buildings;
- tiny homes on wheels, park-model RVs and RVs;
- log homes, log cabins and log-home kits;
- e-houses, power, electrical and equipment enclosures, and telecom or equipment shelters;
- guard booths, kiosks and storm shelters;
- blast-resistant portable buildings, and hazmat or chemical storage buildings;
- refrigeration and walk-in coolers or freezers;
- trailers, cabinets, doors, windows, millwork and furniture;
- infrastructure precast (culverts, pipe, vaults, bridges, highways, utilities, septic tanks);
- businesses that don't manufacture: dealers, retail sales centres, model-home lots, leasing and rental
  companies, contractors who build on site, corporate headquarters and offices, and mobile-home parks.

## Method, for each record
1. Search the name with the city and state, then the street address, then the phone number. The best
   sources are:
   - the company's own site ("our plants" or "locations" pages);
   - corporate plant lists from Clayton, Cavco (Fleetwood, Palm Harbor), Champion (Skyline, Redman),
     Legacy, Sunbelt Modular and Vantem;
   - the MHI HUD-code plant list;
   - state manufactured-housing and modular licence registries (TX TDLR, GA DCA, IN DFBS, FL, NC, PA);
   - local news of plant openings, closures, sales and layoffs;
   - SEC filings, bankruptcy records and WARN notices.
2. Make sure the source is about THIS business at THIS place. Earlier research often matched a
   different company with a similar name (for example a mortgage lender, or a cabinet shop in another
   state). If you can't tie the source to this record, it doesn't count.
3. Spend at most about 4 searches on one record. If it's still unsettled, mark it UNCLEAR and move on.
4. A record is CLOSED only on a positive statement: a closure or layoff notice, a bankruptcy or
   liquidation, a sale of the plant, the site demolished, or another business now at the address. A dead
   website, a missing listing or "no recent activity" is not closure; mark that UNCLEAR.
5. Record the category only when a source states what the plant makes (for example "wood-framed
   modules" or "steel-framed modules"). If the framing material isn't stated, give the verdict and leave
   the category blank.

## Verdicts (exactly one per record)
- `ACTIVE_IC`: an operating IC plant at this place. Fill `capability` with one of: HUD Modular, Wood
  Volumetric Modular, Steel Volumetric Modular, Relocatable Modular, Bathroom Pods, Specialty Volumetric
  MEP (Skids, Racks), Open Wood Panel, Closed Wood Panel, Open LGS Panel, Closed LGS Panel, Precast
  Concrete Panel, SIP / ICF (Other Composite Panel), Exterior Envelope Panels, Wood Structural Components
  (Trusses, etc.), Light Gauge Steel Structural Components, Hybrid Structural Components, Mass Timber
  (CLT), Pre-Engineered Metal Building. Leave it blank if the evidence doesn't say.
- `CLOSED`: the plant at this place no longer operates, on positive evidence (step 4).
- `NOT_IC`: the business at this place makes only non-IC products or doesn't manufacture. Say which in
  `why`.
- `DUPLICATE`: the same plant as another record in the queue, or as a plant you can name. Put the other
  record's facility_id in `duplicate_of` if it's in the queue.
- `MOVED`: the business operates, but its plant is at a different address. Put that address in
  `correct_address`.
- `UNCLEAR`: not enough evidence either way.

## Output
Return a CSV with exactly these columns, one row per input record, in input order:

```
facility_id,verdict,capability,duplicate_of,correct_address,evidence_url,quote,why
```

- `quote` is up to 30 words copied exactly from the source.
- `why` is up to 25 words of your own.
- Don't leave `evidence_url` or `quote` empty for any verdict except UNCLEAR.

At the end, give a count of each verdict and list any records where you overturned the earlier finding.
