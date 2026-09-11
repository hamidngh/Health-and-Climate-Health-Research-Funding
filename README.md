# Climate-related Health Research Funding

This repository extracts grant data and health-research denominator aggregates from the
Dimensions Analytics API, reconstructs the **Main Search Approach (Scenario 14)**, and
runs the ten alternative scenarios needed for its **methodological bands** that are used for 
the **indicator 4.3.4 of the Lancet Global Report 2026 (Health and Climate-Health Research Funding)**. 
It then creates local CSV/XLSX tables, PNG/SVG/PDF figures, chart-source CSVs, and an HTML gallery.

**Start here:** [`notebooks/01_dimensions_to_outputs.ipynb`](notebooks/01_dimensions_to_outputs.ipynb).
After the one-time environment setup, the normal workflow is **Run → Run All Cells**.
The same implementation is also available through `run_pipeline.py`; there are not two
separate scientific pipelines to keep synchronised.


## Public code, private data

The public release contains code, clean notebooks, methods/configuration and synthetic
test generators—not the report, appendices, Dimensions data, generated outputs or API
credentials. The author’s separate **local-run bundle** additionally contains two input
workbooks, which are:

```text
top5_values.xlsx
Copy of 2026 Guidance_Country Names and Groupings.xlsx
```

The first file cannot be shared due to Dimensions Analytics policy, but the second is 
available under `private/`. The authors reviewed `top5_values.xlsx` 
after it was generated and handpicked the grants to be excluded from the data.

A new reader cloning the code-only repository must obtain these two historical inputs
separately for exact exclusion and geographic replication. 
**An API key cannot recreate an external historical exclusion list or the exact country-grouping workbook.** 
No raw Dimensions grants or precomputed denominator workbooks are required as inputs. The
notebook’s `MODE = "demo"` option needs neither the private workbooks nor an API key.

## Quick start: run the notebook

Use Python **3.11–3.13**. The delivered build was tested on Python 3.11; the CI matrix
also specifies other versions/platforms but is not evidence they have already run.

### macOS / Linux

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt -r requirements-notebook.txt -r requirements-dev.txt
python -m jupyterlab
```

### Windows PowerShell

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt -r requirements-notebook.txt -r requirements-dev.txt
.\.venv\Scripts\python.exe -m jupyterlab
```

Open `notebooks/01_dimensions_to_outputs.ipynb` in JupyterLab and select **Run → Run All Cells**. 
The default is a live 1990–2025 run. The notebook imports the local workbooks,
prompts for the API key if needed, checks the connection, extracts all required data,
creates the new top-five sample, and generates the results. Keep the computer awake.

To store the key locally rather than enter it at each session:

```bash
python run_pipeline.py init
```

Edit **`.env`**, replacing only its `XXXXXXX` placeholder. Do not edit `.env.example` or
put a real key in notebook source. The public example remains:

```dotenv
DIMENSIONS_API_KEY=XXXXXXX
DIMENSIONS_ENDPOINT=https://app.dimensions.ai
```

