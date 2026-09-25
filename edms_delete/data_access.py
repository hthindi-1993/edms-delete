import logging
from typing import Any

import pandas as pd
from cognite.client import CogniteClient
from cognite.client.data_classes.data_modeling import ViewId
from cognite.client.exceptions import CogniteAPIError

logger = logging.getLogger(__name__)


def ensure_raw_database(client: CogniteClient, db_name: str) -> None:
    existing_names = {database.name for database in client.raw.databases.list(limit=None)}
    if db_name in existing_names:
        logger.info("Database %s already exists.", db_name)
        return

    logger.info("Attempting to create database %s", db_name)
    try:
        created = client.raw.databases.create(db_name)
    except CogniteAPIError as exc:
        if exc.code == 409:
            logger.info("Database %s already exists.", db_name)
            return
        logger.error("Failed to create database %s: %s", db_name, exc)
        raise

    created_time = getattr(created, "created_time", None)
    if created_time and hasattr(created_time, "strftime"):
        logger.info(
            "Database %s was successfully created on %s UTC",
            db_name,
            created_time.strftime("%Y-%m-%d %H:%M:%S"),
        )
    else:
        logger.info("Database %s was successfully created.", db_name)


def load_yaml_config(client: CogniteClient, pipeline_name: str) -> dict[str, Any]:
    import yaml

    res = client.extraction_pipelines.config.retrieve(pipeline_name)
    return yaml.safe_load(res.config)


def load_raw_tbl(
    client: CogniteClient,
    raw_db_name: str,
    raw_tbl_name: str,
    last_updated_time: bool = False,
) -> pd.DataFrame:
    if "tbl_indp_edms_files_metadata" in raw_tbl_name:
        sample_row = client.raw.rows.retrieve_dataframe(
            db_name=raw_db_name,
            table_name=raw_tbl_name,
            limit=1,
            partitions=4,
            last_updated_time_in_index=last_updated_time,
        )
        if "Cognite_Id" in sample_row.columns.tolist():
            columns_to_fetch = ["File_Type_Short_Name", "Cognite_Delete", "Cognite_Ingest", "Cognite_Id", "primary_key"]
            tbl = client.raw.rows.retrieve_dataframe(
                db_name=raw_db_name,
                table_name=raw_tbl_name,
                limit=None,
                partitions=4,
                columns=columns_to_fetch,
                last_updated_time_in_index=last_updated_time,
            )
        else:
            columns_to_fetch = ["File_Type_Short_Name", "Cognite_Delete", "Cognite_Ingest", "primary_key"]
            tbl = client.raw.rows.retrieve_dataframe(
                db_name=raw_db_name,
                table_name=raw_tbl_name,
                limit=None,
                partitions=4,
                columns=columns_to_fetch,
                last_updated_time_in_index=last_updated_time,
            )
            tbl["Cognite_Id"] = None
    else:
        tbl = client.raw.rows.retrieve_dataframe(
            db_name=raw_db_name,
            table_name=raw_tbl_name,
            limit=None,
            partitions=4,
            last_updated_time_in_index=last_updated_time,
        )

    if last_updated_time:
        tbl = tbl.reset_index("last_updated_time")
    return tbl


def load_instances_files(
    client: CogniteClient,
    dm_instance_space: str,
    dm_schema_space: str,
    dm_view_name: str,
    dm_version: str,
) -> pd.DataFrame:
    empty = pd.DataFrame(columns=["external_id", "space"])
    empty["key"] = pd.Series(dtype="object")
    empty = empty.set_index("key")

    instances_list = client.data_modeling.instances.list(
        instance_type="node",
        space=dm_instance_space,
        sources=[ViewId(space=dm_schema_space, external_id=dm_view_name, version=dm_version)],
        limit=None,
    )
    if len(instances_list) == 0:
        return empty

    instances = instances_list.to_pandas()
    if "externalId" in instances.columns and "external_id" not in instances.columns:
        instances = instances.rename(columns={"externalId": "external_id"})

    if not {"external_id", "space"}.issubset(instances.columns):
        logger.warning(
            "Instance list for space %s / view %s returned unexpected columns: %s",
            dm_instance_space,
            dm_view_name,
            list(instances.columns),
        )
        return empty

    instances = instances[["external_id", "space"]].copy()
    instances["key"] = instances["external_id"]
    return instances.set_index("key")


