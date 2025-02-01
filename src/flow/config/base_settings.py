"""
Module for Foundry base settings.

This module defines the FoundryBaseSettings class that loads configuration
from environment variables (or a .env file) using the pydantic_settings library.
It ensures required settings are not empty and provides a base for further extension.
"""

import os
from typing import Dict
from pydantic_settings import BaseSettings, SettingsConfigDict
from pydantic import Field, SecretStr, field_validator


class FoundryBaseSettings(BaseSettings):
    """
    Base settings for Foundry environment variables.

    This class loads settings using a .env file if present and ensures that
    required variables such as foundry_email, foundry_password, foundry_project_name,
    and foundry_ssh_key_name are provided and non-empty.
    """

    # Environment variable mappings
    foundry_email: str = Field(..., alias="FOUNDRY_EMAIL")
    foundry_password: SecretStr = Field(..., alias="FOUNDRY_PASSWORD")
    foundry_project_name: str = Field(..., alias="FOUNDRY_PROJECT_NAME")
    foundry_ssh_key_name: str = Field(..., alias="FOUNDRY_SSH_KEY_NAME")

    # Constant mapping for priority pricing.
    PRIORITY_PRICE_MAPPING: Dict[str, float] = Field(
        default={"critical": 14.99, "high": 12.29, "standard": 4.24, "low": 2.00},
        exclude=True,
    )

    # Model configuration with .env file loading support.
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        env_prefix="",
        case_sensitive=True,
        extra="allow",
    )

    # Validators
    @field_validator("foundry_email", "foundry_project_name", "foundry_ssh_key_name")
    @classmethod
    def no_empty_strings(cls, value: str) -> str:
        """
        Validator to ensure string fields are not empty or whitespace.

        Args:
            value (str): The value of the field to validate.

        Returns:
            str: The original value if valid.

        Raises:
            ValueError: If the string is empty or only whitespace.
        """
        if not value.strip():
            raise ValueError("Required environment variable must not be empty.")
        return value

    @field_validator("foundry_password")
    @classmethod
    def non_empty_password_validator(cls, v: SecretStr) -> SecretStr:
        """
        Validator to ensure the foundry_password field is not empty.

        Args:
            v (SecretStr): The secret string for the password.

        Returns:
            SecretStr: The original secret string if valid.

        Raises:
            ValueError: If the password is empty or only whitespace.
        """
        if not v or not v.get_secret_value().strip():
            raise ValueError(
                "Required environment variable 'foundry_password' is empty."
            )
        return v
