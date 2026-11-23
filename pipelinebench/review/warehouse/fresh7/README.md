# fresh7: a held-out set of labelled warehouse bugs

Real bugs from public SQL and dbt warehouse repositories. Each bug is placed at the commit that introduced it. Its label comes from the later commit that fixed it.

The labels come only from public upstream history: the fix commit, its message and its diff. The labeller worked only from the brief folder and from its own anonymous clones. It never read, opened, grepped or ran ChangeLens, or any other project folder under `/home/user`. It never read scoring results and never ran a model.

No repository from the earlier sets was reused: nothing in `excluded-repos.txt`, nothing that `fresh5-README.md` or `fresh6-README.md` lists as used, tried, cloned, scanned or rejected, and no source package of an excluded package. This set is meant to be scored once.

The set has 39 bugs, all in scope. None was recorded as `needs_data`. They come from 17 repositories, and no repository has more than 4. Of the 39 in-scope bugs, 22 are `compiles: true` and 17 are `compiles: false`. The brief asked for about 40. The set was closed at 39 because the remaining candidates were weaker labels or more compiles-false bugs.

## Files

- **One YAML per repository**, named after the repository.
  - Each file has top-level `repo`, `url` and `bugs`.
  - The bug fields are `commit`, `parent`, `title`, `fix`, `fix_title`, `bug`, `category`, `in_scope`, `compiles`, `needs_data`, `must_mention` and `expect`.
  - Every commit id is quoted, so YAML always reads it as a string.
- **`repos.txt`**: the local clone path and the upstream URL of each repository used. The clones are in `/home/user/fresh-repos-7/`.
- **`progress.md`**: the running count kept while labelling.
- There is no `manifest.sha256`, as the brief asked.

Commit ids are 10-character prefixes. `parent` is the first parent of the introducing commit. `compiles` says whether the introducing commit's SQL still parses and runs: `true` means a silent wrong result, and `false` means invalid SQL or Jinja. `needs_data` means the defect cannot be seen without knowing the source data; such bugs would be recorded with `in_scope: false`.

## Repositories

| Repository | URL | Commits at clone | SQL files | Bugs | In scope |
|---|---|---|---|---|---|
| CohortDiagnostics | https://github.com/OHDSI/CohortDiagnostics | 1,553 | 60 | 4 | 4 |
| Phenotype_Data_Acquisition | https://github.com/National-COVID-Cohort-Collaborative/Phenotype_Data_Acquisition | 711 | 47 | 4 | 4 |
| ossinsight | https://github.com/pingcap/ossinsight | 2,732 | 495 | 4 | 4 |
| PheValuator | https://github.com/OHDSI/PheValuator | 420 | 26 | 3 | 3 |
| bigquery-utils | https://github.com/GoogleCloudPlatform/bigquery-utils | 360 | 321 | 3 | 3 |
| stripe | https://github.com/dbt-labs/stripe | 59 | 35 | 3 | 3 |
| Characterization | https://github.com/OHDSI/Characterization | 290 | 30 | 2 | 2 |
| CohortGenerator | https://github.com/OHDSI/CohortGenerator | 198 | 29 | 2 | 2 |
| CohortMethod | https://github.com/OHDSI/CohortMethod | 1,110 | 11 | 2 | 2 |
| almanac.httparchive.org | https://github.com/HTTPArchive/almanac.httparchive.org | 3,700 | 3,680 | 2 | 2 |
| bigquery | https://github.com/HTTPArchive/bigquery | 518 | 137 | 2 | 2 |
| op-analytics | https://github.com/ethereum-optimism/op-analytics | 50,122 | 180 | 2 | 2 |
| queries-rj-escritorio | https://github.com/prefeitura-rio/queries-rj-escritorio | 202 | 25 | 2 | 2 |
| SelfControlledCaseSeries | https://github.com/OHDSI/SelfControlledCaseSeries | 570 | 15 | 1 | 1 |
| WebAPI | https://github.com/OHDSI/WebAPI | 1,827 | 714 | 1 | 1 |
| dbt_twilio | https://github.com/fivetran/dbt_twilio | 73 | 32 | 1 | 1 |
| queries-datario | https://github.com/prefeitura-rio/queries-datario | 383 | 54 | 1 | 1 |

"SQL files" counts the `.sql`, `.hql` and `.sqlx` files that are tracked at HEAD.

The repositories fall into five groups:

- **OHDSI analytics packages** (CohortDiagnostics, PheValuator, Characterization, CohortGenerator, CohortMethod, SelfControlledCaseSeries and WebAPI): SqlRender templates in the SQL Server dialect over the OMOP common data model. None of them is among the OHDSI repositories used or tried in fresh5 and fresh6.
- **N3C Phenotype_Data_Acquisition**: the COVID cohort phenotype and extract scripts, one per platform (Oracle, Redshift, BigQuery and others) over OMOP, ACT, PCORnet and TriNetX.
- **Analytics query repositories**: ossinsight (TiDB over GH Archive), op-analytics (ClickHouse and BigQuery for the OP stack), HTTPArchive `bigquery` and the Web Almanac (BigQuery over HTTP Archive), and Google's bigquery-utils (BigQuery scripts and UDFs).
- **Rio de Janeiro city dbt projects**: queries-rj-escritorio and queries-datario (BigQuery).
- **dbt packages**: the dbt-labs `stripe` package and Fivetran `dbt_twilio`. Neither appears in the earlier sets or in `excluded-repos.txt`.

