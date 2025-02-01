"""Tests for the StorageClient class with a Pydantic-based implementation."""

import re
import threading
import uuid
from typing import Any, Dict, List, Optional, Type
from unittest.mock import Mock, patch

import pytest
import requests
import responses
from pydantic import ValidationError

from flow.clients.authenticator import Authenticator
from flow.clients.storage_client import StorageClient
from flow.models import DiskAttachment, DiskResponse
from flow.utils.exceptions import (
    APIError,
    AuthenticationError,
    InvalidResponseError,
    NetworkError,
    TimeoutError,
)


def _url(base_url: str, endpoint: str) -> str:
    """Constructs a full URL.

    Args:
      base_url (str): The base API URL.
      endpoint (str): The API endpoint path.

    Returns:
      str: The concatenated endpoint URL.
    """
    return f"{base_url}{endpoint}"


@pytest.fixture
def auth_token() -> str:
    """Provides a test authentication token.

    Returns:
      str: A sample authentication token for tests.
    """
    return "test_token"


@pytest.fixture
def authenticator(auth_token: str) -> Mock:
    """Provides a mocked Authenticator.

    Args:
      auth_token (str): A test token from the auth_token fixture.

    Returns:
      Mock: A mocked Authenticator that returns the provided test token.
    """
    mock_auth = Mock(spec=Authenticator)
    mock_auth.get_access_token.return_value = auth_token
    return mock_auth


@pytest.fixture
def storage_client(authenticator: Mock) -> StorageClient:
    """Provides a StorageClient instance with a mocked Authenticator.

    Args:
      authenticator (Mock): The mocked Authenticator fixture.

    Returns:
      StorageClient: A configured StorageClient instance for tests.
    """
    return StorageClient(authenticator=authenticator)


@pytest.fixture
def test_data() -> Dict[str, Any]:
    """Provides test disk data.

    Returns:
      Dict[str, Any]: A dictionary containing test disk configuration.
    """
    return {
        "project_id": str(uuid.uuid4()),
        "disk_id": str(uuid.uuid4()),
        "name": "test-disk",
        "disk_interface": "Block",
        "region_id": str(uuid.uuid4()),
        "size": 10,
        "size_unit": "gb",
    }


@pytest.fixture
def base_url() -> str:
    """Provides the API base URL.

    Returns:
      str: A sample API base URL for tests.
    """
    return "https://api.mlfoundry.com"


