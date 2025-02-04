"""FlowTaskManager module.

This module provides the FlowTaskManager class, which orchestrates the execution
of tasks to Foundry. It handles YAML config parsing, startup script creation
(including ephemeral/persistent storage and port forwarding logic), user authentication,
auction lookup, bid payload preparation, and bid submission.
"""

from __future__ import annotations

import base64
import gzip
import json
import logging
from io import BytesIO
from typing import Any, List, Optional, Tuple

from flow.clients.foundry_client import FoundryClient
from flow.config import get_config
from flow.formatters.table_formatter import TableFormatter
from flow.logging.spinner_logger import SpinnerLogger
from flow.managers.auction_finder import AuctionFinder
from flow.managers.bid_manager import BidManager
from flow.managers.instance_manager import InstanceManager
from flow.managers.storage_manager import StorageManager
from flow.models import (
    Auction,
    Bid,
    BidDiskAttachment,
    BidPayload,
    Project,
    SshKey,
    User,
)
from flow.task_config import (
    ConfigModel,
    EphemeralStorageConfig,
    PersistentStorage,
    Port,
    ResourcesSpecification,
    TaskManagement,
    ContainerImageConfig,
)
from flow.task_config.config_parser import ConfigParser
from flow.startup_script_builder.startup_script_builder import StartupScriptBuilder

# Global settings loaded from configuration.
_SETTINGS = get_config()


class AuthenticationError(Exception):
    """Exception raised when user authentication fails."""


class NoMatchingAuctionsError(Exception):
    """Exception raised when no matching auctions are found."""


class BidSubmissionError(Exception):
    """Exception raised when there is an error submitting a bid."""