## Method

The method is the same as in `fresh5-README.md` and `fresh6-README.md`:

1. Clone each repository in full, anonymously over https, into `/home/user/fresh-repos-7/`.
2. List the commits whose subject matches any of these words, case-insensitive: fix, revert, hotfix, wrong, duplicat, dedup, fan out, join, null, filter, double, missing, incorrect, off by, timezone, boundary, bug, correct or broken. Keep a commit only if it touches at most six SQL, HQL, SQLX or YAML files and changes at most 300 lines in them. The helper is `tools/mine.py`.
3. Run `git blame` on the fix's parent over every line the fix removes or replaces, to find the commits that wrote those lines. Keep a candidate only if its main introducing commit changed under 400 lines of SQL and YAML. Where blame landed on a later edit, `git log -S` found the commit that first wrote the faulty text.
4. Read the fix diff, its message and the introducing commit by hand. Keep a bug only when the introducing commit itself added the faulty lines, and when a careful reviewer of that diff could in principle see the problem with the repository at that commit in hand.
   - When seeing the bug needed knowledge of the source data, the bug would be kept as `needs_data`, out of scope. Such candidates were met (see below) but none was recorded.
   - A bug was dropped for any of these reasons:
     - the repository at that commit documented or tested the opposite of the fix;
     - the fix was a feature, a style change, a rename, or a fix for a later upstream change;
     - blame landed on a move or reorganisation;
     - the introducing commit was over 400 lines of SQL and YAML.
5. Record the lines in the introducing commit's own version of each file, checking each one with `git show <commit>:<path>`. A check script, `tools/check.py`, confirmed these things:
   - every line is non-empty in that version;
   - `commit`, `parent` and `fix` resolve, and `parent` is the first parent;
   - `title` and `fix_title` are the exact subjects;
   - the fix descends from the introducing commit (`git merge-base --is-ancestor`);
   - each bug has two `must_mention` words and a valid category;
   - no `needs_data` bug is in scope, and no repository has more than 5 bugs;
   - no file contains an em or en dash.

   Every YAML parses with PyYAML. No introducing commit is a merge commit.

The 400-line size limit counts SQL, HQL, SQLX and YAML lines only. R, Java, Python, notebooks, JSON, CSV, Rd and Rmd files in the same commit are not counted.

`must_mention` holds two words that a correct finding would use. Column and model names were preferred, along with the mechanism word, such as alias, left join, distinct or group by.

## Counts by category

| Category | Bugs | In scope |
|---|---|---|
| row_count_change (filter moved after a left join, inner join to an optional CTE, LEFT JOIN UNNEST fan-out, join on too few keys, 24-month window folded onto 12 month slots, reversed date filter, dedup partitioned on the wrong column, negated subset joined on person only, GROUP BY on extra columns, exclusive start date) | 10 | 10 |
| nullability_or_cast (ClickHouse LEFT JOIN without `join_use_nulls` returns defaults, twice; averages declared bigint) | 3 | 3 |
| business_logic (prefix trim that removes characters, wrong id column, swapped loop bounds, end date used for a before-start measure, former contributors taken from the wrong events, COUNT(*) over a per-repo table, window total not partitioned by client, unanchored regex, DELETE keyed on concept ids instead of cohort ids) | 9 | 9 |
| schema_contract (CAST with no alias in SELECT INTO, so the column has no name) | 1 | 1 |
| other (invalid SQL: misspelt table, nonexistent column or alias, R vector syntax in SQL, unbalanced paren, missing END, derived table with no alias, qualified name used as an alias, undeclared variable, ROW_NUMBER filtered at the same level) | 16 | 16 |
| **Total** | **39** | **39** |

| compiles | In scope |
|---|---|
| true | 22 |
| false | 17 |
| needs_data (out of scope) | 0 |

Every compiles-true bug is in row_count_change, nullability_or_cast or business_logic; every compiles-false bug is in other or schema_contract.

## Notes on individual labels

- **Two bugs in one introducing commit, each listed separately:**
  - Phenotype_Data_Acquisition `a6d56fee84`: the reversed visit_dimension date filter, fixed by `117a3e644d`, and the misspelt `visit_dimiension`, fixed by `d62d6f871f`.
  - stripe `79bf7cffc0`: the `trim('charge.' from "type")` prefix strip, fixed by `62bf643bd0`, and the wrong id column, fixed by `63b73e3e72`.
