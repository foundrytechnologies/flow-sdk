import json
import logging
import os
import random
import string
import unittest
import uuid
import time
from pathlib import Path
from typing import List

import pytest
import yaml

from src.flow.config import get_config
from src.flow.task_config import ConfigParser
from src.flow.clients.foundry_client import FoundryClient
from src.flow.managers.auction_finder import AuctionFinder
from src.flow.managers.bid_manager import BidManager
from src.flow.managers.storage_manager import StorageManager
from src.flow.managers.task_manager import FlowTaskManager
from src.flow.utils.exceptions import APIError

settings = get_config()

TEST_YAML_FILE = os.path.abspath(
    os.path.join(os.path.dirname(__file__), "../../../flow_example.yaml")
)


@pytest.mark.skipif(
    not all(
        [
            settings.foundry_email,
            bool(settings.foundry_password.get_secret_value().strip()),
            settings.foundry_project_name,
            settings.foundry_ssh_key_name,
        ]
    ),
    reason="Skipping FlowTaskManagerIntegration tests due to missing required environment variables.",
)
class TestFlowTaskManagerIntegration(unittest.TestCase):
    """Integration tests for FlowTaskManager, verifying end-to-end functionality."""

    def setUp(self):
        """Set up the integration test environment with real config usage."""
        logging.basicConfig(level=logging.DEBUG)
        self.logger = logging.getLogger(__name__)

        # Generate a random 3-digit suffix
        self.random_suffix = "".join(random.choices(string.digits, k=3))

        # Load and modify the YAML configuration file
        with open(TEST_YAML_FILE, "r", encoding="utf-8") as file:
            config_data = yaml.safe_load(file)

        original_name = config_data.get("name", "flow-task")
        config_data["name"] = f"{original_name}-test-{self.random_suffix}"
        config_data["resources_specification"].update(
            {
                "fcp_instance": "h100.8x.SXM5.IB",
                "gpu_type": "NVIDIA H100",
                "num_gpus": 8,
                "num_instances": 1,
                "intranode_interconnect": "SXM5",
                "internode_interconnect": "IB_1600",
                "instance_type_id": "46e17546-847a-40b8-928b-1a3388338f0f",
            }
        )

        print("\nUsing resource specifications:")
        print(json.dumps(config_data["resources_specification"], indent=2))

        # Generate a truly unique disk name
        timestamp_str = time.strftime("%Y%m%d%H%M%S")
        rand_tail = "".join(random.choices(string.ascii_lowercase + string.digits, k=4))
        self.disk_id = str(uuid.uuid4())
        self.disk_name = f"testdisk-{timestamp_str}-{rand_tail}"
        self.disk_interface = "Block"
        self.size = 1
        self.size_unit = "gb"
        self.actual_disk_id = None
        self.disks_to_cleanup = []

        # Update the persistent_storage create config
        if "persistent_storage" not in config_data:
            config_data["persistent_storage"] = {}
        if "create" not in config_data["persistent_storage"]:
            config_data["persistent_storage"]["create"] = {}
        config_data["persistent_storage"]["create"].update(
            {
                "disk_interface": "Block",
                "size": 1,
                "size_unit": "gb",
                # Use our unique self.disk_name
                "volume_name": self.disk_name,
            }
        )

        # Persist the YAML with the updated config
        self.temp_yaml_file = os.path.join(
            os.path.dirname(__file__),
            "temp_test_config.yaml",
        )
        with open(self.temp_yaml_file, "w", encoding="utf-8") as file:
            yaml.dump(config_data, file)

        print(f"Using task name: {config_data['name']}")

        self.config_parser = ConfigParser(filename=self.temp_yaml_file)
        self.foundry_client = FoundryClient(
            email=settings.foundry_email,
            password=settings.foundry_password.get_secret_value(),
        )

        self.default_test_catalog = (
            Path(__file__).parents[3] / "fcp_auction_catalog.yaml"
        )
        self.auction_finder = AuctionFinder(
            foundry_client=self.foundry_client,
            local_catalog_path=self.default_test_catalog,
        )
        self.bid_manager = BidManager(self.foundry_client)

        # Retrieve project ID
        projects = self.foundry_client.get_projects()
        self.project_id = None
        for project in projects:
            if project.name == settings.foundry_project_name:
                self.project_id = project.id
                break
        if not self.project_id:
            self.fail(f"Project '{settings.foundry_project_name}' not found.")

        self.task_manager = FlowTaskManager(
            config_parser=self.config_parser,
            foundry_client=self.foundry_client,
            auction_finder=self.auction_finder,
            bid_manager=self.bid_manager,
        )

        # Initialize storage logic
        self.storage_manager = StorageManager(foundry_client=self.foundry_client)
        print(f"Using disk name: {self.disk_name}")

        # Adjust threshold
        self.config_parser.config.task_management.utility_threshold_price = 600

    def test_create_and_cancel_bid(self):
        """Test creating a bid and then canceling it to ensure end-to-end flow."""
        self.logger.info("Starting integration test: test_create_and_cancel_bid")

        # Get and augment auctions
        auctions = self.auction_finder.fetch_auctions(
            project_id=self.project_id, local_catalog_path=self.default_test_catalog
        )

        # New debug logging for raw auctions
        self.logger.debug("Fetched auctions:")
        for auction in auctions:
            self.logger.debug(
                "Auction ID: %s, GPU: %s, Instance Type: %s, Region: %s, FCP Instance: %s",
                auction.id,
                auction.gpu_type,
                auction.instance_type_id,
                auction.region,
                getattr(auction, "fcp_instance", "MISSING"),
            )

        # Check for matches against the criteria
        matching_auctions = self.auction_finder.find_matching_auctions(
            auctions=auctions,
            criteria=self.config_parser.config.resources_specification,
        )

        # New debug logging for augmented auctions
        self.logger.debug("Augmented auctions after processing:")
        for auction in matching_auctions:
            self.logger.debug("Augmented Auction: %s", auction.model_dump())

        self.logger.debug("Matching auctions after criteria check:")
        for auction in matching_auctions:
            self.logger.debug("Matched auction: %s", auction.model_dump())
            # New: Log specific matching attributes
            self.logger.debug(
                "Matching Details - FCP Instance: %s, GPU Type: %s, Num GPUs: %s, Interconnect: %s/%s",
                auction.fcp_instance,
                auction.gpu_type,
                auction.num_gpus,
                auction.intranode_interconnect,
                auction.internode_interconnect,
            )

        self.assertGreater(len(matching_auctions), 0, "No matching auctions found")
        matching_auction = matching_auctions[0]

        # New: Log full details of selected auction
        self.logger.debug("Selected matching auction full details:")
        for k, v in matching_auction.model_dump().items():
            self.logger.debug("%s: %s", k, v)

        # Replace the hardcoded region with catalog-aligned value
        region_val = matching_auction.region  # Use region from matched auction
        print(f"Using auction's region: {region_val} for alignment")

        self.config_parser.config.persistent_storage.create = (
            self.config_parser.config.persistent_storage.create.model_copy(
                update={"region_id": region_val}
            )
        )

        print("\nUsing resource specifications:")
        print(
            json.dumps(
                self.config_parser.config.resources_specification.model_dump(),
                indent=2,
            )
        )

        timestamp_str = time.strftime("%Y%m%d%H%M%S")
        self.config_parser.config.name = f"flow-test-task-{timestamp_str}"
        print(f"Using random name: {self.config_parser.config.name}")

        try:
            self.task_manager.run()
        except APIError as exc:
            # Handle the 409 (disk already exists) scenario by deleting the disk and retrying:
            if getattr(exc, "status_code", None) == 409:
                self.logger.warning("Disk creation conflict (409). Attempting cleanup and re-run.")
                # Attempt to cleanup the disk if partially created or left from a previous run
                try:
                    self.storage_manager.delete_disk(self.project_id, self.disk_id)
                    self.logger.info(
                        f"Deleted disk {self.disk_id} after 409 conflict; retrying run."
                    )
                except Exception as cleanup_exc:
                    self.logger.warning(
                        f"Failed to delete conflicting disk {self.disk_id}: {cleanup_exc}"
                    )
                # Retry once
                self.task_manager.run()
            else:
                raise

        print("Retrieving projects")
        projects = self.foundry_client.get_projects()
        print(f"Projects retrieved: {projects}")
        project_id = None
        for project in projects:
            print(f"Inspecting project: {project}")
            if project.name == settings.foundry_project_name:
                project_id = project.id
                break
        if not project_id:
            self.fail(f"Project '{settings.foundry_project_name}' not found.")

        print(f"Retrieving bids for project ID: {project_id}")
        bids = self.bid_manager.get_bids(project_id=project_id)
        print(f"Bids retrieved: {bids}")
        self.assertTrue(len(bids) > 0, "No bids found after submission.")

        task_name = self.config_parser.config.name
        print(f"Task name from configuration: {task_name}")

        bid_to_cancel = next(
            (bid for bid in bids if hasattr(bid, "name") and bid.name == task_name),
            None,
        )
        if not bid_to_cancel:
            self.fail(f"Bid with name '{task_name}' not found after submission.")
        bid_id = bid_to_cancel.id

        order_name = bid_to_cancel.name
        print(f"Order name to cancel: {order_name}")
        print(f"Order ID to cancel: {bid_id}")

        print(f"Cancelling bid with name: {order_name}")
        self.task_manager.cancel_bid(order_name)

        bids_after_cancellation = self.bid_manager.get_bids(project_id=project_id)
        print(f"Bids after cancellation: {bids_after_cancellation}")
        canceled_bid = next(
            (b for b in bids_after_cancellation if b.id == bid_id), None
        )
        if canceled_bid is None:
            print(f"Bid '{order_name}' has been canceled successfully.")
        else:
            if canceled_bid.status == "canceled" or canceled_bid.deactivated_at:
                print(f"Bid '{order_name}' has been canceled successfully.")
            else:
                self.fail(f"Bid '{order_name}' was not canceled.")

        # After augmentation, log augmented auctions
        self.logger.debug("Augmented auctions:")
        for auction in matching_auctions:
            self.logger.debug(
                "Auction %s: fcp_instance=%s, intranode=%s, internode=%s",
                auction.id,
                auction.fcp_instance,
                auction.intranode_interconnect,
                auction.internode_interconnect,
            )
