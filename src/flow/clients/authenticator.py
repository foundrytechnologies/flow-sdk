"""Module for handling user authentication and access token retrieval.

This module provides the Authenticator class, which is responsible for
authenticating a user by sending HTTP requests to the configured authentication
API and retrieving an access token.

Example:
    To use the Authenticator, simply instantiate the class with valid credentials:
        auth = Authenticator(email="user@example.com", password="securepassword")
        token = auth.get_access_token()
"""

import logging
import os
from typing import Any, Dict, Optional

import requests
from requests.adapters import HTTPAdapter, Retry

from flow.utils.exceptions import (
    AuthenticationError,
    InvalidCredentialsError,
    NetworkError,
    TimeoutError,
)

logger = logging.getLogger(__name__)


class Authenticator:
    """Handles user authentication and access token retrieval.

    This class encapsulates the logic for authenticating against the API and
    storing the access token. Authentication is performed immediately during
    initialization.

    Attributes:
        email (str): The user's email address.
        password (str): The user's password.
        api_url (str): The base URL for the authentication API.
        request_timeout (int): Timeout value in seconds for HTTP requests.
        session (requests.Session): A configured session with a retry strategy.
        access_token (str): The access token retrieved upon successful authentication.
    """

    def __init__(
        self,
        email: str,
        password: str,
        api_url: Optional[str] = None,
        request_timeout: int = 10,
        max_retries: int = 3,
    ) -> None:
        """Initializes the Authenticator and performs immediate authentication.

        Args:
            email (str): The user's email address.
            password (str): The user's password.
            api_url (Optional[str]): The base URL for the authentication API.
                Defaults to the value of the environment variable 'API_URL' or
                'https://api.mlfoundry.com' if not provided.
            request_timeout (int): The timeout for HTTP requests in seconds.
                Defaults to 10.
            max_retries (int): The maximum number of retry attempts for HTTP
                requests. Defaults to 3.

        Raises:
            TypeError: If either email or password is not a string.
            ValueError: If either email or password is empty.
            AuthenticationError: If authentication fails.
        """
        # Validate input types.
        if not isinstance(email, str):
            raise TypeError("Email must be a string.")
        if not isinstance(password, str):
            raise TypeError("Password must be a string.")

        # Validate non-empty credentials.
        if not email:
            raise ValueError("Email must not be empty.")
        if not password:
            raise ValueError("Password must not be empty.")

        self.email: str = email
        self.password: str = password
        self.api_url: str = api_url or os.getenv("API_URL", "https://api.mlfoundry.com")
        self.request_timeout: int = request_timeout

        # Create a configured HTTP session with a retry strategy.
        self.session: requests.Session = self._create_session(max_retries)

        # Immediately authenticate to retrieve the access token.
        self.access_token: str = self.authenticate()

    def _create_session(self, max_retries: int) -> requests.Session:
        """Creates and configures an HTTP session with retry behavior.

        Args:
            max_retries (int): Maximum number of retry attempts for HTTP requests.

        Returns:
            requests.Session: A session object configured with a retry strategy.
        """
        session = requests.Session()
        retry_strategy = Retry(
            total=max_retries,
            backoff_factor=0.5,
            status_forcelist=[429, 500, 502, 503, 504],
            allowed_methods={"POST"},
            raise_on_status=False,
        )
        adapter = HTTPAdapter(max_retries=retry_strategy)
        session.mount("https://", adapter)
        session.mount("http://", adapter)

        logger.debug(
            "Created a new HTTP session with retry configuration.",
            extra={"max_retries": max_retries, "backoff_factor": 0.5},
        )
        return session

    def authenticate(self) -> str:
        """Authenticates the user and retrieves an access token.

        Sends a POST request with the user's credentials to the authentication API
        endpoint. If the request is successful and an access token is returned, it is
        stored and returned. Otherwise, appropriate exceptions are raised.

        Returns:
            str: The access token retrieved from the authentication API.

        Raises:
            InvalidCredentialsError: If the credentials are invalid (e.g., HTTP 401).
            NetworkError: If a network-related error occurs.
            TimeoutError: If the request times out.
            AuthenticationError: For other authentication-related failures.
        """
        auth_payload: Dict[str, str] = {"email": self.email, "password": self.password}
        login_url = f"{self.api_url}/login"

        logger.debug(
            "Attempting user authentication.",
            extra={"url": login_url, "email_provided": bool(self.email)},
        )

        try:
            response = self.session.post(
                login_url,
                json=auth_payload,
                timeout=self.request_timeout,
                headers={"Content-Type": "application/json"},
            )
        except requests.exceptions.Timeout as timeout_error:
            logger.exception("Authentication request timed out.")
            raise TimeoutError("Authentication request timed out.") from timeout_error
        except requests.exceptions.ConnectionError as conn_error:
            logger.exception("Network error during authentication.")
            raise NetworkError(
                "Network error occurred during authentication."
            ) from conn_error
        except requests.exceptions.RequestException as req_error:
            logger.exception("General request exception during authentication.")
            raise AuthenticationError("Authentication request failed.") from req_error

        # Check for HTTP errors.
        if response.status_code >= 400:
            logger.error(
                "Authentication failed.",
                extra={
                    "status_code": response.status_code,
                    "url": login_url,
                    "email": self.email,
                },
            )
            if response.status_code == 401:
                raise InvalidCredentialsError("Invalid email or password.")
            raise AuthenticationError(
                f"Authentication failed with status code {response.status_code}."
            )

        # Parse the JSON response.
        try:
            response_data: Dict[str, Any] = response.json()
        except ValueError as json_error:
            logger.exception("Unable to decode response as JSON.")
            raise AuthenticationError("Invalid response format.") from json_error

        access_token: Optional[str] = response_data.get("access_token")
        if not access_token:
            logger.error(
                "Access token not found in response.",
                extra={"response_data": response_data, "url": login_url},
            )
            raise AuthenticationError("Access token not found in response.")

        logger.info("User authenticated successfully; access token retrieved.")
        return access_token

    def get_access_token(self) -> str:
        """Retrieves the stored access token.

        Returns:
            str: The authenticated access token.
        """
        return self.access_token
