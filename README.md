# EDMS Delete

Orchestrates EDMS file deletion: CDF data model instances, RAW state store rows, and files on the VM (target and DWG drop folders). Configuration is loaded from a CDF extraction pipeline (same pattern as `csv-extractor`).

## Prerequisites

1. Poetry and dependencies:

   ```powershell
   cd edms-delete
   poetry install
   ```

2. `.env` with CDF credentials (`CDF_PROJECT`, `CDF_CLUSTER`, `IDP_*`).

3. Extraction pipeline YAML with `rawTables`, `dataModelViews`, and `vmProperties` (see `EXTRACTION_PIPELINE_CONFIG_EXAMPLE.yaml`).

## Run locally

```powershell
cd edms-delete
poetry run python edms_delete/__main__.py .env <pipeline_external_id>
```

Or:

```powershell
poetry run edms_delete .env ep_src_indp_edms_<site>
```

## Build executable

```powershell
poetry run python build_exe.py
```

Output: `dist/edms_delete-<version>-<platform>.exe`

## Layout (mirrors csv-extractor)

| csv-extractor | edms-delete |
|---------------|-------------|
| `csv_extractor/__main__.py` | `edms_delete/__main__.py` |
| `utils/cdf_functions.py` | `utils/cdf_functions.py` |
| Strategy / processing modules | `runner.py`, `state.py`, `delete_operations.py` |
| Pipeline YAML in CDF | Same keys as legacy notebook script |

The monolithic `delete_working_script.py` at the repo root is kept for reference; new work should use this package.
