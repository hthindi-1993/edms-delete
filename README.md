# EDMS Delete Extractor — Deployment Guide

The EDMS Delete Extractor removes EDMS files that have been flagged for deletion. For every file whose metadata row has `Cognite_Delete = 1`, it removes that file from:

1. **Cognite Data Fusion (CDF) data model** — the `CogniteFile` instance
2. **CDF RAW file state store** — the upload extractor's state row for that file
3. **The VM** — the file in the target folder, and (for DWG/DGN files) the file in the DWG drop folder

Every run is recorded in CDF RAW so you can audit what was deleted and when.

> **Warning:** This tool permanently deletes CDF instances, RAW rows, and files on disk. Validate each site's configuration and metadata flags in a test environment before scheduling it in production.

---

## Contents

- [How multi-site deployment works](#how-multi-site-deployment-works)
- [Prerequisites](#prerequisites)
- [One-time setup on the VM](#one-time-setup-on-the-vm)
- [Adding a site](#adding-a-site)
- [Scheduling runs](#scheduling-runs)
- [Checking that a run worked](#checking-that-a-run-worked)
- [What gets written to CDF](#what-gets-written-to-cdf)
- [Troubleshooting](#troubleshooting)
- [Upgrading](#upgrading)
- [Building the executable (developers)](#building-the-executable-developers)

---

## How multi-site deployment works

One executable and one credentials file serve every site. What changes per site is an **extraction pipeline** in CDF, which holds that site's configuration.

```text
VM
├── edms_delete-0.1.0-win32.exe      one copy, shared by all sites
├── .env                             CDF credentials, shared by all sites
├── run_mtz.bat                      one small launcher per site
├── run_abc.bat
└── Logs\

CDF
├── Extraction pipeline ep_src_indp_edms_deletes_mtz   config for site "mtz"
└── Extraction pipeline ep_src_indp_edms_deletes_abc   config for site "abc"
```

Each run takes two arguments: the `.env` file and the extraction pipeline external ID for that site.

```powershell
edms_delete-0.1.0-win32.exe .env ep_src_indp_edms_deletes_mtz
```

The extractor reads the site's configuration from CDF at startup, so changing a site's tables or folders means editing its extraction pipeline config in CDF. You don't need to redeploy anything on the VM.

---

## Prerequisites

### On the VM

- Windows, with network access to your CDF cluster
- Local access to each site's target folder and DWG drop folder
- A Windows account to run the scheduled task, with **delete** rights on those folders

### In CDF

A service principal (app registration) whose group has these capabilities:

| Capability | Actions | Scope |
|------------|---------|-------|
| RAW | Read, Write | Metadata, file state-store, and deletion-extractor databases |
| Data model instances | Read, Write | Each site's instance space |
| Data models | Read | `cdf_cdm` (for the `CogniteFile` view) |
| Extraction pipelines | Read | The site pipelines |
| Extraction configs | Read | The site pipelines |
| Files | Read | Used for the connection check at startup |

The extractor creates the deletion-extractor database and its tables if they are missing. If your RAW access is scoped to specific databases, create that database up front so the extractor doesn't need permission to create it.

### Data that must already exist

- **Metadata RAW table**, one row per file, with these columns:

  | Column | Meaning |
  |--------|---------|
  | `primary_key` | Unique file identifier |
  | `File_Type_Short_Name` | File type, e.g. `PDF`, `DWG`, `DGN` |
  | `Cognite_Delete` | `1` means delete this file; any other value means keep it |
  | `Cognite_Ingest` | Carried into the delete tracker for reference |
  | `Cognite_Id` | Optional. File name in the target folder, if it differs from the default |

- **File state-store RAW table** written by the EDMS upload extractor
- **`CogniteFile` instances** in the site's instance space

---

## One-time setup on the VM

### 1. Create an install folder

For example `D:\Cognite_psaas\FileDeleteExtractor\`, and copy `edms_delete-0.1.0-win32.exe` into it.

### 2. Create the `.env` credentials file

Create a file named `.env` in the same folder:

```ini
CDF_PROJECT=<cdf-project-name>
CDF_CLUSTER=<cluster>            # e.g. westeurope-1
IDP_TENANT_ID=<azure-tenant-id>
IDP_CLIENT_ID=<app-registration-client-id>
IDP_CLIENT_SECRET=<app-registration-secret>
```

Restrict read access to this file. It contains a client secret.

---

## Adding a site

Repeat these steps for every site. The examples use the site code `mtz`; replace it with your own.

### 1. Create the extraction pipeline in CDF

In CDF, create an extraction pipeline with the external ID `ep_src_indp_edms_deletes_<site>`, for example `ep_src_indp_edms_deletes_mtz`. Open its **Configuration** and paste in the site config from the next step.

### 2. Fill in the site configuration

```yaml
rawTables:
  # Existing databases/tables
  rawDbMetadata: db_indp_edms_files_metadata_ref
  rawTableMetadata: tbl_indp_edms_files_metadata_mtz
  rawDbFileStateStore: db_edms_files_upload_ref
  rawTableFileStateStore: tbl_edms_files_mtz_state

  # Written by this extractor (created if missing)
  rawDbDeletionExtractor: db_edms_files_delete_extractor_ref
  rawTableDeleteTrackerTbl: tbl_edms_files_mtz_delete_event
  rawTableDeletionExtractorSummary: tbl_edms_files_mtz_delete_summary

dataModelViews:
  schemaSpace: cdf_cdm
  dmExternalId: CogniteCore
  viewName: CogniteFile
  version: v1
  instanceSpace: sp_dat_edms_files_mtz
  instanceExternalIdPrefix: edms_

vmProperties:
  targetFolderPath: D:\Data\edms\target\mtz\
  dwgDropFolderPath: D:\Data\edms\dwg-drop\mtz\

logger:
  console:
    level: INFO
  file:
    level: DEBUG
    path: D:\Cognite_psaas\FileDeleteExtractor\Logs\mtz\edms-deletes-mtz.log
    per_run: true
    retention: 30
```

#### What usually changes per site

| Setting | Shared or per site | Notes |
|---------|--------------------|-------|
| `rawDbMetadata`, `rawDbFileStateStore`, `rawDbDeletionExtractor` | Usually shared | One database can hold tables for many sites |
| `rawTableMetadata`, `rawTableFileStateStore` | Per site | Must match the tables the upload process already uses |
| `rawTableDeleteTrackerTbl`, `rawTableDeletionExtractorSummary` | Per site | Give each site its own tables |
| `instanceSpace` | Per site | Space holding that site's `CogniteFile` instances |
| `schemaSpace`, `dmExternalId`, `viewName`, `version` | Shared | Leave as shown unless you use a different view |
| `instanceExternalIdPrefix` | Usually shared | Must match the upload extractor's prefix |
| `targetFolderPath`, `dwgDropFolderPath` | Per site | See the path rules below |
| `logger.file.path` | Per site | Keeps each site's logs separate |

#### Path rules (important)

- **End both folder paths with a backslash** (`\`). File names are appended directly to the path.
- **`targetFolderPath` must match exactly what the upload extractor used.** The extractor finds each file's CDF instance by hashing its full target path. A different drive letter, casing, or missing backslash means instances won't be found or deleted.
- Use absolute paths everywhere, including the log path. Scheduled tasks don't always start in the install folder.

#### Logging options

| Setting | Meaning |
|---------|---------|
| `console.level` | Detail shown in the console (`INFO` is usual) |
| `file.level` | Detail written to the log file (`DEBUG` is usual) |
| `file.path` | Log file location. The folder is created if missing |
| `file.per_run` | `true` writes one log file per run, named with the run ID. `false` appends to a single file |
| `file.retention` | With `per_run: true`, deletes this site's log files older than N days. `0` keeps everything |

The whole `logger` section is optional. Without it, output goes to the console only.

### 3. Create a launcher for the site

Create `run_mtz.bat` in the install folder:

```bat
@echo off
cd /d D:\Cognite_psaas\FileDeleteExtractor
edms_delete-0.1.0-win32.exe .env ep_src_indp_edms_deletes_mtz
```

The `cd /d` line makes sure `.env` is found no matter how the batch file is started.

### 4. Test the site manually

Before scheduling, confirm in the metadata table that only the intended files have `Cognite_Delete = 1`. Then run the batch file from a command prompt:

```powershell
D:\Cognite_psaas\FileDeleteExtractor\run_mtz.bat
```

See [Checking that a run worked](#checking-that-a-run-worked) for what to expect.

---

## Scheduling runs

Create one Windows Task Scheduler task per site.

1. Open **Task Scheduler** and choose **Create Task**.
2. **General:** name it, e.g. `EDMS Delete - mtz`. Select **Run whether user is logged on or not** and use the account that has delete rights on the site folders.
3. **Triggers:** choose the schedule, e.g. daily at 02:00.
4. **Actions:** **Start a program**, with Program `D:\Cognite_psaas\FileDeleteExtractor\run_mtz.bat` and Start in `D:\Cognite_psaas\FileDeleteExtractor`.
5. **Settings:** set **If the task is already running** to **Do not start a new instance**.

Different sites can run at the same time, because each site writes to its own tables. Don't run two tasks for the **same** site at once.

---

## Checking that a run worked

### Console or log file

A healthy run looks like this:

```text
INFO - Cognite client authenticated.
INFO - Loading pipeline config: ep_src_indp_edms_deletes_mtz
INFO - edms_delete.runner - RunId: 07e752b8a0365018b238329255f6db27
INFO - edms_delete.runner - Built in-memory delete state for 36 flagged file(s).
INFO - edms_delete.delete_operations - Deleted 3 instances from view CogniteFile (space=cdf_cdm, version=v1).
INFO - edms_delete.delete_operations - Deleted 3 rows from db_edms_files_upload_ref.tbl_edms_files_mtz_state
INFO - edms_delete.delete_operations - Deleted 1 files from directory D:\Data\edms\dwg-drop\mtz\
INFO - edms_delete.delete_operations - Deleted 3 files from directory D:\Data\edms\target\mtz\
INFO - edms_delete.runner - Completed run 07e752b8...; wrote 3 tracker rows.
INFO - edms_delete.runner - Upserted run summary for 07e752b8... (runFinished=True) ...
```

`Deleted 0 ...` is normal when flagged files were already removed by an earlier run.

### In CDF RAW

- The **summary table** has a row for the run ID with `runFinished = True`.
- The **delete tracker** has one row per file that had something deleted this run.

A summary row stuck at `runFinished = False` means the run stopped partway. Check that run's log file.

A warning like `You are using version='7.x' of the SDK, however version='8.x' is available` is harmless.

---

## What gets written to CDF

Both tables live in `rawDbDeletionExtractor`. Each always keeps a placeholder row with the key `DummyRowKey` so the table's columns stay defined. Leave it in place; the extractor re-adds it if it's removed.

### Run summary (`rawTableDeletionExtractorSummary`)

One row per run, keyed by run ID. It's written when the run starts and updated when the run finishes.

| Column | Meaning |
|--------|---------|
| `RunId` | Unique ID for the run |
| `runstart` | When the run started (UTC) |
| `runend` | When the run finished (UTC). Empty while the run is in progress |
| `runFinished` | `False` while the run is in progress, `True` once it completes |

### Delete tracker (`rawTableDeleteTrackerTbl`)

One row per file **that had at least one item deleted** in a run, keyed by `{RunId}|{primary_key}`. Files that were flagged but had nothing left to delete aren't written.

| Column group | Columns |
|--------------|---------|
| File identity | `primary_key`, `sourceId`, `File_Type_Short_Name`, `Cognite_Id`, `Cognite_Ingest`, `external_id`, `target_filepath_defined`, `dwg_drop_path_defined` |
| Run | `RunId`, `runstart`, `runend`, `runFinished`, `CurrentDeleteFlag` |
| Found before delete | `DoesInstanceExistBeforeStatus`, `DoesStateStoreRecordExistBeforeStatus`, `DoesTargetFolderPathExistBeforeStatus`, `DoesDwgDropFolderPathExistBeforeStatus` |
| Still present after delete | Matching `Does*ExistAfterStatus` columns |
| When it was deleted | `DeletedInstanceTimestamp`, `DeletedStateStoreRecordTimestamp`, `DeletedTargetFolderPathTimestamp`, `DeletedDwgDropFolderPathTimestamp` |

A delete timestamp is set only when that item was confirmed gone after the delete. A row can have some timestamps set and others empty, e.g. when only the target file still existed.

The DWG drop-folder columns apply only to `DWG` and `DGN` files.

---

## Troubleshooting

| Symptom | Likely cause | Fix |
|---------|--------------|-----|
| `Missing required arguments` | Batch file is missing the `.env` path or pipeline ID | Use `edms_delete-0.1.0-win32.exe .env ep_src_indp_edms_deletes_<site>` |
| `Failed to initialize Cognite Client` or `Could not verify Cognite connection` | Wrong or missing `.env` values, expired secret, or no network to CDF | Check the `.env` values and that the task starts in the install folder |
| `Not able to retrieve pipeline config` | Wrong pipeline external ID, or missing extraction config read access | Check the ID and the service principal's capabilities |
| `Invalid pipeline configuration: 'rawTable...'` | A required key is missing or misspelled in the site config | Compare with the example in [Adding a site](#adding-a-site) |
| HTTP 403 errors | Service principal lacks access to a database, table, or space | Add the capability for that resource |
| Error creating the deletion-extractor database | RAW access is scoped and the database doesn't exist | Create the database in CDF first |
| Flagged files are never deleted | `Cognite_Delete` isn't exactly `1`, folder paths are wrong, or `targetFolderPath` doesn't match the upload extractor | Check the metadata values and [path rules](#path-rules-important) |
| `Deleted 0 files from directory ...` | Files already gone, or the task's account can't see or delete them | Check the folder contents and the account's permissions |
| No log file | `logger.file.path` missing or relative | Set an absolute log path in the site config |

---

## Upgrading

1. Pause the scheduled tasks.
2. Replace the exe in the install folder with the new version.
3. If the file name changed (e.g. `edms_delete-0.2.0-win32.exe`), update each `run_<site>.bat`.
4. Apply any new config keys from the release notes to each site's extraction pipeline in CDF.
5. Run one site manually, then re-enable the tasks.

`.env` and the site configs don't need to change unless the release notes say so.

---

## Building the executable (developers)

Requires Python 3.11–3.13 and Poetry.

```powershell
poetry install
poetry run python build_exe.py
```

The build writes `dist\edms_delete-<version>-win32.exe`. To run from source instead:

```powershell
poetry run python edms_delete/__main__.py .env ep_src_indp_edms_deletes_<site>
```

`EXTRACTION_PIPELINE_CONFIG_EXAMPLE.yaml` has a complete example site config.

### Code layout

| Module | Responsibility |
|--------|----------------|
| `edms_delete/__main__.py` | Command line: authenticate, load site config, start the run |
| `edms_delete/config.py` | Parses the extraction pipeline YAML |
| `edms_delete/runner.py` | Run order, tracker and summary writes |
| `edms_delete/state.py` | Works out which files and items to delete |
| `edms_delete/delete_operations.py` | Deletes CDF instances, state-store rows, and VM files |
| `edms_delete/tracker.py` | Builds delete lists and records results |
| `edms_delete/data_access.py` | RAW and data model reads, table creation |
| `edms_delete/paths.py` | File name and folder listing helpers |
| `edms_delete/utils/` | CDF client, logging, and time helpers |
