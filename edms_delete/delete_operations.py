import logging
import os
from datetime import UTC, datetime
from typing import Any

import pandas as pd
from cognite.client import CogniteClient
from cognite.client.data_classes.data_modeling import NodeId

from edms_delete.data_access import load_instances_files
from edms_delete.paths import get_files_in_directory

logger = logging.getLogger(__name__)


def delete_file_instances_operation(
    client: CogniteClient,
    dm_instance_space: str,
    dm_schema_space: str,
    dm_version: str,
    dm_view_name: str,
    instance_tbl: pd.DataFrame,
    delete_list: list[str],
) -> dict[str, Any]:
    before_df = instance_tbl

    nodes_to_delete = [NodeId(space=dm_instance_space, external_id=external_id) for external_id in delete_list]

    if nodes_to_delete:
        client.data_modeling.instances.delete(nodes=nodes_to_delete)

    after_df = load_instances_files(
        client,
        dm_instance_space,
        dm_schema_space,
        dm_view_name,
        dm_version,
    )

    before_ids = before_df["external_id"].dropna().tolist() if "external_id" in before_df.columns else []
    after_ids = after_df["external_id"].dropna().tolist() if "external_id" in after_df.columns else []

    logger.info(
        "Deleted %s instances from view %s (space=%s, version=%s).",
        len(before_ids) - len(after_ids),
        dm_view_name,
        dm_schema_space,
        dm_version,
    )

    actually_deleted_instances = list(set(before_ids) - set(after_ids))
    failed_to_delete_instances = list(set(delete_list) - set(actually_deleted_instances))

    return {
        "before_df": {"data": before_df, "len": len(before_df)},
        "after_df": {"data": after_df, "len": len(after_df)},
        "deleted_input": delete_list,
        "deleted_actual": actually_deleted_instances,
        "delete_timestamp": datetime.now(UTC).strftime("%Y-%m-%d %H:%M:%S"),
        "failed_to_delete": failed_to_delete_instances,
    }


def delete_statestore_operation(
    client: CogniteClient,
    dbname: str,
    tablename: str,
    statestore_tbl: pd.DataFrame,
    delete_list: list[str],
) -> dict[str, Any]:
    before_df = statestore_tbl

    if delete_list:
        client.raw.rows.delete(db_name=dbname, table_name=tablename, key=delete_list)

    after_df = client.raw.rows.retrieve_dataframe(db_name=dbname, table_name=tablename, limit=None)

    logger.info("Deleted %s rows from %s.%s", len(before_df) - len(after_df), dbname, tablename)

    actually_deleted_statestore_rows = list(set(before_df.index.tolist()) - set(after_df.index.tolist()))
    failed_to_delete_statestore_rows = list(set(delete_list) - set(actually_deleted_statestore_rows))

    return {
        "before_df": {"data": before_df, "len": len(before_df)},
        "after_df": {"data": after_df, "len": len(after_df)},
        "deleted_input": delete_list,
        "deleted_actual": actually_deleted_statestore_rows,
        "delete_timestamp": datetime.now(UTC).strftime("%Y-%m-%d %H:%M:%S"),
        "failed_to_delete": failed_to_delete_statestore_rows,
    }


def delete_vm_files_operation(delete_list: list[str], directory_path: str) -> dict[str, Any]:
    before_files = set(get_files_in_directory(directory_path))
    requested_files = set(delete_list)

    for file_path in requested_files:
        if os.path.isfile(file_path):
            os.remove(file_path)

    after_files = set(get_files_in_directory(directory_path))
    actually_deleted_files = before_files - after_files
    failed_to_delete_files = requested_files - actually_deleted_files

    logger.info("Deleted %s files from directory %s", len(actually_deleted_files), directory_path)

    if failed_to_delete_files:
        logger.warning("Failed to delete or could not verify %s files", len(failed_to_delete_files))

    return {
        "before_df": {"data": pd.Series(sorted(before_files), dtype="object"), "len": len(before_files)},
        "after_df": {"data": pd.Series(sorted(after_files), dtype="object"), "len": len(after_files)},
        "deleted_input": list(requested_files),
        "deleted_actual": list(actually_deleted_files),
        "delete_timestamp": datetime.now(UTC).strftime("%Y-%m-%d %H:%M:%S"),
        "failed_to_delete": list(failed_to_delete_files),
    }
