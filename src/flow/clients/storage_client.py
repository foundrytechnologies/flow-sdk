"""
Storage Client Module.

This module provides the StorageClient class for interacting with storage
resources on the Foundry Cloud Platform. It manages authentication, session
configuration with retry logic, and provides methods to create, retrieve,
and delete disks as well as fetch storage quotas and available regions.
"""

import logging
import uuid
from typing import Any, List, Optional

from pydantic import ValidationError
import requests

from flow.clients.authenticator import Authenticator
from flow.clients.http_client import HTTPClient
from flow.models import (
    DiskAttachment,
    DiskResponse,
    RegionResponse,
    StorageQuotaResponse,
)
from flow.utils.exceptions import (
    AuthenticationError,
    InvalidResponseError,
    APIError,
)

__all__ = ["StorageClient"]

_logger: logging.Logger = logging.getLogger(__name__)


class StorageClient:
    """
    Provides storage-related operations on the Foundry Cloud Platform.

    This service uses a shared HTTPClient for all HTTP interactions, reusing the same
    authentication and session configuration logic as other clients.
    """

    DEFAULT_BASE_URL: str = "https://api.mlfoundry.com"

    def __init__(
        self,
        authenticator: Authenticator,
        base_url: Optional[str] = None,
        timeout: int = 10,
        max_retries: int = 3,
    ) -> None:
        """
        Initialize the StorageService with authentication and HTTP client settings.

        Args:
            authenticator (Authenticator): An instance to retrieve an access token.
            base_url (Optional[str]): Base URL for the Storage API.
                Defaults to "https://api.mlfoundry.com" if not provided.
            timeout (int): Request timeout in seconds. Defaults to 10.
            max_retries (int): Maximum number of retries for failed requests. Defaults to 3.

        Raises:
            TypeError: If `authenticator` is not an instance of Authenticator.
            AuthenticationError: If unable to obtain a valid access token.
        """
        if not isinstance(authenticator, Authenticator):
            raise TypeError("authenticator must be an instance of Authenticator.")

        self._logger: logging.Logger = _logger
        self._authenticator: Authenticator = authenticator
        self._base_url: str = base_url or self.DEFAULT_BASE_URL
        self._timeout: int = timeout

        self._logger.debug("Attempting to retrieve access token for StorageService.")
        try:
            token = self._authenticator.get_access_token()
            if not token:
                self._logger.error("No token received from Authenticator.")
                raise AuthenticationError("Authentication failed: No token received")
        except Exception as exc:
            self._logger.error(
                "Failed to obtain token from Authenticator", exc_info=True
            )
            raise AuthenticationError("Authentication failed") from exc

        # Initialize the shared HTTP client.
        self._http_client = HTTPClient(
            base_url=self._base_url,
            token=token,
            timeout=self._timeout,
            max_retries=max_retries,
            logger=self._logger,
        )

    # -------------------------------------------------------------------------
    # Private Helper Methods
    # -------------------------------------------------------------------------

    def _validate_non_empty_string(self, value: str, field_name: str) -> None:
        """
        Validate that a string is non-empty and non-whitespace.

        Args:
            value (str): The string to validate.
            field_name (str): The name of the field (used in error messages).

        Raises:
            ValueError: If `value` is empty or contains only whitespace.
        """
        if not value.strip():
            raise ValueError(f"{field_name} must be provided and non-empty.")

    def _is_valid_uuid(self, value: str) -> bool:
        """
        Check whether a string is a valid UUID.

        Args:
            value (str): The string to validate.

        Returns:
            bool: True if `value` is a valid UUID, False otherwise.
        """
        try:
            uuid.UUID(value)
            return True
        except ValueError:
            return False

    def _resolve_region_id(self, region_str: str) -> str:
        """
        Resolve a region string to a valid region UUID.

        This method first checks if the given region string matches an existing
        region_id. If not, it searches among the region names.

        Args:
            region_str (str): The region string (could be a region_id or name).

        Returns:
            str: The resolved region_id.

        Raises:
            ValueError: If no matching region is found.
        """
        self._logger.debug(
            "Resolving region string '%s' into a valid region_id.", region_str
        )
        all_regions: List[RegionResponse] = self.get_regions()
        for region in all_regions:
            if region.region_id == region_str:
                self._logger.debug(
                    "Exact match found for region_id='%s'.", region.region_id
                )
                return region.region_id
        for region in all_regions:
            if region.name == region_str:
                self._logger.debug(
                    "Region name match found: '%s' resolved to region_id='%s'.",
                    region.name,
                    region.region_id,
                )
                return region.region_id
        raise ValueError(f"No matching region found for '{region_str}'")

    # -------------------------------------------------------------------------
    # Public Storage Operations
    # -------------------------------------------------------------------------

    def create_disk(
        self, project_id: str, disk_attachment: DiskAttachment
    ) -> DiskResponse:
        """
        Create a new disk in the specified project.

        Args:
            project_id (str): The project ID.
            disk_attachment (DiskAttachment): Object containing disk details.

        Returns:
            DiskResponse: Details of the created disk.

        Raises:
            ValueError: If `project_id` is empty.
            InvalidResponseError: If the response cannot be parsed correctly.
        """
        self._logger.debug(
            "Creating disk with disk_id='%s' in project_id='%s'.",
            disk_attachment.disk_id,
            project_id,
        )
        self._validate_non_empty_string(project_id, "project_id")

        # Resolve region identifier if needed.
        if disk_attachment.region_id and not self._is_valid_uuid(
            disk_attachment.region_id
        ):
            original_region_id: str = disk_attachment.region_id
            disk_attachment.region_id = self._resolve_region_id(original_region_id)

        payload: dict[str, Any] = {
            "disk_id": disk_attachment.disk_id,
            "name": disk_attachment.name,
            "disk_interface": disk_attachment.disk_interface,
            "region_id": disk_attachment.region_id,
            "size": disk_attachment.size,
            "size_unit": disk_attachment.size_unit,
        }
        self._logger.debug("Payload for create_disk: %s", payload)

        endpoint: str = f"/marketplace/v1/projects/{project_id}/disks"
        response: requests.Response = self._http_client.request(
            method="POST",
            path=endpoint,
            json=payload,
        )

        try:
            data: Any = self._http_client.parse_json(
                response, context="create_disk response"
            )
            disk: DiskResponse = DiskResponse.model_validate(data)
            self._logger.debug("Disk created successfully: %s", disk)
            return disk
        except (ValidationError, ValueError) as err:
            self._logger.error("Failed to parse create_disk response: %s", err)
            raise InvalidResponseError(
                "Invalid JSON response from create_disk."
            ) from err

    def get_disks(self, project_id: str) -> List[DiskResponse]:
        """
        Retrieve a list of all disks for the specified project.

        Args:
            project_id (str): The project ID.

        Returns:
            List[DiskResponse]: A list of disk details.

        Raises:
            ValueError: If `project_id` is empty.
            InvalidResponseError: If the response data is invalid.
        """
        self._logger.debug("Retrieving disks for project_id='%s'.", project_id)
        self._validate_non_empty_string(project_id, "project_id")

        endpoint: str = f"/marketplace/v1/projects/{project_id}/disks"
        response: requests.Response = self._http_client.request(
            method="GET", path=endpoint
        )

        try:
            data: Any = self._http_client.parse_json(response, context="get_disks")
            disks: List[DiskResponse] = [
                DiskResponse.model_validate(item) for item in data
            ]
            self._logger.debug("Retrieved %d disks.", len(disks))
            return disks
        except (ValidationError, ValueError) as err:
            self._logger.error("Failed to parse get_disks data: %s", err)
            raise InvalidResponseError("Invalid JSON response from get_disks.") from err

    def get_disk(self, project_id: str, disk_id: str) -> DiskResponse:
        """
        Retrieve details of a specific disk.

        Args:
            project_id (str): The project ID.
            disk_id (str): The disk ID.

        Returns:
            DiskResponse: Details of the specified disk.

        Raises:
            ValueError: If `project_id` or `disk_id` is empty.
            InvalidResponseError: If the response data is invalid.
        """
        self._logger.debug(
            "Retrieving disk '%s' for project_id='%s'.", disk_id, project_id
        )
        self._validate_non_empty_string(project_id, "project_id")
        self._validate_non_empty_string(disk_id, "disk_id")

        endpoint: str = f"/marketplace/v1/projects/{project_id}/disks/{disk_id}"
        response: requests.Response = self._http_client.request(
            method="GET", path=endpoint
        )

        try:
            data: Any = self._http_client.parse_json(response, context="get_disk")
            disk: DiskResponse = DiskResponse.model_validate(data)
            self._logger.debug("Disk retrieved successfully: %s", disk)
            return disk
        except (ValidationError, ValueError) as err:
            self._logger.error("Failed to parse get_disk data: %s", err)
            raise InvalidResponseError("Invalid JSON response from get_disk.") from err

    def delete_disk(self, project_id: str, disk_id: str) -> None:
        """
        Delete a disk from the specified project.

        Args:
            project_id (str): The project ID.
            disk_id (str): The disk ID to delete.

        Raises:
            ValueError: If `project_id` or `disk_id` is empty.
            APIError: If the deletion request fails.
        """
        self._logger.debug(
            "Deleting disk_id='%s' from project_id='%s'.", disk_id, project_id
        )
        self._validate_non_empty_string(project_id, "project_id")
        self._validate_non_empty_string(disk_id, "disk_id")

        endpoint: str = f"/marketplace/v1/projects/{project_id}/disks/{disk_id}"
        self._http_client.request(method="DELETE", path=endpoint)
        self._logger.info(
            "Disk '%s' successfully deleted from project '%s'.", disk_id, project_id
        )

    def get_storage_quota(self, project_id: str) -> StorageQuotaResponse:
        """
        Retrieve the storage quota for the specified project.

        Args:
            project_id (str): The project ID.

        Returns:
            StorageQuotaResponse: The storage quota details.

        Raises:
            ValueError: If `project_id` is empty.
            InvalidResponseError: If the response data is invalid.
        """
        self._logger.debug("Retrieving storage quota for project_id='%s'.", project_id)
        self._validate_non_empty_string(project_id, "project_id")

        endpoint: str = f"/marketplace/v1/projects/{project_id}/disks/quotas"
        response: requests.Response = self._http_client.request(
            method="GET", path=endpoint
        )

        try:
            data: Any = self._http_client.parse_json(
                response, context="get_storage_quota"
            )
            quota: StorageQuotaResponse = StorageQuotaResponse.model_validate(data)
            self._logger.debug("Storage quota retrieved: %s", quota)
            return quota
        except (ValidationError, ValueError) as err:
            self._logger.error("Failed to parse storage quota data: %s", err)
            raise InvalidResponseError(
                "Invalid JSON response from get_storage_quota."
            ) from err

    def get_regions(self) -> List[RegionResponse]:
        """
        Retrieve all available regions from the marketplace.

        Returns:
            List[RegionResponse]: A list of regions.

        Raises:
            InvalidResponseError: If the response data is invalid.
        """
        self._logger.debug("Retrieving list of regions.")
        endpoint: str = "/marketplace/v1/regions"
        response: requests.Response = self._http_client.request(
            method="GET", path=endpoint
        )

        try:
            data: Any = self._http_client.parse_json(response, context="get_regions")
            if isinstance(data, dict):
                data = [data]
            elif not isinstance(data, list):
                raise ValueError(
                    f"Expected dict or list for regions, got: {type(data)}. Data: {data}"
                )

            regions: List[RegionResponse] = [
                RegionResponse.model_validate(item) for item in data
            ]
            self._logger.debug("Retrieved %d regions.", len(regions))
            return regions
        except (ValueError, ValidationError) as err:
            self._logger.error("Failed to parse get_regions data: %s", err)
            raise InvalidResponseError(f"Invalid response format: {err}") from err
