import logging
from typing import Any

import pandas as pd
from cognite.client import CogniteClient
from cognite.client.data_classes.data_modeling import ViewId

logger = logging.getLogger(__name__)


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
            columns_to_fetch = ["File_Type_Short_Name", "Cognite_Delete", "Cognite_Id", "primary_key"]
            tbl = client.raw.rows.retrieve_dataframe(
                db_name=raw_db_name,
                table_name=raw_tbl_name,
                limit=None,
                partitions=4,
                columns=columns_to_fetch,
                last_updated_time_in_index=last_updated_time,
            )
        else:
            columns_to_fetch = ["File_Type_Short_Name", "Cognite_Delete", "primary_key"]
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


def get_delete_state_tracker_tbl(
    client: CogniteClient,
    db_config: str,
    tbl_config: str,
    cognite_delete_flag: bool,
) -> pd.DataFrame:
    tbls_available = client.raw.tables.list(db_name=db_config).to_pandas()["name"].tolist()

    custom_dummy_values: dict[str, str] = {
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
        "DoesInstanceExistBeforeStatus": "N/A",
        "DoesInstanceExistAfterStatus": "N/A",
        "DoesStateStoreRecordExistBeforeStatus": "N/A",
        "DoesStateStoreRecordExistAfterStatus": "N/A",
        "DoesDwgDropFolderPathExistBeforeStatus": "N/A",
        "DoesDwgDropFolderPathExistAfterStatus": "N/A",
        "DoesTargetFolderPathExistBeforeStatus": "N/A",
        "DoesTargetFolderPathExistAfterStatus": "N/A",
        "CurrentDeleteFlag": "N/A",
        "Cognite_Id": "N/A",
    }
    if cognite_delete_flag:
        custom_dummy_values.update(
            {
                "DeletedInstanceTimestamp": "0101-01-01 00:00:00",
                "DeletedStateStoreRecordTimestamp": "0101-01-01 00:00:00",
                "DeletedDwgDropFolderPathTimestamp": "0101-01-01 00:00:00",
                "DeletedTargetFolderPathTimestamp": "0101-01-01 00:00:00",
            }
        )
    else:
        custom_dummy_values.update(
            {
                "InstanceDetectedTimestamp": "0101-01-01 00:00:00",
                "StateStoreRecordDetectedTimestamp": "0101-01-01 00:00:00",
                "DwgDropFolderPathDetectedTimestamp": "0101-01-01 00:00:00",
                "TargetFolderPathDetectedTimestamp": "0101-01-01 00:00:00",
            }
        )

    dummy_df = pd.DataFrame([custom_dummy_values]).set_index("key")

    if tbl_config not in tbls_available:
        client.raw.tables.create(db_name=db_config, name=tbl_config)
        logger.info("Table '%s' created in database '%s'.", tbl_config, db_config)
        client.raw.rows.insert_dataframe(db_name=db_config, table_name=tbl_config, dataframe=dummy_df)
        return client.raw.rows.retrieve_dataframe(db_name=db_config, table_name=tbl_config, limit=None)

    existing = client.raw.rows.retrieve_dataframe(db_name=db_config, table_name=tbl_config, limit=None)
    if len(existing) == 0:
        logger.info("Table '%s' exists but is empty in database '%s'. Inserting dummy row.", tbl_config, db_config)
        client.raw.rows.insert_dataframe(db_name=db_config, table_name=tbl_config, dataframe=dummy_df)
        return client.raw.rows.retrieve_dataframe(db_name=db_config, table_name=tbl_config, limit=None)

    logger.info("Table '%s' already exists in database '%s'.", tbl_config, db_config)

    if not cognite_delete_flag and not existing.index.astype(str).isin(["DummyRowKey"]).any():
        logger.info("Dummy row missing from '%s'; re-inserting.", tbl_config)
        client.raw.rows.insert_dataframe(db_name=db_config, table_name=tbl_config, dataframe=dummy_df)
        return client.raw.rows.retrieve_dataframe(db_name=db_config, table_name=tbl_config, limit=None)

    return existing
