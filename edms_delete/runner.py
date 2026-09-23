import logging
from typing import Any

import pandas as pd
from cognite.client import CogniteClient

from edms_delete.config import EdmsDeleteConfig
from edms_delete.data_access import (
    get_delete_state_tracker_tbl,
    load_instances_files,
    load_raw_tbl,
)
from edms_delete.delete_operations import (
    delete_file_instances_operation,
    delete_statestore_operation,
    delete_vm_files_operation,
)
from edms_delete.state import generate_state
from edms_delete.tracker import apply_deletion_results, get_delete_list_from_source
from edms_delete.utils.logging_setup import configure_logging
from edms_delete.utils.time_utils import create_run_id, get_current_time

logger = logging.getLogger(__name__)


class EdmsDeleteRunner:
    """Orchestrates delete tracking, resurrection detection, deletions, and tracker updates."""

    def __init__(self, client: CogniteClient, config: EdmsDeleteConfig) -> None:
        self.client = client
        self.config = config

    def run(self) -> None:
        nowtime = get_current_time()
        runid = create_run_id(nowtime)

        file_cfg = self.config.logger.file
        if file_cfg and file_cfg.enabled and file_cfg.per_run:
            configure_logging(self.config.logger, run_id=runid)

        logger.info("RunId: %s", runid)

        metadata_tbl_df = load_raw_tbl(
            self.client,
            self.config.raw_db_main,
            self.config.raw_table_metadata_keep,
        )
        statestore_tbl_df = load_raw_tbl(
            self.client,
            self.config.raw_db_file_state_store,
            self.config.raw_table_file_state_store,
        )
        cognite_file_tbl_df = load_instances_files(
            self.client,
            self.config.dm_instance_space,
            self.config.dm_schema_space,
            self.config.dm_view_name,
            self.config.dm_version,
        )

        delete_tracker_tbl_df = get_delete_state_tracker_tbl(
            self.client,
            self.config.raw_db_main,
            self.config.raw_table_delete_tracker,
            True,
        )
        resurrect_tracker_tbl_df = get_delete_state_tracker_tbl(
            self.client,
            self.config.raw_db_main,
            self.config.raw_table_resurrect_tracker,
            False,
        )

        if not metadata_tbl_df.empty:
            self._run_resurrection(
                metadata_tbl_df=metadata_tbl_df,
                statestore_tbl_df=statestore_tbl_df,
                cognite_file_tbl_df=cognite_file_tbl_df,
                delete_tracker_tbl_df=delete_tracker_tbl_df,
                resurrect_tracker_tbl_df=resurrect_tracker_tbl_df,
                nowtime=nowtime,
                runid=runid,
            )

        delete_tracker_tbl_df = self._cleanup_empty_timestamp_rows(delete_tracker_tbl_df)

        if not metadata_tbl_df.empty:
            self._insert_preliminary_delete_state(
                metadata_tbl_df=metadata_tbl_df,
                statestore_tbl_df=statestore_tbl_df,
                cognite_file_tbl_df=cognite_file_tbl_df,
                delete_tracker_tbl_df=delete_tracker_tbl_df,
                nowtime=nowtime,
                runid=runid,
            )
            delete_tracker_tbl_df = load_raw_tbl(
                self.client,
                self.config.raw_db_main,
                self.config.raw_table_delete_tracker,
            )
            delete_tracker_tbl_df = delete_tracker_tbl_df[delete_tracker_tbl_df["RunId"] == runid]

        delete_list_instances = get_delete_list_from_source(
            delete_tracker_tbl_df,
            "DoesInstanceExistBeforeStatus",
            "external_id",
            runid,
        )
        delete_list_statestore = get_delete_list_from_source(
            delete_tracker_tbl_df,
            "DoesStateStoreRecordExistBeforeStatus",
            "external_id",
            runid,
        )
        delete_list_dwgdropfolder = get_delete_list_from_source(
            delete_tracker_tbl_df,
            "DoesDwgDropFolderPathExistBeforeStatus",
            "dwg_drop_path_defined",
            runid,
        )
        delete_list_targetfolder = get_delete_list_from_source(
            delete_tracker_tbl_df,
            "DoesTargetFolderPathExistBeforeStatus",
            "target_filepath_defined",
            runid,
        )

        delete_instance_results = delete_file_instances_operation(
            self.client,
            self.config.dm_instance_space,
            self.config.dm_schema_space,
            self.config.dm_version,
            self.config.dm_view_name,
            cognite_file_tbl_df,
            delete_list_instances,
        )
        delete_statestore_results = delete_statestore_operation(
            self.client,
            self.config.raw_db_file_state_store,
            self.config.raw_table_file_state_store,
            statestore_tbl_df,
            delete_list_statestore,
        )
        delete_dwgdropfiles_results = delete_vm_files_operation(
            delete_list_dwgdropfolder,
            self.config.dwg_drop_folder_path,
        )
        delete_targetfiles_results = delete_vm_files_operation(
            delete_list_targetfolder,
            self.config.target_folder_path,
        )

        delete_tracker_tbl_df = self._apply_all_deletion_results(
            delete_tracker_tbl_df=delete_tracker_tbl_df,
            runid=runid,
            delete_instance_results=delete_instance_results,
            delete_statestore_results=delete_statestore_results,
            delete_dwgdropfiles_results=delete_dwgdropfiles_results,
            delete_targetfiles_results=delete_targetfiles_results,
        )

        self._finalize_run(delete_tracker_tbl_df, runid)

    def _run_resurrection(
        self,
        metadata_tbl_df: pd.DataFrame,
        statestore_tbl_df: pd.DataFrame,
        cognite_file_tbl_df: pd.DataFrame,
        delete_tracker_tbl_df: pd.DataFrame,
        resurrect_tracker_tbl_df: pd.DataFrame,
        nowtime: str,
        runid: str,
    ) -> None:
        generate_nondeleted_state_results = generate_state(
            metadata_tbl_df,
            self.config.dwg_drop_folder_path,
            self.config.target_folder_path,
            self.config.dm_instance_external_id_prefix,
            cognite_file_tbl_df,
            statestore_tbl_df,
            nowtime,
            runid,
            False,
        )

        if generate_nondeleted_state_results is None:
            logger.info("No Cognite_Delete <> 1 rows; nothing to evaluate for resurrection.")
            return

        nondeleted_pks = set(generate_nondeleted_state_results["primary_key"].astype(str))
        delete_tracker_pks = {
            pk
            for pk in delete_tracker_tbl_df["primary_key"].astype(str)
            if pk not in {"DummyRowKey", "N/A"}
        }
        resurrected_files_pk = list(nondeleted_pks & delete_tracker_pks)

        if not resurrected_files_pk:
            logger.info("No files were resurrected.")
            return

        resurrected_df = generate_nondeleted_state_results[
            generate_nondeleted_state_results["primary_key"].astype(str).isin(resurrected_files_pk)
        ].copy()

        existing_keys = [
            key for key in resurrect_tracker_tbl_df.index.astype(str).tolist() if key != "DummyRowKey"
        ]
        if existing_keys:
            logger.info(
                "Clearing %s existing resurrect-tracker row(s) before inserting %s resurrected file(s).",
                len(existing_keys),
                len(resurrected_df),
            )
            self.client.raw.rows.delete(
                db_name=self.config.raw_db_main,
                table_name=self.config.raw_table_resurrect_tracker,
                key=existing_keys,
            )

        logger.info("Resurrected files count: %s", len(resurrected_df))
        self.client.raw.rows.insert_dataframe(
            db_name=self.config.raw_db_main,
            table_name=self.config.raw_table_resurrect_tracker,
            dataframe=resurrected_df,
        )

    def _cleanup_empty_timestamp_rows(self, delete_tracker_tbl_df: pd.DataFrame) -> pd.DataFrame:
        delete_empty_timestamp_records_list = [
            key
            for key in delete_tracker_tbl_df[
                delete_tracker_tbl_df["DeletedInstanceTimestamp"].isna()
                & delete_tracker_tbl_df["DeletedStateStoreRecordTimestamp"].isna()
                & delete_tracker_tbl_df["DeletedTargetFolderPathTimestamp"].isna()
                & delete_tracker_tbl_df["DeletedDwgDropFolderPathTimestamp"].isna()
            ].index.astype(str).tolist()
            if key != "DummyRowKey"
        ]

        if len(delete_empty_timestamp_records_list) == 0:
            return delete_tracker_tbl_df

        logger.info("Deleting records with empty timestamps:")
        rows_before_delete_tracker_df = len(delete_tracker_tbl_df)
        self.client.raw.rows.delete(
            self.config.raw_db_main,
            self.config.raw_table_delete_tracker,
            delete_empty_timestamp_records_list,
        )
        delete_tracker_tbl_df = load_raw_tbl(
            self.client, self.config.raw_db_main, self.config.raw_table_delete_tracker
        )
        rows_after_delete_tracker_df = len(delete_tracker_tbl_df)
        if rows_after_delete_tracker_df < rows_before_delete_tracker_df:
            logger.info(
                "Deleted %s records with empty timestamps.",
                rows_before_delete_tracker_df - rows_after_delete_tracker_df,
            )
        else:
            logger.info("Failed to delete records with empty timestamps.")
            logger.info("Proceeding with rest of workflow")
        return delete_tracker_tbl_df

    def _insert_preliminary_delete_state(
        self,
        metadata_tbl_df: pd.DataFrame,
        statestore_tbl_df: pd.DataFrame,
        cognite_file_tbl_df: pd.DataFrame,
        delete_tracker_tbl_df: pd.DataFrame,
        nowtime: str,
        runid: str,
    ) -> None:
        generate_state_results = generate_state(
            metadata_tbl_df,
            self.config.dwg_drop_folder_path,
            self.config.target_folder_path,
            self.config.dm_instance_external_id_prefix,
            cognite_file_tbl_df,
            statestore_tbl_df,
            nowtime,
            runid,
            True,
        )
        if generate_state_results is None:
            logger.info("No Cognite_Delete == 1 rows; skipping delete-tracker insert.")
            return

        logger.info(
            "Inserting preliminary state into %s.%s (%s rows in tracker before insert).",
            self.config.raw_db_main,
            self.config.raw_table_delete_tracker,
            len(delete_tracker_tbl_df),
        )
        self.client.raw.rows.insert_dataframe(
            db_name=self.config.raw_db_main,
            table_name=self.config.raw_table_delete_tracker,
            dataframe=generate_state_results,
        )

    def _apply_all_deletion_results(
        self,
        delete_tracker_tbl_df: pd.DataFrame,
        runid: str,
        delete_instance_results: dict[str, Any],
        delete_statestore_results: dict[str, Any],
        delete_dwgdropfiles_results: dict[str, Any],
        delete_targetfiles_results: dict[str, Any],
    ) -> pd.DataFrame:
        instance_after_df = delete_instance_results["after_df"]["data"]
        instance_after_ids = (
            instance_after_df["external_id"].dropna().tolist() if "external_id" in instance_after_df.columns else []
        )

        statestore_after_ids = delete_statestore_results["after_df"]["data"].index.dropna().tolist()
        dwgdrop_after_ids = delete_dwgdropfiles_results["after_df"]["data"].dropna().tolist()
        target_after_ids = delete_targetfiles_results["after_df"]["data"].dropna().tolist()

        deletion_operations = [
            {
                "results": delete_instance_results,
                "identifier_col": "external_id",
                "exists_after_col": "DoesInstanceExistAfterStatus",
                "timestamp_col": "DeletedInstanceTimestamp",
                "after_identifiers": instance_after_ids,
            },
            {
                "results": delete_statestore_results,
                "identifier_col": "external_id",
                "exists_after_col": "DoesStateStoreRecordExistAfterStatus",
                "timestamp_col": "DeletedStateStoreRecordTimestamp",
                "after_identifiers": statestore_after_ids,
            },
            {
                "results": delete_dwgdropfiles_results,
                "identifier_col": "dwg_drop_path_defined",
                "exists_after_col": "DoesDwgDropFolderPathExistAfterStatus",
                "timestamp_col": "DeletedDwgDropFolderPathTimestamp",
                "after_identifiers": dwgdrop_after_ids,
            },
            {
                "results": delete_targetfiles_results,
                "identifier_col": "target_filepath_defined",
                "exists_after_col": "DoesTargetFolderPathExistAfterStatus",
                "timestamp_col": "DeletedTargetFolderPathTimestamp",
                "after_identifiers": target_after_ids,
            },
        ]

        for operation in deletion_operations:
            delete_tracker_tbl_df = apply_deletion_results(
                df=delete_tracker_tbl_df,
                delete_object_results=operation["results"],
                identifier_col=operation["identifier_col"],
                exists_after_col=operation["exists_after_col"],
                delete_timestamp_col=operation["timestamp_col"],
                after_identifiers=operation["after_identifiers"],
                run_id=runid,
            )

        return delete_tracker_tbl_df

    def _finalize_run(self, delete_tracker_tbl_df: pd.DataFrame, runid: str) -> None:
        current_run_mask = delete_tracker_tbl_df["RunId"].eq(runid)

        if "runend" not in delete_tracker_tbl_df.columns:
            delete_tracker_tbl_df["runend"] = pd.Series(None, index=delete_tracker_tbl_df.index, dtype="object")
        else:
            delete_tracker_tbl_df["runend"] = (
                delete_tracker_tbl_df["runend"].astype("object").where(delete_tracker_tbl_df["runend"].notna(), None)
            )

        if "runFinished" not in delete_tracker_tbl_df.columns:
            delete_tracker_tbl_df["runFinished"] = pd.Series(None, index=delete_tracker_tbl_df.index, dtype="object")
        else:
            delete_tracker_tbl_df["runFinished"] = (
                delete_tracker_tbl_df["runFinished"]
                .astype("object")
                .where(delete_tracker_tbl_df["runFinished"].notna(), None)
            )

        run_end_timestamp = get_current_time()
        delete_tracker_tbl_df.loc[current_run_mask, "runend"] = run_end_timestamp
        delete_tracker_tbl_df.loc[current_run_mask, "runFinished"] = True

        current_run_df = delete_tracker_tbl_df.loc[current_run_mask].copy()
        current_run_df = current_run_df.astype("object").where(current_run_df.notna(), None)

        self.client.raw.rows.insert_dataframe(
            db_name=self.config.raw_db_main,
            table_name=self.config.raw_table_delete_tracker,
            dataframe=current_run_df,
        )
        logger.info("Completed run %s; wrote %s tracker rows.", runid, len(current_run_df))
