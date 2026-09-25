from dataclasses import dataclass
from typing import Any

from edms_delete.utils.logging_setup import LoggerConfig


@dataclass(frozen=True)
class EdmsDeleteConfig:
    raw_db_metadata: str
    raw_db_deletion_extractor: str
    raw_db_file_state_store: str
    raw_table_metadata: str
    raw_table_delete_tracker: str
    raw_table_deletion_extractor_summary: str
    raw_table_file_state_store: str
    dm_schema_space: str
    dm_external_id: str
    dm_view_name: str
    dm_version: str
    dm_instance_space: str
    dm_instance_external_id_prefix: str
    target_folder_path: str
    dwg_drop_folder_path: str
    logger: LoggerConfig

    @classmethod
    def from_pipeline_yaml(cls, app_config: dict[str, Any]) -> "EdmsDeleteConfig":
        raw_tables = app_config["rawTables"]
        data_model_views = app_config["dataModelViews"]
        vm_properties = app_config["vmProperties"]

        return cls(
            raw_db_metadata=raw_tables["rawDbMetadata"],
            raw_db_deletion_extractor=raw_tables["rawDbDeletionExtractor"],
            raw_db_file_state_store=raw_tables["rawDbFileStateStore"],
            raw_table_metadata=raw_tables["rawTableMetadata"],
            raw_table_delete_tracker=raw_tables["rawTableDeleteTrackerTbl"],
            raw_table_deletion_extractor_summary=raw_tables["rawDbDeletionExtractorSummary"],
            raw_table_file_state_store=raw_tables["rawTableFileStateStore"],
            dm_schema_space=data_model_views["schemaSpace"],
            dm_external_id=data_model_views["dmExternalId"],
            dm_view_name=data_model_views["viewName"],
            dm_version=data_model_views["version"],
            dm_instance_space=data_model_views["instanceSpace"],
            dm_instance_external_id_prefix=data_model_views["instanceExternalIdPrefix"],
            target_folder_path=vm_properties["targetFolderPath"],
            dwg_drop_folder_path=vm_properties["dwgDropFolderPath"],
            logger=LoggerConfig.from_pipeline_yaml(app_config),
        )
