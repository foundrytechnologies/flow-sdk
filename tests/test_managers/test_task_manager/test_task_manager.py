"""Integration tests (and related test classes) for the FlowTaskManager class.

These tests validate functionality around authentication, auctions,
bids, and storage handling. The tests aim to emulate real usage scenarios
of the FlowTaskManager class by leveraging mocks and integration logic.
"""

import os
import random
import string
import unittest
import uuid

import pytest
from unittest.mock import Mock, patch

# Local imports
from src.flow.managers.task_manager import (
    FlowTaskManager,
    AuthenticationError,
    NoMatchingAuctionsError,
    BidSubmissionError,
)
from src.flow.task_config import ConfigModel, ResourcesSpecification, TaskManagement
from src.flow.task_config import ConfigParser
from src.flow.task_config.models import Port
from flow.config import get_config
from src.flow.clients.foundry_client import FoundryClient, Authenticator
from src.flow.managers.auction_finder import AuctionFinder
from src.flow.managers.bid_manager import BidManager
from src.flow.models import (
    User,
    Project,
    SshKey,
    Auction,
    Bid,
    SpotInstance,
)
from src.flow.models import BidPayload
from src.flow.managers.instance_manager import InstanceManager
from src.flow.models.disk_attachment import DiskAttachment
from src.flow.clients.storage_client import StorageClient
from src.flow.managers.storage_manager import StorageManager
from src.flow.models.storage_responses import RegionResponse

# Path to the 'flow_example.yaml' configuration file used in some tests.
TEST_YAML_FILE = os.path.abspath(
    os.path.join(os.path.dirname(__file__), "../../../flow_example.yaml")
)

"""
Integration tests for FlowTaskManager.
"""

# -----------------------------------------------------------------------------
# Retrieve typed environment-based settings for skip logic
# -----------------------------------------------------------------------------
settings = get_config()

import pytest

pytestmark = pytest.mark.skipif(
    not all(
        [
            settings.foundry_email,
            bool(settings.foundry_password.get_secret_value().strip()),
            settings.foundry_project_name,
            settings.foundry_ssh_key_name,
        ]
    ),
    reason="Skipping FlowTaskManagerIntegration tests due to missing required Foundry environment variables.",
)


