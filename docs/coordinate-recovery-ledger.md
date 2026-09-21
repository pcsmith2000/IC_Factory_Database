# Coordinate recovery campaign ledger

Campaign: `missing-rooftops-2026-09-21`  
Frozen cohort: 1,668 rows  
Current result: 431 rooftop assertions recovered; 1,237 unresolved  
Recorded API cost: $0.714585 of the $9.50 campaign ceiling

Times are GitHub Actions elapsed runtime. Repeated implementation-triggered plan/test runs are
excluded; dry-run validation passes that directly informed a write are included.

|Pass #|Pipeline or Action Used|Time|Cost|
|---:|---|---:|---:|
|1|Frozen-cohort baseline and diagnostics|0m 55s|$0.000|
|2|Cached Geocodio rooftop replay|1m 45s|$0.000|
|3|Historical-address cached rooftop replay|0m 32s|$0.000|
|4|Florida BCIS official-detail address recovery, 11 batches|21m 36s|$0.000|
|5|Post-Florida cached rooftop replay|0m 27s|$0.000|
|6|Maryland Labor official-street recovery iterations|2m 59s|$0.000|
|7|Maryland unique-name plant crossmatch|0m 48s|$0.000|
|8|Post-Maryland cached rooftop replay, 1 recovered|0m 59s|$0.000|
|9|Geocodio paid-1 batch 1, 24 recovered|4m 07s|$0.093|
|10|Geocodio paid-1 batch 2, 63 recovered|1m 06s|$0.097|
|11|Geocodio paid-1 batch 3, 62 recovered|1m 07s|$0.096|
|12|Geocodio paid-1 batch 4, 59 recovered|3m 55s|$0.096|
|13|Geocodio paid-1 batch 5, 46 recovered|2m 41s|$0.097|
|14|Geocodio paid-1 batch 6, 17 recovered|1m 08s|$0.092|
|15|Geocodio paid-1 batch 7, 7 recovered|3m 16s|$0.079|
|16|Geocodio paid-1 cached remainder, 0 recovered|0m 49s|$0.000|
|17|Internal exact-name/city/state rooftop plan|0m 21s|$0.000|
|18|Internal exact-name/city/state write, 55 recovered|0m 50s|$0.000|
|19|Internal contact-and-locality rooftop plan|0m 28s|$0.000|
|20|Internal contact-and-locality write, 16 recovered|0m 24s|$0.000|
|21|Internal nationally unique-name rooftop plan|0m 23s|$0.000|
|22|Internal nationally unique-name write, 78 recovered|0m 48s|$0.000|
|23|Internal exact-address rooftop plan|0m 24s|$0.000|
|24|Internal exact-address write, 3 recovered|0m 19s|$0.000|
|25|Tako coordinate-recovery read-only plan, 10 of 825 address-research-eligible rows; research estimate $0.15 and cap $0.50|0m 19s|$0.000|
|26|Tako coordinate-recovery pilot, 10/10 completed; 6 matched, 2 review, 2 conflict|2m 57s|$0.064585|
|27|Strict Tako assertion plan, 0 address bundles accepted and 0 database writes|0m 26s|$0.000|