def ensure_raw_table_with_dummy_row(
    client: CogniteClient,
    db_name: str,
    table_name: str,
    dummy_values: dict[str, str],
) -> pd.DataFrame:
    tbls_available = [table.name for table in client.raw.tables.list(db_name=db_name, limit=None)]
    dummy_df = pd.DataFrame([dummy_values]).set_index("key")

    if table_name not in tbls_available:
        client.raw.tables.create(db_name=db_name, name=table_name)
        logger.info("Table '%s' created in database '%s'.", table_name, db_name)
        client.raw.rows.insert_dataframe(db_name=db_name, table_name=table_name, dataframe=dummy_df)
        return client.raw.rows.retrieve_dataframe(db_name=db_name, table_name=table_name, limit=None)

    existing = client.raw.rows.retrieve_dataframe(db_name=db_name, table_name=table_name, limit=None)
    if len(existing) == 0:
        logger.info("Table '%s' exists but is empty in database '%s'. Inserting dummy row.", table_name, db_name)
        client.raw.rows.insert_dataframe(db_name=db_name, table_name=table_name, dataframe=dummy_df)
        return client.raw.rows.retrieve_dataframe(db_name=db_name, table_name=table_name, limit=None)

    logger.info("Table '%s' already exists in database '%s'.", table_name, db_name)

    if not existing.index.astype(str).isin(["DummyRowKey"]).any():
        logger.info("Dummy row missing from '%s'; re-inserting.", table_name)
        client.raw.rows.insert_dataframe(db_name=db_name, table_name=table_name, dataframe=dummy_df)
        existing = client.raw.rows.retrieve_dataframe(db_name=db_name, table_name=table_name, limit=None)

    return existing


def get_delete_state_tracker_tbl(
    client: CogniteClient,
    db_config: str,
    tbl_config: str,
) -> pd.DataFrame:
    dummy_values: dict[str, str] = {
        "primary_key": "DummyRowKey",
        "runstart": "0101-01-01 00:00:00",
        "runend": "0101-01-01 00:00:00",
        "runFinished": "N/A",
        "RunId": "N/A",
        "key": "DummyRowKey",
        "sourceId": "N/A",
        "File_Type_Short_Name": "N/A",
        "dwg_drop_path_defined": "N/A",
        "target_filepath_defined": "N/A",
        "external_id": "N/A",
        "CurrentDeleteFlag": "N/A",
        "Cognite_Id": "N/A",
        "Cognite_Ingest": "N/A",
        "DoesInstanceExistBeforeStatus": "N/A",
        "DoesInstanceExistAfterStatus": "N/A",
        "DoesStateStoreRecordExistBeforeStatus": "N/A",
        "DoesStateStoreRecordExistAfterStatus": "N/A",
        "DoesDwgDropFolderPathExistBeforeStatus": "N/A",
        "DoesDwgDropFolderPathExistAfterStatus": "N/A",
        "DoesTargetFolderPathExistBeforeStatus": "N/A",
        "DoesTargetFolderPathExistAfterStatus": "N/A",
        "DeletedInstanceTimestamp": "0101-01-01 00:00:00",
        "DeletedStateStoreRecordTimestamp": "0101-01-01 00:00:00",
        "DeletedDwgDropFolderPathTimestamp": "0101-01-01 00:00:00",
        "DeletedTargetFolderPathTimestamp": "0101-01-01 00:00:00",
    }
    return ensure_raw_table_with_dummy_row(client, db_config, tbl_config, dummy_values)


def get_run_summary_tbl(
    client: CogniteClient,
    db_config: str,
    tbl_config: str,
) -> pd.DataFrame:
    dummy_values: dict[str, str] = {
        "key": "DummyRowKey",
        "RunId": "N/A",
        "runstart": "0101-01-01 00:00:00",
        "runend": "0101-01-01 00:00:00",
        "runFinished": "N/A",
    }
    return ensure_raw_table_with_dummy_row(client, db_config, tbl_config, dummy_values)
