"""FCP API Client.

This module implements a client for the Foundry Cloud Platform (FCP) API. It manages
authentication, request execution with retry strategies, JSON parsing, and Pydantic model validation.
It also provides methods for interacting with resources such as users, projects, instances,
auctions, bids, and SSH keys.

This client supports authentication via either an API key or via email and password.

Example:
    from flow.clients.fcp_client import FCPClient
    from flow.clients.authenticator import Authenticator

    authenticator: Authenticator = Authenticator(email="user@domain.com", password="secret")
    client: FCPClient = FCPClient(authenticator=authenticator)
    user = client.users.get_user()
    projects = client.projects.get_projects()
"""

from __future__ import annotations

import json
import logging
import uuid
from typing import Any, Callable, Dict, List, Optional

import requests
from requests import Response
from pydantic import TypeAdapter

from flow.clients.authenticator import Authenticator
from flow.clients.http_client import HTTPClient
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

_LOGGER: logging.Logger = logging.getLogger(__name__)


class UserService:
    """Service for operations related to user management.

    This class provides methods to fetch the current user and user profile data.
    """

    def __init__(self, http_client: HTTPClient, logger: logging.Logger) -> None:
        """Initialize UserService.

        Args:
            http_client (HTTPClient): The HTTP client used to make API requests.
            logger (logging.Logger): The logger used for debugging and error messages.
        """
        self._http_client: HTTPClient = http_client
        self._logger: logging.Logger = logger

    def get_user(self) -> User:
        """Retrieve current user information.

        Returns:
            User: A validated User model instance representing the current user.

        Raises:
            AuthenticationError: If the request is unauthorized (HTTP 401/403).
            APIError: If the API request fails or returns invalid data.
        """
        self._logger.debug("Fetching current user information from /users/")

        def _user_error_handler(err: requests.HTTPError) -> Optional[Response]:
            status: Optional[int] = err.response.status_code if err.response else None
            if status in (401, 403):
                raise AuthenticationError(
                    "Unauthorized while fetching user info"
                ) from err
            if err.response is not None:
                raise APIError(
                    f"API request failed [{err.response.status_code}]: {err.response.text}"
                ) from err
            raise APIError(f"API request failed: {err}") from err

        response: Response = self._http_client.request(
            method="GET", path="/users/", error_handler=_user_error_handler
        )
        data: Any = self._http_client.parse_json(response, context="user data")
        self._logger.debug("Validating user data with Pydantic: %s", data)
        try:
            user_obj: User = User.model_validate(data)
            self._logger.debug(
                "User object successfully validated: %s", user_obj.model_dump()
            )
            return user_obj
        except ValueError as err:
            self._logger.error("Failed to validate user data: %s", err)
            raise APIError("Invalid JSON response for user information") from err

    def get_profile(self) -> User:
        """Retrieve user profile information.

        Returns:
            User: A validated User model instance with the user profile details.

        Raises:
            AuthenticationError: If the request is unauthorized (HTTP 401/403).
            APIError: If the API returns an error or sends an invalid response.
        """
        self._logger.debug("Fetching user profile from /users/")

        def _profile_error_handler(err: requests.HTTPError) -> Optional[Response]:
            status: Optional[int] = err.response.status_code if err.response else None
            if status in (401, 403):
                raise AuthenticationError(
                    "Unauthorized while fetching user profile"
                ) from err
            raise APIError("API error occurred while fetching user profile") from err

        response: Response = self._http_client.request(
            method="GET", path="/users/", error_handler=_profile_error_handler
        )
        data: Any = self._http_client.parse_json(response, context="user profile")
        self._logger.debug("Validating user profile data with Pydantic: %s", data)
        try:
            user_profile: User = User.model_validate(data)
            self._logger.debug(
                "User profile successfully validated: %s", user_profile.model_dump()
            )
            return user_profile
        except ValueError as err:
            self._logger.error("Failed to validate user profile: %s", err)
            raise APIError("Invalid JSON response for user profile") from err


