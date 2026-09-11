# Run and verify on your computer

## 1. Install a clean environment once

Use Python 3.11, 3.12 or 3.13. Open a terminal in the extracted project folder.

macOS/Linux:

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt -r requirements-notebook.txt -r requirements-dev.txt
python -m jupyterlab
```

Windows PowerShell:

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt -r requirements-notebook.txt -r requirements-dev.txt
.\.venv\Scripts\python.exe -m jupyterlab
```

Launching JupyterLab from this environment ensures that the notebook sees the installed
dependencies. On Windows, use `.\.venv\Scripts\python.exe` in place of `python` for the
remaining terminal commands. On macOS/Linux, reactivate the environment after reopening
a terminal. Avoid installing the requirements into an unrelated working environment.

For an existing Anaconda/Miniconda installation, an alternative is:

```bash
conda create -n lancet-funding python=3.12 -y
conda activate lancet-funding
python -m pip install -r requirements.txt -r requirements-notebook.txt -r requirements-dev.txt
python -m jupyterlab
```

Use one environment route, not both. Launch JupyterLab from the selected environment.


## 2. Rehearse offline or resume a real run

Open `notebooks/01_dimensions_to_outputs.ipynb`. For a quick no-key rehearsal, 
set `MODE = "demo"` in the main notebook and run all cells.
It uses fabricated grants/reference inputs under `runs/synthetic_demo/` and marks the figures
synthetic. Its success checks software execution, not real data or provider access.

After interruption, use the same run name to resume cached queries. After a completed run,
`02_reanalyse_existing_run.ipynb` recalculates results without API calls. To retrieve the
live database anew, set `RUN_NAME = "report_check_02"`. Different methods or reference
inputs require a new run. Do not rename/edit cached CSVs or `COMPLETE.json` to bypass checks.


## 3. Open the notebook and supply the key

Open `notebooks/01_dimensions_to_outputs.ipynb`. Keep `MODE = "live"` and leave the years
at 1990–2025. The notebook offers a masked key prompt. Alternatively run:

```bash
python run_pipeline.py init
```

Open the new `.env` file and replace `XXXXXXX` with your key. 
Save `.env` locally. The endpoint should be your authorised Dimensions base URL. 
The code permits only HTTPS hosts under `.dimensions.ai` and does not follow 
authentication redirects to other hosts.

## 4. Run all cells

Choose **Run → Run All Cells**.


## 5. Inspect the outputs

Open `runs/report_check/results/figures/index.html` in a browser and
`runs/report_check/results/ALL_TABLES.xlsx` in Excel. The main 1990–2025 figure is
`main_funder_dashboard.png`; `report_window_funder_dashboard.png` is the 2010–2024 crop.
Inspect `main_global.csv`, `global_bands.csv`, `headline_values.csv` and `quality_report.json`.
Check historical-list reconciliation and unmapped classifications before interpreting shares.

The ratio columns are fractions; chart axes display percentages. Bands are definition
sensitivity ranges, not statistical confidence intervals. Do not compare funder full-credit
counts to recipient unique global counts as though they were the same universe.


## Troubleshooting

**Key rejected / HTTP 401 or 403:** verify API access and the endpoint with your provider.
Do not share the key in an issue or screenshot. A normal Dimensions login is not equivalent
to Analytics API permission.

**Rate limit / HTTP 429:** allow the retry delay and stop other extractions sharing your
network. Successful earlier pages remain cached.

**Missing input workbook:** use the local bundle or place the two files at the exact paths
in `LOCAL_INPUTS.md`. Do not substitute the newly generated ranking for the historical list.

**Negative deduction, changed metadata, missing facet or count mismatch:** the code stops
rather than silently producing partial/invalid totals. Keep the failed run locally for
inspection, read the last query log entry and the reconciliation, and use a new directory
for a clean live retry. If a schema changed, revise the code and tests explicitly.

**Notebook import error:** ensure JupyterLab is running from the `.venv` environment. Use
`import sys; print(sys.executable)` to check the interpreter. Reinstall into that environment
and restart the kernel after dependency changes.

**Only some plots visible:** wait for the cell to finish, then refresh the local gallery.
It uses lazy loading. Check the final run summary rather than treating an intermediate
folder as a completed run. 
