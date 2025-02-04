import multiprocessing
import resource  # For resource consumption monitoring
import threading
import time
import unittest
from typing import Optional, Type

import requests
import responses
from parameterized import parameterized

from flow.clients.authenticator import Authenticator
from flow.utils.exceptions import (
    AuthenticationError,
    InvalidCredentialsError,
    NetworkError,
    TimeoutError,
)


class AuthenticatorTestBase(unittest.TestCase):
    """Base test class for Authenticator tests."""

    @classmethod
    def setUpClass(cls) -> None:
        """Sets up shared resources for the tests.

        Initializes the API URL and authentication URL used in the tests.
        """
        cls.api_url = "https://api.mlfoundry.com"
        cls.auth_url = f"{cls.api_url}/login"

    def setUp(self) -> None:
        """Sets up test case variables.

        Initializes the email and password for authentication used in the tests.
        """
        self.email: str = "test@example.com"
        self.password: str = "password"


class TestAuthenticationSuccess(AuthenticatorTestBase):
    """Tests related to successful authentication."""

    @responses.activate
    def test_successful_authentication(self) -> None:
        """Tests that the Authenticator retrieves an access token on success."""
        responses.add(
            responses.POST,
            self.auth_url,
            json={"access_token": "test_token"},
            status=200,
        )
        auth = Authenticator(self.email, self.password, api_url=self.api_url)
        self.assertEqual(auth.get_access_token(), "test_token")
        self.assertEqual(len(responses.calls), 1)


class TestAuthenticationFailures(AuthenticatorTestBase):
    """Tests related to authentication failures."""

    @responses.activate
    def test_authentication_failure_invalid_credentials(self) -> None:
        """Tests that invalid credentials raise InvalidCredentialsError."""
        responses.add(
            responses.POST,
            self.auth_url,
            json={"error": "Invalid credentials"},
            status=401,
        )
        with self.assertRaises(InvalidCredentialsError) as context:
            Authenticator(self.email, "wrongpassword", api_url=self.api_url)
        self.assertIn("Invalid email or password.", str(context.exception))

    @responses.activate
    def test_invalid_json_response(self) -> None:
        """Tests handling of an invalid JSON response from the API."""
        responses.add(responses.POST, self.auth_url, body="Invalid JSON", status=200)
        with self.assertRaises(AuthenticationError) as context:
            Authenticator(self.email, self.password, api_url=self.api_url)
        self.assertIn("Invalid response format.", str(context.exception))

    @responses.activate
    def test_missing_access_token_in_response(self) -> None:
        """Tests handling for missing access_token in a successful response."""
        responses.add(
            responses.POST,
            self.auth_url,
            json={"message": "Success"},
            status=200,
        )
        with self.assertRaises(AuthenticationError) as context:
            Authenticator(self.email, self.password, api_url=self.api_url)
        self.assertIn("Access token not found in response.", str(context.exception))


class TestInputValidation(AuthenticatorTestBase):
    """Tests related to input validation."""

    @parameterized.expand(
        [
            ("", "password", ValueError),
            ("email@example.com", "", ValueError),
            ("", "", ValueError),
            (None, "password", TypeError),  # None as email
            ("email@example.com", None, TypeError),  # None as password
            (12345, "password", TypeError),  # Integer as email
            ("email@example.com", ["password"], TypeError),  # List as password
        ]
    )
    @responses.activate
    def test_invalid_input_types(
        self, email: str, password: str, expected_exception: Optional[Type[Exception]]
    ) -> None:
        """Tests input validation for email and password types.

        Args:
            email: The email address to test.
            password: The password to test.
            expected_exception: The type of exception expected when invalid.
        """
        with self.assertRaises(expected_exception):
            Authenticator(email, password)

    @parameterized.expand(
        [
            ("a" * 10000 + "@example.com", "password"),  # Extremely long email
            ("email@example.com", "p" * 10000),  # Extremely long password
            ("email@例子.测试", "p@sswörd❤️"),  # Unicode in email and password
            ("email@example.com", "\0\0\0"),  # Null characters in password
            ("email@example.com", "<script>alert(1);</script>"),  # Injection attempt
        ]
    )
    @responses.activate
    def test_edge_case_inputs(self, email: str, password: str) -> None:
        """Tests Authenticator with various edge case inputs.

        Args:
            email: The email address to test.
            password: The password to test.
        """
        responses.add(
            responses.POST,
            self.auth_url,
            json={"access_token": "test_token"},
            status=200,
        )
        auth = Authenticator(email, password, api_url=self.api_url)
        self.assertEqual(auth.get_access_token(), "test_token")


