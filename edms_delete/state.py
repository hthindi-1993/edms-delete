import hashlib
import logging
from typing import Optional

import pandas as pd

from edms_delete.paths import get_files_in_directory, get_source_id
from edms_delete.utils.time_utils import get_current_time

logger = logging.getLogger(__name__)


def generate_state(
    rawtblmetadatadf: pd.DataFrame,
    dwgdropfolderpath: str,
    targetfolderpath: str,
    external_id_prefix: str,
    instance_tbl: pd.DataFrame,
    statestore_tbl: pd.DataFrame,
    nowtime: str,
    runid: str,
    cognite_delete_flag: bool,
) -> Optional[pd.DataFrame]:
    metadata_tbl = rawtblmetadatadf.copy()

    if cognite_delete_flag:
        metadata_tbl = metadata_tbl[metadata_tbl["Cognite_Delete"] == 1]
        logger.info("Filtering metadata for Cognite_Delete == 1 rows.")
        if metadata_tbl.empty:
            logger.info("No metadata available after filtering for Cognite_Delete == 1 rows.")
            return None
    else:
        metadata_tbl = metadata_tbl[metadata_tbl["Cognite_Delete"] != 1]
        if metadata_tbl.empty:
            logger.info("No metadata available after filtering for Cognite_Delete <> 1 rows.")
            return None

    required_columns = ["File_Type_Short_Name", "Cognite_Delete", "Cognite_Ingest", "primary_key"]
    has_cognite_id = "Cognite_Id" in metadata_tbl.columns

    if has_cognite_id:
        required_columns.append("Cognite_Id")

    metadata_tbl = metadata_tbl[required_columns].reset_index().rename(columns={"index": "key"})

    if has_cognite_id:
        metadata_tbl["sourceId"] = metadata_tbl.apply(
            lambda r: (
                str(r["Cognite_Id"]).strip()
                if pd.notna(r["Cognite_Id"]) and str(r["Cognite_Id"]).strip()
                else get_source_id(r["key"], r["File_Type_Short_Name"])
            ),
            axis=1,
        )
    else:
        metadata_tbl["sourceId"] = metadata_tbl.apply(
            lambda r: get_source_id(r["key"], r["File_Type_Short_Name"]),
            axis=1,
        )

    metadata_tbl["target_filepath"] = metadata_tbl.apply(
        lambda r: f"{targetfolderpath}{r['sourceId']}",
        axis=1,
    )
    metadata_tbl["external_id"] = metadata_tbl.apply(
        lambda r: external_id_prefix + hashlib.sha1(r["target_filepath"].encode()).hexdigest(),
        axis=1,
    )
    metadata_tbl["dwg_drop_path_defined"] = metadata_tbl.apply(
        lambda r: (
            f"{dwgdropfolderpath}{r['key']}.{r['File_Type_Short_Name'].lower()}"
            if r["File_Type_Short_Name"].lower() in ("dgn", "dwg")
            else None
        ),
        axis=1,
    )
    metadata_tbl.set_index("external_id", inplace=True)

    instance_rows_loaded = instance_tbl.reset_index()
    instance_rows_loaded.drop(columns=["key"], inplace=True, errors="ignore")
    if "externalId" in instance_rows_loaded.columns:
        instance_rows_loaded = instance_rows_loaded.rename(columns={"externalId": "external_id"})
    instance_rows_loaded.set_index("external_id", inplace=True)

    statestore_rows_loaded = statestore_tbl.reset_index()
    statestore_rows_loaded.rename(columns={"index": "external_id"}, inplace=True)
    statestore_rows_loaded.set_index("external_id", inplace=True)

    target_files = get_files_in_directory(targetfolderpath)
    dwgdrop_files = get_files_in_directory(dwgdropfolderpath)

    metadata_tbl = metadata_tbl.join(instance_rows_loaded, how="left")
    metadata_tbl = metadata_tbl.join(statestore_rows_loaded, how="left")
    metadata_tbl = metadata_tbl.reset_index()
    metadata_tbl.set_index("key", inplace=True)

    metadata_tbl["target_filepath_Exist"] = metadata_tbl["target_filepath"].isin(target_files)
    metadata_tbl["dwg_drop_path_Exist"] = metadata_tbl.apply(
        lambda r: (
            r["dwg_drop_path_defined"] in dwgdrop_files
            if r["File_Type_Short_Name"].lower() in ("dgn", "dwg")
            else None
        ),
        axis=1,
    )

    metadata_tbl["runstart"] = nowtime
    metadata_tbl["RunId"] = runid

    if cognite_delete_flag:
        metadata_tbl["DoesInstanceExistBeforeStatus"] = metadata_tbl.apply(
            lambda r: True if pd.notnull(r["space"]) else False,
            axis=1,
        )
        metadata_tbl["DoesStateStoreRecordExistBeforeStatus"] = metadata_tbl.apply(
            lambda r: True if pd.notnull(r["high"]) else False,
            axis=1,
        )
        metadata_tbl["DoesTargetFolderPathExistBeforeStatus"] = metadata_tbl.apply(
            lambda r: r["target_filepath_Exist"],
            axis=1,
        )
        metadata_tbl["DoesDwgDropFolderPathExistBeforeStatus"] = metadata_tbl.apply(
            lambda r: r["dwg_drop_path_Exist"] if pd.notnull(r["dwg_drop_path_Exist"]) else None,
            axis=1,
        )
        metadata_tbl["runend"] = None
        metadata_tbl["runFinished"] = False
        metadata_tbl["DoesInstanceExistAfterStatus"] = None
        metadata_tbl["DoesStateStoreRecordExistAfterStatus"] = None
        metadata_tbl["DoesTargetFolderPathExistAfterStatus"] = None
        metadata_tbl["DoesDwgDropFolderPathExistAfterStatus"] = None
        metadata_tbl["DeletedStateStoreRecordTimestamp"] = None
        metadata_tbl["DeletedTargetFolderPathTimestamp"] = None
        metadata_tbl["DeletedDwgDropFolderPathTimestamp"] = None
        metadata_tbl["DeletedInstanceTimestamp"] = None
    else:
        metadata_tbl["runend"] = get_current_time()
        metadata_tbl["runFinished"] = True
        metadata_tbl["InstanceDetectedTimestamp"] = metadata_tbl.apply(
            lambda r: get_current_time() if pd.notnull(r["space"]) else None,
            axis=1,
        )
        metadata_tbl["StateStoreRecordDetectedTimestamp"] = metadata_tbl.apply(
            lambda r: get_current_time() if pd.notnull(r["high"]) else None,
            axis=1,
        )
        metadata_tbl["TargetFolderPathDetectedTimestamp"] = metadata_tbl.apply(
            lambda r: get_current_time() if r["target_filepath_Exist"] else None,
            axis=1,
        )
        metadata_tbl["DwgDropFolderPathDetectedTimestamp"] = metadata_tbl.apply(
            lambda r: get_current_time() if r["dwg_drop_path_Exist"] else None,
            axis=1,
        )

    metadata_tbl["CurrentDeleteFlag"] = metadata_tbl.apply(
        lambda r: True if r["Cognite_Delete"] == 1 else False,
        axis=1,
    )

    metadata_tbl.reset_index(inplace=True)
    metadata_tbl.rename(columns={"target_filepath": "target_filepath_defined"}, inplace=True)
    metadata_tbl.drop(
        columns=[
            "key",
            "space",
            "high",
            "low",
            "Cognite_Delete",
            "target_filepath_Exist",
            "dwg_drop_path_Exist",
        ],
        inplace=True,
        errors="ignore",
    )
    metadata_tbl["key"] = metadata_tbl.apply(lambda r: runid + "|" + r["primary_key"], axis=1)
    metadata_tbl.set_index("key", inplace=True)

    return metadata_tbl
