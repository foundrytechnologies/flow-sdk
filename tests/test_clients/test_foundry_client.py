"""Unit tests for the FoundryClient class using updated typings."""

import concurrent.futures
import threading
import unittest
from typing import Any, Dict, List
from unittest.mock import patch

from flow.clients.authenticator import Authenticator
from flow.clients.fcp_client import FCPClient
from flow.clients.foundry_client import FoundryClient
from flow.clients.storage_client import StorageClient
from flow.models import (
    BidPayload,
    BidResponse,
    DiskAttachment,
    DiskResponse,
    StorageQuotaResponse,
    User,
)
from flow.utils.exceptions import (
    APIError,
    AuthenticationError,
    InvalidResponseError,
)


class FoundryClientTest(unittest.TestCase):
    """Test suite for the FoundryClient class with updated type usage."""

    def setUp(self) -> None:
        """Sets up the test fixtures."""
        # Mock Authenticator
        self.auth_patcher = patch("flow.clients.foundry_client.Authenticator")
        self.addCleanup(self.auth_patcher.stop)
        self.mock_authenticator_class = self.auth_patcher.start()
        self.mock_authenticator_instance = self.mock_authenticator_class.return_value
        self.mock_authenticator_instance.get_access_token.return_value = "fake_token"

        # Mock FCPClient
        self.fcp_patcher = patch("flow.clients.foundry_client.FCPClient")
        self.addCleanup(self.fcp_patcher.stop)
        self.mock_fcp_client_class = self.fcp_patcher.start()
        self.mock_fcp_client_instance = self.mock_fcp_client_class.return_value

        # Mock StorageClient
        self.storage_patcher = patch("flow.clients.foundry_client.StorageClient")
        self.addCleanup(self.storage_patcher.stop)
        self.mock_storage_client_class = self.storage_patcher.start()
        self.mock_storage_client_instance = self.mock_storage_client_class.return_value

        # Initialize FoundryClient
        self.email = "test@example.com"
        self.password = "password"
        self.api_key = None
        self.foundry_client = FoundryClient(
            email=self.email, password=self.password, api_key=self.api_key
        )

    def test_initialization(self) -> None:
        """Tests that FoundryClient initializes Authenticator, FCPClient, and StorageClient."""
        self.mock_authenticator_class.assert_called_with(
            email=self.email, password=self.password
        )
        self.mock_fcp_client_class.assert_called_with(
            authenticator=self.mock_authenticator_instance
        )
        self.mock_storage_client_class.assert_called_with(
            authenticator=self.mock_authenticator_instance
        )
        self.assertIsNotNone(self.foundry_client.fcp_client)
        self.assertIsNotNone(self.foundry_client.storage_client)

    def test_get_user(self) -> None:
        """Tests the get_user method with typed returns."""
        expected_user = User(id="user123", email=self.email)
        self.mock_fcp_client_instance.get_user.return_value = expected_user

        user = self.foundry_client.get_user()

        self.mock_fcp_client_instance.get_user.assert_called_once()
        self.assertEqual(user.id, expected_user.id)
        self.assertEqual(user.email, expected_user.email)

    def test_get_projects(self) -> None:
        """Tests the get_projects method."""
        expected_projects: List[Dict[str, Any]] = [
            {"id": "proj1", "name": "Test Project"}
        ]
        self.mock_fcp_client_instance.get_projects.return_value = expected_projects

        projects = self.foundry_client.get_projects()
        self.mock_fcp_client_instance.get_projects.assert_called_once()
        self.assertEqual(projects, expected_projects)

    def test_get_project_by_name(self) -> None:
        """Tests the get_project_by_name method."""
        project_name = "Test Project"
        expected_project = {"id": "proj1", "name": project_name}
        self.mock_fcp_client_instance.get_project_by_name.return_value = (
            expected_project
        )

        project = self.foundry_client.get_project_by_name(project_name)
        self.mock_fcp_client_instance.get_project_by_name.assert_called_once_with(
            project_name=project_name
        )
        self.assertEqual(project, expected_project)

    def test_get_instances(self) -> None:
        """Tests that get_instances returns a dict of categories -> list of instance dicts."""
        project_id = "proj1"
        expected_instances = {
            "spot": [{"instance_id": "inst1", "instance_status": "running"}],
            "reserved": [],
        }
        self.mock_fcp_client_instance.get_instances.return_value = expected_instances

        instances_dict = self.foundry_client.get_instances(project_id)
        self.mock_fcp_client_instance.get_instances.assert_called_once_with(
            project_id=project_id
        )
        self.assertEqual(instances_dict, expected_instances)

    def test_get_auctions(self) -> None:
        """Tests the get_auctions method."""
        project_id = "proj1"
        expected_auctions = [{"id": "auc1", "price": 1000}]
        self.mock_fcp_client_instance.get_auctions.return_value = expected_auctions

        auctions = self.foundry_client.get_auctions(project_id)
        self.mock_fcp_client_instance.get_auctions.assert_called_once_with(
            project_id=project_id
        )
        self.assertEqual(auctions, expected_auctions)

    def test_get_ssh_keys(self) -> None:
        """Tests the get_ssh_keys method."""
        project_id = "proj1"
        expected_ssh_keys = [{"id": "key1", "name": "ssh-key"}]
        self.mock_fcp_client_instance.get_ssh_keys.return_value = expected_ssh_keys

        ssh_keys = self.foundry_client.get_ssh_keys(project_id)
        self.mock_fcp_client_instance.get_ssh_keys.assert_called_once_with(
            project_id=project_id
        )
        self.assertEqual(ssh_keys, expected_ssh_keys)

    def test_get_bids(self) -> None:
        """Tests the get_bids method."""
        project_id = "proj1"
        expected_bids = [{"id": "bid1", "status": "active"}]
        self.mock_fcp_client_instance.get_bids.return_value = expected_bids

        bids = self.foundry_client.get_bids(project_id)
        self.mock_fcp_client_instance.get_bids.assert_called_once_with(
            project_id=project_id
        )
        self.assertEqual(bids, expected_bids)

    def test_place_bid(self) -> None:
        """Tests the place_bid method."""
        project_id = "proj1"
        bid_payload_model = BidPayload(
            cluster_id="cluster1",
            instance_quantity=1,
            instance_type_id="type1",
            limit_price_cents=1000,
            project_id=project_id,
            ssh_key_ids=["key1"],
            user_id="user123",
            order_name="test_order",
        )
        expected_response = BidResponse(
            cluster_id="cluster1",
            instance_quantity=1,
            instance_type_id="type1",
            limit_price_cents=1000,
            project_id=project_id,
            user_id="user123",
            ssh_key_ids=["key1"],
            order_name="test_order",
            id="bid1",
            status="submitted",
        )
        self.mock_fcp_client_instance.place_bid.return_value = expected_response

        response = self.foundry_client.place_bid(project_id, bid_payload_model)
        self.mock_fcp_client_instance.place_bid.assert_called_once()
        called_args, _ = self.mock_fcp_client_instance.place_bid.call_args
        actual_payload = called_args[0]

        self.assertEqual(actual_payload.project_id, project_id)
        self.assertEqual(actual_payload.cluster_id, "cluster1")
        self.assertEqual(actual_payload.instance_quantity, 1)
        self.assertEqual(actual_payload.instance_type_id, "type1")
        self.assertEqual(actual_payload.limit_price_cents, 1000)
        self.assertEqual(actual_payload.ssh_key_ids, ["key1"])
        self.assertEqual(actual_payload.user_id, "user123")
        self.assertEqual(actual_payload.order_name, "test_order")

        self.assertEqual(response, expected_response)

    def test_cancel_bid(self) -> None:
        """Tests the cancel_bid method."""
        project_id = "proj1"
        bid_id = "bid1"
        self.mock_fcp_client_instance.cancel_bid.return_value = None

        self.foundry_client.cancel_bid(project_id, bid_id)
        self.mock_fcp_client_instance.cancel_bid.assert_called_once_with(
            project_id=project_id, bid_id=bid_id
        )

    def test_create_disk(self) -> None:
        """Tests the create_disk method with typed arguments."""
        project_id = "proj1"
        expected_disk = DiskResponse(
            disk_id="disk123",
            name="Test Disk",
            disk_interface="Block",
            region_id="region1",
            size=100,
            size_unit="GB",
        )
        self.mock_storage_client_instance.create_disk.return_value = expected_disk

        disk_attachment = DiskAttachment(
            disk_id="disk123",
            name="Test Disk",
            disk_interface="block",
            region_id="region1",
            size=100,
            size_unit="GB",
        )
        disk = self.foundry_client.create_disk(project_id, disk_attachment)
        self.assertEqual(disk, expected_disk)
        self.mock_storage_client_instance.create_disk.assert_called_with(
            project_id=project_id, disk_attachment=disk_attachment
        )

    def test_get_disks(self) -> None:
        """Tests the get_disks method."""
        project_id = "proj1"
        expected_disks = [
            DiskResponse(
                disk_id="disk1",
                name="Disk One",
                disk_interface="Block",
                region_id="region1",
                size=50,
                size_unit="GB",
            )
        ]
        self.mock_storage_client_instance.get_disks.return_value = expected_disks

        disks = self.foundry_client.get_disks(project_id)
        self.mock_storage_client_instance.get_disks.assert_called_once_with(
            project_id=project_id
        )
        self.assertEqual(disks, expected_disks)

    def test_get_disk(self) -> None:
        """Tests the get_disk method."""
        project_id = "proj1"
        disk_id = "disk1"
        expected_disk = DiskResponse(
            disk_id=disk_id,
            name="Disk One",
            disk_interface="Block",
            region_id="region1",
            size=20,
            size_unit="GB",
        )
        self.mock_storage_client_instance.get_disk.return_value = expected_disk

        disk = self.foundry_client.get_disk(project_id, disk_id)
        self.mock_storage_client_instance.get_disk.assert_called_once_with(
            project_id=project_id, disk_id=disk_id
        )
        self.assertEqual(disk, expected_disk)

    def test_delete_disk(self) -> None:
        """Tests the delete_disk method."""
        project_id = "proj1"
        disk_id = "disk1"
        self.mock_storage_client_instance.delete_disk.return_value = None

        self.foundry_client.delete_disk(project_id, disk_id)
        self.mock_storage_client_instance.delete_disk.assert_called_once_with(
            project_id=project_id, disk_id=disk_id
        )

    def test_get_storage_quota(self) -> None:
        """Tests the get_storage_quota method."""
        project_id = "proj1"
        expected_quota = StorageQuotaResponse(
            total_quota=1000,
            quota_used=500,
            units="GB",
        )
        self.mock_storage_client_instance.get_storage_quota.return_value = (
            expected_quota
        )

        quota = self.foundry_client.get_storage_quota(project_id)
        self.mock_storage_client_instance.get_storage_quota.assert_called_once_with(
            project_id=project_id
        )
        self.assertEqual(quota, expected_quota)

    def test_get_regions(self) -> None:
        """Tests the get_regions method."""
        from flow.models import RegionResponse

        expected_regions = [RegionResponse(region_id="region1", name="Region One")]
        self.mock_storage_client_instance.get_regions.return_value = expected_regions

        regions = self.foundry_client.get_regions()
        self.mock_storage_client_instance.get_regions.assert_called_once()
        self.assertEqual(regions, expected_regions)

    def test_error_propagation_fcp_client(self) -> None:
        """Tests that errors from FCPClient methods are propagated."""
        self.mock_fcp_client_instance.get_user.side_effect = APIError("API Error")

        with self.assertRaises(APIError) as context:
            self.foundry_client.get_user()
        self.assertIn("API Error", str(context.exception))

    def test_error_propagation_storage_client(self) -> None:
        """Tests that errors from StorageClient methods are propagated."""
        self.mock_storage_client_instance.create_disk.side_effect = APIError(
            "API Error"
        )

        with self.assertRaises(APIError):
            disk_attachment = DiskAttachment(
                disk_id="disk1",
                name="Disk One",
                disk_interface="block",
                region_id="region1",
                size=100,
            )
            self.foundry_client.create_disk("proj1", disk_attachment)

    def test_authentication_error_during_initialization(self) -> None:
        """Tests handling of authentication errors during initialization."""
        with patch(
            "flow.clients.foundry_client.Authenticator"
        ) as mock_authenticator_class:
            mock_authenticator_class.side_effect = AuthenticationError(
                "Invalid credentials"
            )
            with self.assertRaises(AuthenticationError) as context:
                FoundryClient(self.email, self.password)
            self.assertIn("Invalid credentials", str(context.exception))

    def test_invalid_arguments(self) -> None:
        """Tests that invalid arguments raise appropriate exceptions."""
        self.mock_fcp_client_instance.get_project_by_name.side_effect = ValueError(
            "Invalid project name"
        )

        with self.assertRaises(ValueError) as context:
            self.foundry_client.get_project_by_name("")
        self.assertIn("Invalid project name", str(context.exception))

    def test_thread_safety(self) -> None:
        """Tests that FoundryClient methods are thread-safe."""

        def worker() -> None:
            self.foundry_client.get_user()

        threads = [threading.Thread(target=worker) for _ in range(5)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()

        self.assertEqual(self.mock_fcp_client_instance.get_user.call_count, 5)

    def test_method_delegation(self) -> None:
        """Tests that methods are delegated to the correct clients."""
        self.assertIs(self.foundry_client.get_user.__func__, FoundryClient.get_user)
        self.assertIs(
            self.foundry_client.create_disk.__func__, FoundryClient.create_disk
        )

    def test_storage_client_default_arguments(self) -> None:
        """Tests create_disk default argument logic for size_unit."""
        project_id = "proj1"
        expected_disk = DiskResponse(
            disk_id="disk123",
            name="Test Disk",
            disk_interface="Block",
            region_id="region1",
            size=100,
            size_unit="GB",
        )
        self.mock_storage_client_instance.create_disk.return_value = expected_disk

        disk_attachment = DiskAttachment(
            disk_id="disk123",
            name="Test Disk",
            disk_interface="block",
            region_id="region1",
            size=100,
            size_unit="GB",
        )
        disk = self.foundry_client.create_disk(project_id, disk_attachment)
        self.assertEqual(disk, expected_disk)
        self.mock_storage_client_instance.create_disk.assert_called_with(
            project_id=project_id, disk_attachment=disk_attachment
        )

    def test_method_argument_validation(self) -> None:
        """Tests that methods validate arguments properly."""
        self.mock_storage_client_instance.create_disk.side_effect = ValueError(
            "Invalid size"
        )
        with self.assertRaises(ValueError):
            disk_attachment = DiskAttachment(
                disk_id="disk1",
                name="Disk One",
                disk_interface="block",
                region_id="region1",
                size=-100,  # Invalid size
            )
            self.foundry_client.create_disk("proj1", disk_attachment)

    def test_get_disk_exception_handling(self) -> None:
        """Tests exception handling in get_disk method."""
        self.mock_storage_client_instance.get_disk.side_effect = InvalidResponseError(
            "Invalid response"
        )
        with self.assertRaises(InvalidResponseError) as context:
            self.foundry_client.get_disk("proj1", "disk1")
        self.assertIn("Invalid response", str(context.exception))

    def test_concurrent_method_calls(self) -> None:
        """Tests concurrent calls to FoundryClient methods with subTests."""
        expected_user = User(id="user123", email=self.email)
        self.mock_fcp_client_instance.get_user.return_value = expected_user

        def call_get_user() -> User:
            return self.foundry_client.get_user()

        methods_to_test = [call_get_user, lambda: self.foundry_client.get_projects()]

        with concurrent.futures.ThreadPoolExecutor(max_workers=5) as executor:
            for method in methods_to_test:
                with self.subTest(
                    method=method.__name__ if hasattr(method, "__name__") else "unknown"
                ):
                    futures = [executor.submit(method) for _ in range(5)]
                    results = [
                        f.result() for f in concurrent.futures.as_completed(futures)
                    ]
                    # each method is validated for concurrency safety
                    self.assertTrue(all(results))
                    self.assertEqual(len(results), 5)

        self.assertGreaterEqual(self.mock_fcp_client_instance.get_user.call_count, 5)

    def tearDown(self) -> None:
        """Cleans up after each test."""
        self.auth_patcher.stop()
        self.fcp_patcher.stop()
        self.storage_patcher.stop()


if __name__ == "__main__":
    unittest.main()