class TestRetryLogic(AuthenticatorTestBase):
    """Tests related to retry logic."""

    @responses.activate
    def test_retry_logic_on_failure(self) -> None:
        """Tests that the session retries on server errors.

        Asserts an AuthenticationError is raised and that the request is retried
        the expected number of times.
        """
        responses.add(responses.POST, self.auth_url, status=500)
        with self.assertRaises(AuthenticationError):
            Authenticator(self.email, self.password, api_url=self.api_url)
        self.assertEqual(len(responses.calls), 4)  # 1 initial + 3 retries


class TestTimeoutsAndFailures(AuthenticatorTestBase):
    """Tests related to network timeouts and general request failures."""

    @responses.activate
    def test_authentication_network_failure(self) -> None:
        """Tests that network failures raise NetworkError."""

        def request_callback(request: requests.Request) -> None:
            raise requests.exceptions.ConnectionError("Network error")

        responses.add_callback(responses.POST, self.auth_url, callback=request_callback)
        with self.assertRaises(NetworkError) as context:
            Authenticator(self.email, self.password, api_url=self.api_url)
        self.assertIn(
            "Network error occurred during authentication.",
            str(context.exception),
        )

    @responses.activate
    def test_authentication_timeout(self) -> None:
        """Tests that timeouts raise TimeoutError."""

        def request_callback(request: requests.Request) -> None:
            raise requests.exceptions.Timeout("Request timed out")

        responses.add_callback(responses.POST, self.auth_url, callback=request_callback)
        with self.assertRaises(TimeoutError) as context:
            Authenticator(self.email, self.password, api_url=self.api_url)
        self.assertIn("Authentication request timed out.", str(context.exception))

    @responses.activate
    def test_custom_timeout(self) -> None:
        """Tests that a custom timeout is respected."""
        timeout: int = 5
        responses.add(
            responses.POST,
            self.auth_url,
            json={"access_token": "test_token"},
            status=200,
        )
        auth = Authenticator(
            self.email, self.password, api_url=self.api_url, request_timeout=timeout
        )
        self.assertEqual(auth.get_access_token(), "test_token")
        self.assertEqual(len(responses.calls), 1)


def authenticate(email: str, password: str, api_url: str, auth_url: str) -> None:
    """Authenticates in a separate process."""
    import responses  # Import within function for child process

    with responses.RequestsMock() as rsps:
        rsps.add(
            responses.POST,
            auth_url,
            json={"access_token": "test_token"},
            status=200,
        )
        auth = Authenticator(email, password, api_url=api_url)
        assert auth.get_access_token() == "test_token"


class TestConcurrency(AuthenticatorTestBase):
    """Tests related to concurrency and thread/process safety."""

    @responses.activate
    def test_concurrent_authentication_threads(self) -> None:
        """Tests concurrent authentication attempts using threads."""
        responses.add(
            responses.POST,
            self.auth_url,
            json={"access_token": "test_token"},
            status=200,
        )

        def authenticate_thread() -> None:
            """Authenticates and asserts the access token."""
            auth = Authenticator(self.email, self.password, api_url=self.api_url)
            self.assertEqual(auth.get_access_token(), "test_token")

        threads = []
        for _ in range(5):
            thread = threading.Thread(target=authenticate_thread)
            thread.start()
            threads.append(thread)

        for thread in threads:
            thread.join()

        self.assertEqual(len(responses.calls), 5)

    def test_concurrent_authentication_processes(self) -> None:
        """Tests authentication across multiple processes for robustness."""
        processes = []
        for _ in range(5):
            proc = multiprocessing.Process(
                target=authenticate,
                args=(self.email, self.password, self.api_url, self.auth_url),
            )
            proc.start()
            processes.append(proc)

        for proc in processes:
            proc.join()
            self.assertEqual(proc.exitcode, 0)