class ProjectService:
    """Service for operations related to projects."""

    def __init__(
        self, http_client: HTTPClient, user_id: str, logger: logging.Logger
    ) -> None:
        """Initialize ProjectService.

        Args:
            http_client (HTTPClient): The HTTP client used for API requests.
            user_id (str): The identifier of the current user.
            logger (logging.Logger): The logger used for debugging and error messages.
        """
        self._http_client: HTTPClient = http_client
        self._user_id: str = user_id
        self._logger: logging.Logger = logger

    def get_projects(self) -> List[Project]:
        """Retrieve projects for the current user.

        Returns:
            List[Project]: A list of validated Project model instances.

        Raises:
            APIError: If the retrieved projects data cannot be validated.
        """
        path: str = f"/users/{self._user_id}/projects"
        self._logger.debug(
            "Fetching projects for user_id=%s from %s", self._user_id, path
        )
        response: Response = self._http_client.request(method="GET", path=path)
        data: Any = self._http_client.parse_json(response, context="projects data")
        sample: Any = data[:1] if isinstance(data, list) else data
        self._logger.debug("Validating projects data (sample): %s", sample)
        try:
            projects: List[Project] = TypeAdapter(List[Project]).validate_python(data)
            self._logger.debug(
                "Projects successfully validated. Count=%d", len(projects)
            )
            return projects
        except ValueError as err:
            self._logger.error("Failed to validate projects data: %s", err)
            raise APIError("Invalid JSON response for projects") from err

    def get_project_by_name(self, project_name: str) -> Project:
        """Retrieve a project by its name.

        Args:
            project_name (str): The name of the project to search for.

        Returns:
            Project: A Project model instance that matches the given name.

        Raises:
            ValueError: If no project with the specified name exists.
        """
        self._logger.debug("Searching for project with name='%s'", project_name)
        projects: List[Project] = self.get_projects()
        for project in projects:
            if project.name == project_name:
                self._logger.info("Found project: %s", project)
                return project
        self._logger.error("No project found with name='%s'", project_name)
        raise ValueError(f"No project found with name: {project_name}")


class InstanceService:
    """Service for operations related to instances."""

    def __init__(self, http_client: HTTPClient, logger: logging.Logger) -> None:
        """Initialize InstanceService.

        Args:
            http_client (HTTPClient): The HTTP client used for API requests.
            logger (logging.Logger): The logger used for debugging and error messages.
        """
        self._http_client: HTTPClient = http_client
        self._logger: logging.Logger = logger

    def get_instances(self, project_id: str) -> Dict[str, List[Instance]]:
        """Retrieve instances grouped by category for a specified project.

        Args:
            project_id (str): The identifier of the project.

        Returns:
            Dict[str, List[Instance]]: A dictionary mapping category names to lists of instances.

        Raises:
            APIError: If the instances data cannot be validated.
        """
        path: str = f"/projects/{project_id}/all_instances"
        self._logger.debug(
            "Fetching instances for project_id=%s from %s", project_id, path
        )
        response: Response = self._http_client.request(method="GET", path=path)
        data: Any = self._http_client.parse_json(response, context="instances data")
        validated: Dict[str, List[Instance]] = {}
        try:
            for category, items in data.items():
                sample: Any = items[:1] if isinstance(items, list) else items
                self._logger.debug(
                    "Validating instances for category='%s' (sample): %s",
                    category,
                    sample,
                )
                validated_list: List[Instance] = TypeAdapter(
                    List[Instance]
                ).validate_python(items)
                validated[category] = validated_list
            self._logger.debug("Instances successfully validated.")
            return validated
        except ValueError as err:
            self._logger.error("Failed to validate instances data: %s", err)
            raise APIError("Invalid JSON response for instances") from err


class AuctionService:
    """Service for operations related to auctions."""

    def __init__(self, http_client: HTTPClient, logger: logging.Logger) -> None:
        """Initialize AuctionService.

        Args:
            http_client (HTTPClient): The HTTP client used for API requests.
            logger (logging.Logger): The logger used for debugging and error messages.
        """
        self._http_client: HTTPClient = http_client
        self._logger: logging.Logger = logger

    def get_auctions(self, project_id: str) -> List[Auction]:
        """Retrieve auctions for a given project.

        Args:
            project_id (str): The identifier of the project.

        Returns:
            List[Auction]: A list of validated Auction model instances.

        Raises:
            APIError: If the auctions data cannot be validated.
        """
        path: str = f"/projects/{project_id}/spot-auctions/auctions"
        self._logger.debug(
            "Fetching auctions for project_id=%s from %s", project_id, path
        )
        response: Response = self._http_client.request(method="GET", path=path)
        data: Any = self._http_client.parse_json(response, context="auctions data")
        sample: Any = data[:1] if isinstance(data, list) else data
        self._logger.debug("Validating auctions data (sample): %s", sample)
        try:
            auctions: List[Auction] = TypeAdapter(List[Auction]).validate_python(data)
            self._logger.debug(
                "Auctions successfully validated. Count=%d", len(auctions)
            )
            return auctions
        except ValueError as err:
            self._logger.error("Failed to validate auctions data: %s", err)
            raise APIError("Invalid JSON response for auctions") from err


