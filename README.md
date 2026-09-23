# EDMS Delete

Orchestrates deletion of EDMS files across three layers:

1. **CDF data model instances** (`CogniteFile` nodes in an instance space)
2. **CDF RAW file state-store rows** (upload / extractor state)
3. **Files on the VM** (target folder and DWG/DGN drop folder)

It also records every run in RAW **delete** and **resurrect** tracker tables so operators can audit what was removed, what failed, and when a previously deleted file reappears with `Cognite_Delete ≠ 1`.

Configuration is loaded from a Cognite extraction pipeline YAML (same pattern as `csv-extractor`).

---

## Table of contents

- [What this script does](#what-this-script-does)
- [Prerequisites](#prerequisites)
- [Configuration](#configuration)
- [How a run works](#how-a-run-works)
- [Identifier and path derivation](#identifier-and-path-derivation)
- [Deletion eligibility](#deletion-eligibility)
- [Deletion operations](#deletion-operations)
- [Tracker columns and semantics](#tracker-columns-and-semantics)
- [Resurrection detection](#resurrection-detection)
- [Logging](#logging)
- [Run locally](#run-locally)
- [Build executable](#build-executable)
- [Package layout](#package-layout)
- [Operational notes](#operational-notes)

---

## What this script does

Given a metadata RAW table that flags files with `Cognite_Delete == 1`, each run:

1. Loads metadata, state-store, DM file instances, and existing trackers from CDF.
2. Cleans delete-tracker rows that are present in the delete tracker table with a null deletion timestamp for all sources. This is done to avoid our delete tracker from growing to infinte.
3. Builds a **preliminary delete state** for flagged rows and inserts it into the delete tracker.
preliminary delete state is essentially an insert of rows at the beginning of the run where cognite_delete =1 in the 
metadata table.
4. Detects **resurrected** files (present in the delete tracker history but currently `Cognite_Delete ≠ 1`) and records them in the resurrect tracker.
5. Deletes, for the current run only, resources that existed **before** this run:
   - DM file instances
   - state-store RAW rows
   - files under the DWG drop folder (DGN/DWG only)
   - files under the target folder
6. Writes after-status and deletion timestamps back to the delete tracker and marks the run finished.

Nothing is deleted unless the corresponding **before-status** flag is `True` for that resource on a current-run tracker row with `CurrentDeleteFlag == True`.

---

## Prerequisites

1. Poetry and dependencies:

   ```powershell
   cd edms-delete
   poetry install
   ```

2. A `.env` file with CDF credentials:

   | Variable | Purpose |
   |----------|---------|
   | `CDF_PROJECT` | CDF project name |
   | `CDF_CLUSTER` | Cluster (used in API base URL and OAuth scope) |
   | `IDP_TENANT_ID` | Azure AD tenant |
   | `IDP_CLIENT_ID` | App registration client ID |
   | `IDP_CLIENT_SECRET` | App registration secret |

3. An extraction pipeline in CDF whose config YAML includes `rawTables`, `dataModelViews`, and `vmProperties` (see `EXTRACTION_PIPELINE_CONFIG_EXAMPLE.yaml`).

4. Network access from the host/VM to CDF, and local filesystem access to the configured target and DWG drop folders.

---

## Configuration

Config is **not** read from a local YAML file at runtime. The CLI takes an extraction pipeline external ID; the script retrieves that pipeline’s config from CDF and parses it.

### Required sections

#### `rawTables`

| Key | Role |
|-----|------|
| `rawDbMain` | Database for metadata keep table and delete/resurrect trackers |
| `rawTableMetadataKeep` | Source of truth for which files are flagged for delete (`Cognite_Delete`) |
| `rawDbFileStateStore` | Database for the file upload state store |
| `rawTableFileStateStore` | State-store table (row key = instance external ID) |
| `rawTableDeleteTrackerTbl` | Per-run delete audit / work queue |
| `rawTableResurrectTrackerTbl` | Audit of files that reappear after a prior delete |

#### `dataModelViews`

| Key | Role |
|-----|------|
| `schemaSpace` | View schema space (e.g. `cdf_cdm`) |
| `dmExternalId` | Data model external ID (informational in config) |
| `viewName` | View used to list/delete file nodes (e.g. `CogniteFile`) |
| `version` | View version |
| `instanceSpace` | Space where file instances live |
| `instanceExternalIdPrefix` | Prefix prepended when deriving instance `external_id` |

#### `vmProperties`

| Key | Role |
|-----|------|
| `targetFolderPath` | Directory of ingested/target files (trailing path separator expected) |
| `dwgDropFolderPath` | Directory of CAD drop files for DGN/DWG (trailing path separator expected) |

#### `logger` (optional)

If omitted, logs go to the console at INFO.

| Key | Role |
|-----|------|
| `console.level` | Console log level |
| `file.level` | File log level |
| `file.path` | Log file path (prefer absolute for Task Scheduler / `.exe`) |
| `file.per_run` | `true`: one file per run (`stem_<run_id>.log`); `false`: append to a single file |
| `file.retention` | When `per_run` is true, delete matching log files older than N days; `0` keeps all |

See `EXTRACTION_PIPELINE_CONFIG_EXAMPLE.yaml` for a full example.

---

## How a run works

Entry point: `edms_delete.__main__:main` (or the packaged `.exe`).

```text
.env + pipeline_ext_id
        │
        ▼
Authenticate CogniteClient
        │
        ▼
Load extraction pipeline YAML → EdmsDeleteConfig
        │
        ▼
EdmsDeleteRunner.run()
```

### Step-by-step

#### 1. Run identity and logging

- `runstart` / clock values use UTC as `YYYY-MM-DD HH:MM:SS`.
- `RunId` is an MD5 hash of that timestamp string.
- If file logging with `per_run: true` is configured, the file handler is (re)attached using this `RunId`.

#### 2. Load source data

| Source | How it is loaded |
|--------|------------------|
| Metadata keep table | RAW rows; for tables whose name contains `tbl_indp_edms_files_metadata`, only `File_Type_Short_Name`, `Cognite_Delete`, `primary_key`, and optional `Cognite_Id` are fetched |
| File state store | Full RAW table |
| DM file instances | All nodes in `instanceSpace` for the configured view (`external_id`, `space`) |
| Delete tracker | Created with a dummy row if missing/empty |
| Resurrect tracker | Created with a dummy row if missing/empty |

#### 3. Clean incomplete delete-tracker rows

Any delete-tracker row where **all four** of these are null is deleted from RAW:

- `DeletedInstanceTimestamp`
- `DeletedStateStoreRecordTimestamp`
- `DeletedTargetFolderPathTimestamp`
- `DeletedDwgDropFolderPathTimestamp`

These are treated as abandoned / never-completed work items. If cleanup fails, the run continues.

#### 4. State generation and resurrection (only if metadata is non-empty)

See [Resurrection detection](#resurrection-detection) and [Identifier and path derivation](#identifier-and-path-derivation).

After inserting preliminary delete state, the delete tracker is reloaded and **filtered to the current `RunId`** for the deletion phase.

If metadata is empty, state generation is skipped; deletion still runs against whatever current-`RunId` rows already exist in the in-memory tracker snapshot (typically none for a fresh run).

#### 5. Build delete lists

For the current run, four lists are built (see [Deletion eligibility](#deletion-eligibility)):

| List | Filter column (must be boolean `True`) | Value returned |
|------|----------------------------------------|----------------|
| DM instances | `DoesInstanceExistBeforeStatus` | `external_id` |
| State store | `DoesStateStoreRecordExistBeforeStatus` | `external_id` |
| DWG drop files | `DoesDwgDropFolderPathExistBeforeStatus` | `dwg_drop_path_defined` |
| Target files | `DoesTargetFolderPathExistBeforeStatus` | `target_filepath_defined` |

#### 6. Perform deletions

Each list is passed to the matching operation (see [Deletion operations](#deletion-operations)). Operations are independent: a failure or empty list in one does not skip the others.

#### 7. Apply results to the tracker

For each resource type, current-run rows are updated with:

- `Does*ExistAfterStatus` — whether the identifier still exists after the operation
- `Deleted*Timestamp` — set only when the identifier appears in that operation’s `deleted_actual` set

#### 8. Finalize

For all current-run rows:

- `runend` = current UTC timestamp
- `runFinished` = `True`

Those rows are written back to the delete tracker via `insert_dataframe` (upsert by row key).

---

## Identifier and path derivation

For each metadata row considered during state generation:

| Field | Derivation |
|-------|------------|
| `sourceId` | `Cognite_Id` (trimmed) if present and non-empty; otherwise `{raw_row_key}.{type}` where DGN/DWG become `{key}.dgn.pdf` / `{key}.dwg.pdf`, and other types use the short name as the extension |
| `target_filepath_defined` | `{targetFolderPath}{sourceId}` |
| `external_id` | `{instanceExternalIdPrefix}` + SHA-1 hex digest of `target_filepath_defined` |
| `dwg_drop_path_defined` | For DGN/DWG only: `{dwgDropFolderPath}{raw_row_key}.{filetype}`; otherwise `null` |
| Tracker row `key` | `{RunId}\|{primary_key}` |

### Existence probes used for “before” status

| Resource | Considered present when |
|----------|-------------------------|
| DM instance | Left-join to instances yields a non-null `space` |
| State-store row | Left-join to state store yields a non-null `high` |
| Target file | `target_filepath_defined` appears in a scandir of the target folder |
| DWG drop file | For DGN/DWG, `dwg_drop_path_defined` appears in a scandir of the drop folder; otherwise `null` (N/A) |

Directory scans return **files only** (not subdirectories). If a configured folder does not exist, the scan returns an empty set (nothing is treated as present on disk).

---

## Deletion eligibility

A resource identifier is included in a delete list only when **all** of the following hold on the delete-tracker dataframe:

1. `CurrentDeleteFlag == True` (metadata had `Cognite_Delete == 1` when state was generated)
2. The relevant `Does*ExistBeforeStatus` column is the boolean `True` (string/`N/A` values do not qualify)
3. `RunId` equals the current run’s ID

So the script only attempts to delete what it observed as present at state-generation time for this run’s flagged rows.

---

## Deletion operations

### Data model instances

- Builds `NodeId(space=instanceSpace, external_id=...)` for each eligible ID.
- Calls `data_modeling.instances.delete`.
- Re-lists instances from the same view/space to verify.
- `deleted_actual` = IDs present before but not after; `failed_to_delete` = requested minus actually deleted.

### State-store RAW rows

- Deletes RAW keys matching the eligible external IDs.
- Re-retrieves the full table to verify.
- Same actual/failed accounting as instances.

### VM files (target and DWG drop)

- For each requested path, if it is an existing file, calls `os.remove`.
- Re-scans the directory to verify.
- `deleted_actual` = paths present in the directory before but not after.
- Paths that were never on disk, or could not be verified as gone, appear in `failed_to_delete` (warned in logs).

---

## Tracker columns and semantics

### Delete tracker (`rawTableDeleteTrackerTbl`)

Row key: `{RunId}|{primary_key}`.

| Column group | Meaning |
|--------------|---------|
| Identity / paths | `primary_key`, `sourceId`, `File_Type_Short_Name`, `external_id`, `target_filepath_defined`, `dwg_drop_path_defined`, `Cognite_Id` |
| Run control | `RunId`, `runstart`, `runend`, `runFinished`, `CurrentDeleteFlag` |
| Before status | `DoesInstanceExistBeforeStatus`, `DoesStateStoreRecordExistBeforeStatus`, `DoesTargetFolderPathExistBeforeStatus`, `DoesDwgDropFolderPathExistBeforeStatus` |
| After status | Matching `Does*ExistAfterStatus` columns filled after deletion |
| Delete timestamps | `DeletedInstanceTimestamp`, `DeletedStateStoreRecordTimestamp`, `DeletedTargetFolderPathTimestamp`, `DeletedDwgDropFolderPathTimestamp` — set only on successful verified deletes |

Preliminary insert sets after-status and delete-timestamp columns to `null` and `runFinished` to `False`. Finalize sets `runend` / `runFinished` and upserts the current-run rows.

### Resurrect tracker (`rawTableResurrectTrackerTbl`)

Used when files that were previously tracked for deletion are found again with `Cognite_Delete ≠ 1`. Rows capture detection-oriented timestamps (`InstanceDetectedTimestamp`, `StateStoreRecordDetectedTimestamp`, etc.) rather than deletion timestamps. See below.

### Dummy rows

If a tracker table is missing or empty, a single `DummyRowKey` row is inserted so the table has a known schema. Both the delete tracker and the resurrect tracker always keep that dummy row. If it is missing from a non-empty table, it is re-inserted on load. Empty-timestamp cleanup on the delete tracker also skips `DummyRowKey`.

---

## Resurrection detection

Resurrection is evaluated on every run that has metadata, **including runs with zero `Cognite_Delete == 1` rows** (delete-tracker insert is skipped in that case; resurrection still runs).

1. Optionally inserts a delete-state snapshot for `Cognite_Delete == 1` rows (skipped when none exist).
2. Builds a state snapshot for metadata where `Cognite_Delete ≠ 1`.
3. Computes the intersection of `primary_key` values between that snapshot and the delete tracker (string-normalized).
4. If the intersection is empty → logs “No files were resurrected” and leaves the resurrect tracker unchanged.
5. If non-empty → **deletes all existing resurrect-tracker rows except `DummyRowKey`**, then inserts the intersecting (resurrected) rows.

Clearing the table first keeps it as a snapshot of the current resurrected set instead of appending a new `{RunId}|{primary_key}` row every run or leaving stale rows for files that are no longer resurrected. `DummyRowKey` stays so the table never loses its schema row.

Interpretation: a `primary_key` that already appears in delete-tracker history and is again present in metadata **without** the delete flag is treated as a resurrection signal for auditing. Resurrection recording does not undelete anything; it only writes audit rows.

---

## Logging

- Console logging is always configured (default INFO unless overridden).
- Optional file logging is driven by the pipeline `logger` section.
- With `per_run: true`, the effective filename is `{stem}_{RunId}{suffix}` unless `{run_id}` is embedded in `path`.
- With `retention > 0` and `per_run: true`, older log files in the same folder whose names start with `{stem}_` are deleted after the retention window.

---

## Run locally

```powershell
cd edms-delete
poetry run python edms_delete/__main__.py .env <pipeline_external_id>
```

Or via the installed script entry point:

```powershell
poetry run edms_delete .env ep_src_indp_edms_<site>
```

Arguments:

1. Path to `.env` (CDF / IdP credentials)
2. Extraction pipeline external ID whose YAML configures this site’s tables and folders

On missing args, failed auth, or invalid pipeline config, the process exits with code `1`.

---

## Build executable

```powershell
poetry run python build_exe.py
```

This generates `edms_delete.spec` (via PyInstaller / `cogex`) and builds:

`dist/edms_delete-<version>-<platform>.exe`

Run the exe the same way as the module (pass `.env` path and pipeline external ID). Prefer absolute log and folder paths in the pipeline config when scheduling the exe (working directory may not be the install folder).

---

## Package layout

| Module | Responsibility |
|--------|----------------|
| `edms_delete/__main__.py` | CLI: auth, load pipeline config, invoke runner |
| `edms_delete/config.py` | `EdmsDeleteConfig` from pipeline YAML |
| `edms_delete/runner.py` | End-to-end orchestration |
| `edms_delete/state.py` | Build delete / non-delete state dataframes |
| `edms_delete/tracker.py` | Delete-list selection and result application |
| `edms_delete/delete_operations.py` | CDF instance, RAW, and VM file deletes |
| `edms_delete/data_access.py` | RAW / instance loads and tracker bootstrap |
| `edms_delete/paths.py` | `sourceId` and directory file listing helpers |
| `edms_delete/utils/cdf_functions.py` | Cognite client + pipeline YAML load |
| `edms_delete/utils/logging_setup.py` | Console / file logging from config |
| `edms_delete/utils/time_utils.py` | UTC timestamps and `RunId` |

Layout mirrors `csv-extractor` (pipeline-driven config, shared CDF client helpers).

---

## Operational notes

- **Idempotency:** Re-running after a successful run creates a new `RunId`. Rows already deleted will typically show before-status `False` and will not be re-queued. Incomplete rows (all delete timestamps null) are purged at the start of the next run.
- **Partial success:** Each of the four delete channels is verified independently. A row can have some `Deleted*Timestamp` values set and others still null.
- **DWG/DGN only for drop folder:** Non-CAD types never get a `dwg_drop_path_defined`; drop-folder before/after status stays N/A/`null`.
- **Permissions:** The service principal needs rights to read/write the configured RAW DBs/tables, list/delete DM instances in the instance space, and read the extraction pipeline config. The OS user running the process needs delete rights on the VM folders.
- **Trailing separators:** `targetFolderPath` and `dwgDropFolderPath` are concatenated directly with file names; include the trailing `\` (Windows) or `/` as used in your environment.
- **Safety:** This tool permanently deletes CDF instances, RAW rows, and local files. Validate pipeline config and metadata flags in a non-production project before scheduling in production.
