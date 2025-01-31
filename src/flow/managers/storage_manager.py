import logging
import re
import uuid
from typing import Optional

from flow.task_config import PersistentStorage
from flow.clients.foundry_client import FoundryClient
from flow.logging.spinner_logger import SpinnerLogger
from flow.models import DiskAttachment
from flow.utils.exceptions import APIError


class StorageManager:
    """Manages storage creation and attachment."""

    def __init__(self, foundry_client: FoundryClient):
        """Initializes StorageManager.

        Args:
            foundry_client: The FoundryClient to use for disk operations.
        """
        self.foundry_client = foundry_client
        self.logger = logging.getLogger(__name__)
        self.spinner_logger = SpinnerLogger(self.logger)

    def looks_like_uuid(self, value: str) -> bool:
        """Checks whether the given string matches a UUID v4 pattern.

        Args:
            value: A string to test.

        Returns:
            True if the string matches the UUID v4 pattern, otherwise False.
        """
        return bool(re.match(r"^[0-9a-fA-F-]{36}$", value))

    def handle_persistent_storage(
        self,
        project_id: str,
        persistent_storage: PersistentStorage,
        region_id: Optional[str] = None,
    ) -> Optional[DiskAttachment]:
        """Handles creating or attaching persistent storage.

        If there's no 'create' configuration in persistent_storage, returns None.
        If region_id is not provided, uses the default region. Attempts to create
        a new disk in Foundry if the configuration indicates so.

        Args:
            project_id: The project ID.
            persistent_storage: The PersistentStorage object with configuration.
            region_id: Optional region identifier.

        Returns:
            A DiskAttachment object if a disk is created, or None if 'create' is
            not specified.

        Raises:
            ValueError: If volume name or disk size is not specified.
            APIError: If an API error occurs during disk creation.
            Exception: If an unexpected error occurs during disk creation.
        """
        if persistent_storage.create:
            if not region_id:
                region_id = self.get_default_region_id()

            create_config = persistent_storage.create
            volume_name = create_config.volume_name
            disk_interface = create_config.disk_interface or "Block"
            size = create_config.size
            size_unit = getattr(create_config, "size_unit", None)
            size_unit = size_unit.lower() if size_unit else "gb"

            if not volume_name:
                raise ValueError("Volume name must be specified.")
            if not size:
                raise ValueError("Disk size must be specified.")

            unique_id = uuid.uuid4().hex
            volume_name = f"{volume_name}-{unique_id}"
            disk_id = str(uuid.uuid4())

            disk_attachment = DiskAttachment(
                disk_id=disk_id,
                name=volume_name,
                volume_name=volume_name,
                disk_interface=disk_interface,
                region_id=region_id,
                size=size,
                size_unit=size_unit,
            )

            self.logger.info("Creating persistent storage...")
            try:
                response = self.foundry_client.create_disk(project_id, disk_attachment)
                self.logger.debug("Disk creation response: %s", response)
                disk_attachment.disk_id = response.disk_id
            except APIError as api_err:
                self.logger.error("APIError during disk creation: %s", api_err)
                raise APIError(f"Failed to create disk: {api_err}") from api_err
            except Exception as err:
                self.logger.error("Unexpected error during disk creation: %s", err)
                raise

            self.logger.info("Persistent storage creation completed!")
            return disk_attachment
        elif persistent_storage.attach:
            # Handle attach case
            attach_config = persistent_storage.attach
            # Resolve region_id if not provided
            if not region_id:
                region_id = attach_config.region_id
            if not region_id:
                region_id = self.get_default_region_id()
            # Fetch disk information using get_disk
            disk = self.foundry_client.get_disk(
                project_id=project_id, disk_id=attach_config.volume_name
            )
            # Create DiskAttachment from fetched disk information
            disk_attachment = DiskAttachment(
                disk_id=disk.disk_id,
                name=disk.name,
                volume_name=disk.volume_name,
                disk_interface=disk.disk_interface,
                region_id=region_id,
                size=disk.size,
                size_unit=disk.size_unit,
            )
            return disk_attachment
        else:
            return None

    def get_default_region_id(self) -> str:
        """Retrieves the default region ID from available regions in Foundry.

        Returns:
            A string representing the default region ID.

        Raises:
            Exception: If no regions are available or if an error occurs.
        """
        try:
            regions = self.foundry_client.get_regions()
            if not regions:
                raise Exception("No regions available.")
            default_region = regions[0]
            region_id = default_region.region_id
            self.logger.debug("Default region ID: %s", region_id)
            return region_id
        except Exception as err:
            self.logger.error("Failed to get default region ID: %s", err)
            raise
