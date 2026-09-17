"""
EDMS Delete - Main Entry Point

Usage:
    python -m edms_delete <env_file_path> <extraction_pipeline_ext_id>

Example:
    poetry run python edms_delete/__main__.py .env ep_src_indp_edms_dummy
"""

import logging
import sys

from edms_delete.config import EdmsDeleteConfig
from edms_delete.runner import EdmsDeleteRunner
from edms_delete.utils.cdf_functions import initialize_cognite_client, load_config_from_extraction_pipeline
from edms_delete.utils.logging_setup import configure_logging

logger = logging.getLogger(__name__)


def main() -> None:
    if len(sys.argv) < 3:
        logger.error(
            "Missing required arguments. Usage: python -m edms_delete <env_file_path> <extraction_pipeline_ext_id>"
        )
        sys.exit(1)

    env_file_path = sys.argv[1]
    pipeline_ext_id = sys.argv[2]

    logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")

    logger.info("Initializing Cognite client from %s", env_file_path)
    client = initialize_cognite_client(env_file_path)
    if not client:
        sys.exit(1)

    try:
        client.files.list(limit=1)
        logger.info("Cognite client authenticated.")
    except Exception as e:
        logger.error("Could not verify Cognite connection: %s", e)
        sys.exit(1)

    logger.info("Loading pipeline config: %s", pipeline_ext_id)
    try:
        app_config = load_config_from_extraction_pipeline(client, pipeline_ext_id)
        delete_config = EdmsDeleteConfig.from_pipeline_yaml(app_config)
    except (ValueError, RuntimeError, KeyError) as e:
        logger.error("Invalid pipeline configuration: %s", e)
        sys.exit(1)

    configure_logging(delete_config.logger)

    EdmsDeleteRunner(client, delete_config).run()


if __name__ == "__main__":
    main()
