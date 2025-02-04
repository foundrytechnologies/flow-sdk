import unittest
import pytest

from typing import Any, Dict, List
from unittest.mock import MagicMock

from pydantic import ValidationError

from flow.clients.foundry_client import FoundryClient
from flow.config import get_config
from flow.managers.bid_manager import BidManager
from flow.models import (
    Bid,
    BidPayload,
    BidDiskAttachment,
    DiskAttachment,
)

settings = get_config()

pytestmark = pytest.mark.skipif(
    not all(
        [
            settings.foundry_email,
            bool(settings.foundry_password.get_secret_value().strip()),
            settings.PROJECT_NAME,
            settings.SSH_KEY_NAME,
        ]
    ),
    reason="Skipping BidManager tests due to missing required environment variables.",
)


class TestBidManager(unittest.TestCase):
    """Unit tests for the BidManager class."""

    def setUp(self) -> None:
        """Set up the test case with a mock FoundryClient and sample data."""
        self.mock_foundry_client: FoundryClient = MagicMock(spec=FoundryClient)
        self.bid_manager: BidManager = BidManager(
            foundry_client=self.mock_foundry_client
        )
        self.valid_kwargs: Dict[str, Any] = {
            "cluster_id": "cluster123",
            "instance_quantity": 2,
            "instance_type_id": "instance_type_abc",
            "limit_price_cents": 5000,
            "order_name": "test_order",
            "project_id": "projectXYZ",
            "ssh_key_id": "sshkey789",
            "startup_script": "#!/bin/bash\necho Hello World",
            "user_id": "user456",
        }
        self.project_id: str = self.valid_kwargs["project_id"]

        bid_kwargs = self.valid_kwargs.copy()
        bid_kwargs["ssh_key_ids"] = [bid_kwargs.pop("ssh_key_id")]
        self.bid_payload: BidPayload = BidPayload(**bid_kwargs)

    def test_prepare_bid_payload_success(self) -> None:
        """Tests prepare_bid_payload with valid arguments."""
        payload: BidPayload = self.bid_manager.prepare_bid_payload(**self.valid_kwargs)
        expected_payload: BidPayload = self.bid_payload
        self.assertEqual(payload.model_dump(), expected_payload.model_dump())

    def test_prepare_bid_payload_missing_fields(self) -> None:
        """Tests prepare_bid_payload raises TypeError when required fields are missing."""
        incomplete_kwargs: Dict[str, Any] = self.valid_kwargs.copy()
        incomplete_kwargs.pop("cluster_id")
        with self.assertRaises(TypeError):
            self.bid_manager.prepare_bid_payload(**incomplete_kwargs)

    def test_prepare_bid_payload_extra_fields(self) -> None:
        """Tests prepare_bid_payload raises TypeError when extra fields are provided."""
        extra_kwargs: Dict[str, Any] = self.valid_kwargs.copy()
        extra_kwargs["extra_field"] = "extra_value"
        with self.assertRaises(TypeError):
            self.bid_manager.prepare_bid_payload(**extra_kwargs)

    def test_prepare_bid_payload_invalid_types(self) -> None:
        """Tests prepare_bid_payload raises ValidationError with invalid argument types."""
        invalid_kwargs: Dict[str, Any] = self.valid_kwargs.copy()
        invalid_kwargs["instance_quantity"] = "two"
        with self.assertRaises(ValidationError) as context:
            self.bid_manager.prepare_bid_payload(**invalid_kwargs)
        self.assertIn("Input should be a valid integer", str(context.exception))

    def test_submit_bid_success(self) -> None:
        """Tests successfully submitting a bid."""
        response_data = {
            "id": "bid123",
            "name": "test_order",
            "status": "submitted",
            "created_at": "2023-10-01T12:00:00Z",
            "deactivated_at": None,
            "limit_price_cents": 5000,
            "instance_quantity": 2,
            "instance_type_id": "instance_type_abc",
            "cluster_id": "cluster123",
            "project_id": "projectXYZ",
            "ssh_key_ids": ["sshkey789"],
            "startup_script": "#!/bin/bash\necho Hello World",
            "user_id": "user456",
        }
        self.mock_foundry_client.place_bid.return_value = [Bid(**response_data)]
        bid_response_list: List[Bid] = self.bid_manager.submit_bid(
            project_id=self.project_id, bid_payload=self.bid_payload
        )
        self.mock_foundry_client.place_bid.assert_called_once_with(
            project_id=self.project_id,
            bid_payload=self.bid_payload,
        )
        expected_bid = Bid(**response_data)
        self.assertEqual(bid_response_list, [expected_bid])

    def test_submit_bid_api_failure(self) -> None:
        """Tests that an exception is raised for API failures."""
        self.mock_foundry_client.place_bid.side_effect = Exception("API error")
        with self.assertRaises(Exception) as context:
            self.bid_manager.submit_bid(
                project_id=self.project_id, bid_payload=self.bid_payload
            )
        self.assertIn("API error", str(context.exception))

    def test_submit_bid_invalid_bid_payload(self) -> None:
        """Tests that ValueError is raised with an invalid bid payload."""
        self.mock_foundry_client.place_bid.side_effect = ValueError(
            "Invalid bid payload"
        )
        with self.assertRaises(ValueError) as context:
            self.bid_manager.submit_bid(
                project_id=self.project_id, bid_payload=self.bid_payload
            )
        self.assertIn("Invalid bid payload", str(context.exception))

    def test_submit_bid_exception_handling(self) -> None:
        """Tests that unexpected exceptions are handled properly."""
        self.mock_foundry_client.place_bid.side_effect = RuntimeError(
            "Unexpected error"
        )
        with self.assertRaises(RuntimeError) as context:
            self.bid_manager.submit_bid(
                project_id=self.project_id, bid_payload=self.bid_payload
            )
        self.assertIn("Unexpected error", str(context.exception))

    def test_get_bids_success(self) -> None:
        """Tests retrieving bids successfully."""
        project_id: str = self.project_id
        expected_bids: List[Bid] = [Bid(id="bid123", status="active")]
        self.mock_foundry_client.get_bids.return_value = expected_bids
        bids: List[Bid] = self.bid_manager.get_bids(project_id=project_id)
        self.mock_foundry_client.get_bids.assert_called_once_with(project_id=project_id)
        self.assertEqual(bids, expected_bids)

    def test_cancel_bid_success(self) -> None:
        """Tests successfully canceling a bid."""
        project_id: str = self.project_id
        bid_id: str = "bid123"
        self.bid_manager.cancel_bid(project_id=project_id, bid_id=bid_id)
        self.mock_foundry_client.cancel_bid.assert_called_once_with(
            project_id=project_id,
            bid_id=bid_id,
        )

    def test_prepare_bid_payload_with_disk_attachments(self) -> None:
        """Tests that prepare_bid_payload includes disk attachments correctly."""
        disk_attachment = DiskAttachment(
            disk_id="disk-id",
            name="disk-name",
            volume_name="disk-volume-name",
            disk_interface="Block",
            region_id="region-id",
            size=10,
            size_unit="gb",
        )
        BidDiskAttachment.from_disk_attachment(disk_attachment)
        bid_payload = self.bid_manager.prepare_bid_payload(
            cluster_id="cluster-id",
            instance_quantity=1,
            instance_type_id="instance-type-id",
            limit_price_cents=100,
            order_name="order-name",
            project_id="project-id",
            ssh_key_id="ssh-key-id",
            user_id="user-id",
            disk_attachments=[disk_attachment],
        )
        self.assertIsNotNone(bid_payload.disk_attachments)
        self.assertEqual(len(bid_payload.disk_attachments), 1)
        self.assertEqual(bid_payload.disk_attachments[0].disk_id, "disk-id")
        self.assertEqual(
            bid_payload.disk_attachments[0].volume_name,
            "disk-volume-name",
        )

    def test_prepare_bid_payload_without_startup_script(self) -> None:
        """Tests prepare_bid_payload without providing startup_script."""
        valid_kwargs = self.valid_kwargs.copy()
        del valid_kwargs["startup_script"]
        payload: BidPayload = self.bid_manager.prepare_bid_payload(**valid_kwargs)
        expected_payload = self.bid_payload.model_copy(update={"startup_script": None})
        self.assertEqual(payload.model_dump(), expected_payload.model_dump())


if __name__ == "__main__":
    unittest.main()
