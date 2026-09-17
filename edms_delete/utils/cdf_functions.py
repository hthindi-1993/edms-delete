"""CDF client initialization (same pattern as csv_extractor)."""

import logging
import os
from typing import Optional

import yaml
from cognite.client import ClientConfig, CogniteClient, global_config
from cognite.client.credentials import OAuthClientCredentials
from cognite.client.exceptions import CogniteAPIError
from dotenv import load_dotenv

logger = logging.getLogger(__name__)


def initialize_cognite_client(filepath: Optional[str] = None) -> Optional[CogniteClient]:
    try:
        if filepath:
            load_dotenv(override=True, dotenv_path=filepath)
        else:
            load_dotenv(override=True)

        cdf_project = os.getenv("CDF_PROJECT")
        cdf_cluster = os.getenv("CDF_CLUSTER")
        tenant_id = os.getenv("IDP_TENANT_ID")
        client_id = os.getenv("IDP_CLIENT_ID")
        client_secret = os.getenv("IDP_CLIENT_SECRET")

        scopes = [f"https://{cdf_cluster}.cognitedata.com/.default"]
        token_url = f"https://login.microsoftonline.com/{tenant_id}/oauth2/v2.0/token"

        creds = OAuthClientCredentials(
            token_url=token_url,
            client_id=client_id,
            client_secret=client_secret,
            scopes=scopes,
        )

        cnf = ClientConfig(
            client_name="edms_delete",
            project=cdf_project,
            base_url=f"https://p001.plink.{cdf_cluster}.cognitedata.com",
            credentials=creds,
            debug=False,
        )

        global_config.apply_settings({"max_retries": 5, "disable_ssl": True})

        client = CogniteClient(cnf)
        logger.info(
            f"Cognite Client initialized for project '{client.config.project}' using environment variables."
        )
        return client

    except Exception as e:
        logger.error(f"Failed to initialize Cognite Client: {e}")
        return None


def load_config_from_extraction_pipeline(client: CogniteClient, pipeline_ext_id: str) -> dict:
    try:
        raw_config = client.extraction_pipelines.config.retrieve(pipeline_ext_id)
        if raw_config.config is None:
            raise ValueError(f"No config found for extraction pipeline: {pipeline_ext_id!r}")
    except CogniteAPIError:
        raise RuntimeError(f"Not able to retrieve pipeline config for extraction pipeline: {pipeline_ext_id!r}")

    loaded_yaml_data = yaml.safe_load(raw_config.config)

    if isinstance(loaded_yaml_data, dict):
        return loaded_yaml_data
    raise ValueError("Invalid configuration structure from CDF: expected a YAML dictionary.")
