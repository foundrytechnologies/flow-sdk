import json
import logging
from typing import Any, Dict, List

import requests
from pydantic import TypeAdapter
from requests import Response
from requests.adapters import HTTPAdapter, Retry

from flow.clients.authenticator import Authenticator
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
    NetworkError,
    TimeoutError,
)

_LOGGER = logging.getLogger(__name__)


class FCPClient:
    """
    Client for interacting with the Foundry Cloud Platform (FCP) API.

    This client provides methods for managing projects, instances, auctions,
    bids, SSH keys, user profile data, etc. It also handles authentication
    (via the provided Authenticator), request retries, and JSON validation
    against Pydantic models.

    Example:
        authenticator = Authenticator(email="user@domain.com", password="secret")
        client = FCPClient(authenticator=authenticator)
        user = client.get_user()
    """

    def __init__(
        self,
        authenticator: Authenticator,
        base_url: str = "https://api.mlfoundry.com",
        timeout: int = 30,
        max_retries: int = 3,
        log_limit: int = 1,
    ) -> None:
        """
        Initialize an FCPClient instance.

        Args:
            authenticator: An Authenticator used to retrieve access tokens.
            base_url: The base URL of the FCP API (default: https://api.mlfoundry.com).
            timeout: Timeout for each HTTP request in seconds (default: 30).
            max_retries: Number of times to retry requests on certain status codes (default: 3).
            log_limit: The maximum number of items to display in debug logs (default: 3).

        Raises:
            TypeError: If `authenticator` is not an instance of Authenticator.
            AuthenticationError: If the client fails to retrieve a token or receives an empty token.
        """
        if not isinstance(authenticator, Authenticator):
            raise TypeError("authenticator must be an Authenticator instance")

        self._logger = _LOGGER
        self._authenticator = authenticator
        self._base_url = base_url
        self._timeout = timeout
        self._session = self._create_session(max_retries)

        self._logger.debug("Attempting to retrieve access token via Authenticator.")
        try:
            token = self._authenticator.get_access_token()
        except Exception as exc:
            self._logger.error(
                "Failed to obtain token from Authenticator", exc_info=True
            )
            raise AuthenticationError(
                "Authentication failed: Invalid credentials"
            ) from exc

        if not token:
            self._logger.error("Received empty token from Authenticator.")
            raise AuthenticationError("Authentication failed: No token received")

        # Update session headers with authentication token
        self._session.headers.update(
            {
                "Authorization": f"Bearer {token}",
                "Content-Type": "application/json",
            }
        )

        self._user_id = self._get_user_id()
        self._logger.info(
            "FCPClient initialized successfully for user_id=%s.", self._user_id
        )

        self._log_limit = log_limit

    def _create_session(self, max_retries: int) -> requests.Session:
        """
        Create an HTTP session with a retry strategy.

        Args:
            max_retries: The maximum number of retries for HTTP requests.

        Returns:
            A configured requests.Session instance.
        """
        self._logger.debug(
            "Creating a requests session with max_retries=%d.", max_retries
        )
        session = requests.Session()
        
        # Note that the Foundry API has some non-idempotent POST and PUT operations, so we don't want to retry them by default
        retries = Retry(
            total=max_retries,
            backoff_factor=2,  # Exponential backoff
            status_forcelist=[429, 500, 502, 503, 504],
            allowed_methods={"GET", "DELETE"}, 
            raise_on_status=False,
        )

        adapter = HTTPAdapter(max_retries=retries)
        session.mount("https://", adapter)
        return session

    def _get_user_id(self) -> str:
        """
        Retrieve the user ID from the authenticated user's information.

        Returns:
            The user ID as a string.

        Raises:
            APIError: If the user ID cannot be found.
        """
        self._logger.debug("Attempting to fetch current user to obtain user ID.")
        user_info = self.get_user()
        user_id = user_info.id
        if not user_id:
            self._logger.error("User ID not found in user information.")
            raise APIError("User ID not found in user information")
        self._logger.debug("Retrieved user_id=%s.", user_id)
        return user_id

    def _request(self, method: str, path: str, **kwargs: Any) -> Response:
        """
        Send an HTTP request to the FCP API.

        Args:
            method: The HTTP method ("GET", "POST", "PUT", "DELETE", etc.).
            path: The API endpoint (e.g. "/users/").
            **kwargs: Additional arguments passed to requests.Session.request().

        Returns:
            The requests.Response object from the API call.

        Raises:
            TimeoutError: If the request times out.
            NetworkError: If a network connection error occurs.
            AuthenticationError: If a 401 Unauthorized status is returned.
            APIError: For other request-related errors or invalid responses.
        """
        url = f"{self._base_url}{path}"
        kwargs.setdefault("timeout", self._timeout)

        self._logger.debug(
            "Preparing %s request to %s with kwargs=%s", method, url, kwargs
        )
        try:
            response = self._session.request(method=method, url=url, **kwargs)
            response.raise_for_status()
            self._logger.debug(
                "Request to %s succeeded with status_code=%d.",
                url,
                response.status_code,
            )
            return response
        except requests.exceptions.Timeout as err:
            self._logger.error("Request to %s timed out: %s", url, err)
            raise TimeoutError("Request timed out") from err
        except requests.exceptions.ConnectionError as err:
            self._logger.error(
                "Network error occurred while requesting %s: %s", url, err
            )
            raise NetworkError("Network error occurred") from err
        except requests.exceptions.HTTPError as err:
            # If the response is missing in the exception context, attach it
            if err.response is None:
                err.response = response
            self._handle_http_error(err)
        except requests.exceptions.RequestException as err:
            self._logger.error("Request failed for %s: %s", url, err)
            raise APIError(f"Request failed: {err}") from err

        # If we somehow get here, re-raise a generic APIError
        raise APIError("Unknown error during request execution.")

    def _handle_http_error(self, error: requests.HTTPError) -> None:
        """
        Handle HTTP errors by logging and raising appropriate exceptions.

        Args:
            error: The HTTPError exception to handle.

        Raises:
            AuthenticationError: If a 401 status code is returned.
            APIError: For all other HTTP error statuses.
        """
        if error.response is not None:
            status_code = error.response.status_code
            content_type = error.response.headers.get("Content-Type", "")
            try:
                if "application/json" in content_type:
                    parsed_json = error.response.json()
                    error_content_str = json.dumps(parsed_json, indent=2)
                else:
                    error_content_str = error.response.text
            except ValueError:
                error_content_str = error.response.text

            self._logger.error(
                "HTTP error occurred. status_code=%d, response=%s",
                status_code,
                error_content_str,
            )

            if status_code == 401:
                raise AuthenticationError("Authentication token is invalid") from error

            raise APIError(
                f"API request failed [{status_code}]: {error_content_str}"
            ) from error
        else:
            msg = "API request failed: No response received"
            self._logger.error(msg)
            raise APIError(msg) from error

    def _parse_json(self, response: Response, context: str = "") -> Any:
        """
        Safely parse JSON from a Response object and log the raw data.

        Args:
            response: The Response object from which to parse JSON.
            context: A string to indicate which context this JSON belongs to (e.g. "user data").

        Returns:
            The parsed JSON as a Python object (dict, list, etc.).

        Raises:
            ValueError: If the response body is not valid JSON.
        """
        try:
            raw_data = response.json()
            return raw_data
        except ValueError as err:
            self._logger.error(
                "Failed to parse JSON for %s. Error: %s, response text: %s",
                context or "response",
                err,
                response.text,
            )
            raise

    # ----------------------------------------------------------------
    # Public methods that use _request + _parse_json + Pydantic models
    # ----------------------------------------------------------------

    def get_user(self) -> User:
        """
        Fetch the User object representing the currently authenticated user.

        Returns:
            A User Pydantic model.

        Raises:
            APIError: If user information cannot be retrieved or parsed.
        """
        self._logger.debug("Fetching current user information from /users/ endpoint.")
        response = self._request("GET", "/users/")
        data = self._parse_json(response, "user data")
        self._logger.debug("Validating user data with Pydantic: %s", data)
        try:
            user_obj = User.model_validate(data)
            self._logger.debug(
                "User object successfully validated: %s", user_obj.model_dump()
            )
            return user_obj
        except ValueError as err:
            self._logger.error(
                "Failed to parse user information into User model: %s", err
            )
            raise APIError("Invalid JSON response for user information") from err

    def get_profile(self) -> User:
        """
        Retrieve the user profile information (same as get_user in many contexts, but may fork in the future).

        Returns:
            A User Pydantic model.

        Raises:
            APIError: If the user profile data cannot be retrieved or parsed.
        """
        self._logger.debug("Fetching user profile information from /users/ endpoint.")
        response = self._request("GET", "/users/")
        data = self._parse_json(response, "user profile")
        self._logger.debug("Validating user profile data with Pydantic: %s", data)
        try:
            user_profile = User.model_validate(data)
            self._logger.debug(
                "User profile object successfully validated: %s",
                user_profile.model_dump(),
            )
            return user_profile
        except ValueError as err:
            self._logger.error("Failed to parse user profile information: %s", err)
            raise APIError("Invalid JSON response for user profile.") from err

    def get_projects(self) -> List[Project]:
        """
        Retrieve a list of Project objects for the authenticated user.

        Returns:
            A list of Project Pydantic models.

        Raises:
            APIError: If the project data cannot be retrieved or parsed.
        """
        self._logger.debug(
            "Fetching projects for user_id=%s from /users/{user_id}/projects",
            self._user_id,
        )
        response = self._request("GET", f"/users/{self._user_id}/projects")
        data = self._parse_json(response, "projects data")
        truncated_data = data[: self._log_limit] if isinstance(data, list) else data
        self._logger.debug(
            "Validating projects data with Pydantic (showing up to %d): %s",
            self._log_limit,
            truncated_data,
        )
        try:
            projects = TypeAdapter(List[Project]).validate_python(data)
            self._logger.debug(
                "Projects successfully validated. Count=%d", len(projects)
            )
            return projects
        except ValueError as err:
            self._logger.error("Failed to parse projects data: %s", err)
            raise APIError("Invalid JSON response for projects") from err

    def get_project_by_name(self, project_name: str) -> Project:
        """
        Retrieve a project by its name.

        Args:
            project_name: The name of the project to fetch.

        Returns:
            A Project matching the given name.

        Raises:
            ValueError: If no project with the specified name is found.
        """
        self._logger.debug("Searching for project by name='%s'.", project_name)
        projects = self.get_projects()
        for project in projects:
            if project.name == project_name:
                self._logger.info("Found project: %s", project)
                return project
        self._logger.error("No project found with name='%s'.", project_name)
        raise ValueError(f"No project found with name: {project_name}")

    def get_instances(self, project_id: str) -> Dict[str, List[Instance]]:
        """
        Fetch instances grouped by category for a given project.

        Example response from API might look like:
            {
                "spot": [...],
                "reserved": [...]
            }

        Args:
            project_id: The ID of the project.

        Returns:
            A dict where keys are categories (e.g., "spot", "reserved") and
            values are lists of Instance objects.

        Raises:
            APIError: If the instance data is invalid or the request fails.
        """
        self._logger.debug(
            "Fetching instances for project_id=%s from /projects/{project_id}/all_instances",
            project_id,
        )
        response = self._request("GET", f"/projects/{project_id}/all_instances")
        data = self._parse_json(response, "instances data")
        self._logger.debug(
            "Validating instances data with Pydantic for each category: showing up to %d items per category",
            self._log_limit,
        )

        validated_dict: Dict[str, List[Instance]] = {}
        try:
            for category, raw_list in data.items():
                truncated_list = (
                    raw_list[: self._log_limit]
                    if isinstance(raw_list, list)
                    else raw_list
                )
                self._logger.debug(
                    "Category='%s', first %d items: %s",
                    category,
                    self._log_limit,
                    truncated_list,
                )
                validated_list = TypeAdapter(List[Instance]).validate_python(raw_list)
                validated_dict[category] = validated_list
            self._logger.debug("Instances successfully validated.")
            return validated_dict
        except ValueError as err:
            self._logger.error("Failed to parse instance data: %s", err)
            raise APIError("Invalid JSON response for instances") from err

    def get_auctions(self, project_id: str) -> List[Auction]:
        """
        Retrieve a list of Auction objects for the specified project.

        Args:
            project_id: The ID of the project to fetch auctions for.

        Returns:
            A list of Auction Pydantic models.

        Raises:
            APIError: If the auction data cannot be retrieved or parsed.
        """
        self._logger.debug(
            "Fetching auctions for project_id=%s from /projects/{project_id}/spot-auctions/auctions",
            project_id,
        )
        response = self._request(
            "GET", f"/projects/{project_id}/spot-auctions/auctions"
        )
        data = self._parse_json(response, "auctions data")
        truncated_data = data[: self._log_limit] if isinstance(data, list) else data
        self._logger.debug(
            "Validating auctions data with Pydantic (showing up to %d): %s",
            self._log_limit,
            truncated_data,
        )
        try:
            auctions = TypeAdapter(List[Auction]).validate_python(data)
            self._logger.debug(
                "Auctions successfully validated. Count=%d", len(auctions)
            )
            return auctions
        except ValueError as err:
            self._logger.error("Failed to parse auctions data: %s", err)
            raise APIError("Invalid JSON response for auctions") from err

    def get_ssh_keys(self, project_id: str) -> List[SshKey]:
        """
        Retrieve a list of SSHKey objects for the specified project.

        Args:
            project_id: The ID of the project.

        Returns:
            A list of SSHKey Pydantic models.

        Raises:
            APIError: If the SSH key data cannot be retrieved or parsed.
        """
        self._logger.debug(
            "Fetching SSH keys for project_id=%s from /projects/{project_id}/ssh_keys",
            project_id,
        )
        response = self._request("GET", f"/projects/{project_id}/ssh_keys")
        data = self._parse_json(response, "ssh_keys data")
        truncated_data = data[: self._log_limit] if isinstance(data, list) else data
        self._logger.debug(
            "Validating SSH keys data with Pydantic (showing up to %d): %s",
            self._log_limit,
            truncated_data,
        )
        try:
            ssh_keys = TypeAdapter(List[SshKey]).validate_python(data)
            self._logger.debug(
                "SSH keys successfully validated. Count=%d", len(ssh_keys)
            )
            return ssh_keys
        except ValueError as err:
            self._logger.error("Failed to parse SSH keys data: %s", err)
            raise APIError("Invalid JSON response for SSH keys") from err

    def get_bids(self, project_id: str) -> List[Bid]:
        """
        Retrieve all bids for the specified project.

        Args:
            project_id: The ID of the project.

        Returns:
            A list of Bid Pydantic models.

        Raises:
            APIError: If the bid data cannot be retrieved or parsed.
        """
        self._logger.debug(
            "Fetching bids for project_id=%s from /projects/{project_id}/spot-auctions/bids",
            project_id,
        )
        response = self._request("GET", f"/projects/{project_id}/spot-auctions/bids")
        data = self._parse_json(response, "bids data")
        truncated_data = data[: self._log_limit] if isinstance(data, list) else data
        self._logger.debug(
            "Validating bids data with Pydantic (showing up to %d): %s",
            self._log_limit,
            truncated_data,
        )
        try:
            bids = TypeAdapter(List[Bid]).validate_python(data)
            self._logger.debug("Bids successfully validated. Count=%d", len(bids))
            return bids
        except ValueError as err:
            self._logger.error("Failed to parse bids data: %s", err)
            raise APIError("Invalid JSON response for bids") from err

    def place_bid(self, payload: BidPayload) -> BidResponse:
        """
        Place a bid for a given project.

        Args:
            payload: A BidPayload object containing bid details.

        Returns:
            A BidResponse Pydantic model.

        Raises:
            APIError: If the server returns invalid JSON or if there's an error during the request.
        """
        self._logger.debug(
            "Placing bid for project_id=%s with order_name=%s. Payload=%s",
            payload.project_id,
            payload.order_name,
            payload.model_dump(),
        )
        request_data = payload.model_dump(exclude_none=True)
        response = self._request(
            "POST",
            f"/projects/{payload.project_id}/spot-auctions/bids",
            json=request_data,
        )
        data = self._parse_json(response, "place_bid response")
        self._logger.debug("Validating place_bid response with Pydantic: %s", data)
        try:
            bid_response = BidResponse.model_validate(data)
            self._logger.debug(
                "BidResponse successfully validated: %s", bid_response.model_dump()
            )
            return bid_response
        except ValueError as err:
            self._logger.error("Failed to parse place_bid response: %s", err)
            raise APIError("Invalid JSON response for place_bid") from err

    def cancel_bid(self, project_id: str, bid_id: str) -> None:
        """
        Cancel an existing bid for a project.

        Args:
            project_id: The ID of the project.
            bid_id: The ID of the bid to cancel.

        Raises:
            APIError: If the request fails or the bid cancellation is unsuccessful.
        """
        self._logger.debug("Canceling bid_id=%s for project_id=%s.", bid_id, project_id)
        try:
            self._request(
                "DELETE", f"/projects/{project_id}/spot-auctions/bids/{bid_id}"
            )
            self._logger.info(
                "Successfully canceled bid_id=%s for project_id=%s.",
                bid_id,
                project_id,
            )
        except APIError as err:
            self._logger.error("Failed to cancel bid_id=%s: %s", bid_id, err)
            raise
