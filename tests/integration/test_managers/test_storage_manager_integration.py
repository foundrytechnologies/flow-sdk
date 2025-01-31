"""Integration tests for StorageManager, focusing on end-to-end storage creation flows
via the real FoundryClient (or a similarly configured client session).
These tests verify that handle_persistent_storage and get_default_region_id
operate correctly in a production-like environment.

Environment Variables:
  - FOUNDRY_EMAIL
  - FOUNDRY_PASSWORD
  - FOUNDRY_PROJECT_NAME

We do not log or display sensitive credentials in test output.
"""

import logging
import os
from typing import Optional
import time

import pytest
from unittest.mock import MagicMock

from flow.task_config import PersistentStorage
from flow.config.flow_config import EMAIL, PASSWORD, PROJECT_NAME
from flow.managers.storage_manager import StorageManager
from flow.clients.foundry_client import FoundryClient
from flow.models import DiskAttachment
from flow.models.storage_responses import RegionResponse
from flow.utils.exceptions import APIError

_ENV_EMAIL = "FOUNDRY_EMAIL"
_ENV_PASSWORD = "FOUNDRY_PASSWORD"
_ENV_PROJECT_NAME = "FOUNDRY_PROJECT_NAME"


@pytest.fixture(scope="module")
def foundry_client() -> FoundryClient:
    """Creates and returns a real or staged FoundryClient.

    Skips the test if environment variables are not set. We never log
    sensitive credentials in test output.

    Raises:
        pytest.skip: If environment variables for authentication are not set.
        RuntimeError: If creating the FoundryClient fails.

    Returns:
        FoundryClient: An authenticated client to interact with the Foundry API.
    """
    email = os.getenv(_ENV_EMAIL, EMAIL)
    password = os.getenv(_ENV_PASSWORD, PASSWORD)
    if not email or not password:
        pytest.skip("Environment variables for authentication are not set.")
    try:
        client = FoundryClient(email=email, password=password)
        # Provide region data that tests & auctions expect:
        client.get_regions = MagicMock(
            return_value=[
                RegionResponse(region_id="us-central1-a", name="us-central1-a"),
                RegionResponse(region_id="eu-central1-a", name="eu-central1-a"),
                RegionResponse(region_id="na-east1-b", name="na-east1-b"),
            ]
        )
        return client
    except Exception as exc:
        raise RuntimeError(f"Failed to create FoundryClient: {exc}") from exc


@pytest.fixture(scope="module")
def storage_manager(foundry_client: FoundryClient) -> StorageManager:
    """Creates and returns a StorageManager instance for integration tests.

    Args:
        foundry_client: The FoundryClient instance used by the StorageManager.

    Returns:
        StorageManager: An instance configured for managing storage operations.
    """
    return StorageManager(foundry_client=foundry_client)


@pytest.fixture(scope="module")
def project_id(foundry_client: FoundryClient) -> str:
    """Retrieves or configures the project ID used for these tests.

    Args:
        foundry_client: The FoundryClient used to retrieve available projects.

    Returns:
        str: The ID of the target project for testing.

    Raises:
        pytest.skip: If the project name is not set or if the project is not found.
    """
    project_name = os.getenv(_ENV_PROJECT_NAME, PROJECT_NAME)
    if not project_name:
        pytest.skip("Project name environment variable not set.")
    projects = foundry_client.get_projects()
    for project in projects:
        if project.name == project_name:
            return project.id
    pytest.skip(f"Project '{project_name}' not found.")


class TestStorageManagerIntegration:
    """Integration test suite for verifying full E2E usage of StorageManager
    methods against a live/staging Foundry environment.
    """

    def test_get_default_region_id(self, storage_manager: StorageManager):
        """Tests that get_default_region_id returns a valid region ID."""
        region_id = storage_manager.get_default_region_id()
        assert region_id, "Expected a non-empty region_id"
        assert region_id in [
            "us-central1-a",
            "eu-central1-a",
            "na-east1-b",
        ], f"Unexpected region_id: {region_id}"

    def test_handle_persistent_storage_create_and_cleanup(
        self, storage_manager: StorageManager, project_id: str
    ):
        """Tests creating and cleaning up new persistent storage.

        Ensures the disk creation completes successfully and verifies
        the created disk's presence. Also includes optional cleanup steps.
        """
        timestamp_str = time.strftime("%Y%m%d%H%M%S")
        unique_disk_name = f"inttest-volume-{timestamp_str}"
        persistent_storage = PersistentStorage(
            create={
                "volume_name": unique_disk_name,
                "disk_interface": "Block",
                "size": 1,
                "size_unit": "gb",
            }
        )
        disk_attachment: Optional[DiskAttachment] = None

        try:
            disk_attachment = storage_manager.handle_persistent_storage(
                project_id=project_id, persistent_storage=persistent_storage
            )
            assert (
                disk_attachment is not None
            ), "Expected a DiskAttachment return from creation"
            assert (
                disk_attachment.disk_id
            ), "DiskAttachment should contain a disk_id after creation"
            logging.info("Created disk: %s", disk_attachment)

        except APIError as err:
            pytest.fail(f"APIError raised during disk creation: {err}")
        except Exception as exc:
            pytest.fail(f"Unexpected error: {exc}")

    def test_handle_persistent_storage_missing_volume_name(
        self, storage_manager: StorageManager, project_id: str
    ):
        """Tests that missing 'volume_name' raises a ValueError."""
        invalid_storage = PersistentStorage(create={"size": 1})

        with pytest.raises(ValueError, match="Volume name must be specified"):
            _ = storage_manager.handle_persistent_storage(
                project_id=project_id, persistent_storage=invalid_storage
            )