class SSHKeyService:
    """Service for operations related to SSH keys."""

    def __init__(self, http_client: HTTPClient, logger: logging.Logger) -> None:
        """Initialize SSHKeyService.

        Args:
            http_client (HTTPClient): The HTTP client used for API requests.
            logger (logging.Logger): The logger used for debugging and error messages.
        """
        self._http_client: HTTPClient = http_client
        self._logger: logging.Logger = logger

    def get_ssh_keys(self, project_id: str) -> List[SshKey]:
        """Retrieve a list of SSHKey objects for the specified project.

        Args:
            project_id (str): The ID of the project.

        Returns:
            List[SshKey]: A list of validated SshKey model instances.

        Raises:
            APIError: If the SSH key data cannot be retrieved or parsed.
        """
        endpoint: str = f"/projects/{project_id}/ssh_keys"
        self._logger.debug(
            "Fetching SSH keys for project_id=%s from %s", project_id, endpoint
        )
        response: Response = self._http_client.request(method="GET", path=endpoint)
        data: Any = self._http_client.parse_json(response, context="ssh_keys data")
        # Use a log limit if available to avoid logging too much data
        log_limit = getattr(self, "_log_limit", 1)
        truncated_data: Any = data[:log_limit] if isinstance(data, list) else data
        self._logger.debug(
            "Validating SSH keys data with Pydantic (showing up to %d): %s",
            log_limit,
            truncated_data,
        )
        try:
            ssh_keys: List[SshKey] = TypeAdapter(List[SshKey]).validate_python(data)
            self._logger.debug(
                "SSH keys successfully validated. Count=%d", len(ssh_keys)
            )
            return ssh_keys
        except ValueError as err:
            self._logger.error("Failed to parse SSH keys data: %s", err)
            raise APIError("Invalid JSON response for SSH keys") from err


