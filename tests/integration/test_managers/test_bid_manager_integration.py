import logging
import random
import secrets
import string
import time
import unittest
import uuid
import pytest

from typing import List

from flow.clients.foundry_client import FoundryClient
from flow.config import get_config
from flow.managers.bid_manager import BidManager
from flow.models import (
    Auction,
    Bid,
    DiskAttachment,
    DiskResponse,
    Project,
    RegionResponse,
    SshKey,
    User,
)
from flow.utils.exceptions import APIError, AuthenticationError, NetworkError

settings = get_config()

pytestmark = pytest.mark.skipif(
    not all(
        [
            settings.foundry_email,
            bool(settings.foundry_password.get_secret_value().strip()),
            settings.foundry_project_name,
            settings.foundry_ssh_key_name,
        ]
    ),
    reason="Skipping BidManagerIntegration tests due to missing required environment variables.",
)


class TestBidManagerIntegration(unittest.TestCase):
    """Integration tests for the BidManager class."""

    def setUp(self) -> None:
        """Initialize environment with actual dependencies."""
        logging.basicConfig(level=logging.DEBUG)
        self.logger = logging.getLogger(__name__)
        self.foundry_client = FoundryClient(
            email=settings.foundry_email,
            password=settings.foundry_password.get_secret_value(),
        )
        self.storage_client = self.foundry_client.storage_client
        self.bid_manager = BidManager(foundry_client=self.foundry_client)

        task_random_suffix = "".join(random.choices(string.digits, k=8))
        disk_random_suffix = "".join(random.choices(string.digits, k=8))
        self.task_name = f"test-bid-{task_random_suffix}"
        self.disk_name = f"test-disk-{disk_random_suffix}"

        self.logger.debug("Retrieving projects to find project ID")
        projects: List[Project] = self.foundry_client.get_projects()
        self.project_id = None
        for project in projects:
            self.logger.debug(f"Checking project: {project}")
            if project.name == settings.foundry_project_name:
                self.project_id = project.id
                self.logger.debug(f"Found project ID: {self.project_id}")
                break
        if not self.project_id:
            self.fail(f"Project '{settings.foundry_project_name}' not found.")

        self.logger.debug("Retrieving SSH keys")
        ssh_keys: List[SshKey] = self.foundry_client.get_ssh_keys(
            project_id=self.project_id
        )
        self.ssh_key_id = None
        for key in ssh_keys:
            self.logger.debug(f"Checking SSH key: {key}")
            if key.name == settings.foundry_ssh_key_name:
                self.ssh_key_id = key.id
                self.logger.debug(f"Found SSH key ID: {self.ssh_key_id}")
                break
        if not self.ssh_key_id:
            self.fail(f"SSH key '{settings.foundry_ssh_key_name}' not found.")

        self.logger.debug("Retrieving user ID")
        user: User = self.foundry_client.get_user()
        self.user_id = user.id
        self.logger.debug(f"User ID: {self.user_id}")

    def test_submit_and_cancel_bid_with_disk_attachment(self) -> None:
        """Integration test: submit and cancel a bid with a disk attachment."""
        self.logger.debug("Starting test_submit_and_cancel_bid_with_disk_attachment")

        try:
            self.logger.debug(
                "Retrieving auctions to pick a valid region from an auction."
            )
            auctions: List[Auction] = self.foundry_client.get_auctions(
                project_id=self.project_id
            )
            self.logger.debug(f"Available auctions: {auctions}")
            if not auctions:
                self.fail("No auctions available to bid on.")
            # Pick the first auction or filter specifically for a known GPU, etc.
            chosen_auction = auctions[0]
            region_name = chosen_auction.region
            self.logger.debug(f"Chosen auction has region_name='{region_name}'")
            if not region_name:
                self.skipTest("Auction has no region specified; cannot attach a disk.")
            # Now we look up the region_id from the region_name if needed
            region_list: List[RegionResponse] = self.storage_client.get_regions()
            matching_region = next(
                (r for r in region_list if r.name == region_name), None
            )
            if not matching_region:
                self.skipTest(
                    f"No region_id found matching auction region '{region_name}'"
                )

            region_id = matching_region.region_id
            cluster_id = chosen_auction.cluster_id
            instance_type_id = chosen_auction.instance_type_id
            self.logger.debug(
                f"Auction cluster_id={cluster_id}, instance_type_id={instance_type_id}, region_id={region_id}"
            )

            # Create a unique disk name
            disk_id = str(uuid.uuid4())
            timestamp_str = time.strftime("%Y%m%d%H%M%S")
            # Add an extra random hex piece to reduce collisions
            unique_hex = secrets.token_hex(4)
            rand_tail = "".join(
                random.choices(string.ascii_lowercase + string.digits, k=4)
            )
            # e.g., final suffix = "20250114123045-abc1-91e6a777"
            volume_name = f"test-disk-{timestamp_str}-{rand_tail}-{unique_hex}"
            disk_interface = "Block"
            size = 10
            size_unit = "gb"

        except (APIError, NetworkError, AuthenticationError) as exc:
            pytest.skip(f"Failed to retrieve auctions or region info: {exc}")

        try:
            self.logger.debug("Creating disk attachment object")
            disk_attachment = DiskAttachment(
                disk_id=disk_id,
                name=volume_name,
                volume_name=volume_name,
                disk_interface=disk_interface,
                region_id=region_id,
                size=size,
                size_unit=size_unit,
            )
            self.logger.debug(f"DiskAttachment: {disk_attachment}")

            self.logger.debug("Creating disk via storage client")
            disk_response: DiskResponse = self.storage_client.create_disk(
                project_id=self.project_id, disk_attachment=disk_attachment
            )
            self.logger.debug(f"Disk creation response: {disk_response}")

            if disk_response.disk_id != disk_id:
                self.fail("Mismatch between requested disk_id and returned disk_id.")
        except Exception as exc:
            self.logger.error(f"Failed to create disk: {exc}")
            self.fail(f"Failed to create disk: {exc}")

        instance_quantity = 1
        limit_price_cents = 10000
        startup_script = "#!/bin/bash\necho 'Hello World'"

        # Instead of the old "test-bid" name, append time + random + hex:
        timestamp_str = time.strftime("%Y%m%d%H%M%S")
        unique_hex = secrets.token_hex(4)
        rand_tail = "".join(random.choices(string.ascii_lowercase + string.digits, k=4))
        order_name = (
            f"test-bid-{uuid.uuid4()}-{int(time.time())}-{secrets.token_hex(2)}"
        )
        self.logger.debug(f"Using order name: {order_name}")

        try:
            bid_payload = self.bid_manager.prepare_bid_payload(
                cluster_id=cluster_id,
                instance_quantity=instance_quantity,
                instance_type_id=instance_type_id,
                limit_price_cents=limit_price_cents,
                order_name=order_name,
                project_id=self.project_id,
                ssh_key_id=self.ssh_key_id,
                user_id=self.user_id,
                startup_script=startup_script,
                disk_attachments=[disk_attachment],
            )
        except Exception as exc:
            self.fail(f"Error while preparing bid payload: {exc}")

        try:
            bid_response_list: List[Bid] = self.bid_manager.submit_bid(
                project_id=self.project_id, bid_payload=bid_payload
            )
            self.logger.debug("Bid submitted successfully: %s", bid_response_list)
            self.assertTrue(len(bid_response_list) > 0, "Expected at least one bid.")
            bid = bid_response_list[0]
            bid_id = bid.id
        except TimeoutError as err:
            self.skipTest(f"Place bid timed out: {err}")
        except Exception as exc:
            self.logger.error(f"Failed to submit bid: {exc}")
            self.fail(f"Failed to submit bid: {exc}")

        self.assertIsNotNone(bid.id)
        self.assertEqual(bid.name, order_name)
        self.assertIsNotNone(bid.disk_ids)
        self.assertIn(disk_attachment.disk_id, bid.disk_ids)

        try:
            self.bid_manager.cancel_bid(project_id=self.project_id, bid_id=bid_id)
            self.logger.debug("Bid %s cancelled successfully.", bid_id)
        except Exception as exc:
            self.logger.error(f"Failed to cancel bid: {exc}")
            self.fail(f"Failed to cancel bid: {exc}")

        try:
            bids: List[Bid] = self.bid_manager.get_bids(project_id=self.project_id)
            cancelled_bid = next((bid for bid in bids if bid.id == bid_id), None)
            self.assertIsNotNone(cancelled_bid)
            self.assertIsNotNone(cancelled_bid.deactivated_at)
            self.logger.debug(f"Bid {bid_id} is confirmed cancelled.")
        except Exception as exc:
            self.fail(f"Failed to retrieve bids after cancellation: {exc}")

        try:
            self.storage_client.delete_disk(self.project_id, disk_id)
            self.logger.debug(f"Disk {disk_id} deleted successfully.")
        except Exception as exc:
            self.logger.error(f"Failed to delete disk during cleanup: {exc}")

    def test_submit_bid_without_disk_attachment(self) -> None:
        """Integration test: submit and cancel a bid without a disk attachment."""
        self.logger.debug("Starting test_submit_bid_without_disk_attachment")

        try:
            self.logger.debug(
                "Retrieving auctions to get cluster_id and instance_type_id"
            )
            auctions: List[Auction] = self.foundry_client.get_auctions(
                project_id=self.project_id
            )
            self.logger.debug(f"Available auctions: {auctions}")

            if not auctions:
                self.fail("No auctions available to bid on.")
            auction = auctions[0]
            cluster_id = auction.cluster_id
            instance_type_id = auction.instance_type_id
            self.logger.debug(
                f"Using cluster_id: {cluster_id}, instance_type_id: {instance_type_id}"
            )
        except Exception as exc:
            self.logger.error(f"Failed to retrieve auctions: {exc}")
            self.fail(f"Failed to retrieve auctions: {exc}")

        instance_quantity = 1
        limit_price_cents = 10000
        startup_script = "#!/bin/bash\necho 'Hello World'"
        order_name = (
            f"test-bid-{uuid.uuid4()}-{int(time.time())}-{secrets.token_hex(2)}"
        )
        self.logger.debug(f"Using order name: {order_name}")

        try:
            bid_payload = self.bid_manager.prepare_bid_payload(
                cluster_id=cluster_id,
                instance_quantity=instance_quantity,
                instance_type_id=instance_type_id,
                limit_price_cents=limit_price_cents,
                order_name=order_name,
                project_id=self.project_id,
                ssh_key_id=self.ssh_key_id,
                user_id=self.user_id,
                startup_script=startup_script,
                disk_attachments=[],
            )
        except Exception as exc:
            self.fail(f"Error while preparing bid payload: {exc}")

        try:
            bid_response_list: List[Bid] = self.bid_manager.submit_bid(
                project_id=self.project_id, bid_payload=bid_payload
            )
            self.logger.debug(
                "Bid submitted successfully without disk attachment: %s",
                bid_response_list,
            )
            self.assertTrue(len(bid_response_list) > 0, "Expected at least one bid.")
            bid = bid_response_list[0]
            bid_id = bid.id
        except TimeoutError as err:
            self.skipTest(f"Place bid timed out: {err}")
        except Exception as exc:
            self.logger.error(f"Failed to submit bid without disk attachment: {exc}")
            self.fail(f"Failed to submit bid without disk attachment: {exc}")

        self.assertIsNotNone(bid.id)
        self.assertEqual(bid.name, order_name)
        self.assertFalse(bid.disk_ids)

        try:
            self.bid_manager.cancel_bid(project_id=self.project_id, bid_id=bid_id)
            self.logger.debug("Bid %s cancelled successfully.", bid_id)
        except Exception as exc:
            self.logger.error(f"Failed to cancel bid: {exc}")
            self.fail(f"Failed to cancel bid: {exc}")

        try:
            bids: List[Bid] = self.bid_manager.get_bids(project_id=self.project_id)
            cancelled_bid = next((bid for bid in bids if bid.id == bid_id), None)
            self.assertIsNotNone(cancelled_bid)
            self.assertIsNotNone(cancelled_bid.deactivated_at)
            self.logger.debug(f"Bid {bid_id} is confirmed cancelled.")
        except Exception as exc:
            self.fail(f"Failed to retrieve bids after cancellation: {exc}")


if __name__ == "__main__":
    unittest.main()
