"""
Module for Foundry base settings.

This module defines the FoundryBaseSettings class used to load configuration
from environment variables (or a .env file) using the pydantic_settings library.
It ensures that required settings are provided and valid.
"""

from typing import Dict, Optional
from pydantic_settings import BaseSettings, SettingsConfigDict
from pydantic import Field, SecretStr, field_validator, model_validator


class FoundryBaseSettings(BaseSettings):
    """
    Base settings for Foundry environment variables.

    This class loads settings using a .env file if present and ensures that
    required variables such as foundry_email, foundry_password and/or foundry_api_key are provided and valid.

    Attributes:
        foundry_email (Optional[str]): Foundry email, required if no API key is provided.
        foundry_password (Optional[SecretStr]): Foundry password, required if no API key is provided.
        foundry_api_key (Optional[str]): API key for authentication.
    """

    # Environment variable mappings
    foundry_email: Optional[str] = Field(default=None, alias="FOUNDRY_EMAIL")
    foundry_password: Optional[SecretStr] = Field(
        default=None, alias="FOUNDRY_PASSWORD"
    )
    foundry_api_key: Optional[str] = Field(default=None, alias="FOUNDRY_API_KEY")
    foundry_project_name: Optional[str] = Field(default=None, alias="FOUNDRY_PROJECT_NAME")
    foundry_ssh_key_name: Optional[str] = Field(default=None, alias="FOUNDRY_SSH_KEY_NAME")

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

    # Field validators updated to handle None.
    @field_validator("foundry_email")
    @classmethod
    def no_empty_strings(cls, value: str | None) -> str | None:
        """Validate that a string field is not empty.

        Args:
            value (Optional[str]): The field value.

        Returns:
            Optional[str]: The original value if valid, or None.

        Raises:
            ValueError: If the field value is empty or contains only whitespace.
        """
        if value is None:
            return value
        if not value.strip():
            raise ValueError("Required environment variable must not be empty.")
        return value

    @field_validator("foundry_password")
    @classmethod
    def non_empty_password_validator(cls, v: SecretStr | None) -> SecretStr | None:
        """Validate that the password field is not empty.

        Args:
            v (Optional[SecretStr]): The password field value.

        Returns:
            Optional[SecretStr]: The original password if valid, or None.

        Raises:
            ValueError: If the password field is empty or contains only whitespace.
        """
        if v is None:
            return v
        if not v.get_secret_value().strip():
            raise ValueError(
                "Required environment variable 'foundry_password' is empty."
            )
        return v

    # New model-level validator to enforce that either an API key is provided
    # or both email and password are provided
    @model_validator(mode="after")
    def check_authentication_method(
        cls, values: "FoundryBaseSettings"
    ) -> "FoundryBaseSettings":
        """Ensure that either an API key is provided or both email and password are provided.

        Args:
            values (FoundryBaseSettings): The instance after field validation.

        Returns:
            FoundryBaseSettings: The validated settings instance.

        Raises:
            ValueError: If neither a valid API key nor both a non-empty email and password are provided.
        """
        if values.foundry_api_key is not None and values.foundry_api_key.strip():
            # API key provided: skip email/password checks.
            return values
        # Otherwise ensure both foundry_email and foundry_password are provided and non-empty.
        if (
            values.foundry_email is None
            or not values.foundry_email.strip()
            or values.foundry_password is None
            or not values.foundry_password.get_secret_value().strip()
        ):
            raise ValueError(
                "Either a valid API key or both a non-empty foundry_email and foundry_password must be provided."
            )
        return values

    # New property getters to expose uppercase names expected by tests.
    @property
    def PROJECT_NAME(self) -> Optional[str]:
        return self.foundry_project_name

    @property
    def SSH_KEY_NAME(self) -> Optional[str]:
        return self.foundry_ssh_key_name
