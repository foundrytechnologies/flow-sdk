import os
import sys
import unittest
from unittest.mock import MagicMock, patch

import requests
from requests import Response  # Added for specifying the spec of the fake response

# Add 'src' to sys.path to import modules from 'flow'
sys.path.insert(
    0, os.path.abspath(os.path.join(os.path.dirname(__file__), "../../src"))
)

from flow.clients.authenticator import Authenticator
from flow.clients.fcp_client import FCPClient
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
from flow.utils.exceptions import APIError, AuthenticationError


class TestFCPClient(unittest.TestCase):
    """Tests for the FCPClient class, which uses Pydantic models for inputs and outputs."""

    def setUp(self) -> None:
        """Sets up mocks for requests.Session and Authenticator."""
        # Patch the Session used in the HTTPClient (now in flow.clients.http_client)
        patcher_session = patch("flow.clients.http_client.requests.Session")
        self.mock_session_class = patcher_session.start()
        self.addCleanup(patcher_session.stop)
        self.mock_session_instance = self.mock_session_class.return_value

        # Create a default fake response that returns valid JSON for a user.
        self.mock_response = MagicMock(spec=Response)
        self.mock_response.status_code = 200
        # Return a dict that is valid for the User model.
        self.mock_response.json.return_value = {"id": "123", "name": "Test User"}
        self.mock_session_instance.request.return_value = self.mock_response

        # Patch the Authenticator to return a fake token.
        with patch.object(Authenticator, "authenticate", return_value="fake_token"):
            self.mock_authenticator_instance = Authenticator(
                email="test@example.com", password="password"
            )
            self.mock_authenticator_instance.get_access_token = MagicMock(
                return_value="fake_token"
            )

        # Mock a successful get_user call during __init__ so the client sets user_id.
        self.user_id = "123"
        user_data = User(id=self.user_id, name="Test User")
        with patch.object(FCPClient, "get_user", return_value=user_data):
            self.client = FCPClient(authenticator=self.mock_authenticator_instance)

    def test_authentication_failure_no_token(self) -> None:
        """Tests raising AuthenticationError if token is None."""
        self.mock_authenticator_instance.get_access_token.return_value = None
        with self.assertRaises(AuthenticationError) as context:
            FCPClient(authenticator=self.mock_authenticator_instance)
        self.assertIn(
            "Authentication failed: No token received", str(context.exception)
        )

    def test_authentication_failure_invalid_credentials(self) -> None:
        """Tests raising AuthenticationError if authentication fails."""
        self.mock_authenticator_instance.get_access_token.side_effect = Exception(
            "Invalid credentials"
        )
        with self.assertRaises(AuthenticationError) as context:
            FCPClient(authenticator=self.mock_authenticator_instance)
        self.assertIn("Authentication failed", str(context.exception))

    def test_get_profile(self) -> None:
        """Tests that get_profile returns a valid User instance."""
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {"id": "1234", "name": "Test User"}
        self.mock_session_instance.request.return_value = mock_response

        profile = self.client.get_profile()
        self.assertIsInstance(profile, User)
        self.assertEqual(profile.id, "1234")
        self.assertEqual(profile.name, "Test User")

    def test_get_user_success(self) -> None:
        """Tests retrieving user information as a User model."""
        expected_user = User(id="123", name="Test User")
        mock_response = MagicMock()
        mock_response.ok = True
        mock_response.status_code = 200
        mock_response.json.return_value = expected_user.model_dump()
        self.mock_session_instance.request.return_value = mock_response

        user = self.client.get_user()
        self.assertIsInstance(user, User)
        self.assertEqual(user.id, "123")
        self.assertEqual(self.mock_session_instance.request.call_count, 1)

    def test_get_user_api_error(self) -> None:
        """Tests APIError handling on user retrieval failure."""
        mock_response = MagicMock()
        mock_response.ok = False
        mock_response.status_code = 500
        mock_response.text = "Internal Server Error"
        mock_response.raise_for_status.side_effect = requests.HTTPError(
            "Internal Server Error"
        )
        self.mock_session_instance.request.return_value = mock_response

        with self.assertRaises(APIError) as context:
            self.client.get_user()
        self.assertIn(
            "API request failed [500]: Internal Server Error", str(context.exception)
        )

    def test_get_projects_success(self) -> None:
        """Tests that get_projects returns a list of Project models."""
        mock_projects = [Project(id="proj1", name="Test Project")]
        mock_response = MagicMock()
        mock_response.ok = True
        mock_response.status_code = 200
        mock_response.json.return_value = [p.model_dump() for p in mock_projects]
        self.mock_session_instance.request.return_value = mock_response

        projects = self.client.get_projects()
        self.assertIsInstance(projects, list)
        self.assertTrue(all(isinstance(p, Project) for p in projects))
        self.assertEqual(projects[0].id, "proj1")

    def test_place_bid_success(self) -> None:
        """Tests placing a bid with BidPayload and receiving a BidResponse."""
        project_id = "proj1"
        bid_payload = BidPayload(
            cluster_id="cluster1",
            instance_quantity=1,
            instance_type_id="t1",
            limit_price_cents=2000,
            order_name="Test Order",
            project_id=project_id,
            ssh_key_ids=["ssh1"],
            user_id="12345",
        )
        expected_bid = BidResponse(
            id="bid1",
            name="Test Order",
            cluster_id="cluster1",
            instance_quantity=1,
            instance_type_id="t1",
            limit_price_cents=2000,
            project_id=project_id,
            user_id="12345",
        )
        mock_response = MagicMock()
        mock_response.ok = True
        mock_response.status_code = 200
        mock_response.json.return_value = expected_bid.model_dump()
        self.mock_session_instance.request.return_value = mock_response

        response = self.client.place_bid(bid_payload)
        self.assertIsInstance(response, BidResponse)
        self.assertEqual(response.id, "bid1")
        self.assertEqual(response.name, "Test Order")

    def test_place_bid_api_error(self) -> None:
        """Tests APIError handling when place_bid fails."""
        project_id = "proj1"
        bid_payload = BidPayload(
            cluster_id="cluster1",
            instance_quantity=1,
            instance_type_id="t1",
            limit_price_cents=2000,
            order_name="ErrorTest",
            project_id=project_id,
            ssh_key_ids=["ssh1"],
            user_id="12345",
        )
        mock_response = MagicMock()
        mock_response.ok = False
        mock_response.status_code = 400
        mock_response.text = "Bad Request"
        mock_response.json.return_value = {"error": "Bad Request"}
        mock_response.raise_for_status.side_effect = requests.HTTPError("Bad Request")
        self.mock_session_instance.request.return_value = mock_response

        with self.assertRaises(APIError) as context:
            self.client.place_bid(bid_payload)
        self.assertIn("API request failed [400]: Bad Request", str(context.exception))

    def test_cancel_bid_success(self) -> None:
        """Tests successful cancellation of a bid."""
        project_id = "proj1"
        bid_id = "bid1"
        mock_response = MagicMock()
        mock_response.ok = True
        mock_response.status_code = 200
        mock_response.json.return_value = {}
        self.mock_session_instance.request.return_value = mock_response

        self.client.cancel_bid(project_id, bid_id)
        self.assertEqual(self.mock_session_instance.request.call_count, 1)

    def test_cancel_bid_api_error(self) -> None:
        """Tests APIError handling when cancelling a bid fails."""
        project_id = "proj1"
        bid_id = "bid1"
        mock_response = MagicMock()
        mock_response.ok = False
        mock_response.status_code = 404
        mock_response.text = "Not Found"
        mock_response.raise_for_status.side_effect = requests.HTTPError("Not Found")
        self.mock_session_instance.request.return_value = mock_response

        with self.assertRaises(APIError) as context:
            self.client.cancel_bid(project_id, bid_id)
        self.assertIn("API request failed [404]: Not Found", str(context.exception))

    def test_get_instances_success(self) -> None:
        """Tests returning a dict of category -> list of Instance objects."""
        project_id = "proj1"
        mock_data = {
            "spot": [
                {
                    "instance_id": "inst-123",
                    "instance_status": "running",
                    "instance_type_id": "type-1",
                }
            ],
            "reserved": [],
        }
        mock_response = MagicMock()
        mock_response.ok = True
        mock_response.status_code = 200
        mock_response.json.return_value = mock_data
        self.mock_session_instance.request.return_value = mock_response

        instances_dict = self.client.get_instances(project_id)
        self.assertIsInstance(instances_dict, dict)
        self.assertIn("spot", instances_dict)
        self.assertEqual(len(instances_dict["spot"]), 1)
        self.assertIsInstance(instances_dict["spot"][0], Instance)
        self.assertEqual(instances_dict["spot"][0].instance_id, "inst-123")
        self.assertEqual(instances_dict["spot"][0].instance_status, "running")
        self.assertEqual(self.mock_session_instance.request.call_count, 1)

    def test_get_auctions_success(self) -> None:
        """Tests that get_auctions returns a list of Auction models."""
        project_id = "proj1"
        mock_auctions = [
            Auction(
                id="auction1",
                cluster_id="cluster1",
                gpu_type="A100",
                instance_type_id="a100.xlarge",
                inventory_quantity=10,
                last_price=1200.0,
                num_gpu=1,
                region="us-west1",
                resource_specification_id="spec123",
            )
        ]
        mock_response = MagicMock()
        mock_response.ok = True
        mock_response.status_code = 200
        mock_response.json.return_value = [a.model_dump() for a in mock_auctions]
        self.mock_session_instance.request.return_value = mock_response

        auctions = self.client.get_auctions(project_id)
        self.assertIsInstance(auctions, list)
        self.assertIsInstance(auctions[0], Auction)
        self.assertEqual(auctions[0].id, "auction1")
        self.assertEqual(auctions[0].last_price, 1200.0)

    def test_get_ssh_keys_success(self) -> None:
        """Tests that get_ssh_keys returns a list of SSHKey models."""
        project_id = "proj1"
        mock_ssh_keys = [SshKey(id="key1", name="my_ssh_key")]
        mock_response = MagicMock()
        mock_response.ok = True
        mock_response.status_code = 200
        mock_response.json.return_value = [key.model_dump() for key in mock_ssh_keys]
        self.mock_session_instance.request.return_value = mock_response

        ssh_keys = self.client.get_ssh_keys(project_id)
        self.assertIsInstance(ssh_keys, list)
        self.assertIsInstance(ssh_keys[0], SshKey)
        self.assertEqual(ssh_keys[0].id, "key1")
        self.assertEqual(ssh_keys[0].name, "my_ssh_key")

    def test_request_authentication_error(self) -> None:
        """Tests handling of a 401 response during requests."""
        mock_response = MagicMock()
        mock_response.status_code = 401
        mock_response.ok = False
        mock_response.text = "Unauthorized"
        mock_response.raise_for_status.side_effect = requests.HTTPError("Unauthorized")
        self.mock_session_instance.request.return_value = mock_response

        with self.assertRaises(AuthenticationError) as context:
            self.client.get_user()
        self.assertIn("unauthorized", str(context.exception).lower())

    def test_request_api_error(self) -> None:
        """Tests handling of a general API error (e.g., 500)."""
        mock_response = MagicMock()
        mock_response.status_code = 500
        mock_response.ok = False
        mock_response.text = "Internal Server Error"
        mock_response.raise_for_status.side_effect = requests.HTTPError(
            "Internal Server Error"
        )
        self.mock_session_instance.request.return_value = mock_response

        with self.assertRaises(APIError) as context:
            self.client.get_user()
        self.assertIn(
            "API request failed [500]: Internal Server Error", str(context.exception)
        )

    def test_request_exception(self) -> None:
        """Tests handling of RequestException during an API call."""
        self.mock_session_instance.request.side_effect = requests.RequestException(
            "Connection error"
        )
        with self.assertRaises(APIError) as context:
            self.client.get_user()
        self.assertIn("Request failed: Connection error", str(context.exception))

    def test_get_bids_success(self) -> None:
        """Tests that get_bids returns a list of Bid models."""
        project_id = "proj1"
        mock_bids = [Bid(id="bid1", name="test_order")]
        mock_response = MagicMock()
        mock_response.ok = True
        mock_response.status_code = 200
        mock_response.json.return_value = [b.model_dump() for b in mock_bids]
        self.mock_session_instance.request.return_value = mock_response

        bids = self.client.get_bids(project_id)
        self.assertIsInstance(bids, list)
        self.assertIsInstance(bids[0], Bid)
        self.assertEqual(bids[0].id, "bid1")
        self.assertEqual(bids[0].name, "test_order")

    def test_get_bids_api_error(self) -> None:
        """Tests APIError handling when retrieving bids fails."""
        project_id = "proj1"
        mock_response = MagicMock()
        mock_response.ok = False
        mock_response.status_code = 500
        mock_response.text = "Internal Server Error"
        mock_response.raise_for_status.side_effect = requests.HTTPError(
            "Internal Server Error"
        )
        self.mock_session_instance.request.return_value = mock_response

        with self.assertRaises(APIError) as context:
            self.client.get_bids(project_id)
        self.assertIn(
            "API request failed [500]: Internal Server Error", str(context.exception)
        )


if __name__ == "__main__":
    unittest.main()
