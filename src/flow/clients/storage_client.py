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

import requests
from pydantic import ValidationError
from requests import Response
from requests.adapters import HTTPAdapter, Retry

from flow.clients.authenticator import Authenticator
from flow.models import (
    DiskAttachment,
    DiskResponse,
    RegionResponse,
    StorageQuotaResponse,
)
from flow.utils.exceptions import (
    APIError,
    AuthenticationError,
    InvalidResponseError,
    NetworkError,
    TimeoutError,
)

__all__ = ["StorageClient"]

_logger: logging.Logger = logging.getLogger(__name__)


class StorageClient:
    """Client for interacting with storage resources in the Foundry Cloud Platform.

    This class manages authentication via an Authenticator instance, configures
    a requests.Session with retry logic, and provides methods for common
    storage-related actions such as creating, retrieving, and deleting disks,
    retrieving storage quotas, and listing available regions.
    """

    DEFAULT_BASE_URL: str = "https://api.mlfoundry.com"

    def __init__(
        self,
        authenticator: Authenticator,
        base_url: Optional[str] = None,
        timeout: int = 10,
        max_retries: int = 3,
    ) -> None:
        """Initialize the StorageClient with authentication and session settings.

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
        self._session: requests.Session = self._create_session(max_retries)

        self._logger.debug("Attempting to retrieve access token.")
        try:
            token = self._authenticator.get_access_token()
            if not token:
                self._logger.error("Failed to obtain token from Authenticator.")
                raise AuthenticationError("Authentication failed: No token received")
        except Exception as exc:
            self._logger.error(
                "Failed to obtain token from Authenticator", exc_info=True
            )
            raise AuthenticationError("Authentication failed") from exc

        # Set the default headers for all requests.
        self._session.headers.update(
            {
                "Authorization": f"Bearer {token}",
                "Content-Type": "application/json",
            }
        )
        self._logger.debug("StorageClient initialized with base_url=%s", self._base_url)

    # =========================================================================
    # Private Helper Methods
    # =========================================================================

    def _create_session(self, max_retries: int) -> requests.Session:
        """Create and configure a requests.Session with retry logic.

        Args:
            max_retries (int): Maximum number of retries for HTTP requests.

        Returns:
            requests.Session: Configured session with retry logic.
        """
        self._logger.debug("Creating HTTP session with max_retries=%d", max_retries)
        session = requests.Session()
        retries = Retry(
            total=max_retries,
            backoff_factor=0.5,
            status_forcelist=[429, 500, 502, 503, 504],
            allowed_methods={"GET", "POST", "DELETE"},
            raise_on_status=False,
        )
        adapter = HTTPAdapter(max_retries=retries)
        session.mount("https://", adapter)
        self._logger.debug("HTTP session created with retry adapter.")
        return session

    @staticmethod
    def _validate_non_empty_string(value: str, field_name: str) -> None:
        """Validate that a string is non-empty and non-whitespace.

        Args:
            value (str): The string to validate.
            field_name (str): The name of the field (used in error messages).

        Raises:
            ValueError: If `value` is empty or contains only whitespace.
        """
        if not value.strip():
            raise ValueError(f"{field_name} must be provided and non-empty.")

    def _request(self, method: str, endpoint: str, **kwargs: Any) -> Response:
        """Send an HTTP request and return the response, handling common errors.

        Args:
            method (str): HTTP method (e.g., "GET", "POST", "DELETE").
            endpoint (str): API endpoint path (e.g., "/marketplace/v1/...").
            **kwargs: Additional keyword arguments for requests.Session.request().

        Returns:
            Response: The response object from the API call.

        Raises:
            TimeoutError: If the request times out.
            NetworkError: If a network connection error occurs.
            APIError: For other request-related errors.
        """
        url: str = f"{self._base_url}{endpoint}"
        kwargs.setdefault("timeout", self._timeout)
        self._logger.debug(
            "Making %s request to %s with kwargs=%s", method, url, kwargs
        )

        try:
            response: Response = self._session.request(method=method, url=url, **kwargs)
            response.raise_for_status()
            self._logger.debug(
                "Request to %s succeeded with status_code=%d.",
                url,
                response.status_code,
            )
            return response

        except requests.exceptions.Timeout as err:
            self._logger.error("Request to %s timed out: %s", url, err)
            raise TimeoutError("Request timed out") from err

        except requests.exceptions.ConnectionError as err:
            self._logger.error(
                "Network error occurred while requesting %s: %s", url, err
            )
            raise NetworkError("Network error occurred") from err

        except requests.exceptions.HTTPError as err:
            self._handle_http_error(err)

        except requests.exceptions.RequestException as err:
            self._logger.error("Request failed for %s: %s", url, err)
            raise APIError(f"Request failed: {err}") from err

        # Fallback for any unknown error.
        raise APIError("Unknown error during request execution.")

    def _parse_json(self, response: Response, context: str = "") -> Any:
        """Safely parse JSON from a Response object.

        Logs the raw data and context to help diagnose issues.

        Args:
            response (Response): The response from which to parse JSON.
            context (str): Contextual string for logging purposes.

        Returns:
            Any: Parsed JSON data (dict or list).

        Raises:
            ValueError: If the response body is not valid JSON.
        """
        try:
            data: Any = response.json()
            self._logger.debug("Raw JSON for %s: %s", context or "response", data)
            return data
        except ValueError as err:
            self._logger.error(
                "Failed to parse JSON for %s. Error: %s, response text: %s",
                context or "response",
                err,
                response.text,
            )
            raise

    def _handle_http_error(self, error: requests.HTTPError) -> None:
        """Handle HTTP errors by logging and raising appropriate exceptions.

        Args:
            error (requests.HTTPError): The HTTP error encountered.

        Raises:
            AuthenticationError: If the status code is 401 or 403.
            APIError: For other HTTP error status codes.
        """
        response: Optional[Response] = error.response
        if response is not None:
            status_code: int = response.status_code
            content: str = response.text
            self._logger.error(
                "HTTP error occurred. status_code=%d, response=%s", status_code, content
            )
            if status_code in [401, 403]:
                raise AuthenticationError(
                    f"Authentication failed: {content}"
                ) from error
            message: str = f"API request failed [{status_code}]: {content}"
        else:
            message = "API request failed: No response received"
            self._logger.error(message)

        raise APIError(message) from error

    def _is_valid_uuid(self, value: str) -> bool:
        """Check whether a string is a valid UUID.

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
        """Resolve a region string to a valid region UUID.

        This method attempts to match the provided region string against
        known regions. First, it checks for an exact match on region_id.
        If not found, it checks for a matching region name.

        Args:
            region_str (str): The region string (could be a region_id or name).

        Returns:
            str: The resolved region_id as a UUID string.

        Raises:
            ValueError: If no matching region is found.
        """
        self._logger.debug(
            "Resolving region string '%s' into a valid region_id.", region_str
        )
        all_regions: List[RegionResponse] = self.get_regions()
        for region_info in all_regions:
            if region_info.region_id == region_str:
                self._logger.debug(
                    "Matched region_str='%s' directly to region_id='%s'.",
                    region_str,
                    region_info.region_id,
                )
                return region_info.region_id

        for region_info in all_regions:
            if region_info.name == region_str:
                self._logger.debug(
                    "Matched region_str='%s' to region name='%s', region_id='%s'.",
                    region_str,
                    region_info.name,
                    region_info.region_id,
                )
                return region_info.region_id

        raise ValueError(f"No matching region found for '{region_str}'")

    # =========================================================================
    # Public Methods for Storage-Related Actions
    # =========================================================================

    def create_disk(
        self, project_id: str, disk_attachment: DiskAttachment
    ) -> DiskResponse:
        """Create a new disk in the specified project.

        Args:
            project_id (str): The ID of the project in which to create the disk.
            disk_attachment (DiskAttachment): Object containing disk details.

        Returns:
            DiskResponse: The response object containing details of the created disk.

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
        self._logger.debug("Payload sent to create_disk: %s", payload)

        endpoint: str = f"/marketplace/v1/projects/{project_id}/disks"
        response: Response = self._request("POST", endpoint, json=payload)

        try:
            data: Any = self._parse_json(response, context="create_disk response")
            self._logger.debug("Disk created; validating response data.")
            disk: DiskResponse = DiskResponse.model_validate(data)
            self._logger.debug("Parsed DiskResponse: %s", disk)
            return disk
        except (ValidationError, ValueError) as err:
            self._logger.error("Failed to parse create_disk response: %s", err)
            raise InvalidResponseError(
                "Invalid JSON response from create_disk."
            ) from err

    def get_disks(self, project_id: str) -> List[DiskResponse]:
        """Retrieve a list of all disks for the specified project.

        Args:
            project_id (str): The ID of the project.

        Returns:
            List[DiskResponse]: A list of disk response objects.

        Raises:
            ValueError: If `project_id` is empty.
            InvalidResponseError: If the response data is invalid.
        """
        self._logger.debug("Retrieving disks for project_id='%s'.", project_id)
        self._validate_non_empty_string(project_id, "project_id")

        endpoint: str = f"/marketplace/v1/projects/{project_id}/disks"
        response: Response = self._request("GET", endpoint)

        try:
            data: Any = self._parse_json(response, context="get_disks")
            self._logger.debug("Validating disks data via Pydantic: %s", data)
            disks: List[DiskResponse] = [
                DiskResponse.model_validate(item) for item in data
            ]
            self._logger.debug("Disks successfully validated. Count=%d", len(disks))
            return disks
        except (ValidationError, ValueError) as err:
            self._logger.error("Failed to parse get_disks data: %s", err)
            raise InvalidResponseError("Invalid JSON response from get_disks.") from err

    def get_disk(self, project_id: str, disk_id: str) -> DiskResponse:
        """Retrieve details of a specific disk in a given project.

        Args:
            project_id (str): The project ID.
            disk_id (str): The disk ID to fetch.

        Returns:
            DiskResponse: The response object containing disk details.

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
        response: Response = self._request("GET", endpoint)

        try:
            data: Any = self._parse_json(response, context="get_disk")
            self._logger.debug("Validating disk data via Pydantic: %s", data)
            disk: DiskResponse = DiskResponse.model_validate(data)
            self._logger.debug("Disk successfully validated: %s", disk)
            return disk
        except (ValidationError, ValueError) as err:
            self._logger.error("Failed to parse get_disk data: %s", err)
            raise InvalidResponseError("Invalid JSON response from get_disk.") from err

    def delete_disk(self, project_id: str, disk_id: str) -> None:
        """Delete a disk from a specified project.

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
        self._request("DELETE", endpoint)
        self._logger.info(
            "Disk '%s' successfully deleted from project '%s'.", disk_id, project_id
        )

    def get_storage_quota(self, project_id: str) -> StorageQuotaResponse:
        """Retrieve the storage quota for a given project.

        Args:
            project_id (str): The project ID.

        Returns:
            StorageQuotaResponse: The response object containing quota information.

        Raises:
            ValueError: If `project_id` is empty.
            InvalidResponseError: If the response data is invalid.
        """
        self._logger.debug("Retrieving storage quota for project_id='%s'.", project_id)
        self._validate_non_empty_string(project_id, "project_id")

        endpoint: str = f"/marketplace/v1/projects/{project_id}/disks/quotas"
        response: Response = self._request("GET", endpoint)

        try:
            data: Any = self._parse_json(response, context="get_storage_quota")
            self._logger.debug("Validating storage quota data via Pydantic: %s", data)
            quota: StorageQuotaResponse = StorageQuotaResponse.model_validate(data)
            self._logger.debug("StorageQuotaResponse successfully validated: %s", quota)
            return quota
        except (ValidationError, ValueError) as err:
            self._logger.error("Failed to parse storage quota data: %s", err)
            raise InvalidResponseError(
                "Invalid JSON response from get_storage_quota."
            ) from err

    def get_regions(self) -> List[RegionResponse]:
        """Retrieve all available regions from the marketplace.

        Returns:
            List[RegionResponse]: A list of region response objects.

        Raises:
            InvalidResponseError: If the response data is invalid.
        """
        self._logger.debug("Retrieving list of regions.")
        endpoint: str = "/marketplace/v1/regions"
        response: Response = self._request("GET", endpoint)

        try:
            data: Any = self._parse_json(response, context="get_regions")
            # The API might return a single dict or a list of dicts.
            if isinstance(data, dict):
                data = [data]
            elif not isinstance(data, list):
                raise ValueError(
                    f"Expected dict or list for regions, got: {type(data)}. Data: {data}"
                )

            self._logger.debug("Validating region data with Pydantic: %s", data)
            regions: List[RegionResponse] = [
                RegionResponse.model_validate(item) for item in data
            ]
            self._logger.debug("Regions successfully validated. Count=%d", len(regions))
            return regions
        except (ValueError, ValidationError) as err:
            self._logger.error("Failed to parse get_regions data: %s", err)
            raise InvalidResponseError(f"Invalid response format: {err}") from err
