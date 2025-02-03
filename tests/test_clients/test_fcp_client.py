#!/usr/bin/env python3
"""Unit tests for the FCPClient class.

This suite includes:
  - Basic happy path tests for authentication, user retrieval, etc.
  - Edge/boundary tests for numeric fields in BidPayload.
  - Large payload simulations for endpoints like get_projects.
  - Tests for transient errors, retry logic, and concurrency scenarios.

All tests use mocks so that no real network calls are made.
"""

import os
import sys
import threading
import unittest
from typing import Any, Dict, List
from concurrent.futures import ThreadPoolExecutor

from unittest.mock import MagicMock, patch

import requests
from requests import Response

# Ensure 'src' is on sys.path so we can import from flow.*
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
from flow.utils.exceptions import (
    APIError,
    AuthenticationError,
    TimeoutError,
    NetworkError,
)


class TestFCPClient(unittest.TestCase):
    """Unit tests for the FCPClient class, focusing on correctness and robustness."""

    def setUp(self) -> None:
        """Set up mocks for Session and Authenticator."""
        self._session_patcher = patch(
            "flow.clients.http_client.requests.Session", autospec=True
        )
        self.mock_session_class = self._session_patcher.start()
        self.addCleanup(self._session_patcher.stop)
        self.mock_session_instance = self.mock_session_class.return_value
        self.mock_session_instance.headers = {}

        self.mock_response: MagicMock = MagicMock(spec=Response)
        self.mock_response.status_code = 200
        self.mock_response.json.return_value = {"id": "123", "name": "Test User"}
        self.mock_session_instance.request.return_value = self.mock_response

        # Patch the Authenticator so that it returns a fake token.
        with patch.object(
            Authenticator, "authenticate", autospec=True, return_value="fake_token"
        ):
            self.mock_authenticator = Authenticator(
                email="test@example.com", password="password"
            )
            self.mock_authenticator.get_access_token = MagicMock(
                return_value="fake_token"
            )

        self.user_id: str = "123"
        user_data: User = User(id=self.user_id, name="Test User")
        with patch(
            "flow.clients.fcp_client.UserService.get_user",
            autospec=True,
            return_value=user_data,
        ):
            self.client: FCPClient = FCPClient(authenticator=self.mock_authenticator)

    def tearDown(self) -> None:
        """Reset mocks after each test to avoid call count bleed."""
        self.mock_session_instance.request.reset_mock()

    # -------------------------------------------------------------------------
    # Authentication Tests
    # -------------------------------------------------------------------------
    def test_authentication_failure_no_token(self) -> None:
        """Test that an AuthenticationError is raised when no token is retrieved."""
        self.mock_authenticator.get_access_token.return_value = None
        with self.assertRaises(AuthenticationError) as ctx:
            FCPClient(authenticator=self.mock_authenticator)
        self.assertIn("Authentication failed: No token received", str(ctx.exception))

    def test_authentication_failure_invalid_credentials(self) -> None:
        """Test that an AuthenticationError is raised when authenticator fails."""
        self.mock_authenticator.get_access_token.side_effect = Exception("Bad creds")
        with self.assertRaises(AuthenticationError) as ctx:
            FCPClient(authenticator=self.mock_authenticator)
        self.assertIn("Authentication failed", str(ctx.exception))

    def test_request_authentication_error_401(self) -> None:
        """Test that a 401 response triggers an AuthenticationError."""
        error_response = MagicMock(spec=Response)
        error_response.status_code = 401
        error_response.ok = False
        error_response.text = "Unauthorized"
        error_response.raise_for_status.side_effect = requests.HTTPError("Unauthorized")
        error_response.headers = {"Content-Type": "application/json"}
        self.mock_session_instance.request.return_value = error_response

        with self.assertRaises(AuthenticationError):
            self.client.get_user()

    def test_request_authentication_error_403(self) -> None:
        """Test that a 403 response triggers an AuthenticationError."""
        error_response = MagicMock(spec=Response)
        error_response.status_code = 403
        error_response.ok = False
        error_response.text = "Forbidden"
        error_response.raise_for_status.side_effect = requests.HTTPError("Forbidden")
        error_response.headers = {"Content-Type": "application/json"}
        self.mock_session_instance.request.return_value = error_response

        with self.assertRaises(AuthenticationError):
            self.client.get_user()

    # -------------------------------------------------------------------------
    # Basic GET Tests
    # -------------------------------------------------------------------------
    def test_get_user_success(self) -> None:
        """Test that get_user returns a valid User."""
        expected_user = User(id="abc", name="MockedUser")
        mock_resp = MagicMock(spec=Response)
        mock_resp.status_code = 200
        mock_resp.json.return_value = expected_user.model_dump()
        self.mock_session_instance.request.return_value = mock_resp

        user_obj: User = self.client.get_user()
        self.assertIsInstance(user_obj, User)
        self.assertEqual(user_obj.id, "abc")

    def test_get_profile_success(self) -> None:
        """Test that get_profile returns a valid User."""
        mock_resp = MagicMock(spec=Response)
        mock_resp.status_code = 200
        mock_resp.json.return_value = {"id": "999", "name": "ProfileUser"}
        self.mock_session_instance.request.return_value = mock_resp

        profile: User = self.client.get_profile()
        self.assertIsInstance(profile, User)
        self.assertEqual(profile.id, "999")

    def test_get_projects_large_response(self) -> None:
        """Test handling of a large JSON response in get_projects."""
        large_projects = [
            {"id": f"proj{i}", "name": f"Project {i}"} for i in range(1000)
        ]
        mock_resp = MagicMock(spec=Response)
        mock_resp.status_code = 200
        mock_resp.json.return_value = large_projects
        self.mock_session_instance.request.return_value = mock_resp

        projects: List[Project] = self.client.get_projects()
        self.assertEqual(len(projects), 1000)
        self.assertIsInstance(projects[0], Project)

    def test_get_instances_empty_dict(self) -> None:
        """Test that get_instances gracefully handles an empty dictionary response."""
        empty_json: Dict[str, Any] = {}
        mock_resp = MagicMock(spec=Response)
        mock_resp.status_code = 200
        mock_resp.json.return_value = empty_json
        self.mock_session_instance.request.return_value = mock_resp

        instances_dict = self.client.get_instances("dummy_project")
        self.assertIsInstance(instances_dict, dict)
        self.assertEqual(len(instances_dict), 0)

    # -------------------------------------------------------------------------
    # Bid / Place Bid Tests
    # -------------------------------------------------------------------------
    def test_place_bid_boundary_values(self) -> None:
        """Test that placing a bid with limit_price_cents=0 triggers a validation error."""
        with self.assertRaises(ValueError):
            BidPayload(
                cluster_id="cluster1",
                instance_quantity=1,
                instance_type_id="t1",
                limit_price_cents=0,  # invalid: must be > 0
                order_name="BoundaryTest",
                project_id="proj1",
                ssh_key_ids=["ssh1"],
                user_id="u1",
            )

    def test_place_bid_large_values(self) -> None:
        """Test placing a bid with an extremely large limit_price_cents."""
        huge_bid_payload = BidPayload(
            cluster_id="cluster1",
            instance_quantity=999999,
            instance_type_id="type-huge",
            limit_price_cents=999999999,
            order_name="HugeBid",
            project_id="proj999",
            ssh_key_ids=["ssh1"],
            user_id="test-user",
        )
        mock_resp = MagicMock(spec=Response)
        mock_resp.status_code = 200
        mock_resp.json.return_value = {
            "id": "some-id",
            "name": "HugeBid",
            "cluster_id": "cluster1",
            "instance_quantity": 999999,
            "instance_type_id": "type-huge",
            "limit_price_cents": 999999999,
            "project_id": "proj999",
            "user_id": "test-user",
        }
        self.mock_session_instance.request.return_value = mock_resp

        response: BidResponse = self.client.place_bid(huge_bid_payload)
        self.assertIsInstance(response, BidResponse)
        self.assertEqual(response.limit_price_cents, 999999999)

    def test_place_bid_duplicate(self) -> None:
        """Test a duplicate bid scenario with a dummy response from error handler."""
        duplicate_resp = MagicMock(spec=Response)
        duplicate_resp.status_code = 400
        duplicate_resp.ok = False
        duplicate_resp.text = "Bid already exists"
        duplicate_resp.headers = {"Content-Type": "application/json"}
        duplicate_resp.json.return_value = {"error": "Bid already exists"}
        self.mock_session_instance.request.return_value = duplicate_resp

        bid_payload = BidPayload(
            cluster_id="cluster1",
            instance_quantity=1,
            instance_type_id="t1",
            limit_price_cents=2000,
            order_name="DupeBid",
            project_id="proj1",
            ssh_key_ids=["ssh1"],
            user_id="12345",
        )
        response: BidResponse = self.client.place_bid(bid_payload)
        self.assertIsInstance(response, BidResponse)
        self.assertEqual(response.name, "DupeBid")

    # -------------------------------------------------------------------------
    # Error and Retry Tests
    # -------------------------------------------------------------------------
    def test_retry_exhausted(self) -> None:
        """Simulate repeated 500 errors to ensure APIError is eventually raised."""

        def _mock_500(*args, **kwargs) -> MagicMock:
            resp = MagicMock(spec=Response)
            resp.status_code = 500
            resp.ok = False
            resp.text = "Internal Server Error"
            resp.raise_for_status.side_effect = requests.HTTPError(
                "Internal Server Error"
            )
            resp.headers = {"Content-Type": "application/json"}
            return resp

        self.mock_session_instance.request.side_effect = [
            _mock_500() for _ in range(10)
        ]
        with self.assertRaises(APIError) as ctx:
            self.client.get_user()
        self.assertIn("Internal Server Error", str(ctx.exception))

    def test_retry_eventual_success(self) -> None:
        """Simulate transient failures followed by a successful get_user call."""

        def _side_effect(*args, **kwargs) -> MagicMock:
            if self.mock_session_instance.request.call_count < 3:
                resp_fail = MagicMock(spec=Response)
                resp_fail.status_code = 500
                resp_fail.ok = False
                resp_fail.text = "Transient error"
                resp_fail.raise_for_status.side_effect = requests.HTTPError(
                    "Transient error"
                )
                resp_fail.headers = {"Content-Type": "application/json"}
                return resp_fail
            resp_ok = MagicMock(spec=Response)
            resp_ok.status_code = 200
            resp_ok.json.return_value = {"id": "retry_user", "name": "RetryUser"}
            resp_ok.headers = {"Content-Type": "application/json"}
            return resp_ok

        self.mock_session_instance.request.side_effect = _side_effect

        user_obj: User = self.client.get_user()
        self.assertEqual(user_obj.id, "retry_user")

    def test_timeout_error(self) -> None:
        """Simulate a request timeout to ensure TimeoutError is raised."""
        self.mock_session_instance.request.side_effect = requests.Timeout("Timed out")
        with self.assertRaises(TimeoutError) as ctx:
            self.client.get_user()
        self.assertIn("Request timed out", str(ctx.exception))

    def test_network_error(self) -> None:
        """Simulate a connection error to ensure NetworkError is raised."""
        self.mock_session_instance.request.side_effect = requests.ConnectionError(
            "Conn failed"
        )
        with self.assertRaises(NetworkError) as ctx:
            self.client.get_user()
        self.assertIn("Network error occurred", str(ctx.exception))

    # -------------------------------------------------------------------------
    # Concurrency Test
    # -------------------------------------------------------------------------
    def test_place_bids_in_parallel_executor(self) -> None:
        """Test placing bids concurrently using ThreadPoolExecutor."""
        num_threads: int = 5
        results: List[BidResponse] = []

        def _place_bid_task() -> BidResponse:
            """Task to place a bid and return the BidResponse."""
            payload: BidPayload = BidPayload(
                cluster_id="c",
                instance_quantity=1,
                instance_type_id="t1",
                limit_price_cents=1000,
                order_name="executor_parallel_test",
                project_id="proj",
                ssh_key_ids=["ssh1"],
                user_id="u1",
            )
            # Create a mocked response that simulates a successful bid placement.
            mock_resp: MagicMock = MagicMock(spec=Response)
            mock_resp.status_code = 200
            mock_resp.json.return_value = {
                "id": "bid_executor",
                "name": "executor_parallel_test",
                "cluster_id": "c",
                "instance_quantity": 1,
                "instance_type_id": "t1",
                "limit_price_cents": 1000,
            }
            self.mock_session_instance.request.return_value = mock_resp
            return self.client.place_bid(payload)

        with ThreadPoolExecutor(max_workers=num_threads) as executor:
            futures = [executor.submit(_place_bid_task) for _ in range(num_threads)]
            for future in futures:
                results.append(future.result())

        self.assertEqual(len(results), num_threads)
        for res in results:
            self.assertIsInstance(res, BidResponse)
            self.assertEqual(res.name, "executor_parallel_test")


if __name__ == "__main__":
    unittest.main()