class FlowTaskManager:
    """Manages the execution of tasks within the Flow system.

    This class reads configuration from a ConfigParser, builds the startup script,
    prepares bid details, and submits the bid to Foundry. It requires the Foundry project name
    and SSH key name to be provided (via CLI, config, or interactive prompt).

    Attributes:
        config_parser (Optional[ConfigParser]): The configuration parser instance.
        foundry_client (FoundryClient): Client for interacting with Foundry.
        auction_finder (Optional[AuctionFinder]): Auction finder instance.
        bid_manager (Optional[BidManager]): Bid manager instance.
        project_name (str): Foundry project name.
        ssh_key_name (str): Foundry SSH key name.
    """

    def __init__(
        self,
        config_parser: Optional[ConfigParser],
        foundry_client: FoundryClient,
        auction_finder: Optional[AuctionFinder],
        bid_manager: Optional[BidManager],
        project_name: str,
        ssh_key_name: str,
    ) -> None:
        """Initializes the FlowTaskManager.

        Args:
            config_parser (Optional[ConfigParser]): Instance with user-defined settings.
            foundry_client (FoundryClient): A client for Foundry API operations.
            auction_finder (Optional[AuctionFinder]): Finder for locating suitable auctions.
            bid_manager (Optional[BidManager]): Prepares and submits bids.
            project_name (str): Foundry project name.
            ssh_key_name (str): Foundry SSH key name.
        """
        self.config_parser = config_parser
        self.foundry_client = foundry_client
        self.auction_finder = auction_finder
        self.bid_manager = bid_manager
        self.project_name = project_name
        self.ssh_key_name = ssh_key_name

        self.logger = logging.getLogger(__name__)
        self.logger.debug("Initialized FlowTaskManager instance.")

        self.instance_manager = InstanceManager(foundry_client=self.foundry_client)
        self.storage_manager = StorageManager(foundry_client=self.foundry_client)
        self.logger.debug("StorageManager initialized.")

        self.logger_manager = SpinnerLogger(self.logger)

    # =========================================================================
    # Public API Methods
    # =========================================================================

    def run(self) -> None:
        """Executes the primary Flow task operation.

        Parses the configuration, builds the startup script, and submits the bid.
        """
        with self.logger_manager.spinner(""):
            if not self.config_parser:
                self.logger.error("ConfigParser is required to run the task manager.")
                raise ValueError("ConfigParser is required to run the task manager.")

            self.logger.info("Starting the flow task execution.")

            # Parse and validate configuration.
            self.logger.debug("Parsing configuration from ConfigParser.")
            config: ConfigModel = self.config_parser.config
            if not config:
                raise ValueError("Configuration data is missing or invalid.")

            # Extract essential data from configuration.
            self.logger.debug("Extracting and preparing data from configuration.")
            (
                task_name,
                resources_specification,
                limit_price_cents,
                ports,
            ) = self._extract_and_prepare_data(config=config)
            self.logger.info(
                "Data extraction complete. Task name: %s, Limit price (cents): %d, Ports: %s",
                task_name,
                limit_price_cents,
                ports,
            )

            # Build the full startup script with storage and port settings.
            self.logger.debug("Building final startup script with storage and ports.")
            full_startup_script: str = self._build_full_startup_script(config, ports)

            # Create a bootstrap script to handle large startup scripts.
            self.logger.debug(
                "Creating bootstrap script for large startup script scenarios."
            )
            builder = StartupScriptBuilder(logger=self.logger)
            builder.inject_bootstrap_script(full_startup_script)
            startup_script_bootstrap: str = builder.build_script()

            self.logger.info("Startup script(s) built and compressed (if needed).")

            # Authenticate the user and retrieve user/project/SSH key data.
            self.logger.debug("Authenticating user and retrieving project data.")
            user_id, project_id, ssh_key_id = self._authenticate_and_get_user_data()
            self.logger.info(
                "Authentication successful. User ID: %s, Project ID: %s, SSH Key ID: %s",
                user_id,
                project_id,
                ssh_key_id,
            )

            # Retrieve and filter auctions matching the required resources.
            self.logger.debug("Finding matching auctions for the given resources.")
            matching_auctions: List[Auction] = self._find_matching_auctions(
                project_id=project_id, resources_specification=resources_specification
            )
            self.logger.info("%d matching auctions found.", len(matching_auctions))

            # Prepare and submit the bid.
            self.logger.debug("Preparing and submitting the bid to Foundry.")
            self._prepare_and_submit_bid(
                matching_auctions=matching_auctions,
                resources_specification=resources_specification,
                limit_price_cents=limit_price_cents,
                task_name=task_name,
                project_id=project_id,
                ssh_key_id=ssh_key_id,
                startup_script=startup_script_bootstrap,
                user_id=user_id,
                disk_attachments=[],
            )
            self.logger.info("Bid prepared and submitted successfully.")

        self.logger_manager.notify("Flow task execution completed successfully!")

    def cancel_bid(self, name: str) -> None:
        """Cancels a bid with the specified name.

        Args:
            name: The user-friendly name of the bid to cancel.
        """
        self.logger.debug("Authenticating user for bid cancellation.")
        user_id, project_id, _ = self._authenticate_and_get_user_data()
        self.logger.debug("User ID: %s, Project ID: %s", user_id, project_id)

        self.logger.debug("Retrieving existing bids for the project.")
        bids: List[Bid] = self.bid_manager.get_bids(project_id=project_id)  # type: ignore
        bid_to_cancel: Optional[Bid] = next((b for b in bids if b.name == name), None)
        if bid_to_cancel is None:
            msg: str = f"Bid with name '{name}' not found."
            self.logger.error(msg)
            raise Exception(msg)

        bid_id: str = bid_to_cancel.id
        self.logger.debug("Canceling bid with ID: %s", bid_id)
        self.bid_manager.cancel_bid(project_id=project_id, bid_id=bid_id)  # type: ignore
        self.logger.info("Bid '%s' canceled successfully.", name)

    def check_status(
        self, task_name: Optional[str] = None, show_all: bool = False
    ) -> None:
        """Checks and prints the status of bids and instances.

        Args:
            task_name: Optional; if provided, filters by the task name.
            show_all: If True, shows entries even if some data is missing.
        """
        try:
            self.logger.debug("Authenticating user for status check.")
            user_id, project_id, _ = self._authenticate_and_get_user_data()
            bids_pydantic: List[Bid] = self.bid_manager.get_bids(project_id=project_id)  # type: ignore
            bids_pydantic = self._validate_bids(bids=bids_pydantic, show_all=show_all)

            self.logger.debug("Retrieving instances for the project.")
            instances: List[Any] = self.instance_manager.get_instances(
                project_id=project_id
            )
            if task_name:
                self.logger.debug("Filtering instances by task name: %s", task_name)
                instances = self.instance_manager.filter_instances(
                    instances=instances, name=task_name
                )

            self.logger.debug("Formatting output with TableFormatter.")
            table_formatter: TableFormatter = TableFormatter()
            table_formatter.format_status(bids=bids_pydantic, instances=instances)
        except Exception as err:
            self.logger.exception("An unexpected error occurred during status check.")
            raise

    # =========================================================================
    # Internal Helper Methods
    # =========================================================================

    def _build_full_startup_script(self, config: ConfigModel, ports: List[Any]) -> str:
        """Builds the complete startup script including ephemeral/persistent storage, port configuration, and container image setup.

        Args:
            config: The validated configuration model.
            ports: A list of Port objects or integers representing ports.

        Returns:
            A combined shell script that includes ephemeral storage, persistent storage,
            container image setup, and any user-defined startup logic.
        """
        builder: StartupScriptBuilder = StartupScriptBuilder(logger=self.logger)

        # Ensure all ports are Port objects.
        port_objects: List[Port] = []
        for p in ports:
            if isinstance(p, int):
                # Convert an integer port to a Port object (e.g., 80 -> Port(external=80, internal=80)).
                port_objects.append(Port(external=p, internal=p))
            else:
                port_objects.append(p)

        if port_objects:
            self.logger.debug(
                "Injecting port configurations into startup script builder."
            )
            builder.inject_ports(port_objects)

        # Inject ephemeral storage configuration if available.
        ephemeral_cfg: Optional[EphemeralStorageConfig] = (
            config.ephemeral_storage_config
        )
        if ephemeral_cfg and isinstance(ephemeral_cfg, EphemeralStorageConfig):
            self.logger.debug("Injecting ephemeral storage configuration.")
            builder.inject_ephemeral_storage(ephemeral_cfg)

        # Inject persistent storage configuration if available.
        persistent_cfg: Optional[PersistentStorage] = config.persistent_storage
        if persistent_cfg and isinstance(persistent_cfg, PersistentStorage):
            self.logger.debug("Injecting persistent storage configuration.")
            builder.inject_persistent_storage(persistent_cfg)
        # Inject container image config if provided.
        container_image_cfg: Optional[ContainerImageConfig] = config.container_image
        if container_image_cfg and isinstance(
            container_image_cfg, ContainerImageConfig
        ):
            self.logger.debug("Injecting container image configuration.")
            builder.inject_container_image(container_image_cfg)

        # If the user defined a custom script in config, we add it last.
        if config.startup_script:
            self.logger.debug("Injecting user-provided custom script logic.")
            builder.inject_custom_script(config.startup_script)

        final_script: str = builder.build_script()
        self.logger.debug(
            "Final combined script generated, length: %d characters.", len(final_script)
        )
        return final_script

    def _extract_and_prepare_data(
        self, config: ConfigModel
    ) -> Tuple[str, ResourcesSpecification, int, List[Port]]:
        """Extracts essential data from the configuration.

        Args:
            config: The validated configuration model.

        Returns:
            A tuple containing:
                - task_name (str): The name of the task.
                - resources_specification (ResourcesSpecification): The requested resources.
                - limit_price_cents (int): The limit price in cents.
                - ports (List[Port]): The list of port configurations.

        Raises:
            ValueError: If any required configuration data is missing or invalid.
        """
        self.logger.debug("Extracting task name from configuration.")
        task_name: str = config.name
        if not task_name:
            self.logger.error("Task name is required but not provided.")
            raise ValueError("Task name is required.")

        self.logger.debug("Validating task management settings.")
        task_management: TaskManagement = config.task_management
        if not task_management:
            self.logger.error("Task management settings are missing.")
            raise ValueError("Task management settings are required.")

        priority: str = task_management.priority or "standard"
        valid_priorities = {"critical", "high", "standard", "low"}
        if priority not in valid_priorities:
            self.logger.error("Invalid priority level: %s", priority)
            raise ValueError(f"Invalid priority level: {priority}")

        utility_threshold_price: Optional[float] = (
            task_management.utility_threshold_price
        )
        self.logger.debug(
            "Task priority: %s, Utility threshold price: %s",
            priority,
            utility_threshold_price,
        )

        limit_price_cents: int = self.prepare_limit_price_cents(
            priority=priority, utility_threshold_price=utility_threshold_price
        )
        self.logger.debug("Computed limit price (cents): %d", limit_price_cents)

        self.logger.debug("Retrieving resources specification from configuration.")
        resources_spec: ResourcesSpecification = config.resources_specification
        if not resources_spec:
            raise ValueError("Resources specification is missing or invalid.")

        self.logger.debug("Gathering port configuration from ConfigParser.")
        ports: List[Port] = self.config_parser.get_ports()  # type: ignore

        return task_name, resources_spec, limit_price_cents, ports

    def _authenticate_and_get_user_data(self) -> Tuple[str, str, str]:
        """Authenticates with Foundry and retrieves user, project, and SSH key information.

        Returns:
            A tuple containing:
                - user_id (str): The Foundry user ID.
                - project_id (str): The Foundry project ID.
                - ssh_key_id (str): The Foundry SSH key ID.

        Raises:
            AuthenticationError: If user authentication fails.
        """
        try:
            user: User = self.foundry_client.get_user()
            self.logger.debug("User info retrieved: %s", user)
        except Exception as err:
            self.logger.error("Authentication failed.", exc_info=True)
            raise AuthenticationError("Authentication failed.") from err

        if not user.id:
            raise ValueError("User ID not found in user info.")

        user_id: str = user.id
        projects: List[Project] = self.foundry_client.get_projects()
        project_id: str = self.select_project_id(
            projects=projects, project_name=self.project_name
        )

        ssh_keys: List[SshKey] = self.foundry_client.get_ssh_keys(project_id=project_id)
        ssh_key_id: str = self.select_ssh_key_id(
            ssh_keys=ssh_keys, ssh_key_name=self.ssh_key_name
        )

        return user_id, project_id, ssh_key_id

    def _find_matching_auctions(
        self,
        project_id: str,
        resources_specification: ResourcesSpecification,
    ) -> List[Auction]:
        """Retrieves auctions and filters them based on the resource criteria.

        Args:
            project_id: The Foundry project ID.
            resources_specification: The requested resource specification.

        Returns:
            A list of Auction objects matching the resource specification.

        Raises:
            NoMatchingAuctionsError: If no auctions match the criteria.
        """
        self.logger.debug("Fetching auctions for project_id=%s.", project_id)
        auctions: List[Auction] = self.auction_finder.fetch_auctions(project_id=project_id)  # type: ignore
        self.logger.debug("Total auctions fetched: %d", len(auctions))

        try:
            matching_auctions: List[
                Auction
            ] = self.auction_finder.find_matching_auctions(
                auctions=auctions, criteria=resources_specification
            )  # type: ignore
        except NoMatchingAuctionsError as ex:
            self.logger.error("Auction matching failed: %s", ex)
            raise NoMatchingAuctionsError(str(ex)) from ex

        self.logger.info("%d matching auctions found.", len(matching_auctions))
        return matching_auctions

    def _prepare_and_submit_bid(
        self,
        matching_auctions: List[Auction],
        resources_specification: ResourcesSpecification,
        limit_price_cents: int,
        task_name: str,
        project_id: str,
        ssh_key_id: str,
        startup_script: str,
        user_id: str,
        disk_attachments: Optional[List[BidDiskAttachment]] = None,
    ) -> None:
        """Prepares and submits a spot bid using a selected auction.

        Args:
            matching_auctions: A list of Auction objects that match the resource specification.
            resources_specification: The requested resource specification.
            limit_price_cents: The maximum price (in cents) the user is willing to pay.
            task_name: A unique identifier for this task.
            project_id: The Foundry project ID.
            ssh_key_id: The Foundry SSH key ID.
            startup_script: The (bootstrap) startup script for instance initialization.
            user_id: The Foundry user ID.
            disk_attachments: Optional list of disk attachments.

        Raises:
            NoMatchingAuctionsError: If no matching auctions are available.
            BidSubmissionError: If the bid submission fails.
            ValueError: If required auction details are missing.
        """
        if not matching_auctions:
            self.logger.error("No matching auctions available for bid submission.")
            raise NoMatchingAuctionsError(
                "No matching auctions available to submit bid. Override by specifying the instance_id "
                "directly in the config if needed."
            )

        selected_auction: Auction = matching_auctions[0]
        self.logger.debug("Selected auction: %s", selected_auction)

        region_id: Optional[str] = selected_auction.region_id
        if not region_id and selected_auction.region:
            self.logger.debug(
                "Auction returned region '%s' but no region_id. Attempting region lookup...",
                selected_auction.region,
            )
            region_id = self.foundry_client.get_region_id_by_name(
                selected_auction.region
            )

        if not region_id:
            raise ValueError(
                "Selected auction does not have a valid region or region_id, and region lookup failed."
            )

        self.logger.debug(
            "Using region_id='%s' for disk creation if needed.", region_id
        )

        # Handle persistent storage if defined in the configuration.
        persistent_storage: Optional[PersistentStorage] = self.config_parser.get_persistent_storage()  # type: ignore
        bid_disk_attachments: List[BidDiskAttachment] = []
        if persistent_storage:
            self.logger.debug(
                "Handling persistent storage for region_id='%s'.", region_id
            )
            disk_attachment = self.storage_manager.handle_persistent_storage(
                project_id=project_id,
                persistent_storage=persistent_storage,
                region_id=region_id,
            )
            if disk_attachment:
                disk_attach: BidDiskAttachment = BidDiskAttachment.from_disk_attachment(
                    disk_attachment
                )
                bid_disk_attachments.append(disk_attach)

        bid_payload: BidPayload = self.bid_manager.prepare_bid_payload(  # type: ignore
            cluster_id=selected_auction.cluster_id,
            instance_quantity=resources_specification.num_instances or 1,
            instance_type_id=selected_auction.instance_type_id,
            limit_price_cents=limit_price_cents,
            order_name=task_name,
            project_id=project_id,
            ssh_key_id=ssh_key_id,
            startup_script=startup_script,
            user_id=user_id,
            disk_attachments=bid_disk_attachments,
        )
        self.logger.debug(
            "Bid payload prepared:\n%s", json.dumps(bid_payload.model_dump(), indent=2)
        )

        try:
            self.logger.debug("Submitting bid to Foundry.")
            bid_response_list: List[Bid] = self.bid_manager.submit_bid(
                project_id=project_id, bid_payload=bid_payload
            )  # type: ignore
            if not bid_response_list:
                raise BidSubmissionError("Submit bid returned an empty list.")
            self.logger.info("Bid submitted successfully.")
            bid_resp_data: dict[str, Any] = bid_response_list[0].model_dump()
            self.logger.debug(
                "Bid response:\n%s", json.dumps(bid_resp_data, indent=2, default=str)
            )
        except Exception as err:
            self.logger.error("Bid submission failed.", exc_info=True)
            raise BidSubmissionError("Bid submission failed.") from err

        self.logger.info("Spot bid created successfully.")

    def prepare_limit_price_cents(
        self, priority: str, utility_threshold_price: Optional[float] = None
    ) -> int:
        """Converts a priority or user-defined threshold price to a final limit price in cents.

        Args:
            priority: The priority level (e.g., 'critical' or 'standard').
            utility_threshold_price: Optional; a user-defined price threshold.

        Returns:
            The final limit price as an integer number of cents.

        Raises:
            ValueError: If the utility_threshold_price is invalid or the priority level is unsupported.
        """
        self.logger.debug("Determining limit price in cents.")
        if utility_threshold_price is not None:
            self.logger.debug(
                "User-defined utility threshold price: %s", utility_threshold_price
            )
            try:
                return int(float(utility_threshold_price) * 100)
            except ValueError as exc:
                error_msg: str = (
                    f"Invalid utility_threshold_price value: {utility_threshold_price}"
                )
                self.logger.error(error_msg)
                raise ValueError(error_msg) from exc

        price_map: dict[str, float] = _SETTINGS.PRIORITY_PRICE_MAPPING or {}
        price: Optional[float] = price_map.get(priority.lower())
        if price is None:
            error_msg: str = f"Invalid or unsupported priority level: {priority}"
            self.logger.error(error_msg)
            raise ValueError(error_msg)

        limit_cents: int = int(price * 100)
        self.logger.debug("Computed priority-based limit price: %d cents", limit_cents)
        return limit_cents

    def select_project_id(self, projects: List[Project], project_name: str) -> str:
        """Finds the project ID for a given project name.

        Args:
            projects: A list of Project objects from Foundry.
            project_name: The target project name.

        Returns:
            The project ID corresponding to the matching project.

        Raises:
            Exception: If no matching project is found.
        """
        self.logger.info("Selecting project ID for project name='%s'.", project_name)
        self.logger.debug("Available projects: %s", projects)

        for proj in projects:
            if proj.name == project_name and proj.id:
                self.logger.info(
                    "Found matching project: name=%s, id=%s", proj.name, proj.id
                )
                return proj.id

        error_msg: str = f"Project '{project_name}' not found."
        self.logger.error(error_msg)
        raise Exception(error_msg)

    def select_ssh_key_id(self, ssh_keys: List[SshKey], ssh_key_name: str) -> str:
        """Finds the SSH key ID for a given SSH key name.

        Args:
            ssh_keys: A list of SshKey objects from Foundry.
            ssh_key_name: The target SSH key name.

        Returns:
            The SSH key ID corresponding to the matching key.

        Raises:
            Exception: If no matching SSH key is found.
        """
        self.logger.info("Selecting SSH key ID for ssh_key_name='%s'.", ssh_key_name)
        for key in ssh_keys:
            if key.name == ssh_key_name and key.id:
                self.logger.info("Found SSH key: name=%s, id=%s", key.name, key.id)
                return key.id

        error_msg: str = f"SSH key '{ssh_key_name}' not found."
        self.logger.error(error_msg)
        raise Exception(error_msg)

    def _validate_bids(self, bids: List[Bid], show_all: bool) -> List[Bid]:
        """Validates and filters a list of bids based on input criteria.

        Args:
            bids: A list of Bid objects.
            show_all: If True, retains items with missing or partial data.

        Returns:
            A filtered list of valid Bid objects.
        """
        if not isinstance(bids, list):
            self.logger.warning("Expected bids to be a list, got %s", type(bids))
            return []

        if show_all:
            return bids

        return [bid for bid in bids if bid.name and bid.status]
