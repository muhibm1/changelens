# History replay: calitp, 116 merged changes over 26 weeks, `stub`

| metric | value |
| --- | --- |
| commits | 116 |
| errors | 0 |
| weeks | 26 |
| commits warned | 0 |
| warns | 0 |
| warns per week | 0.0 |
| deterministic warns | 4 |
| model warns | 0 |
| confirmed | 0 |
| plausible | 0 |
| noise | 0 |
| unjudged | 0 |
| setup | 4 |
| false alarms per week | 0.0 |
| bugs flagged | 0/6 |
| bugs surfaced any severity | 1/6 |
| median review s | 0.71 |
| p90 review s | 2.86 |
| tokens in | 0 |
| tokens out | 0 |

| date | commit | change | bug | warnings |
| --- | --- | --- | --- | --- |
| 2026-04-23 | `1bff55fb` | Create new incremental macro and handle models at the `dt`/`service_date` incremental interface (#5053) (#5092) |  | none |
| 2026-04-23 | `531cd123` | Review `deploy-dbt` workflow (#5106) |  | none |
| 2026-04-23 | `3beb7b37` | Convert GTFS Schedule files with Byte Order Marker (#5148) |  | none |
| 2026-04-24 | `18dc437b` | Add product_type to final CTE for fct_payment_rides (#5107) |  | none |
| 2026-04-24 | `2af86094` | Migrate final GTFS schedule incremental models to microbatch (#5064) (#5141) |  | none |
| 2026-04-24 | `7bef4dad` | Manual job to run dbt with arguments (#5095) |  | none |
| 2026-04-27 | `b0e02bd1` | `fct_payments_aggregations` updates for reconciliation (#5017) (#5130) |  | none |
| 2026-04-27 | `8a8cf036` | Alerts and failing emails are not sent from Local Airflow by default (#5175) |  | none |
| 2026-04-28 | `1ca79016` | Add download type to the GTFS results file (#5176) |  | none |
| 2026-04-30 | `51398e84` | Add RT completeness issue closure workflow and refine email reporting (#5182) |  | none |
| 2026-04-30 | `2832a657` | Payments(Enghouse): create new parse operator and associated changes (#5184) |  | none |
| 2026-05-01 | `4adefb96` | payments(enghouse): modify parse schedule to better align with warehouse runs (#5207) |  | none |
| 2026-05-01 | `2152177b` | feat: add enghouse agencies to elavon transactions (#5206) |  | none |
| 2026-05-01 | `d9c7a871` | Update incremental model documentation for microbatch + new macro logic (#4925) (#5178) |  | none |
| 2026-05-01 | `39cdaa1d` | feat: add enghouse org names to fct payments * transactions models (#5212) | inform_only | none |
| 2026-05-05 | `7ef854e8` | fix: add enghouse_operator_id to fct_payments_*_transactions SQL (#5211) (#5241) |  | none |
| 2026-05-06 | `d40bdf16` | Resolve deprecated syntax warnings when running dbt commands (#5103) |  | none |
| 2026-05-06 | `db679552` | 5240: Unpin uv version to align with jupyterlab image in warehouse uv.lock (#5250) |  | none |
| 2026-05-06 | `e4b26f26` | Move dbt manual to monthly (#4849) (#5251) |  | none |
| 2026-05-06 | `e251bff0` | Revert "feat(airflow): Disable prod gtfs-rt parsed/validation copy to staging (#5111) (#5131)" (#5267) |  | none |
| 2026-05-06 | `83c7da30` | Replace enghouse bucket on production with the correct bucket name (#4759) |  | none |
| 2026-05-07 | `9035bbc6` | Fix payments failing tests (#5087) (#5145) |  | none |
| 2026-05-08 | `e56de4ff` | Add fct_tides_vehicle_locations dbt model (closes #4837) (#5216) |  | none |
| 2026-05-08 | `3afb8565` | Add fct_tides_trips_performed dbt model (#5220) |  | none |
| 2026-05-08 | `05bf2c4d` | Refactor TIDES models for SCD Type 2 historical correctness + persistent source_record_id seed (#5287) |  | none |
| 2026-05-08 | `6503a211` | Adding GTFS url to expiring issues model (#5290) |  | none |
| 2026-05-13 | `82ded47c` | Payments: Enghouse mart fixes, Valley onboarding, fix deduplication, and dbt docs (#5284) |  | none |
| 2026-05-19 | `f7b35555` | feat(payments): add status to littlepay micropayments table (#5168) (#5319) |  | none |
| 2026-05-19 | `27453feb` | Add dbt docs for new models in `mart_gtfs` and `mart_gtfs_rollup` (#5305) |  | none |
| 2026-05-21 | `6db8c535` | feat(tides): org-level publication seed, vehicle_locations as view (#5318) |  | none |
| 2026-05-22 | `40027781` | TIDES DAG (part 1 generating parquet files) (#5307) |  | none |
| 2026-05-26 | `2a5459b1` | Remove broken dbt visualization, fix duplicate exposure comments (#5343) |  | none |
| 2026-05-26 | `d9d9f560` | Create Outcomes table for TIDES Audit Dashboard (#5341) |  | none |
| 2026-05-27 | `e38f68ed` | Chore(deps): Bump idna from 3.4 to 3.15 in /warehouse (#5323) |  | none |
| 2026-05-28 | `02fd6f86` | Propagate `service_hours/flex_service_hours` consistently in mart+rollup models, update dbt docs (#5348) |  | none |
| 2026-05-28 | `0ce7a9f6` | Update parse_elavon dag to use new sftp bucket (#5032) |  | none |
| 2026-05-28 | `e2374bab` | fix(ntd): type text API columns as STRING in external tables (#5336) (#5337) |  | none |
| 2026-06-01 | `b376faed` | TIDES external tables `trips_performed_outcomes` and `vehicle_positions_outcomes` (#5362) |  | none |
| 2026-06-01 | `aeecbc34` | Developer sees trips performed in the TIDES bucket (#5353) |  | none |
| 2026-06-01 | `13db6b79` | Materialize fct_tides_vehicle_locations incrementally by service_date (#5364) |  | none |
| 2026-06-01 | `14ec630f` | Developer does not see project access error when uploading TIDES reports (#5367) |  | none |
| 2026-06-01 | `55e0d96c` | Developer does not see an error when uploading TIDES trips perfomed (#5369) |  | none |
| 2026-06-04 | `473c3608` | Chore(payments): SCRRA Payments Service Account and Littlepay Parse (#5316) (#5380) |  | none |
| 2026-06-04 | `a46bf47f` | chore(payments): Payments Warehouse Onboarding for SCRRA (#5316) (#5381) |  | none |
| 2026-06-04 | `8d30bc26` | Fix(Payments): Filter `DELETED` Littlepay Micropayments from Downstream Tables (#5344) |  | none |
| 2026-06-05 | `815b047f` | Local composer3 airflow (#5382) |  | none |
| 2026-06-05 | `2ee5a8cf` | Remove unused pygraphviz and networkx dependencies (#5391) |  | none |
| 2026-06-05 | `9fe7247a` | Add airtable fields (#5383) |  | none |
| 2026-06-09 | `5b241aa4` | Add teo new fields to external_airtable_california_transit_gtfs_datasets.yml (#5392) |  | none |
| 2026-06-09 | `f214df82` | Remove Sentry SDK plumbing from Airflow / warehouse / gtfs-schedule-validator (#5279) |  | none |
| 2026-06-09 | `a172b0ea` | fix(payments)!: Remove `taps` Join and Associated Columns from `fct_payments_settlements_enghouse` (#5399) (#5405) |  | none |
| 2026-06-10 | `8934c2d7` | Disable catchup and reset start_date for parse_and_validate_rt and generate_tides (#5424) |  | none |
| 2026-06-11 | `50d27b75` | Revert "Add teo new fields to external_airtable_california_transit_gtfs_datasets.yml (#5392)" (#5427) |  | **setup** deterministic `warehouse/models/mart/transit_database/_mart_transit_database.yml:359` tides_gtfs_datasets still selects dim_gtfs_datasets.has_authentication; **setup** deterministic `warehouse/models/mart/transit_database/_mart_transit_database.yml:359` dim_gtfs_datasets_latest still selects dim_gtfs_datasets.has_authentication; **setup** deterministic `warehouse/models/mart/transit_database/_mart_transit_database.yml:359` tides_gtfs_datasets still selects dim_gtfs_datasets.authentication_contact_details; **setup** deterministic `warehouse/models/mart/transit_database/_mart_transit_database.yml:359` dim_gtfs_datasets_latest still selects dim_gtfs_datasets.authentication_contact_details |
| 2026-06-11 | `5d9dd884` | fix(airflow): set gusty DAG catchup at METADATA top-level and drop dead core-catchup_by_default override (#5429) |  | none |
| 2026-06-17 | `18b80a29` | Chore(deps): Bump aiohttp from 3.14.0 to 3.14.1 in /warehouse (#5450) |  | none |
| 2026-06-22 | `d7d76c3e` | Remove old elavon infra (#5068) |  | none |
| 2026-06-22 | `70262509` | feat!: De-Duplicate Enghouse Settlements Table (#5397) (#5426) |  | none |
| 2026-06-22 | `506bdd05` | Add authentication fields to GTFS models  (#5461) |  | none |
| 2026-06-24 | `15414b01` | Consolidate NTD annual funding and expenses dbt models into 1 (#5487) |  | none |
| 2026-06-29 | `0750c53f` | Move authentication fields to correct model in transit_database_latest YAML (#5469) |  | none |
| 2026-07-01 | `a0849f40` | feat(airtable): add loader config for payment device mapping table (#5492) |  | none |
| 2026-07-01 | `7e996515` | chore(Payments): Service accounts, Download/Parse for Santa Cruz Metro and Roseville Transit (#5508) |  | none |
| 2026-07-01 | `1b89e916` | feat(payments): expose dt and line_number in enghouse to mart to match lpay (#4912) (#5460) |  | none |
| 2026-07-01 | `01d59fc1` | Reduce TIDES exclusion to only Anaheim Transportation Network (#5503) |  | none |
| 2026-07-02 | `78821f09` | fix(payments): fix aws buckets (#5444, 5384) (#5510) |  | none |
| 2026-07-03 | `91b02d1d` | feat(airtable): add agency name to payment device mapping (#5517) |  | none |
| 2026-07-03 | `ecfb7977` | feat(dbt): add payment device mapping models (#5518) |  | none |
| 2026-07-07 | `5a920d71` | Expire partitions where service_date is older than 15 days (#5502) |  | none |
| 2026-07-09 | `382c32b2` | Refactor ntd monthly models (#5523) |  | none |
| 2026-07-09 | `ff02c783` | Deprecate old NTD models, remove from yaml (#5525) |  | none |
| 2026-07-09 | `04734927` | add trip modifications feed type to dbt (#5528) |  | none |
| 2026-07-14 | `36e10062` | Feat/airtable loader v2 add programs table (#5537) |  | none |
| 2026-07-14 | `90e6ffa2` | Provide `organization_id` in Littlepay Tables to Disambiguate Regional Agencies (#5538) |  | none |
| 2026-07-15 | `2917fcea` | Fix NTD unnamed columns, add month-over-month change in monthly ridership (#5539) |  | none |
| 2026-07-17 | `54ffad28` | Fill out `mart_ntd_funding_and_expenses` YAML (#5541) |  | none |
| 2026-07-23 | `22c8a93f` | Devcontainer (#5519) |  | none |
| 2026-07-23 | `0c739195` | chore(payments): Payments Entity Mapping and Row Level Permissions for SCMTD and Roseville Transit (#5516) |  | none |
| 2026-07-27 | `2eff9b46` | fix(payments): De-duplicate `fct_payments_settlements` Rows (#5884) (#5586) |  | none |
| 2026-07-27 | `e636f6e2` | fix(payments): all datetimes  in fct_payments_aggregations are typed as datetimes (#5582) (#5583) |  | none |
| 2026-07-27 | `b35c8ffb` | chore(payments): onboard Simi Valley, Thousand Oaks, Moorpark, and Gold Coast to Enghouse (#5587) |  | none |
| 2026-07-30 | `cd9fb106` | Add columns to external_airtable.california_transit__services (#5599) |  | none |
| 2026-08-05 | `846a62bc` | feat(payments): add settled and unsettled credit amoutn columns (#5597) (#5629) |  | none |
| 2026-08-07 | `00dfc554` | Add Reconciliation Category and Update Tests for Duplicate RRNs (#5459, #5278) (#5631) |  | none |
| 2026-08-07 | `2e7c8211` | refactor(gtfs): microbatch daily RT validation notices (#5636) |  | none |
| 2026-08-11 | `28724433` | refactor(gtfs): incrementally build daily service alerts (#5643) |  | none |
| 2026-08-11 | `5b9885a0` | refactor(gtfs): incrementally build vehicle position trip summaries (#5642) |  | none |
| 2026-08-13 | `008d78a8` | feat(payments)!: Remove non-RBAP Payments Tables (#5600) (#5651) |  | none |
| 2026-08-13 | `3127f574` | Add `_feed_valid_from`  to multiple daily GTFS summary tables (#5656) |  | none |
| 2026-08-13 | `a61247bd` | Add demand response service fields and update the paratransit bridge table (#5657) |  | none |
| 2026-08-18 | `b6a4ba5a` | Add Rider Requirements Dimension Tables and Bridge Table (#5667) |  | none |
| 2026-08-18 | `fbe45897` | Remove legacy stg_rt models that read the deleted rt-parsed bucket (#5633) |  | none |
| 2026-08-19 | `49a39e77` | Add Public/Conditional Currently Operating col to external airtable (#5668) |  | none |
| 2026-08-21 | `f20856f3` | fix: roll up GTFS download failure alerts (#5672) |  | none |
| 2026-08-25 | `6a6df64e` | Add Public/Conditional Operating Column to Dim Tables (#5674) |  | none |
| 2026-08-28 | `6d8033ef` | Upgrade Schedule Validator to v8.0.01 (#5331) (#5677) |  | none |
| 2026-08-31 | `2f53a705` | Use SUM instead of COUNT to fix total arrivals per route type and time of day (#5679) |  | none |
| 2026-09-02 | `be8ceca2` | Daily and Monthly Scheduled Stop review (#5684) |  | none |
| 2026-09-02 | `c3ca6038` | Publish TIDES reference data in the TIDES bucket (#5561) |  | none |
| 2026-09-03 | `e34fe9ec` | Staging/airflow alerts payments (#5686) |  | none |
| 2026-09-10 | `0eaf78eb` | (bridge): add org_name to partition (#5709) |  | none |
| 2026-09-15 | `e5c0eedf` | Increase GTFS-RT parse and validate retries (#5716) |  | none |
| 2026-09-17 | `248a326f` | Feat: Payments analysts have draft Enghouse Aggregation Table (#5713) |  | none |
| 2026-09-18 | `a2968577` | Fix Airflow failure Slack log link for Caltrans SSO users (#5708) |  | none |
| 2026-09-21 | `1269de75` | chore: remaining yolobus onboard steps (#5521) (#5729) |  | none |
| 2026-09-29 | `ada5891d` | Fix: Enghouse Null Timestamps (#5694) (#5736) |  | none |
| 2026-10-07 | `9d3363b1` | Publish TIDES reference history to stable paths (#5733) |  | none |
| 2026-10-08 | `93b31198` | test: make taps test updates (#5397 #5738) (#5759) |  | none |
| 2026-10-12 | `34716dcc` | Fix daily schedule+RT route-direction summary trip counting (#5767) | missed | none |
| 2026-10-12 | `b0b73b32` | fix refs (#5768) |  | none |
| 2026-10-14 | `ad497afc` | fix: De-duplicate Littlepay Customers and Rides (#5761) |  | none |
| 2026-10-15 | `e8e60ae8` | chore: update payments_entity_mapping for scmetro (#5778) (#5786) |  | none |
| 2026-10-15 | `eaac6432` | Remove deprecated GTFS Schedule DAGs and associated code (issue #4814) (#5777) |  | none |
| 2026-10-15 | `6f1cc4b7` | chore: Enghouse Transactions Cleanup (#5712) (#5763) |  | none |
| 2026-10-16 | `8af13f10` | feat: enghouse parse job writes audit table (#5766) (#5788) |  | none |
| 2026-10-21 | `8799510b` | feat: handle passes and revenue-inspection-outcomes changes (#5793) (#5802) |  | none |
| 2026-10-22 | `4de38421` | Remove deprecated PodOperator and GTFS Schedule configuration (#5796) |  | none |
| 2026-01-06 | `b111f6f5` | payments: warehouse staging and source tables for enghouse data (#4122) | missed | none |
| 2026-02-06 | `f26b2970` | Dedupe payments settlements (#4755) | missed | none |
| 2025-12-29 | `6a9eae65` | Refactor stop time update related models (#4627) | missed | none |
| 2024-11-26 | `0dfe3b65` | Scrape geoportal for shn, add SHN flag on stops table (#3529) | missed | none |