class TestStorageClient:
    """Test suite for StorageClient with Pydantic model usage."""

    @pytest.mark.parametrize(
        "param,value",
        [
            ("project_id", ""),
            ("disk_id", ""),
            ("name", ""),
            ("disk_interface", ""),
            ("size", 0),
        ],
    )
    def test_create_disk_invalid_parameters(
        self,
        storage_client: StorageClient,
        test_data: Dict[str, Any],
        param: str,
        value: Any,
    ) -> None:
        """Ensures disk creation fails with invalid parameters.

        Args:
          storage_client (StorageClient): The StorageClient fixture under test.
          test_data (Dict[str, Any]): A dictionary of test disk data.
          param (str): The parameter to modify.
          value (Any): The value to set for the parameter.

        Raises:
          ValueError: If 'project_id' is invalid.
          ValidationError: If other parameters are invalid.
        """
        data = test_data.copy()
        data[param] = value

        project_id = data["project_id"]
        disk_data = {k: v for k, v in data.items() if k != "project_id"}

        if param == "project_id":
            with pytest.raises(ValueError):
                disk_attachment = DiskAttachment(**disk_data)
                storage_client.create_disk(
                    project_id=project_id, disk_attachment=disk_attachment
                )
        else:
            with pytest.raises(ValidationError):
                DiskAttachment(**disk_data)

    @responses.activate
    def test_create_disk_success(
        self,
        storage_client: StorageClient,
        test_data: Dict[str, Any],
        base_url: str,
    ) -> None:
        """Tests successful disk creation with valid parameters.

        Args:
          storage_client (StorageClient): The StorageClient fixture under test.
          test_data (Dict[str, Any]): A dictionary of test disk data.
          base_url (str): The base URL for the API.
        """
        project_id = test_data["project_id"]
        disk_data = {k: v for k, v in test_data.items() if k != "project_id"}

        endpoint = f"/marketplace/v1/projects/{project_id}/disks"
        responses.add(
            responses.POST,
            _url(base_url, endpoint),
            json={
                "disk_id": disk_data["disk_id"],
                "name": disk_data["name"],
                "disk_interface": disk_data["disk_interface"],
                "region_id": disk_data["region_id"],
                "size": disk_data["size"],
                "size_unit": disk_data["size_unit"],
            },
            status=201,
            content_type="application/json",
        )

        disk_attachment = DiskAttachment(**disk_data)
        response = storage_client.create_disk(
            project_id=project_id, disk_attachment=disk_attachment
        )

        assert isinstance(response, DiskResponse)
        assert response.disk_id == disk_data["disk_id"]
        assert response.name == disk_data["name"]
        assert response.disk_interface == "Block"

    @pytest.mark.parametrize(
        "status_code,expected_exception",
        [
            (400, APIError),
            (401, AuthenticationError),
            (403, AuthenticationError),
            (500, APIError),
        ],
    )
    @responses.activate
    def test_create_disk_api_errors(
        self,
        storage_client: StorageClient,
        test_data: Dict[str, Any],
        base_url: str,
        status_code: int,
        expected_exception: Type[Exception],
    ) -> None:
        """Ensures proper exception handling for various HTTP status codes.

        Args:
          storage_client (StorageClient): The StorageClient fixture under test.
          test_data (Dict[str, Any]): A dictionary of test disk data.
          base_url (str): The base URL for the API.
          status_code (int): The HTTP status code to simulate.
          expected_exception (Type[Exception]): The exception expected.
        """
        project_id = test_data["project_id"]
        disk_data = {k: v for k, v in test_data.items() if k != "project_id"}

        endpoint = f"/marketplace/v1/projects/{project_id}/disks"
        responses.add(
            responses.POST,
            _url(base_url, endpoint),
            json={"error": "Error occurred"},
            status=status_code,
            content_type="application/json",
        )

        disk_attachment = DiskAttachment(**disk_data)
        with pytest.raises(expected_exception):
            storage_client.create_disk(
                project_id=project_id, disk_attachment=disk_attachment
            )

    @pytest.mark.parametrize(
        "exception_cls,expected_exception",
        [
            (requests.exceptions.ConnectionError, NetworkError),
            (requests.exceptions.Timeout, TimeoutError),
        ],
    )
    def test_create_disk_network_errors(
        self,
        storage_client: StorageClient,
        test_data: Dict[str, Any],
        exception_cls: Type[Exception],
        expected_exception: Type[Exception],
    ) -> None:
        """Tests handling of network-related exceptions during disk creation.

        Args:
          storage_client (StorageClient): The StorageClient fixture under test.
          test_data (Dict[str, Any]): A dictionary of test disk data.
          exception_cls (Type[Exception]): The requests library exception to simulate.
          expected_exception (Type[Exception]): The exception type that should be raised.
        """
        project_id = test_data["project_id"]
        disk_data = {k: v for k, v in test_data.items() if k != "project_id"}
        disk_attachment = DiskAttachment(**disk_data)

        with patch(
            "requests.Session.request", side_effect=exception_cls("Network error")
        ):
            with pytest.raises(expected_exception):
                storage_client.create_disk(
                    project_id=project_id, disk_attachment=disk_attachment
                )

    @responses.activate
    def test_create_disk_invalid_json_response(
        self,
        storage_client: StorageClient,
        test_data: Dict[str, Any],
        base_url: str,
    ) -> None:
        """Tests handling of an invalid JSON response from the server.

        Args:
          storage_client (StorageClient): The StorageClient fixture under test.
          test_data (Dict[str, Any]): A dictionary of test disk data.
          base_url (str): The base URL for the API.
        """
        project_id = test_data["project_id"]
        disk_data = {k: v for k, v in test_data.items() if k != "project_id"}

        endpoint = f"/marketplace/v1/projects/{project_id}/disks"
        responses.add(
            responses.POST,
            _url(base_url, endpoint),
            body="Not a JSON response",
            status=200,
            content_type="application/json",
        )

        disk_attachment = DiskAttachment(**disk_data)
        with pytest.raises(InvalidResponseError):
            storage_client.create_disk(
                project_id=project_id, disk_attachment=disk_attachment
            )

    @pytest.mark.parametrize("disk_interface_input", ["block", "Block", "BLOCK"])
    @responses.activate
    def test_create_disk_interface_case_handling(
        self,
        storage_client: StorageClient,
        test_data: Dict[str, Any],
        base_url: str,
        disk_interface_input: str,
    ) -> None:
        """Tests disk creation with different interface casing.

        Args:
          storage_client (StorageClient): The StorageClient fixture under test.
          test_data (Dict[str, Any]): A dictionary of test disk data.
          base_url (str): The base URL for the API.
          disk_interface_input (str): The disk interface value to use in different
            cases.
        """
        project_id = test_data["project_id"]
        disk_data = {k: v for k, v in test_data.items() if k != "project_id"}
        disk_data["disk_interface"] = disk_interface_input

        endpoint_pattern = re.compile(
            rf"{_url(base_url, f'/marketplace/v1/projects/{project_id}/disks')}"
        )
        responses.add(
            responses.POST,
            endpoint_pattern,
            json={
                "disk_id": disk_data["disk_id"],
                "name": disk_data["name"],
                "disk_interface": "Block",
                "region_id": disk_data["region_id"],
                "size": disk_data["size"],
                "size_unit": disk_data["size_unit"],
            },
            status=201,
            content_type="application/json",
        )

        disk_attachment = DiskAttachment(**disk_data)
        response = storage_client.create_disk(
            project_id=project_id, disk_attachment=disk_attachment
        )

        assert isinstance(response, DiskResponse)
        assert response.disk_id == disk_data["disk_id"]
        assert response.disk_interface == "Block"

    @pytest.mark.parametrize("disk_interface_input", ["invalid_interface", None])
    def test_create_disk_invalid_interface(
        self,
        storage_client: StorageClient,
        test_data: Dict[str, Any],
        disk_interface_input: Optional[str],
    ) -> None:
        """Tests disk creation with invalid disk interface values.

        Args:
          storage_client (StorageClient): The StorageClient fixture under test.
          test_data (Dict[str, Any]): A dictionary of test disk data.
          disk_interface_input (Optional[str]): The invalid disk interface value
            to test.

        Raises:
          ValidationError: If a Pydantic validation fails.
          ValueError: If the interface is invalid during creation.
        """
        data = test_data.copy()
        data["disk_interface"] = disk_interface_input
        disk_data = {k: v for k, v in data.items() if k != "project_id"}

        with pytest.raises((ValidationError, ValueError)):
            DiskAttachment(**disk_data)

    @responses.activate
    def test_create_disk_large_size(
        self, storage_client: StorageClient, test_data: Dict[str, Any], base_url: str
    ) -> None:
        """Tests disk creation with a large size value.

        Args:
          storage_client (StorageClient): The StorageClient fixture under test.
          test_data (Dict[str, Any]): A dictionary of test disk data.
          base_url (str): The base URL for the API.
        """
        data = test_data.copy()
        data["size"] = 1024 * 1024  # 1TB in GB
        project_id = data["project_id"]
        disk_data = {k: v for k, v in data.items() if k != "project_id"}

        responses.add(
            responses.POST,
            _url(base_url, f"/marketplace/v1/projects/{project_id}/disks"),
            json={
                "disk_id": disk_data["disk_id"],
                "name": disk_data["name"],
                "disk_interface": disk_data["disk_interface"],
                "region_id": disk_data["region_id"],
                "size": disk_data["size"],
                "size_unit": disk_data["size_unit"],
            },
            status=201,
            content_type="application/json",
        )

        disk_attachment = DiskAttachment(**disk_data)
        response = storage_client.create_disk(
            project_id=project_id, disk_attachment=disk_attachment
        )
        assert isinstance(response, DiskResponse)
        assert response.disk_id == disk_data["disk_id"]
        assert response.size == 1024 * 1024

    @pytest.mark.parametrize("num_threads", [5])
    @patch("flow.clients.http_client.HTTPClient.request")
    @patch("flow.clients.authenticator.Authenticator.get_access_token")
    def test_concurrent_disk_creation(
        self,
        mock_get_token,
        mock_request,
        num_threads: int,
        storage_client: StorageClient,
        test_data: Dict[str, Any],
    ) -> None:
        """
        Tests concurrent disk creation requests using multi-threading.
        Patches are done at the test method level (not inside each thread).
        """
        # Provide a valid token so we never hit "Unauthorized"
        mock_get_token.return_value = "thread_auth_token"

        # Make the mock request side effect read the 'disk_id' we send in the JSON payload
        def concurrency_side_effect(*args, **kwargs):
            data_json = kwargs.get("json") or {}
            request_disk_id = data_json.get("disk_id", "default-disk")

            response = Mock()
            response.status_code = 201
            # Mirror the requested disk_id in the response
            response.json.return_value = {
                "disk_id": request_disk_id,
                "name": data_json.get("name", "mocked-disk"),
                "disk_interface": data_json.get("disk_interface", "Block"),
                "region_id": data_json.get("region_id", "us-central1-a"),
                "size": data_json.get("size", 10),
                "size_unit": data_json.get("size_unit", "gb"),
            }
            return response

        mock_request.side_effect = concurrency_side_effect

        # We can now concurrently create disks
        lock = threading.Lock()
        results: List[tuple[str, Any]] = []

        project_id = test_data["project_id"]

        def create_disk_thread(disk_suffix: int) -> None:
            # Copy test_data so each thread can mutate safely
            data = test_data.copy()
            disk_id_specific = str(uuid.uuid4())
            data["name"] = f"test-disk-thread-{disk_suffix}"
            data["disk_id"] = disk_id_specific

            disk_attachment = DiskAttachment(
                **{k: v for k, v in data.items() if k != "project_id"}
            )

            try:
                response = storage_client.create_disk(
                    project_id=project_id,
                    disk_attachment=disk_attachment,
                )
                with lock:
                    results.append((disk_id_specific, response))
            except Exception as exc:
                with lock:
                    results.append((disk_id_specific, exc))

        threads = []
        for i in range(num_threads):
            thread = threading.Thread(target=create_disk_thread, args=(i,))
            threads.append(thread)
            thread.start()

        for thread in threads:
            thread.join()

        # Finally, validate each result
        for requested_disk_id, result in results:
            if isinstance(result, Exception):
                pytest.fail(f"Disk {requested_disk_id} creation failed: {result}")

            assert isinstance(result, DiskResponse), "Expected a DiskResponse object"
            # The returned disk_id should match what we requested
            assert (
                result.disk_id == requested_disk_id
            ), f"Expected disk_id={requested_disk_id}, got {result.disk_id}"
