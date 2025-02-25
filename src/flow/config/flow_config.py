"""
Module for Foundry configuration and priority pricing mapping.

This module retrieves the environment variables required for Foundry
authentication and defines constants for usage in secondary systems.
"""

from flow.config import get_config  # Local import from our configuration module

# Load settings from the configuration provider
_settings = get_config()

# Mapping environment variables to constants for core credentials.
EMAIL = _settings.foundry_email
PASSWORD = _settings.foundry_password.get_secret_value()
API_KEY = _settings.foundry_api_key
PRIORITY_PRICE_MAPPING = _settings.PRIORITY_PRICE_MAPPING
PROJECT_NAME = getattr(_settings, "foundry_project_name", None)
SSH_KEY_NAME = getattr(_settings, "foundry_ssh_key_name", None)


def log_sanitized_settings() -> dict:
    """
    Return a log-friendly dictionary with masked sensitive information.

    This function is intended for debugging/logging so that secret values (like
    passwords and API keys) are not accidentally written to logs.

    Returns:
        dict: A sanitized copy of the settings.
    """
    return {
        "foundry_email": _settings.foundry_email,
        "foundry_password": "********",
        "foundry_api_key": "********",
        "PRIORITY_PRICE_MAPPING": _settings.PRIORITY_PRICE_MAPPING,
    }


# -------------------------------------------------------------------
# Instructions:
#
# Please set the following environment variables in your shell before running:
#
#   export FOUNDRY_EMAIL='your_email@example.com'
#   export FOUNDRY_PASSWORD='your_password'
#   export FOUNDRY_API_KEY='your_api_key'    # Optional: if using API key authentication
#
# Note: While FOUNDRY_PROJECT_NAME and FOUNDRY_SSH_KEY_NAME may be set (and are used in integration tests),
# they are no longer used as magic values. When instantiating FlowTaskManager,
# please pass these values explicitly as parameters.
#
#   export FOUNDRY_PROJECT_NAME='your_project_name'
#   export FOUNDRY_SSH_KEY_NAME='your_ssh_key_name'
# -------------------------------------------------------------------
