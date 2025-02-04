"""Foundry client.

This module provides the FoundryClient class, a high-level interface to interact
with the Foundry Cloud Platform (FCP). It encapsulates operations for managing
projects, instances, bids, storage resources, and more, providing a unified API
for use in downstream environments.

This client supports authentication via either an API key (using FOUNDRY_API_KEY) 
or via email and password.
"""

import logging
from typing import Dict, List, Optional

from flow.clients.authenticator import Authenticator
from flow.clients.fcp_client import FCPClient
from flow.clients.storage_client import StorageClient
from flow.models import (
    Auction,
    Bid,
    BidPayload,
    BidResponse,
    DetailedInstanceType,
    DiskAttachment,
    DiskResponse,
    Project,
    RegionResponse,
    StorageQuotaResponse,
    SshKey,
    Instance,
    User,
)
from flow.utils.exceptions import APIError


class FoundryClient:
    """Client for interacting with the Foundry Cloud Platform (FCP).

    This class provides convenience methods for managing users, projects,
    instances, bids, storage resources, and regions via underlying FCPClient
    and StorageClient instances.

    Example:
        client = FoundryClient(email="user@example.com", password="secret")
        user = client.get_user()
        projects = client.get_projects()
    """

    def __init__(
        self,
        email: Optional[str] = None,
        password: Optional[str] = None,
        api_key: Optional[str] = None,
    ) -> None:
        """Initialize the FoundryClient with either an API key or email/password.

        Args:
            email (Optional[str]): The user's email.
            password (Optional[str]): The user's password.
            api_key (Optional[str]): The user's API key (if using API key auth).
        """
        self._logger = logging.getLogger(__name__)
        self._logger.debug("Initializing FoundryClient with credentials.")

        # Deciding whether to use API key or email/password
        if api_key:
            self._authenticator: Authenticator = Authenticator(api_key=api_key)
        else:
            # Fallback to email/password
            if not email or not password:
                raise ValueError("Either api_key or email/password must be provided.")
            self._authenticator = Authenticator(email=email, password=password)

        self.fcp_client: FCPClient = FCPClient(authenticator=self._authenticator)
        self.storage_client: StorageClient = StorageClient(
            authenticator=self._authenticator
        )
        self._logger.info("FoundryClient initialized successfully.")

    # =========================================================================
    #                           FCPClient Methods
    # =========================================================================

    def get_user(self) -> User:
        """Retrieve the currently authenticated user's profile.

        Returns:
            User: The profile details of the authenticated user.
        """
        self._logger.debug("Retrieving user information via FCPClient.")
        return self.fcp_client.get_user()

    def get_projects(self) -> List[Project]:
        """Fetch all accessible projects for the authenticated user.

        Returns:
            List[Project]: A list of projects accessible to the user.
        """
        self._logger.debug("Fetching projects from FCPClient.")
        return self.fcp_client.get_projects()

    def get_project_by_name(self, project_name: str) -> Project:
        """Retrieve a project by its name.

        Args:
            project_name (str): The human-readable name of the project to locate.

        Returns:
            Project: A project matching the given name.

        Raises:
            ValueError: If no project with the specified name is found.
        """
        self._logger.debug("Looking up project by name=%s", project_name)
        return self.fcp_client.get_project_by_name(project_name=project_name)

    def get_instances(self, project_id: str) -> Dict[str, List[Instance]]:
        """Retrieve instances within a project, categorized by type.

        Args:
            project_id (str): The unique identifier of the project.

        Returns:
            Dict[str, List[Instance]]: A dictionary mapping instance categories
            (e.g., "spot", "reserved") to lists of instances.
        """
        self._logger.debug("Fetching instances for project_id=%s", project_id)
        return self.fcp_client.get_instances(project_id=project_id)

    def get_auctions(self, project_id: str) -> List[Auction]:
        """Fetch auctions for a specified project.

        Args:
            project_id (str): The unique identifier of the project.

        Returns:
            List[Auction]: A list of auctions for the project.

        Raises:
            Exception: For any error encountered during retrieval.
        """
        self._logger.debug("Fetching auctions for project_id=%s", project_id)
        try:
            auctions: List[Auction] = self.fcp_client.get_auctions(
                project_id=project_id
            )
            self._logger.debug("Successfully retrieved %d auctions.", len(auctions))
            return auctions
        except Exception as exc:
            self._logger.error(
                "Failed to fetch auctions for project_id=%s: %s",
                project_id,
                exc,
                exc_info=True,
            )
            raise

    def get_ssh_keys(self, project_id: str) -> List[SshKey]:
        """Retrieve the SSH keys associated with a project.

        Args:
            project_id (str): The unique identifier of the project.

        Returns:
            List[SshKey]: A list of SSH keys for the project.
        """
        self._logger.debug("Fetching SSH keys for project_id=%s", project_id)
        return self.fcp_client.get_ssh_keys(project_id=project_id)

    def get_bids(self, project_id: str) -> List[Bid]:
        """Retrieve all bids placed within a project.

        Args:
            project_id (str): The unique identifier of the project.

        Returns:
            List[Bid]: A list of bids placed in the project.
        """
        self._logger.debug("Fetching bids for project_id=%s", project_id)
        return self.fcp_client.get_bids(project_id=project_id)

    def place_bid(self, project_id: str, bid_payload: BidPayload) -> BidResponse:
        """Place a bid in a specified project.

        The bid payload is updated with the project ID before sending.

        Args:
            project_id (str): The unique identifier of the project.
            bid_payload (BidPayload): The bid payload (excluding project_id).

        Returns:
            BidResponse: Details of the placed bid.

        Raises:
            Exception: If an error occurs during bid placement.
        """
        self._logger.debug(
            "Placing bid on project_id=%s with payload=%s",
            project_id,
            bid_payload.model_dump(),
        )
        try:
            updated_payload: BidPayload = bid_payload.model_copy(
                update={"project_id": project_id}
            )
            bid_response: BidResponse = self.fcp_client.place_bid(updated_payload)
            self._logger.debug(
                "Bid placed successfully. Response=%s", bid_response.model_dump()
            )
            return bid_response
        except Exception as exc:
            self._logger.error(
                "Error placing bid on project_id=%s: %s", project_id, exc, exc_info=True
            )
            raise

    def cancel_bid(self, project_id: str, bid_id: str) -> None:
        """Cancel an existing bid in a project.

        Args:
            project_id (str): The unique identifier of the project.
            bid_id (str): The identifier of the bid to cancel.
        """
        self._logger.debug("Canceling bid_id=%s for project_id=%s", bid_id, project_id)
        self.fcp_client.cancel_bid(project_id=project_id, bid_id=bid_id)
        self._logger.info(
            "Canceled bid_id=%s in project_id=%s successfully.", bid_id, project_id
        )

    def get_instance_type(self, instance_type_id: str) -> DetailedInstanceType:
        """Retrieve details for a specified instance type.

        If the instance type is not found (HTTP 404), a fallback DetailedInstanceType
        is returned instead of raising an exception.

        Note:
            This method directly calls a protected member of FCPClient. Consider
            refactoring FCPClient to expose a public API for retrieving instance types.

        Args:
            instance_type_id (str): The unique identifier of the instance type.

        Returns:
            DetailedInstanceType: The instance type details if found; otherwise a fallback
            object with default values.

        Raises:
            APIError: If an API error occurs that is not a 404.
        """
        self._logger.debug("Fetching instance type for id=%s", instance_type_id)
        try:
            response = self.fcp_client._request(
                method="GET",
                path=f"/instance_types/{instance_type_id}",
            )
            data = response.json()
            self._logger.info(
                "Retrieved instance_type data for %s: %s", instance_type_id, data
            )
            return DetailedInstanceType(**data)
        except APIError as err:
            # Determine if the error indicates a missing instance type (404).
            not_found: bool = "404" in str(err) or (
                hasattr(err, "response")
                and err.response is not None
                and err.response.status_code == 404
            )
            if not_found:
                self._logger.warning(
                    "InstanceType id=%s not found, returning fallback.",
                    instance_type_id,
                )
                return DetailedInstanceType(
                    id=instance_type_id,
                    name="[Unknown / Not Found]",
                    num_cpus=None,
                    num_gpus=None,
                    memory_gb=None,
                    architecture=None,
                )
            self._logger.error(
                "Failed to retrieve instance_type id=%s due to APIError: %s",
                instance_type_id,
                err,
            )
            raise

    # =========================================================================
    #                        StorageClient Methods
    # =========================================================================

    def create_disk(
        self, project_id: str, disk_attachment: DiskAttachment
    ) -> DiskResponse:
        """Create a new disk within the specified project.

        Args:
            project_id (str): The unique identifier of the project.
            disk_attachment (DiskAttachment): The disk configuration.

        Returns:
            DiskResponse: Details of the created disk.
        """
        self._logger.debug(
            "Creating disk in project_id=%s with disk_id=%s",
            project_id,
            disk_attachment.disk_id,
        )
        disk_response: DiskResponse = self.storage_client.create_disk(
            project_id=project_id,
            disk_attachment=disk_attachment,
        )
        self._logger.debug("Created disk successfully: %s", disk_response.model_dump())
        return disk_response

    def get_disks(self, project_id: str) -> List[DiskResponse]:
        """Retrieve all disks associated with a project.

        Args:
            project_id (str): The unique identifier of the project.

        Returns:
            List[DiskResponse]: A list of disks in the project.
        """
        self._logger.debug("Fetching disks for project_id=%s", project_id)
        disks: List[DiskResponse] = self.storage_client.get_disks(project_id=project_id)
        self._logger.debug("Retrieved %d disks.", len(disks))
        return disks

    def delete_disk(self, project_id: str, disk_id: str) -> None:
        """Delete a disk from the specified project.

        Args:
            project_id (str): The unique identifier of the project.
            disk_id (str): The unique identifier of the disk to delete.
        """
        self._logger.debug(
            "Deleting disk_id=%s from project_id=%s", disk_id, project_id
        )
        self.storage_client.delete_disk(project_id=project_id, disk_id=disk_id)
        self._logger.info(
            "Deleted disk_id=%s from project_id=%s successfully.", disk_id, project_id
        )

    def get_storage_quota(self, project_id: str) -> StorageQuotaResponse:
        """Retrieve the storage quota details for a project.

        Args:
            project_id (str): The unique identifier of the project.

        Returns:
            StorageQuotaResponse: The quota details for the project.
        """
        self._logger.debug("Fetching storage quota for project_id=%s", project_id)
        quota: StorageQuotaResponse = self.storage_client.get_storage_quota(
            project_id=project_id
        )
        self._logger.debug("Retrieved storage quota: %s", quota.model_dump())
        return quota

    def get_regions(self) -> List[RegionResponse]:
        """Retrieve all available regions from the Foundry platform.

        Returns:
            List[RegionResponse]: A list of regions available.

        Raises:
            Exception: If fetching regions fails.
        """
        self._logger.debug("Fetching all regions from StorageClient.")
        try:
            regions: List[RegionResponse] = self.storage_client.get_regions()
            self._logger.debug("Retrieved %d region(s).", len(regions))
            return regions
        except Exception as exc:
            self._logger.error("Failed to retrieve regions: %s", exc, exc_info=True)
            raise

    def get_disk(self, project_id: str, disk_id: str) -> DiskResponse:
        """Retrieve detailed information about a disk in a project.

        Args:
            project_id (str): The unique identifier of the project.
            disk_id (str): The unique identifier of the disk.

        Returns:
            DiskResponse: Detailed disk information.
        """
        self._logger.debug("Fetching disk_id=%s in project_id=%s", disk_id, project_id)
        disk_info: DiskResponse = self.storage_client.get_disk(
            project_id=project_id, disk_id=disk_id
        )
        self._logger.debug("Fetched disk info: %s", disk_info.model_dump())
        return disk_info

    def get_region_id_by_name(self, region_name: str) -> str:
        """Look up a region's unique identifier by its human-friendly name.

        Args:
            region_name (str): The name of the region (e.g., 'us-central1-a').

        Returns:
            str: The unique identifier corresponding to the given region name.

        Raises:
            ValueError: If no region with the specified name is found.
        """
        self._logger.debug("Looking up region ID for region_name='%s'", region_name)
        all_regions: List[RegionResponse] = self.get_regions()
        self._logger.debug("Retrieved %d region(s) total.", len(all_regions))

        for region in all_regions:
            self._logger.debug(
                "Examining region '%s' (id=%s)", region.name, region.region_id
            )
            if region.name == region_name:
                self._logger.debug(
                    "Matched region_name='%s' -> region_id='%s'",
                    region_name,
                    region.region_id,
                )
                return region.region_id

        self._logger.warning("No matching region found for name='%s'", region_name)
        raise ValueError(f"Region not found for name='{region_name}'")
