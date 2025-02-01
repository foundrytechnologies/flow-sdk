"""Foundry Cloud Platform (FCP) HTTP Client.

This module encapsulates HTTP request logic, including retries, timeouts,
error handling, and JSON parsing. It is shared across different FCP clients.
"""

from __future__ import annotations

import json
import logging
from typing import Any, Callable, Dict, Optional

import requests
from requests import Response
from requests.adapters import HTTPAdapter, Retry

from flow.utils.exceptions import (
    APIError,
    AuthenticationError,
    NetworkError,
    TimeoutError,
)

_LOGGER: logging.Logger = logging.getLogger(__name__)


class HTTPClient:
    """Encapsulates HTTP request logic including retries, timeouts, error handling, and JSON parsing."""

    def __init__(
        self,
        *,
        base_url: str,
        token: str,
        timeout: int,
        max_retries: int,
        logger: logging.Logger,
    ) -> None:
        """Initialize an HTTPClient instance.

        Args:
            base_url: The base URL of the FCP API.
            token: The authentication token.
            timeout: Timeout (in seconds) for each HTTP request.
            max_retries: Maximum number of retries for HTTP requests.
            logger: The logger for debug and error messages.
        """
        self._base_url: str = base_url
        self._timeout: int = timeout
        self._logger: logging.Logger = logger
        self._session: requests.Session = self._create_session(max_retries)
        self._session.headers.update(
            {
                "Authorization": f"Bearer {token}",
                "Content-Type": "application/json",
            }
        )

    def _create_session(self, max_retries: int) -> requests.Session:
        """Create and configure an HTTP session with retries.

        Args:
            max_retries: Maximum number of retries.

        Returns:
            A configured requests.Session instance.
        """
        self._logger.debug("Creating HTTP session with max_retries=%d", max_retries)
        session: requests.Session = requests.Session()
        retries: Retry = Retry(
            total=max_retries,
            backoff_factor=3,
            status_forcelist=[429, 500, 502, 503, 504],
            allowed_methods={"GET", "PUT", "DELETE"},
            raise_on_status=False,
        )
        adapter: HTTPAdapter = HTTPAdapter(max_retries=retries)
        session.mount("https://", adapter)
        return session

    def request(
        self,
        *,
        method: str,
        path: str,
        error_handler: Optional[
            Callable[[requests.HTTPError], Optional[Response]]
        ] = None,
        **kwargs: Any,
    ) -> Response:
        """Send an HTTP request to the FCP API.

        Args:
            method: The HTTP method (e.g., 'GET', 'POST').
            path: The API endpoint path.
            error_handler: An optional error handler function that can process HTTP errors.
            **kwargs: Additional keyword arguments to pass to requests.Session.request().

        Returns:
            The HTTP response.

        Raises:
            TimeoutError: If the request times out.
            NetworkError: If a network error occurs.
            AuthenticationError: If the server returns a 401 status.
            APIError: For other HTTP errors.
        """
        url: str = f"{self._base_url}{path}"
        kwargs.setdefault("timeout", self._timeout)
        self._logger.debug(
            "Preparing %s request to %s with kwargs=%s", method, url, kwargs
        )

        try:
            response: Response = self._session.request(method=method, url=url, **kwargs)
        except requests.exceptions.Timeout as err:
            self._logger.error("Request to %s timed out: %s", url, err)
            raise TimeoutError("Request timed out") from err
        except requests.exceptions.ConnectionError as err:
            self._logger.error(
                "Network error occurred while requesting %s: %s", url, err
            )
            raise NetworkError("Network error occurred") from err
        except requests.exceptions.RequestException as err:
            self._logger.error("Request failed for %s: %s", url, err)
            raise APIError(f"Request failed: {err}") from err

        if response.status_code >= 400:
            content_type: str = response.headers.get("Content-Type", "")
            try:
                if "application/json" in content_type:
                    parsed_json: Any = response.json()
                    error_content_str: str = json.dumps(parsed_json)
                else:
                    error_content_str = response.text
            except ValueError:
                error_content_str = response.text

            status_code: int = response.status_code
            self._logger.error(
                "HTTP error occurred. status_code=%d, response=%s",
                status_code,
                error_content_str,
            )

            if error_handler is not None:
                mock_err: requests.HTTPError = requests.HTTPError(error_content_str)
                mock_err.response = response
                handled_resp: Optional[Response] = error_handler(mock_err)
                if handled_resp is not None:
                    return handled_resp

            if status_code in (401, 403):
                raise AuthenticationError("Authentication token is invalid")

            raise APIError(f"API request failed [{status_code}]: {error_content_str}")

        self._logger.debug(
            "Request to %s succeeded with status_code=%d", url, response.status_code
        )
        return response

    def parse_json(self, response: Response, *, context: str = "") -> Any:
        """Parse JSON content from an HTTP response.

        Args:
            response: The HTTP response object.
            context: Optional context for debugging purposes.

        Returns:
            The parsed JSON content.

        Raises:
            ValueError: If the response does not contain valid JSON.
        """
        try:
            return response.json()
        except ValueError as err:
            self._logger.error(
                "Failed to parse JSON for %s. Error: %s, response text: %s",
                context or "response",
                err,
                response.text,
            )
            raise