The key needs Dimensions Analytics API/grants access, not just a normal web login. The
API authenticates the key to obtain a temporary token. See the [official API guide](https://docs.dimensions.ai/dsl/api.html).

Full installation, restart, comparison and troubleshooting instructions:
[`docs/RUN_NOTEBOOK.md`](docs/RUN_NOTEBOOK.md).

## Exactly what runs

| Stage | Implementation | Local products |
|---|---|---|
| 0. Inputs and credentials | `reference.py`, notebook setup, `.env` | Hash-checked private reference tables; credentials stay local |
| 1. Category mapping and numerator | `queries.py`, `dimensions.py`, `scenarios.py` | Four climate-plus-health base searches; eleven deduplicated numerator exports |
| 2. Health-only denominator | `dimensions.py`, `scenarios.py` | Twelve conjunctions; overlap-corrected aggregates by funder and recipient country |
| 3. Inspection sample | `review.py` | Tagged Scenario 14 data; a **new** `review/top5_values.xlsx`; historical-list reconciliation |
| 4. Historical cleaning | `cleaning.py` | Separate funder and recipient master tables; explicit omission/exclusion diagnostics |
| 5. Indicators and bands | `analysis.py`, `workflow.py` | Annual values, counts, shares, growth, geography, HDI, funder types and reporting-funder check |
| 6. Publication outputs | `excel.py`, `plots.py` | CSV/XLSX tables; PNG/SVG/PDF figures; chart-source CSVs; HTML gallery |

All annual extraction and analysis windows default to **1990–2025 inclusive**. The main
full-period figure has that window. A **separate 2010–2024 crop** supports comparison with
the Lancet Global report figures; it does not replace or truncate the full-period outputs.

## Main approach and band definitions

Let **H** be the health-keyword search; **A** be ANZSRC health classifications;
**R** be nonempty HRCS health categories; and **U** be health-related UoA groups. The main
approach requires **at least three of H, A, R and U**, including grants satisfying all four.
Climate terms are required for numerators only. The classification veto is applied to
both numerators and denominators.

| IDs | Health-research definition |
|---|---|
| 01–04 | H; A; R; U |
| 05–07 | H AND A; H AND R; H AND U |
| 11-13 | H OR A; H OR R; H OR U |
| 14 | At least three of H, A, R, U — **Main Search Approach** |

The default `historical_all_11` band follows the actual historical plotting code: the
pointwise minimum and maximum across the **eleven retained approaches, including main**.
Every band table also reports the bounds across the **ten alternatives alone**. Set
`band_policy` to `alternatives_only` to plot that explicitly different envelope.

Each scenario’s share is calculated using **its own numerator and its own denominator**
before finding the envelope. Bands are sensitivity ranges across definitions—not
sampling confidence intervals. See indicator 4.3.4 of the Lancet Global 2026 Report (Health and Climate-Health Research Funding).

## The two different “top5” files

The notebook regenerates `runs/<run>/review/top5_values.xlsx` from the current raw Scenario
14 extraction: five largest awards per start-date year, before **analytical cleaning**.
A new extraction can produce different candidates or funding values.

That generated inspection sample is **never automatically used as an exclusion list**.
The `top5_values.xlsx` workbook, should be added separately at `private/` after careful 
**analytical cleaning**. The materials generated at this stage do not capture the manual
decision-making process followed by the authors.

## Where the results go

The default live run is `runs/report_check/`. Start with:

```text
results/ALL_TABLES.xlsx
results/tables/main_global.csv
results/tables/global_bands.csv
results/tables/headline_values.csv
results/figures/main_funder_dashboard.png
results/figures/index.html
results/quality_report.json
review/top5_values.xlsx
review/historical_list_reconciliation.xlsx
```

The main table and every other analytical table are available in CSV and XLSX. Individual
figures have a matching `_data.csv`. Raw numerator grants also have JSONL files. Each
run records query plans, category mappings, input/output hashes, dates and software
versions.

## API time, restart and reproducibility

The base denominator plan alone has 12 conjunctions × 36 years × 2 attribution modes ×
2 facets = **1,728 logical facet calls**, before numerator pages, metadata and partitions.
At the configured 2.2-second request pause, do not expect a quick notebook run. The API
currently limits requests to 30 per IP per minute; avoid concurrent extractions sharing
an IP. Successful calls are cached; failed calls are not treated as empty results.
[Official limits](https://help.dimensions.ai/en/articles/9785601).

After an interruption, repeat the same notebook/run name. For a fresh database retrieval,
change `RUN_NAME`, e.g. to `report_check_02`. A completed run is hash-checked and reused;
it is not silently refreshed. An extraction assembled through API calls is not an atomic
snapshot of the entire database. A fresh query need not match an earlier extract.

## Command-line alternative

```bash
python run_pipeline.py init
# Edit the local .env, or set DIMENSIONS_API_KEY in your environment.
python run_pipeline.py prepare-inputs
python run_pipeline.py doctor --live
python run_pipeline.py run --run-dir runs/report_check
```

Individual stages are `extract` and `analyse`. The latter does not contact the API.


The CI workflow uses fabricated data only and needs no API key. The software licence and
citation attribution must be confirmed by the repository owner (`h.nejadghorban@ucl.ac.uk` or `h.nejadghorban@gmail.com`). 
API data access and redistribution remain subject to the provider’s terms.