class TestFlowTaskManager(unittest.TestCase):
    """Tests for the FlowTaskManager class with mocks, focusing on unit test coverage."""

    def setUp(self):
        """Set up the test environment with a mock configuration and clients."""
        # Sample configuration
        self.config = ConfigModel(
            name="test-task",
            task_management=TaskManagement(priority="standard"),
            resources_specification=ResourcesSpecification(
                fcp_instance="fh1.xlarge",
                num_instances=1,
                gpu_type="a100",
                num_gpus=1,
                intranode_interconnect="PCIe",
                internode_interconnect="100G_IB",
            ),
            startup_script='echo "Hello World"',
        )
        self.config_parser = Mock(spec=ConfigParser)
        self.config_parser.config = self.config
        self.config_parser.get_ports.return_value = [80, 443]

        # Mock the FoundryClient
        self.foundry_client = Mock(spec=FoundryClient)
        self.foundry_client.get_user.return_value = User(
            id="user-123",
            name="FakeUser",
            email="fake@example.com",
        )
        self.foundry_client.get_projects.return_value = [
            Project(id="proj-123", name=settings.foundry_project_name, created_ts=None)
        ]
        self.foundry_client.get_ssh_keys.return_value = [
            SshKey(id="key-123", name=settings.foundry_ssh_key_name)
        ]

        # Mock the authenticator
        self.authenticator = Mock(spec=Authenticator)
        self.authenticator.access_token = "fake-token"

        # Set the authenticator on the foundry_client mock
        self.foundry_client.authenticator = self.authenticator

        # Real managers with the mocked FoundryClient
        self.auction_finder = AuctionFinder(
            foundry_client=self.foundry_client,
            logger_obj=None,
            local_catalog_path=None
        )
        self.bid_manager = BidManager(self.foundry_client)
        self.instance_manager = InstanceManager(self.foundry_client)

        # Initialize the FlowTaskManager
        self.task_manager = FlowTaskManager(
            config_parser=self.config_parser,
            foundry_client=self.foundry_client,
            auction_finder=self.auction_finder,
            bid_manager=self.bid_manager,
        )

        # Mock create_disk return value
        self.foundry_client.create_disk.return_value = DiskAttachment(
            disk_id="disk-123",
            name="test-disk",
            volume_name="test-disk",
            disk_interface="Block",
            region_id="eu-central1-a",
            size=100,
            size_unit="gb",
        )

        # Generate a valid UUID for disk_id
        test_disk_id = str(uuid.uuid4())

        # Generate a random 8-digit suffix
        self.random_suffix = "".join(random.choices(string.digits, k=8))

        # Create a valid disk name
        self.disk_name = f"testdisk-{self.random_suffix}"

        # Build a DiskAttachment object with the valid disk_name
        self.disk_attachment = DiskAttachment(
            disk_id=test_disk_id,
            name=self.disk_name,
            volume_name=self.disk_name,
            disk_interface="Block",
            region_id="eu-central1-a",
            size=100,
            size_unit="gb",
        )

        # Mock the StorageManager
        self.storage_manager = Mock(spec=StorageManager)
        self.storage_manager.handle_persistent_storage.return_value = (
            self.disk_attachment
        )

        # Patch the StorageManager in FlowTaskManager
        patcher = patch(
            "src.flow.managers.task_manager.StorageManager",
            return_value=self.storage_manager,
        )
        self.addCleanup(patcher.stop)
        self.mock_storage_manager = patcher.start()

        # Mock auctions data with matching criteria
        mock_auction = Auction(
            cluster_id="cluster-123",
            instance_type_id="type-123",
            fcp_instance="a100.2x.SXM4.IB",
            gpu_type="a100",
            inventory_quantity=self.config.resources_specification.num_gpus,
            num_gpus=1,
            intranode_interconnect=self.config.resources_specification.intranode_interconnect.lower(),  # noqa: E501
            internode_interconnect=self.config.resources_specification.internode_interconnect.lower(),  # noqa: E501
            region_id="us-central1-a",
        )
        self.foundry_client.get_auctions.return_value = [mock_auction]

        # Mock config_parser's persistent_storage method
        self.config_parser.get_persistent_storage = Mock(return_value=Mock())

        # Generate a valid UUID for region_id
        test_region_id = str(uuid.uuid4())

        # Mock FoundryClient's get_regions
        self.foundry_client.get_regions.return_value = [
            RegionResponse(region_id="us-central1-a", name="US Central A"),
            RegionResponse(region_id="eu-central1-a", name="EU Central A"),
        ]

        # Real StorageManager for possible disk logic
        self.storage_manager = StorageManager(foundry_client=self.foundry_client)
        test_disk_id = str(uuid.uuid4())
        self.disk_attachment = DiskAttachment(
            disk_id=test_disk_id,
            name="test-disk",
            volume_name="test-volume",
            disk_interface="Block",
            region_id=test_region_id,
            size=100,
            size_unit="gb",
        )
        self.storage_manager.handle_persistent_storage = Mock(
            return_value=self.disk_attachment
        )

        # Patch the StorageManager again (this time with real logic if needed)
        patcher = patch(
            "src.flow.managers.task_manager.StorageManager",
            return_value=self.storage_manager,
        )
        self.addCleanup(patcher.stop)
        self.mock_storage_manager = patcher.start()

    def test_extract_and_prepare_data_valid(self):
        """Test that _extract_and_prepare_data returns correct values with valid config."""
        (task_name, resources_spec, limit_price_cents, ports) = (
            self.task_manager._extract_and_prepare_data(self.config)
        )

        self.assertEqual(task_name, "test-task")
        self.assertEqual(resources_spec, self.config.resources_specification)
        expected_limit_price_cents = int(
            settings.PRIORITY_PRICE_MAPPING["standard"] * 100
        )
        self.assertEqual(limit_price_cents, expected_limit_price_cents)
        self.assertEqual(ports, [80, 443])

    def test_extract_and_prepare_data_missing_task_name(self):
        """Test that ValueError is raised when task name is missing."""
        self.config.name = ""
        with self.assertRaises(ValueError) as context:
            self.task_manager._extract_and_prepare_data(self.config)
        self.assertIn("Task name is required.", str(context.exception))

    def test_extract_and_prepare_data_invalid_priority(self):
        """Test that ValueError is raised when priority is invalid."""
        self.config.task_management.priority = "invalid"
        with self.assertRaises(ValueError) as context:
            self.task_manager._extract_and_prepare_data(self.config)
        self.assertIn("Invalid priority level", str(context.exception))

    def test_prepare_limit_price_cents_with_valid_priority(self):
        """Test limit price calculation with a valid priority."""
        limit_price = self.task_manager.prepare_limit_price_cents(
            priority="high", utility_threshold_price=None
        )
        self.assertEqual(
            limit_price, int(settings.PRIORITY_PRICE_MAPPING["high"] * 100)
        )

    def test_prepare_limit_price_cents_with_invalid_priority(self):
        """Test limit price calculation with an invalid priority."""
        with self.assertRaises(ValueError) as context:
            self.task_manager.prepare_limit_price_cents(
                priority="invalid", utility_threshold_price=None
            )
        self.assertIn("Invalid or unsupported priority level", str(context.exception))

    def test_prepare_limit_price_cents_with_utility_threshold(self):
        """Test limit price calculation with a utility threshold price."""
        limit_price = self.task_manager.prepare_limit_price_cents(
            priority="standard", utility_threshold_price=1.23
        )
        self.assertEqual(limit_price, 123)

    def test_prepare_limit_price_cents_with_invalid_utility_threshold(self):
        """Test limit price calculation with an invalid utility threshold price."""
        with self.assertRaises(ValueError) as context:
            self.task_manager.prepare_limit_price_cents(
                priority="standard", utility_threshold_price="invalid"
            )
        self.assertIn("Invalid utility_threshold_price value", str(context.exception))

    def test_select_project_id_project_exists(self):
        """Test selecting a project ID when the project exists."""
        projects = [Project(id="proj-123", name="TestProject", created_ts=None)]
        project_id = self.task_manager.select_project_id(projects, "TestProject")
        self.assertEqual(project_id, "proj-123")

    def test_select_project_id_project_not_found(self):
        """Test selecting a project ID when the project does not exist."""
        projects = [Project(id="proj-123", name="OtherProject", created_ts=None)]
        with self.assertRaises(Exception) as context:
            self.task_manager.select_project_id(projects, "TestProject")
        self.assertIn("Project 'TestProject' not found.", str(context.exception))

    def test_select_ssh_key_id_key_exists(self):
        """Test selecting an SSH key ID when the key exists."""
        ssh_keys = [SshKey(id="key-123", name="OtherKey")]
        ssh_key_id = self.task_manager.select_ssh_key_id(ssh_keys, "OtherKey")
        self.assertEqual(ssh_key_id, "key-123")

    def test_select_ssh_key_id_key_not_found(self):
        """Test selecting an SSH key ID when the key does not exist."""
        ssh_keys = [SshKey(id="key-123", name="MySSHKey")]
        with self.assertRaises(Exception) as context:
            self.task_manager.select_ssh_key_id(ssh_keys, "OtherKey")
        self.assertIn("SSH key 'OtherKey' not found.", str(context.exception))

    @patch("src.flow.clients.foundry_client.FoundryClient.get_user")
    @patch("src.flow.clients.foundry_client.FoundryClient.get_projects")
    @patch("src.flow.clients.foundry_client.FoundryClient.get_ssh_keys")
    def test_authenticate_and_get_user_data_success(
        self,
        mock_get_ssh_keys,
        mock_get_projects,
        mock_get_user,
    ):
        """Test successful authentication and retrieval of user data."""
        mock_get_user.return_value = User(
            id="user-123",
            name="FakeUser",
            email="fake@example.com",
        )
        mock_get_projects.return_value = [
            Project(id="proj-123", name=settings.foundry_project_name, created_ts=None)
        ]
        mock_get_ssh_keys.return_value = [
            SshKey(id="key-123", name=settings.foundry_ssh_key_name)
        ]

        user_id, project_id, ssh_key_id = (
            self.task_manager._authenticate_and_get_user_data()
        )

        self.assertEqual(user_id, "user-123")
        self.assertEqual(project_id, "proj-123")
        self.assertEqual(ssh_key_id, "key-123")

    @patch("src.flow.clients.foundry_client.FoundryClient.get_user")
    def test_authenticate_and_get_user_data_authentication_failure(self, mock_get_user):
        """Test authentication failure."""
        mock_get_user.side_effect = Exception("Authentication failed.")
        self.task_manager.foundry_client.get_user = mock_get_user

        with self.assertRaises(AuthenticationError) as context:
            self.task_manager._authenticate_and_get_user_data()
        self.assertIn("Authentication failed.", str(context.exception))

    def test_find_matching_auctions_success(self):
        """Test finding matching auctions with valid resource specifications."""
        mock_auction = Auction(
            cluster_id="auction-123",
            instance_type_id="type-123",
            fcp_instance="a100.2x.SXM4.IB",
            gpu_type="a100",
            inventory_quantity=1,
            num_gpus=1,
            intranode_interconnect="SXM4",
            internode_interconnect="IB_1600",
            region_id="eu-central1-a",
        )
        self.foundry_client.get_auctions.return_value = [mock_auction]
        
        # Replace the entire resources specification instead of using update()
        self.config.resources_specification = ResourcesSpecification(
            fcp_instance="a100.2x.SXM4.IB",
            gpu_type="a100",
            intranode_interconnect="SXM4",
            internode_interconnect="IB_1600",
            region="eu-central1-a",
            num_instances=1,  # Maintain required fields
            num_gpus=1        # From original config
        )

        matching_auctions = self.task_manager._find_matching_auctions(
            project_id="proj-123",
            resources_specification=self.config.resources_specification,
        )
        self.assertEqual(len(matching_auctions), 1)
        self.assertEqual(matching_auctions[0].cluster_id, "auction-123")

    def test_prepare_and_submit_bid_success(self):
        """Test successful bid preparation and submission."""
        self.task_manager.config_parser.get_persistent_storage = lambda: None

        matching_auctions = [
            Auction(
                cluster_id="cluster-123",
                instance_type_id="type-123",
                region_id="us-central1-a",
            )
        ]
        self.foundry_client.place_bid.return_value = Bid(
            id="bid-123",
            name="test-task",
            status="active",
        )
        self.task_manager._prepare_and_submit_bid(
            matching_auctions=matching_auctions,
            resources_specification=self.config.resources_specification,
            limit_price_cents=100,
            task_name="test-task",
            project_id="proj-123",
            ssh_key_id="key-123",
            startup_script='echo "Hello World"',
            user_id="user-123",
            disk_attachments=[],
        )

        self.foundry_client.place_bid.assert_called_once()
        _, call_kwargs = self.foundry_client.place_bid.call_args
        self.assertEqual(call_kwargs["project_id"], "proj-123")

        raw_payload = call_kwargs["bid_payload"]
        if hasattr(raw_payload, "model_dump"):
            raw_payload = raw_payload.model_dump()

        actual_payload = BidPayload.model_validate(raw_payload)
        self.assertIsInstance(actual_payload, BidPayload)
        self.assertEqual(
            actual_payload.model_dump(),
            {
                "cluster_id": "cluster-123",
                "instance_quantity": 1,
                "instance_type_id": "type-123",
                "limit_price_cents": 100,
                "order_name": "test-task",
                "project_id": "proj-123",
                "ssh_key_ids": ["key-123"],
                "startup_script": 'echo "Hello World"',
                "user_id": "user-123",
                "disk_attachments": [],
            },
        )

    @patch.object(
        BidManager, "submit_bid", side_effect=Exception("Mocked submit error")
    )
    def test_prepare_and_submit_bid_failure(self, mock_submit_bid):
        """Test bid submission failure."""
        self.task_manager.config_parser.get_persistent_storage = lambda: None
        matching_auctions = [
            Auction(
                cluster_id="cluster-123",
                instance_type_id="type-123",
                region_id="us-central1-a",
            )
        ]
        with self.assertRaises(BidSubmissionError):
            self.task_manager._prepare_and_submit_bid(
                matching_auctions=matching_auctions,
                resources_specification=self.config.resources_specification,
                limit_price_cents=100,
                task_name="test-task",
                project_id="proj-123",
                ssh_key_id="key-123",
                startup_script='echo "Hello World"',
                user_id="user-123",
                disk_attachments=[],
            )

    def test_cancel_bid_success(self):
        """Test successful bid cancellation."""
        self.bid_manager.cancel_bid = Mock()
        self.foundry_client.get_bids.return_value = [
            Bid(id="bid-123", name="test-task", status="active")
        ]
        self.task_manager.cancel_bid(name="test-task")
        self.bid_manager.cancel_bid.assert_called_once_with(
            project_id="proj-123", bid_id="bid-123"
        )

    def test_cancel_bid_not_found(self):
        """Test bid cancellation when the bid is not found."""
        self.task_manager._authenticate_and_get_user_data = Mock(
            return_value=("user-123", "proj-123", "key-123")
        )
        self.foundry_client.get_bids.return_value = [
            Bid(id="bid-123", name="other-bid", status="active")
        ]

        with self.assertRaises(Exception) as context:
            self.task_manager.cancel_bid("nonexistent-bid")
        self.assertIn(
            "Bid with name 'nonexistent-bid' not found.", str(context.exception)
        )

    def test_check_status_success(self):
        """Test checking bid/instance status without errors."""
        self.task_manager._authenticate_and_get_user_data = Mock(
            return_value=("user-123", "proj-123", "key-123")
        )
        mock_bids = [Bid(id="abc-123", name="test-bid", status="active")]
        mock_instances_response = {
            "spot": [
                SpotInstance(
                    instance_id="inst-123",
                    name="test-instance",
                    instance_status="running",
                )
            ],
            "reserved": [],
            "legacy": [],
            "blocks": [],
            "control": [],
        }
        self.foundry_client.get_bids.return_value = mock_bids
        self.foundry_client.get_instances.return_value = mock_instances_response

        # Suppress console output.
        with patch("builtins.print"):
            self.task_manager.check_status()

    def test_find_matching_auctions_with_fcp_instance(self):
        """Integration-like test ensuring that FlowTaskManager filters auctions by fcp_instance."""
        self.config.resources_specification.fcp_instance = "a40.1x.PCIe.ICI"
        self.config.resources_specification.gpu_type = "a40"
        self.config.resources_specification.internode_interconnect = "100G_DCN"

        auctions = [
            Auction(
                cluster_id="clust-001",
                instance_type_id="type-abc",
                fcp_instance="a40.1x.PCIe.ICI",
                gpu_type="a40",
                inventory_quantity=1,
                num_gpus=1,
                intranode_interconnect="PCIe",
                internode_interconnect="100G_DCN",
                region_id="us-central1-a",
            ),
            Auction(
                cluster_id="clust-002",
                instance_type_id="type-def",
                fcp_instance="a40.8x.PCIe.ICI",
                gpu_type="a40",
                inventory_quantity=8,
                num_gpus=8,
                intranode_interconnect="PCIe", 
                internode_interconnect="100G_DCN",
                region_id="us-central1-a",
            ),
        ]
        self.foundry_client.get_auctions.return_value = auctions

        matching_auctions = self.task_manager._find_matching_auctions(
            project_id="proj-123",
            resources_specification=self.config.resources_specification,
        )

        self.assertEqual(len(matching_auctions), 1)
        self.assertEqual(matching_auctions[0].fcp_instance, "a40.1x.PCIe.ICI")

    @patch.object(FlowTaskManager, "_extract_and_prepare_data")
    @patch.object(FlowTaskManager, "_authenticate_and_get_user_data")
    @patch.object(FlowTaskManager, "_find_matching_auctions")
    @patch.object(FlowTaskManager, "_prepare_and_submit_bid")
    def test_run_success(
        self,
        mock_prepare_and_submit_bid,
        mock_find_matching_auctions,
        mock_authenticate_and_get_user_data,
        mock_extract_and_prepare_data,
    ):
        """Test successful run method execution."""
        mock_extract_and_prepare_data.return_value = (
            "test-task",
            self.config.resources_specification,
            100,
            [
                Port(external=80, internal=80),
                Port(external=443, internal=443),
            ],
        )
        mock_authenticate_and_get_user_data.return_value = (
            "user-123",
            "proj-123",
            "key-123",
        )
        mock_find_matching_auctions.return_value = [{"id": "auction-123"}]

        self.task_manager.run()

        mock_extract_and_prepare_data.assert_called_once()
        mock_authenticate_and_get_user_data.assert_called_once()
        mock_find_matching_auctions.assert_called_once()
        mock_prepare_and_submit_bid.assert_called_once()

    def test_run_no_matching_auctions(self):
        """Test run method when no matching auctions are found."""
        self.task_manager._extract_and_prepare_data = Mock(
            return_value=(
                "test-task",
                self.config.resources_specification,
                100,
                [
                    Port(external=80, internal=80),
                    Port(external=443, internal=443),
                ],
            )
        )
        self.task_manager._authenticate_and_get_user_data = Mock(
            return_value=("user-123", "proj-123", "key-123")
        )
        self.task_manager._find_matching_auctions = Mock(
            side_effect=NoMatchingAuctionsError("No matching auctions")
        )
        with self.assertRaises(NoMatchingAuctionsError) as context:
            self.task_manager.run()
        self.assertIn("No matching auctions", str(context.exception))

    def test_find_matching_auctions_fcp_instance(self):
        """Integration-like test ensuring that FlowTaskManager filters auctions by fcp_instance."""
        self.config.resources_specification.fcp_instance = "a40.1x.PCIe.ICI"
        self.config.resources_specification.gpu_type = "a40"
        self.config.resources_specification.internode_interconnect = "100G_DCN"

        auctions = [
            Auction(
                cluster_id="clust-001",
                instance_type_id="type-abc",
                fcp_instance="a40.1x.PCIe.ICI",
                gpu_type="a40",
                inventory_quantity=1,
                num_gpus=1,
                intranode_interconnect="PCIe",
                internode_interconnect="100G_DCN",
                region_id="us-central1-a",
            ),
            Auction(
                cluster_id="clust-002",
                instance_type_id="type-def",
                fcp_instance="a40.8x.PCIe.ICI",
                gpu_type="a40",
                inventory_quantity=8,
                num_gpus=8,
                intranode_interconnect="PCIe", 
                internode_interconnect="100G_DCN",
                region_id="us-central1-a",
            ),
        ]
        self.foundry_client.get_auctions.return_value = auctions

        matching_auctions = self.task_manager._find_matching_auctions(
            project_id="proj-123",
            resources_specification=self.config.resources_specification,
        )

        self.assertEqual(len(matching_auctions), 1)
        self.assertEqual(matching_auctions[0].fcp_instance, "a40.1x.PCIe.ICI")
