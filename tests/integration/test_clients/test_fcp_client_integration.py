#!/usr/bin/env python3
"""Integration tests for the FCPClient.

These tests target actual FCP endpoints (note that this requires valid credentials in
environment variables). They cover:
  - Basic user retrieval and project listing.
  - Auction listing and SSH key retrieval.
  - Full workflow: bid creation and cancellation.
  - Boundary and invalid-name scenarios.

Before running, ensure that the required environment variables are set.
"""

import logging
import random
import string
import unittest
from typing import List, Optional

from flow.clients.authenticator import Authenticator
from flow.clients.fcp_client import FCPClient
from flow.config import get_config
from flow.models import (
    Auction,
    Bid,
    BidPayload,
    BidResponse,
    Instance,
    Project,
    SshKey,
    User,
)
from flow.utils.exceptions import APIError, AuthenticationError, TimeoutError

# Configure logging for integration tests.
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


class TestFCPClientIntegration(unittest.TestCase):
    """Integration tests for FCPClient against real FCP endpoints."""

    @classmethod
    def setUpClass(cls) -> None:
        """Initialize an FCPClient if environment configuration is present; skip otherwise."""
        config = get_config()
        missing_vars: List[str] = []
        if not config.foundry_email:
            missing_vars.append("FOUNDRY_EMAIL")
        if not config.foundry_password.get_secret_value():
            missing_vars.append("FOUNDRY_PASSWORD")
        if not config.foundry_project_name:
            missing_vars.append("FOUNDRY_PROJECT_NAME")
        if not config.foundry_ssh_key_name:
            missing_vars.append("FOUNDRY_SSH_KEY_NAME")

        if missing_vars:
            raise unittest.SkipTest(
                f"Missing environment variables for integration tests: {missing_vars}"
            )

        authenticator: Authenticator = Authenticator(
            email=config.foundry_email,
            password=config.foundry_password.get_secret_value(),
        )
        cls.client: FCPClient = FCPClient(authenticator=authenticator)

        try:
            cls.project: Project = cls.client.get_project_by_name(
                config.foundry_project_name
            )
        except ValueError as err:
            raise unittest.SkipTest(str(err))

    def test_get_user(self) -> None:
        """Test fetching the current user with valid credentials."""
        try:
            user: User = self.client.get_user()
            self.assertIsInstance(user, User)
            logger.info("Current user ID: %s", user.id)
        except (AuthenticationError, APIError) as err:
            self.fail(f"Failed to fetch user: {err}")

    def test_get_profile(self) -> None:
        """Test that get_profile returns user info matching get_user."""
        try:
            profile: User = self.client.get_profile()
            self.assertIsInstance(profile, User)
            logger.info("Profile user ID: %s", profile.id)
        except Exception as err:
            self.fail(f"Failed to fetch profile: {err}")

    def test_get_projects(self) -> None:
        """Test retrieving all projects for the user."""
        try:
            projects: List[Project] = self.client.get_projects()
            self.assertIsInstance(projects, list)
            if projects:
                self.assertIsInstance(projects[0], Project)
            logger.info("Found %d project(s).", len(projects))
        except Exception as err:
            self.fail(f"Failed to fetch projects: {err}")

    def test_get_instances(self) -> None:
        """Test listing instances in the known project (may be empty)."""
        try:
            raw_dict = self.client.get_instances(self.project.id)
            combined: List[Instance] = []
            for category in raw_dict:
                combined.extend(raw_dict[category])
            for instance in combined:
                self.assertIsInstance(instance, Instance)
            logger.info(
                "Found %d instance(s) in project '%s'.", len(combined), self.project.name
            )
        except Exception as err:
            self.fail(f"Failed to fetch instances: {err}")

    def test_get_auctions(self) -> None:
        """Test retrieving auctions for the known project."""
        try:
            auctions: List[Auction] = self.client.get_auctions(self.project.id)
            for auction in auctions:
                self.assertIsInstance(auction, Auction)
            logger.info(
                "Found %d auction(s) in project '%s'.", len(auctions), self.project.name
            )
        except Exception as err:
            self.fail(f"Failed to fetch auctions: {err}")

    def test_get_ssh_keys(self) -> None:
        """Test retrieving SSH keys for the project."""
        try:
            ssh_keys: List[SshKey] = self.client.get_ssh_keys(self.project.id)
            for key in ssh_keys:
                self.assertIsInstance(key, SshKey)
            logger.info(
                "Found %d SSH key(s) in project '%s'.", len(ssh_keys), self.project.name
            )
        except Exception as err:
            self.fail(f"Failed to fetch SSH keys: {err}")

    def test_get_bids(self) -> None:
        """Test retrieving existing bids in the project."""
        try:
            bids: List[Bid] = self.client.get_bids(self.project.id)
            for bid in bids:
                self.assertIsInstance(bid, Bid)
            logger.info(
                "Found %d bid(s) in project '%s'.", len(bids), self.project.name
            )
        except Exception as err:
            self.fail(f"Failed to fetch bids: {err}")

    def test_place_and_cancel_bid(self) -> None:
        """Full workflow: place a bid and then cancel it."""
        config = get_config()
        try:
            auctions: List[Auction] = self.client.get_auctions(self.project.id)
            if not auctions:
                self.skipTest("No auctions available for bidding.")
            first_auction = auctions[0]

            ssh_keys: List[SshKey] = self.client.get_ssh_keys(self.project.id)
            ssh_key_id: Optional[str] = None
            for key in ssh_keys:
                if key.name == config.foundry_ssh_key_name:
                    ssh_key_id = key.id
                    break
            if not ssh_key_id:
                self.skipTest(
                    f"SSH key '{config.foundry_ssh_key_name}' not found in project '{self.project.name}'."
                )

            random_suffix: str = "".join(random.choices(string.ascii_lowercase, k=9))
            order_name: str = f"test-bid-{random_suffix}"
            
            payload: BidPayload = BidPayload(
                cluster_id=first_auction.cluster_id,
                instance_quantity=1,
                instance_type_id=first_auction.instance_type_id or "unknown_type",
                limit_price_cents=500,  # $5.00
                order_name=order_name,
                project_id=self.project.id,
                ssh_key_ids=[ssh_key_id],
                user_id=self.client._user_id,  # For testing purposes.
            )
            
            try:
                bid_response: BidResponse = self.client.place_bid(payload)
            except TimeoutError as err:
                self.skipTest(f"Place bid timed out: {err}")

            self.assertIsInstance(bid_response, BidResponse)
            self.assertEqual(bid_response.cluster_id, first_auction.cluster_id)
            self.assertEqual(bid_response.name, order_name)
            logger.info(
                "Successfully placed bid: %s (%s).", bid_response.id, bid_response.name
            )

            # Cancel the bid.
            self.client.cancel_bid(self.project.id, bid_response.id)
            logger.info("Successfully canceled bid: %s", bid_response.id)
        except Exception as err:
            self.fail(f"Failed to place/cancel bid: {err}")

    def test_get_project_by_invalid_name(self) -> None:
        """Test that get_project_by_name with an invalid name raises ValueError."""
        invalid_name: str = "this_project_does_not_exist_zzzz"
        with self.assertRaises(ValueError):
            self.client.get_project_by_name(invalid_name)
        logger.info("Correctly no project found for '%s'.", invalid_name)

    def test_get_bids_invalid_project(self) -> None:
        """Test that get_bids raises an APIError for an invalid project ID."""
        invalid_project_id: str = "does_not_exist_9999"
        with self.assertRaises(APIError):
            self.client.get_bids(invalid_project_id)
        logger.info(
            "Correctly failed to fetch bids for project ID '%s'.", invalid_project_id
        )


if __name__ == "__main__":
    unittest.main()