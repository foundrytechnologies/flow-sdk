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
from flow.managers.auction_finder import (
    AuctionFinder,
)
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
)
from flow.task_config.config_parser import ConfigParser
from flow.startup_script_builder.startup_script_builder import StartupScriptBuilder
from flow.task_config.models import Port

_SETTINGS = get_config()

"""Provides FlowTaskManager, which manages the execution of tasks within Foundry.

This module is responsible for orchestrating the creation of a startup script
(including ephemeral/persistent storage, port forwarding, etc.), compressing
it if needed, and submitting a spot bid via Foundry.
"""


class AuthenticationError(Exception):
    """Raised when user authentication fails."""


class NoMatchingAuctionsError(Exception):
    """Raised when no matching auctions are found."""


class BidSubmissionError(Exception):
    """Raised when there is an error submitting a bid."""


class FlowTaskManager:
    """Manages the execution of tasks within the Flow system.

    This class reads configuration from a ConfigParser, constructs a
    startup script (potentially large), compresses it if needed, then
    prepares and submits a bid via Foundry.
    """

    def __init__(
        self,
        config_parser: Optional[ConfigParser],
        foundry_client: FoundryClient,
        auction_finder: Optional[AuctionFinder],
        bid_manager: Optional[BidManager],
    ) -> None:
        """Initializes a FlowTaskManager.

        Args:
            config_parser: A ConfigParser holding user-defined settings.
            foundry_client: A FoundryClient instance for Foundry API interactions.
            auction_finder: An AuctionFinder for retrieving auctions from Foundry.
            bid_manager: A BidManager for preparing and submitting bids.
        """
        self.config_parser = config_parser
        self.foundry_client = foundry_client
        self.auction_finder = auction_finder
        self.bid_manager = bid_manager

        self.logger = logging.getLogger(__name__)
        self.logger.debug("Initialized FlowTaskManager instance.")

        self.instance_manager = InstanceManager(foundry_client=self.foundry_client)
        self.storage_manager = StorageManager(self.foundry_client)
        self.logger.debug("StorageManager initialized.")

        self.logger_manager = SpinnerLogger(self.logger)

    def run(self) -> None:
        """Executes the primary Flow task operation: parse config, build script, submit bid."""
        with self.logger_manager.spinner(""):
            if not self.config_parser:
                self.logger.error("ConfigParser is required to run the task manager.")
                raise ValueError("ConfigParser is required to run the task manager.")

            self.logger.info("Starting the flow task execution.")

            self.logger.debug("Parsing configuration from ConfigParser.")
            config = self.config_parser.config
            if not config:
                raise ValueError("Configuration data is missing or invalid.")

            self.logger.debug("Extracting and preparing data from configuration.")
            (
                task_name,
                resources_specification,
                limit_price_cents,
                ports,
            ) = self._extract_and_prepare_data(config=config)
            self.logger.info(
                "Data extraction complete. Task name: %s, "
                "Limit price (cents): %d, Ports: %s",
                task_name,
                limit_price_cents,
                ports,
            )

            self.logger.debug(
                "Building final startup script with ephemeral/persistent storage and ports."
            )
            full_startup_script = self._build_full_startup_script(config, ports)

            self.logger.debug(
                "Creating a small bootstrap to handle large script scenario."
            )
            builder = StartupScriptBuilder(logger=self.logger)
            builder.inject_bootstrap_script(full_startup_script)
            startup_script_bootstrap = builder.build_script()

            self.logger.info("Startup script(s) built and compressed (if needed).")

            self.logger.debug("Authenticating user and retrieving project data.")
            user_id, project_id, ssh_key_id = self._authenticate_and_get_user_data()
            self.logger.info(
                "Authentication successful. User ID: %s, Project ID: %s, SSH Key ID: %s",
                user_id,
                project_id,
                ssh_key_id,
            )

            self.logger.debug("Finding matching auctions for the given resources.")
            matching_auctions = self._find_matching_auctions(
                project_id=project_id, resources_specification=resources_specification
            )
            self.logger.info("%d matching auctions found.", len(matching_auctions))

            self.logger.debug("Preparing and submitting the bid to Foundry.")
            self._prepare_and_submit_bid(
                matching_auctions=matching_auctions,
                resources_specification=resources_specification,
                limit_price_cents=limit_price_cents,
                task_name=task_name,
                project_id=project_id,
                ssh_key_id=ssh_key_id,
                startup_script=startup_script_bootstrap,  # The small bootstrap
                user_id=user_id,
                disk_attachments=[],
            )
            self.logger.info("Bid prepared and submitted successfully.")

        self.logger_manager.notify("Flow task execution completed successfully!")

    def _build_full_startup_script(self, config: ConfigModel, ports: List[Any]) -> str:
        """Builds the complete startup script including ephemeral/persistent storage.

        Args:
            config: The validated config from which ephemeral/persistent data is parsed.
            ports: A list of Port objects from the config.

        Returns:
            A possibly large shell script that includes ephemeral storage,
            persistent storage, and any user-defined startup logic.
        """
        builder = StartupScriptBuilder(logger=self.logger)

        # Convert int-based ports to Port objects if needed
        port_objects = []
        for p in ports:
            if isinstance(p, int):
                # e.g., 80 -> Port(external=80, internal=80)
                port_objects.append(Port(external=p, internal=p))
            else:
                # Already a Port or user-provided structure
                port_objects.append(p)

        if port_objects:
            self.logger.debug("Injecting ports into StartupScriptBuilder.")
            builder.inject_ports(port_objects)

        # Ephemeral Storage
        ephemeral_cfg = config.ephemeral_storage_config
        if ephemeral_cfg and isinstance(ephemeral_cfg, EphemeralStorageConfig):
            self.logger.debug("Injecting ephemeral storage config.")
            builder.inject_ephemeral_storage(ephemeral_cfg)

        # Persistent Storage
        persistent_cfg = config.persistent_storage
        if persistent_cfg and isinstance(persistent_cfg, PersistentStorage):
            self.logger.debug("Injecting persistent storage config.")
            builder.inject_persistent_storage(persistent_cfg)

        # If the user defined a custom script in config, add it last
        if config.startup_script:
            self.logger.debug("Injecting user-provided custom script.")
            builder.inject_custom_script(config.startup_script)

        final_script = builder.build_script()
        self.logger.debug(
            "Final combined script generated, length: %d characters.", len(final_script)
        )
        return final_script

    def _extract_and_prepare_data(
        self, config: ConfigModel
    ) -> Tuple[str, ResourcesSpecification, int, List[Port]]:
        """Extracts essential data (task name, resources, price, ports) from the config.

        Args:
            config: The validated ConfigModel.

        Returns:
            A tuple of (task_name, resources_specification, limit_price_cents, ports).
        """
        self.logger.debug("Extracting task name.")
        task_name: str = config.name
        if not task_name:
            self.logger.error("Task name is required but not found.")
            raise ValueError("Task name is required.")

        self.logger.debug("Validating priority from task_management.")
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
            "Priority: %s, Utility threshold price: %s",
            priority,
            utility_threshold_price,
        )

        limit_price_cents = self.prepare_limit_price_cents(
            priority=priority, utility_threshold_price=utility_threshold_price
        )
        self.logger.debug("Limit price (cents): %d", limit_price_cents)

        self.logger.debug("Retrieving resources specification from config.")
        resources_spec = config.resources_specification
        if not resources_spec:
            raise ValueError("Resources specification is missing or invalid.")

        self.logger.debug("Gathering ports from config parser.")
        ports = self.config_parser.get_ports()

        return task_name, resources_spec, limit_price_cents, ports

    def _authenticate_and_get_user_data(self) -> Tuple[str, str, str]:
        """Authenticates with Foundry and retrieves user/project/SSH key information.

        Returns:
            A tuple of (user_id, project_id, ssh_key_id).

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

        user_id = user.id
        projects: List[Project] = self.foundry_client.get_projects()
        project_id = self.select_project_id(
            projects=projects, project_name=_SETTINGS.foundry_project_name
        )

        ssh_keys: List[SshKey] = self.foundry_client.get_ssh_keys(project_id=project_id)
        ssh_key_id = self.select_ssh_key_id(
            ssh_keys=ssh_keys, ssh_key_name=_SETTINGS.foundry_ssh_key_name
        )

        return user_id, project_id, ssh_key_id

    def _find_matching_auctions(
        self,
        project_id: str,
        resources_specification: ResourcesSpecification,
    ) -> List[Auction]:
        """Retrieves auctions from Foundry and filters by resource specification.

        Args:
            project_id: The Foundry project ID.
            resources_specification: The user's requested resource specification.

        Returns:
            A list of matching auctions that fulfill the spec.

        Raises:
            NoMatchingAuctionsError: If no auctions match the resource criteria.
        """
        self.logger.debug("Fetching auctions for project_id=%s.", project_id)
        auctions = self.auction_finder.fetch_auctions(project_id=project_id)
        self.logger.debug("Auctions fetched: %d total auctions.", len(auctions))

        try:
            matching_auctions = self.auction_finder.find_matching_auctions(
                auctions=auctions,
                criteria=resources_specification,
            )
        except NoMatchingAuctionsError as ex:
            self.logger.error("Detailed mismatch:\n%s", ex)
            # Re-raise in our local format
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
        """Prepares and submits a spot bid using a chosen auction.

        Args:
            matching_auctions: Auctions that match the user's resource specification.
            resources_specification: The user's requested resource spec.
            limit_price_cents: The maximum price in cents the user is willing to pay.
            task_name: A unique identifier for this order or task.
            project_id: The Foundry project ID.
            ssh_key_id: The Foundry SSH key ID.
            startup_script: The final (or small bootstrap) script for instance initialization.
            user_id: The Foundry user ID.
            disk_attachments: Optional list of existing disk attachments.
        """
        if not matching_auctions:
            self.logger.error(
                "No matching auctions available for bid submission."
            )
            raise NoMatchingAuctionsError(
                "No matching auctions available to submit bid. "
                "As a quick fix, override by specifying the instance_id associated "
                "with your desired instance directly in the config."
            )

        selected_auction: Auction = matching_auctions[0]
        self.logger.debug("Selected auction: %s", selected_auction)

        region_id = selected_auction.region_id
        if not region_id and selected_auction.region:
            self.logger.debug(
                "Auction returned region name '%s' but no region_id. "
                "Attempting region lookup...",
                selected_auction.region,
            )
            region_id = self.foundry_client.get_region_id_by_name(
                selected_auction.region
            )

        if not region_id:
            raise ValueError(
                "Selected auction does not have a region or region_id, "
                "and lookup failed."
            )

        self.logger.debug(
            "Using region_id='%s' for disk creation if needed.", region_id
        )

        persistent_storage = self.config_parser.get_persistent_storage()
        bid_disk_attachments: List[BidDiskAttachment] = []
        if persistent_storage:
            self.logger.debug(
                "Handling persistent storage with region_id='%s'.", region_id
            )
            disk_attachment = self.storage_manager.handle_persistent_storage(
                project_id=project_id,
                persistent_storage=persistent_storage,
                region_id=region_id,
            )
            if disk_attachment:
                disk_attach = BidDiskAttachment.from_disk_attachment(disk_attachment)
                bid_disk_attachments.append(disk_attach)

        bid_payload: BidPayload = self.bid_manager.prepare_bid_payload(
            cluster_id=selected_auction.cluster_id,
            instance_quantity=resources_specification.num_instances or 1,
            instance_type_id=selected_auction.instance_type_id,
            limit_price_cents=limit_price_cents,
            order_name=task_name,
            project_id=project_id,
            ssh_key_id=ssh_key_id,
            startup_script=startup_script,
            user_id=user_id,
            disk_attachments=bid_disk_attachments or [],
        )
        self.logger.debug(
            "Bid payload prepared:\n%s", json.dumps(bid_payload.model_dump(), indent=2)
        )

        try:
            self.logger.debug("Submitting bid to Foundry.")
            bid_response = self.bid_manager.submit_bid(
                project_id=project_id, bid_payload=bid_payload
            )
            self.logger.info("Bid submitted successfully.")
            bid_resp_data = bid_response.model_dump()
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
        """Converts the priority or user-defined threshold to a final limit price in cents.

        Args:
            priority: The priority level, e.g. 'critical' or 'standard'.
            utility_threshold_price: An optional user-provided float limit.

        Returns:
            The final limit price in integer cents.

        Raises:
            ValueError: If the priority is unrecognized, or the threshold price is invalid.
        """
        self.logger.debug("Determining limit price in cents.")
        if utility_threshold_price is not None:
            self.logger.debug(
                "User-defined utility threshold price: %s", utility_threshold_price
            )
            try:
                return int(float(utility_threshold_price) * 100)
            except ValueError as exc:
                error_msg = (
                    f"Invalid utility_threshold_price value: {utility_threshold_price}"
                )
                self.logger.error(error_msg)
                raise ValueError(error_msg) from exc

        price_map = _SETTINGS.PRIORITY_PRICE_MAPPING or {}
        price = price_map.get(priority.lower())
        if price is None:
            error_msg = f"Invalid or unsupported priority level: {priority}"
            self.logger.error(error_msg)
            raise ValueError(error_msg)

        limit_cents = int(price * 100)
        self.logger.debug("Priority-based limit price: %d cents", limit_cents)
        return limit_cents

    def select_project_id(self, projects: List[Any], project_name: str) -> str:
        """Finds the project ID matching a user-specified project name.

        Args:
            projects: A list of Project objects from Foundry.
            project_name: The target project name from user config.

        Returns:
            The project ID for the matching project.

        Raises:
            Exception: If no matching project is found.
        """
        self.logger.info("Selecting project ID for project name='%s'.", project_name)
        self.logger.debug("Available projects: %s", projects)

        for proj in projects:
            p_id = proj.id
            p_name = proj.name
            self.logger.debug("Checking project: id=%s, name=%s", p_id, p_name)
            if p_name == project_name and p_id:
                self.logger.info("Found matching project: name=%s, id=%s", p_name, p_id)
                return p_id

        error_msg = f"Project '{project_name}' not found."
        self.logger.error(error_msg)
        raise Exception(error_msg)

    def select_ssh_key_id(self, ssh_keys: List[Any], ssh_key_name: str) -> str:
        """Finds the SSH key ID matching a user-specified SSH key name.

        Args:
            ssh_keys: A list of SshKey objects from Foundry.
            ssh_key_name: The target SSH key name from user config.

        Returns:
            The SSH key ID for the matching key.

        Raises:
            Exception: If no matching SSH key is found.
        """
        self.logger.info("Selecting SSH key ID for ssh_key_name='%s'.", ssh_key_name)
        for key in ssh_keys:
            if key.name == ssh_key_name and key.id:
                self.logger.info("Found SSH key: name=%s, id=%s", key.name, key.id)
                return key.id

        error_msg = f"SSH key '{ssh_key_name}' not found."
        self.logger.error(error_msg)
        raise Exception(error_msg)

    def cancel_bid(self, name: str) -> None:
        """Cancels a bid with the given name.

        Args:
            name: The user-friendly name of the bid.
        """
        self.logger.debug("Authenticating and retrieving user data for cancel_bid.")
        user_id, project_id, _ = self._authenticate_and_get_user_data()
        self.logger.debug("User ID: %s, Project ID: %s", user_id, project_id)

        self.logger.debug("Retrieving existing bids for the project.")
        bids = self.bid_manager.get_bids(project_id=project_id)
        bid_to_cancel = next((b for b in bids if b.name == name), None)
        if bid_to_cancel is None:
            msg = f"Bid with name '{name}' not found."
            self.logger.error(msg)
            raise Exception(msg)

        bid_id = bid_to_cancel.id
        self.logger.debug("Canceling bid with ID: %s", bid_id)
        self.bid_manager.cancel_bid(project_id=project_id, bid_id=bid_id)
        self.logger.info("Bid '%s' canceled successfully.", name)

    def check_status(
        self,
        task_name: Optional[str] = None,
        show_all: bool = False,
    ) -> None:
        """Checks and prints the status of bids and instances.

        Args:
            task_name: If provided, filters by this task name.
            show_all: Whether to show entries with missing data.
        """
        try:
            self.logger.debug("Authenticating user for check_status.")
            user_id, project_id, _ = self._authenticate_and_get_user_data()
            bids_pydantic = self.bid_manager.get_bids(project_id=project_id)
            bids_pydantic = self._validate_bids(bids=bids_pydantic, show_all=show_all)

            self.logger.debug("Retrieving instances for the project.")
            instances = self.instance_manager.get_instances(project_id=project_id)
            if task_name:
                self.logger.debug("Filtering instances by task name: %s", task_name)
                instances = self.instance_manager.filter_instances(
                    instances=instances, name=task_name
                )

            self.logger.debug("Formatting output with TableFormatter.")
            table_formatter = TableFormatter()
            table_formatter.format_status(bids=bids_pydantic, instances=instances)
        except Exception as err:
            self.logger.exception("An unexpected error occurred in check_status.")
            raise

    def _validate_bids(self, bids: List[Bid], show_all: bool) -> List[Bid]:
        """Validates and filters the list of bids based on input criteria.

        Args:
            bids: A list of Bid objects.
            show_all: Whether to retain items with missing or partial data.

        Returns:
            A filtered list of valid Bid objects.
        """
        if not isinstance(bids, list):
            self.logger.warning("Expected bids to be a list, got %s", type(bids))
            return []

        if show_all:
            return bids
        return [bid for bid in bids if bid.name and bid.status]
