"""CDF client initialization (same pattern as csv_extractor)."""

import logging
import os
from pathlib import Path
from typing import Optional

import yaml
from cognite.client import ClientConfig, CogniteClient, global_config
from cognite.client.credentials import OAuthClientCredentials
from cognite.client.exceptions import CogniteAPIError
from dotenv import load_dotenv

logger = logging.getLogger(__name__)

_REQUIRED_ENV = (
    "CDF_PROJECT",
    "CDF_CLUSTER",
    "IDP_TENANT_ID",
    "IDP_CLIENT_ID",
    "IDP_CLIENT_SECRET",
)


def _read_env(name: str) -> str | None:
    """Read env var with strip and optional surrounding quotes (common in hand-edited .env)."""
    raw = os.getenv(name)
    if raw is None:
        return None
    value = raw.strip()
    if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
        value = value[1:-1]
    return value or None


def initialize_cognite_client(filepath: Optional[str] = None) -> Optional[CogniteClient]:
    try:
        if filepath:
            env_path = Path(filepath)
            if not env_path.is_absolute():
                env_path = Path.cwd() / env_path
            env_path = env_path.resolve()
            if not env_path.is_file():
                logger.error("Env file not found: %s (cwd=%s)", env_path, Path.cwd())
                return None
            load_dotenv(override=True, dotenv_path=env_path)
            logger.info("Loaded credentials from %s", env_path)
        else:
            load_dotenv(override=True)

        cdf_project = _read_env("CDF_PROJECT")
        cdf_cluster = _read_env("CDF_CLUSTER")
        tenant_id = _read_env("IDP_TENANT_ID")
        client_id = _read_env("IDP_CLIENT_ID")
        client_secret = _read_env("IDP_CLIENT_SECRET")

        missing = [name for name in _REQUIRED_ENV if not _read_env(name)]
        if missing:
            logger.error("Missing or empty environment variables: %s", ", ".join(missing))
            return None

        logger.info(
            "Auth config: project=%s cluster=%s client_id=%s secret_len=%s",
            cdf_project,
            cdf_cluster,
            client_id,
            len(client_secret or ""),
        )

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