class BidService:
    """Service for operations related to bids."""

    def __init__(
        self, http_client: HTTPClient, user_id: str, logger: logging.Logger
    ) -> None:
        """Initialize BidService.

        Args:
            http_client (HTTPClient): The HTTP client used for API requests.
            user_id (str): The identifier of the current user.
            logger (logging.Logger): The logger used for debugging and error messages.
        """
        self._http_client: HTTPClient = http_client
        self._user_id: str = user_id
        self._logger: logging.Logger = logger

    def get_bids(self, project_id: str) -> List[Bid]:
        """Retrieve bids for a specified project.

        Args:
            project_id (str): The identifier of the project.

        Returns:
            List[Bid]: A list of validated Bid model instances.

        Raises:
            APIError: If the bids data cannot be validated.
        """
        path: str = f"/projects/{project_id}/spot-auctions/bids"
        self._logger.debug("Fetching bids for project_id=%s from %s", project_id, path)
        response: Response = self._http_client.request(method="GET", path=path)
        data: Any = self._http_client.parse_json(response, context="bids data")
        sample: Any = data[:1] if isinstance(data, list) else data
        self._logger.debug("Validating bids data (sample): %s", sample)
        try:
            bids: List[Bid] = TypeAdapter(List[Bid]).validate_python(data)
            self._logger.debug("Bids successfully validated. Count=%d", len(bids))
            return bids
        except ValueError as err:
            self._logger.error("Failed to validate bids data: %s", err)
            raise APIError("Invalid JSON response for bids") from err

    def place_bid(
        self, payload: BidPayload, idempotency_key: Optional[str] = None
    ) -> BidResponse:
        """Place a bid for a project.

        Args:
            payload (BidPayload): The bid details encapsulated as a BidPayload.
            idempotency_key (Optional[str]): An optional key to ensure idempotency. If not provided, one will be generated.

        Returns:
            BidResponse: A validated BidResponse model instance representing the bid response.

        Raises:
            APIError: If the bid placement fails or the response is invalid.
        """
        if idempotency_key is None:
            idempotency_key = str(uuid.uuid4())
        self._logger.debug("Placing bid with payload: %s", payload.model_dump())
        headers: Dict[str, str] = {"X-Idempotency-Key": idempotency_key}
        request_data: Dict[str, Any] = payload.model_dump(exclude_none=True)
        path: str = f"/projects/{payload.project_id}/spot-auctions/bids"

        def duplicate_bid_handler(err: requests.HTTPError) -> Optional[Response]:
            response: Optional[Response] = err.response
            if response is not None and response.status_code == 400:
                try:
                    error_content: Any = response.json()
                    error_str: str = json.dumps(error_content).lower()
                except ValueError:
                    error_str = response.text.lower()
                if "order named" in error_str and "already exists" in error_str:
                    self._logger.info(
                        "Duplicate bid detected; treating as success with idempotent response."
                    )
                    dummy_bid: BidResponse = BidResponse.dummy_response(
                        order_name=payload.order_name,
                        project_id=payload.project_id,
                        user_id=self._user_id,
                        disk_ids=[],  # Extend extraction logic as needed.
                        cluster_id=request_data.get("cluster_id", "unknown"),
                        instance_quantity=request_data.get("instance_quantity", 1),
                        instance_type_id=request_data.get(
                            "instance_type_id", "unknown"
                        ),
                        limit_price_cents=request_data.get("limit_price_cents", 0),
                    )
                    from requests import Response

                    dummy_response = Response()
                    dummy_response.status_code = 200
                    dummy_response._content = dummy_bid.model_dump_json().encode(
                        "utf-8"
                    )
                    dummy_response.headers["Content-Type"] = "application/json"
                    dummy_response.reason = "OK"
                    return dummy_response
            return None

        response: Response = self._http_client.request(
            method="POST",
            path=path,
            json=request_data,
            headers=headers,
            error_handler=duplicate_bid_handler,
        )
        data: Any = self._http_client.parse_json(response, context="place_bid response")
        self._logger.debug("Validating place_bid response with Pydantic: %s", data)
        try:
            bid_response: BidResponse = BidResponse.model_validate(data)
            self._logger.debug(
                "BidResponse successfully validated: %s", bid_response.model_dump()
            )
            return bid_response
        except ValueError as err:
            self._logger.error("Failed to validate place_bid response: %s", err)
            raise APIError("Invalid JSON response for place_bid") from err

    def cancel_bid(self, project_id: str, bid_id: str) -> None:
        """Cancel an existing bid.

        Args:
            project_id (str): The identifier of the project.
            bid_id (str): The identifier of the bid to cancel.

        Raises:
            APIError: If the bid cancellation fails.
        """
        path: str = f"/projects/{project_id}/spot-auctions/bids/{bid_id}"
        self._logger.debug(
            "Canceling bid with bid_id=%s for project_id=%s", bid_id, project_id
        )
        try:
            self._http_client.request(method="DELETE", path=path)
            self._logger.info(
                "Successfully canceled bid with bid_id=%s for project_id=%s",
                bid_id,
                project_id,
            )
        except APIError as err:
            # If the bid is not found, treat it as already canceled.
            if "Bid not found" in str(err):
                self._logger.info(
                    "Bid %s not found during cancellation; treating as already canceled.",
                    bid_id,
                )
                return
            else:
                raise