class TestPerformance(AuthenticatorTestBase):
    """Tests related to authentication performance."""

    @responses.activate
    def test_authentication_performance(self) -> None:
        """Tests that authentication completes within acceptable time frames."""
        responses.add(
            responses.POST,
            self.auth_url,
            json={"access_token": "test_token"},
            status=200,
        )
        start_time = time.time()
        auth = Authenticator(self.email, self.password, api_url=self.api_url)
        end_time = time.time()
        duration = end_time - start_time
        self.assertEqual(auth.get_access_token(), "test_token")
        self.assertLess(duration, 0.5)

    @responses.activate
    def test_performance_under_load(self) -> None:
        """Tests authentication performance under simulated load."""
        responses.add(
            responses.POST,
            self.auth_url,
            json={"access_token": "test_token"},
            status=200,
        )

        durations = []

        def authenticate_and_measure() -> None:
            """Authenticates and records performance."""
            start_t = time.time()
            auth = Authenticator(self.email, self.password, api_url=self.api_url)
            end_t = time.time()
            durations.append(end_t - start_t)
            self.assertEqual(auth.get_access_token(), "test_token")

        threads = []
        for _ in range(50):
            thread = threading.Thread(target=authenticate_and_measure)
            thread.start()
            threads.append(thread)

        for thread in threads:
            thread.join()

        self.assertEqual(len(responses.calls), 50)
        average_duration = sum(durations) / len(durations)
        self.assertLess(average_duration, 1)

    @responses.activate
    def test_resource_consumption(self) -> None:
        """Tests that resource usage does not spike significantly."""
        responses.add(
            responses.POST,
            self.auth_url,
            json={"access_token": "test_token"},
            status=200,
        )
        initial_memory = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
        auth = Authenticator(self.email, self.password, api_url=self.api_url)
        final_memory = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
        self.assertEqual(auth.get_access_token(), "test_token")
        memory_increase = final_memory - initial_memory
        self.assertLess(memory_increase, 10 * 1024)


class TestApiKeyAuthentication(AuthenticatorTestBase):
    """Tests for authentication using an API key.

    This test suite verifies that the Authenticator class correctly handles
    API key authentication by giving precedence over email/password credentials,
    and by falling back to credential-based authentication when no API key is provided.
    """

    @responses.activate
    def test_successful_api_key_authentication(self) -> None:
        """Test that providing only an API key sets it as the access token without any HTTP calls.

        Ensures that an API key bypasses any network-based authentication.
        """
        api_key: str = "test_api_key_123"
        authenticator = Authenticator(api_key=api_key, api_url=self.api_url)
        self.assertEqual(authenticator.get_access_token(), api_key)
        self.assertEqual(len(responses.calls), 0)

    @responses.activate
    def test_api_key_takes_precedence_over_credentials(self) -> None:
        """Test that an API key takes precedence over email/password credentials.

        When both authentication methods are provided, the API key should be used
        and no network authentication request should be issued.
        """
        api_key: str = "test_api_key_456"
        authenticator = Authenticator(
            email=self.email,
            password=self.password,
            api_key=api_key,
            api_url=self.api_url,
        )
        self.assertEqual(authenticator.get_access_token(), api_key)
        self.assertEqual(len(responses.calls), 0)

    @responses.activate
    def test_api_key_without_credentials(self) -> None:
        """Test that API key authentication works even when email and password are omitted.

        Validates that solely providing an API key is sufficient for authentication.
        """
        api_key: str = "test_api_key_789"
        authenticator = Authenticator(api_key=api_key, api_url=self.api_url)
        self.assertEqual(authenticator.get_access_token(), api_key)
        self.assertEqual(len(responses.calls), 0)

    @responses.activate
    def test_missing_both_api_key_and_credentials(self) -> None:
        """Test that an error is raised if neither an API key nor credentials are provided.

        Expects a TypeError when no authentication method is supplied.
        """
        with self.assertRaises(TypeError):
            Authenticator(api_url=self.api_url)

    @responses.activate
    def test_fallback_to_credentials_when_no_api_key(self) -> None:
        """Test that credential-based authentication is used when the API key is explicitly set to None.

        Verifies that the fallback logic triggers an HTTP call to retrieve an access token.
        """
        responses.add(
            responses.POST,
            self.auth_url,
            json={"access_token": "test_token"},
            status=200,
        )
        authenticator = Authenticator(
            email=self.email,
            password=self.password,
            api_key=None,
            api_url=self.api_url,
        )
        self.assertEqual(authenticator.get_access_token(), "test_token")
        self.assertEqual(len(responses.calls), 1)


if __name__ == "__main__":
    unittest.main()
