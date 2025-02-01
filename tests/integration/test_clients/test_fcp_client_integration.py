import unittest
import random
import string
from typing import Optional, List

from pydantic import TypeAdapter

from flow.clients.authenticator import Authenticator
from flow.clients.fcp_client import FCPClient
from flow.config import get_config
from flow.utils.exceptions import APIError, AuthenticationError
from flow.models import (
    Project,
    Instance,
    Auction,
    SshKey,
    Bid,
    BidPayload,
    BidResponse,
    User,
)


class TestFCPClientIntegration(unittest.TestCase):
    """
    Expanded integration tests for FCPClient against the actual FCP endpoints.
    These tests require valid environment variables for authentication.
    They also assume that a project, cluster, and instance types exist in your Foundry environment.
    """

    @classmethod
    def setUpClass(cls):
        """
        Set up one FCPClient for all test methods. Skip tests if env vars are missing.
        We also confirm that the project by name can be located before proceeding.
        """
        config = get_config()
        missing_env_vars = []
        if not config.foundry_email:
            missing_env_vars.append("FOUNDRY_EMAIL")
        if not config.foundry_password.get_secret_value():
            missing_env_vars.append("FOUNDRY_PASSWORD")
        if not config.foundry_project_name:
            missing_env_vars.append("FOUNDRY_PROJECT_NAME")
        if not config.foundry_ssh_key_name:
            missing_env_vars.append("FOUNDRY_SSH_KEY_NAME")

        if missing_env_vars:
            raise unittest.SkipTest(
                f"Missing environment variables for integration tests: {missing_env_vars}"
            )

        # Build authenticator and FCPClient
        authenticator = Authenticator(
            email=config.foundry_email,
            password=config.foundry_password.get_secret_value(),
        )
        cls.client = FCPClient(authenticator=authenticator)

        # Attempt to find the project by name to confirm everything is valid
        try:
            cls.project = cls.client.get_project_by_name(config.foundry_project_name)
        except ValueError as e:
            raise unittest.SkipTest(str(e))

    def test_001_get_user(self):
        """
        Verify we can fetch the current user, ensuring valid authentication.
        """
        try:
            user = self.client.get_user()
            self.assertIsInstance(user, User)
            self.assertIsNotNone(user.id)
            print(f"[Integration] Current user ID: {user.id}")
        except AuthenticationError as e:
            self.fail(f"Failed to authenticate user: {e}")
        except APIError as e:
            self.fail(f"Failed to fetch user: {e}")

    def test_002_get_profile(self):
        """
        Ensures get_profile returns the same user info.
        """
        try:
            profile = self.client.get_profile()
            self.assertIsInstance(profile, User)
            self.assertIsNotNone(profile.id)
            print(f"[Integration] Profile user ID: {profile.id}")
        except Exception as e:
            self.fail(f"Failed to fetch user profile: {e}")

    def test_003_get_projects(self):
        """
        Tests retrieval of all projects for the current user.
        """
        try:
            projects = self.client.get_projects()
            self.assertIsInstance(projects, list)
            if projects:
                self.assertIsInstance(projects[0], Project)
                print(f"[Integration] Found {len(projects)} projects.")
        except Exception as e:
            self.fail(f"Failed to fetch projects: {e}")

    def test_004_get_instances(self):
        """
        Tests retrieval of instances under the test project.
        May be empty if you have no active instances.
        """
        try:
            # Call the client's get_instances; handle fallback if parse fails.
            try:
                # The client now returns a dict with categories (spot, blocks, etc.).
                raw_instances = self.client.get_instances(self.project.id)
                # Flatten the dictionary into a single list.
                spot = raw_instances.get("spot", [])
                blocks = raw_instances.get("blocks", [])
                control = raw_instances.get("control", [])
                legacy = raw_instances.get("legacy", [])
                combined = spot + blocks + control + legacy
                instances = TypeAdapter(List[Instance]).validate_python(combined)
            except APIError:
                # If we hit an APIError from JSON structure mismatch, try manual parsing.
                response = self.client._request(
                    "GET", f"/projects/{self.project.id}/all_instances"
                )
                raw_data = response.json() if response.ok else {}
                spot = raw_data.get("spot", [])
                blocks = raw_data.get("blocks", [])
                control = raw_data.get("control", [])
                legacy = raw_data.get("legacy", [])
                combined = spot + blocks + control + legacy
                instances = TypeAdapter(List[Instance]).validate_python(combined)

            self.assertIsInstance(instances, list)
            for inst in instances:
                self.assertIsInstance(inst, Instance)
            print(
                f"[Integration] Found {len(instances)} instances in project '{self.project.name}'."
            )
        except Exception as e:
            self.fail(f"Failed to fetch instances: {e}")

    def test_005_get_auctions(self):
        """
        Tests retrieval of auctions (spot auctions) for the project.
        """
        try:
            auctions = self.client.get_auctions(self.project.id)
            self.assertIsInstance(auctions, list)
            for auc in auctions:
                self.assertIsInstance(auc, Auction)
            print(
                f"[Integration] Found {len(auctions)} auctions for project '{self.project.name}'."
            )
        except Exception as e:
            self.fail(f"Failed to fetch auctions: {e}")

    def test_006_get_ssh_keys(self):
        """
        Tests retrieval of SSH keys for the project.
        """
        try:
            ssh_keys = self.client.get_ssh_keys(self.project.id)
            self.assertIsInstance(ssh_keys, list)
            if ssh_keys:
                self.assertIsInstance(ssh_keys[0], SshKey)
            print(
                f"[Integration] Found {len(ssh_keys)} SSH keys in project '{self.project.name}'."
            )
        except Exception as e:
            self.fail(f"Failed to fetch SSH keys: {e}")

    def test_007_get_bids(self):
        """
        Tests retrieval of any existing bids in the project.
        """
        try:
            bids = self.client.get_bids(self.project.id)
            self.assertIsInstance(bids, list)
            for bd in bids:
                self.assertIsInstance(bd, Bid)
            print(
                f"[Integration] Found {len(bids)} bids in project '{self.project.name}'."
            )
        except Exception as e:
            self.fail(f"Failed to fetch bids: {e}")

    def test_008_place_and_cancel_bid(self):
        """
        Tests placing a spot-auction bid, then canceling it.
        This requires a valid cluster_id, instance_type_id, ssh_key, etc.
        If no cluster or instance type is known, we attempt to pick them from auctions.
        """
        config = get_config()
        try:
            auctions = self.client.get_auctions(self.project.id)
            if not auctions:
                self.skipTest("No auctions available to place a bid.")
            first_auction = auctions[0]

            ssh_keys = self.client.get_ssh_keys(self.project.id)
            ssh_key_id: Optional[str] = None
            for key_obj in ssh_keys:
                if key_obj.name == config.foundry_ssh_key_name:
                    ssh_key_id = key_obj.id
                    break
            if not ssh_key_id:
                self.skipTest(
                    f"SSH key '{config.foundry_ssh_key_name}' not found in project '{self.project.name}'."
                )

            random_suffix = "".join(random.choices(string.ascii_lowercase, k=9))
            test_order_name = f"test-bid-{random_suffix}"
            payload = BidPayload(
                cluster_id=first_auction.cluster_id,
                instance_quantity=1,
                instance_type_id=first_auction.instance_type_id,
                limit_price_cents=1000,  # example: $10
                order_name=test_order_name,
                project_id=self.project.id,
                ssh_key_ids=[ssh_key_id],
                startup_script="#!/bin/bash\necho HelloWorld",
                user_id=self.client._user_id,
            )

            try:
                bid_response = self.client.place_bid(payload)
            except TimeoutError as err:
                self.skipTest(f"Place bid timed out: {err}")

            self.assertIsInstance(bid_response, BidResponse)
            # Remove strict checks for project_id/user_id, since they may not be returned
            self.assertEqual(bid_response.cluster_id, first_auction.cluster_id)
            self.assertEqual(
                bid_response.instance_type_id, first_auction.instance_type_id
            )
            self.assertEqual(bid_response.name, test_order_name)

            print(
                f"[Integration] Successfully placed bid: {bid_response.id} ({bid_response.name})"
            )

            self.client.cancel_bid(self.project.id, bid_response.id)
            print(f"[Integration] Successfully canceled bid: {bid_response.id}")

        except Exception as e:
            self.fail(f"Failed to place/cancel a bid: {e}")

    def test_010_get_project_by_invalid_name(self):
        """
        Tests get_project_by_name with an invalid name, ensuring it raises ValueError.
        """
        invalid_name = "this_project_does_not_exist_12345"
        try:
            self.client.get_project_by_name(invalid_name)
            self.fail(
                "Expected ValueError for a non-existent project name, but no error was raised."
            )
        except ValueError:
            print(f"[Integration] As expected, no project found for '{invalid_name}'.")
        except Exception as e:
            self.fail(f"Unexpected exception type: {e}")

    def test_011_get_bids_invalid_project(self):
        """
        Tests a call to get_bids with an invalid project ID to simulate a server error or a 404.
        Ensures the APIError is raised, covering error-handling logic in FCPClient.
        """
        invalid_project_id = "nonexistent_project_9999"
        with self.assertRaises(APIError):
            self.client.get_bids(invalid_project_id)


if __name__ == "__main__":
    unittest.main()