- **Introducers found with `git log -S`:** Phenotype_Data_Acquisition, where blame landed on later whitespace or neighbouring edits for some lines.
- **Exact subjects with odd spacing:** PheValuator subjects begin with " - " or "- " (`f21d9cf471`, `d8af662f32`, and the fixes `97977c73be` and `4187aaa9b2`); `d8af662f32` also has a double space. CohortMethod `78186d6a74` has a double space before "functions." The titles are copied exactly.
- **PheValuator `07e04e4833`:** the same one-line fix appears twice upstream, as `090dd78d39` and `1ff53a761d`, with identical diffs on two lines of history. `090dd78d39` is used; both descend from the introducer.
- **CohortGenerator `5437d9877d`:** the commit is about 3,000 lines in total, but only 85 lines are SQL and YAML; the rest is R, Rd, Rmd and tests. The faulty RIGHT JOIN is its own.
- **Characterization `e2d04cd5d8`:** 279 lines of SQL and YAML (the rest is R, CSV and tests). The same commit's `resultsDataModelSpecification.csv` declares the three columns float, so the repository documented the fix, not the bug.
- **op-analytics `566683c385`:** a migration of the teleportr transform to ClickHouse. The SQL is new in this commit, including the `SETTINGS` line without `join_use_nulls`, so blame does not land on a move. op-analytics `a0e159c3be` is the same ClickHouse mechanism in a different model.
- **queries-datario `cf2a2071a1`:** its subject is "initial commit", but it is not the repository's first commit; it introduced the `date_range_start` variable while keeping the exclusive `>` filter.
- **dbt_twilio `5271240051`:** introducer ("updates") and fix are one day apart, before the package's first release. No note says the defect was known while the work was unreleased, so it was kept; it is the one label closest to the work-in-progress exclusion.
- **bigquery:** the repository is `HTTPArchive/bigquery`, cloned as `bigquery`. The name is generic; the URL is the identifier.
- **almanac.httparchive.org:** cloned first as `almanac` and renamed to `almanac.httparchive.org` so the folder matches the repository name.
- **ossinsight `3880a16359`:** the overview query's COUNT(*) relies on the shape of `mv_repo_participants`, which the same commit creates with one row per repository and user.
- **WebAPI:** a second candidate, `cb8a7b77a1`, was labelled and then removed because the intended threshold logic was ambiguous.

## Repositories tried and what was rejected

**Cloned and scanned, with no bug kept:**

- `abstractions`: it turned out to be the excluded spellbook under an older name. The clone was deleted and nothing from it was read further.
- dbt-labs `quickbooks`: the fixes add a config option (`uses_classes`) or blame a 668-line commit ("general ledger complete").
- dbt-labs `zendesk`: the fixes are column renames and the switch from hard-coded schemas to `ref` and `var`.
- dbt-labs `mailchimp`: four commits, no fix.
- OHDSI `circe-be`: the SQL bugs trace to the 447-line import, or depend on the Java code that builds the SQL.
- OHDSI `CohortIncidence`: the introducers are 422 to 1,361 lines, and one fix is a revert of a feature.
- OHDSI `PatientLevelPrediction`: the introducers are mostly over 400 lines; the rest are results-table DDL changes, copyright edits, or a duplicate `person_id` column that only fails on PDW.
- OHDSI `ETL-LambdaBuilder` and `ETL-CDMBuilder`: the fixes are temporary batch filters for testing, DDL from very large imports, or null-versus-column choices without a clear intent.
- Fivetran `dbt_iterable`: no candidate passed step 4. Its source package `dbt_iterable_source` is in `excluded-repos.txt`.
- HTTPArchive `dataform` and `ga4dataform`: no fix commit matched the scan.
- Wikimedia `analytics-reportupdater-queries`: two fixes; one introducer is 567 lines and the other fix mixes a new metric with label changes.

**Examples of rejected candidates, by reason:**

- **The fix is a feature, a convention change or a rename:**
  - zendesk `fe47bb6ebd` and `7978a73e79`: the `reply_time_in_minutes__calendar` alias rename and its downstream follow-up.
  - queries-datario `2575cb3bcc`: a change of sentinel value for `date_range_start`, consistent with `dbt_project.yml` at the introducer.
  - PheValuator `467e4ec18c`: `count(*)` became `count(distinct subject_id)` together with a new visit join; counting cohort entries was a defensible reading before.
- **Needs source-data knowledge (not recorded):**
  - ossinsight `0d19f007df`: star history by `actor_login` versus `actor_id` matters only when users rename.
  - almanac `04b7213536`: technology names that differ only by case.
  - HTTPArchive bigquery `8e6e828f00` (NULL Core Web Vitals values) and `ee80c2b640` (a CrUX field path).
- **Introducer over 400 lines:** Characterization `3a72f93285` (881 lines), SelfControlledCaseSeries `355d8c0ac2` and `6f863e5f94` (463 lines), almanac `8ecc397987` (448 lines) and most other almanac chapter fixes.
- **Platform-specific rather than visible in the diff:** SelfControlledCaseSeries `857b28e06b` (date subtraction) and PatientLevelPrediction `303f032683` (duplicate column on PDW).
- **Kept for balance elsewhere:** further compiles-false candidates were left out so that compiles false stays under half: PheValuator `950658972e` and `c8331b0f6` (missing alias, trailing commas), SelfControlledCaseSeries `a20130d43` (migration typo), Phenotype_Data_Acquisition `c952120c1f` (semicolon) and CohortDiagnostics `e931a610ce`.