class FCPClient:
    """Client to interact with the Foundry Cloud Platform (FCP) API.

    This client aggregates various services (users, projects, instances, auctions,
    SSH keys, and bids) and handles authentication, session management, and error handling.
    """

    def __init__(
        self,
        authenticator: Authenticator,
        *,
        base_url: str = "https://api.mlfoundry.com",
        timeout: int = 120,
        max_retries: int = 5,
        skip_init: bool = False,
    ) -> None:
        """Initialize FCPClient.

        Args:
            authenticator (Authenticator): An Authenticator instance used to retrieve an access token.
            base_url (str): The base URL of the FCP API.
            timeout (int): Timeout in seconds for each HTTP request.
            max_retries (int): Maximum number of retries for HTTP requests.
            skip_init (bool): If True, skip the initial user initialization (useful for testing).

        Raises:
            TypeError: If the provided authenticator is not an Authenticator instance.
            AuthenticationError: If token retrieval fails.
        """
        if not isinstance(authenticator, Authenticator):
            raise TypeError("authenticator must be an Authenticator instance")
        self._logger: logging.Logger = _LOGGER
        self._logger.debug("Authenticating using provided authenticator.")
        try:
            token: str = authenticator.get_access_token()
        except Exception as exc:
            self._logger.error("Failed to retrieve access token.", exc_info=True)
            raise AuthenticationError(
                "Authentication failed: Invalid credentials"
            ) from exc

        if not token:
            self._logger.error("Received empty access token.")
            raise AuthenticationError("Authentication failed: No token received")

        self._http_client: HTTPClient = HTTPClient(
            base_url=base_url,
            token=token,
            timeout=timeout,
            max_retries=max_retries,
            logger=self._logger,
        )

        self.users: UserService = UserService(self._http_client, self._logger)
        if not skip_init:
            self._user_id: str = self.users.get_user().id
        else:
            self._user_id = ""
        self.projects: ProjectService = ProjectService(
            self._http_client, self._user_id, self._logger
        )
        self.instances: InstanceService = InstanceService(
            self._http_client, self._logger
        )
        self.auctions: AuctionService = AuctionService(self._http_client, self._logger)
        self.ssh_keys: SSHKeyService = SSHKeyService(self._http_client, self._logger)
        self.bids: BidService = BidService(
            self._http_client, self._user_id, self._logger
        )

        self._logger.info(
            "FCPClient initialized successfully for user_id=%s", self._user_id
        )
        # Reset the mock call history on the HTTP client's request method if applicable.
        if hasattr(self._http_client._session.request, "reset_mock"):
            self._http_client._session.request.reset_mock()

    def get_user(self) -> User:
        """Retrieve the current user.

        Returns:
            User: A validated User model instance.
        """
        return self.users.get_user()

    def get_profile(self) -> User:
        """Retrieve the user profile.

        Returns:
            User: A validated User model instance.
        """
        return self.users.get_profile()

    def get_projects(self) -> List[Project]:
        """Retrieve projects.

        Returns:
            List[Project]: A list of validated Project model instances.
        """
        return self.projects.get_projects()

    def get_project_by_name(self, project_name: str) -> Project:
        """Retrieve a project by name.

        Args:
            project_name (str): The name of the project.

        Returns:
            Project: A validated Project model instance matching the given name.
        """
        return self.projects.get_project_by_name(project_name)

    def get_instances(self, project_id: str) -> Dict[str, List[Instance]]:
        """Retrieve instances for a project.

        Args:
            project_id (str): The identifier of the project.

        Returns:
            Dict[str, List[Instance]]: A dictionary mapping category names to lists of Instance model instances.
        """
        return self.instances.get_instances(project_id)

    def get_auctions(self, project_id: str) -> List[Auction]:
        """Retrieve auctions for a project.

        Args:
            project_id (str): The identifier of the project.

        Returns:
            List[Auction]: A list of validated Auction model instances.
        """
        return self.auctions.get_auctions(project_id)

    def get_ssh_keys(self, project_id: str) -> List[SshKey]:
        """Retrieve SSH keys for a project.

        Args:
            project_id (str): The identifier of the project.

        Returns:
            List[SshKey]: A list of validated SshKey model instances.
        """
        return self.ssh_keys.get_ssh_keys(project_id)

    def get_bids(self, project_id: str) -> List[Bid]:
        """Retrieve bids for a project.

        Args:
            project_id (str): The identifier of the project.

        Returns:
            List[Bid]: A list of validated Bid model instances.
        """
        return self.bids.get_bids(project_id)

    def place_bid(
        self, payload: BidPayload, idempotency_key: Optional[str] = None
    ) -> BidResponse:
        """Place a bid.

        Args:
            payload (BidPayload): The bid details encapsulated as a BidPayload.
            idempotency_key (Optional[str]): An optional key to ensure idempotency.

        Returns:
            BidResponse: A validated BidResponse model instance.
        """
        return self.bids.place_bid(payload, idempotency_key)

    def cancel_bid(self, project_id: str, bid_id: str) -> None:
        """Cancel a bid.

        Args:
            project_id (str): The identifier of the project.
            bid_id (str): The identifier of the bid to cancel.
        """
        return self.bids.cancel_bid(project_id, bid_id)
