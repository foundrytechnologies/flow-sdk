import logging
from typing import Any, Dict, List, Literal, Optional, Tuple, Union

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

# TODO (jaredquincy): consider splitting these into separate files
# ---------------------------------------------------------
#                  Port Utility Functions
# ---------------------------------------------------------


def validate_single_port(port_value: int, field_name: str) -> None:
    """Validates a single port integer.

    Args:
        port_value: The port value to validate.
        field_name: The name of the field being validated.

    Raises:
        ValueError: If the port value is not in the valid range.
    """
    if not 1 <= port_value <= 65535:
        error_msg = f"'{field_name}' port number must be between 1 and 65535."
        logging.error(error_msg)
        raise ValueError(error_msg)


def validate_port_range(port_range_str: str, field_name: str) -> None:
    """Validates that a port range string has valid integer boundaries.

    Args:
        port_range_str: The port range string, e.g. "8080-8090".
        field_name: The name of the field being validated.

    Raises:
        ValueError: If the range cannot be split or if invalid numbers are provided.
    """
    try:
        start_port_str, end_port_str = port_range_str.split("-")
        start_port = int(start_port_str)
        end_port = int(end_port_str)
    except ValueError as val_err:
        error_msg = f"'{field_name}' port range must contain valid integers."
        logging.error(error_msg)
        raise ValueError(error_msg) from val_err

    if not 1 <= start_port <= end_port <= 65535:
        error_msg = f"'{field_name}' port numbers must be between 1 and 65535."
        logging.error(error_msg)
        raise ValueError(error_msg)


def expand_port_spec(port_spec: Union[int, str]) -> List[int]:
    """Expands a port specification (int or str) into a list of port numbers.

    Args:
        port_spec: An integer or string specifying a port or range.

    Raises:
        ValueError: If the port specification is invalid.

    Returns:
        A list of valid port numbers.
    """
    if isinstance(port_spec, int):
        logging.debug("Port specification is an integer: %d", port_spec)
        return [port_spec]

    # String path
    if "-" in port_spec:
        # Port range
        validate_port_range(port_spec, field_name="(range)")
        start_port_str, end_port_str = port_spec.split("-")
        start_port = int(start_port_str)
        end_port = int(end_port_str)
        ports = list(range(start_port, end_port + 1))
        logging.debug("Expanded port range '%s' into ports: %s", port_spec, ports)
        return ports

    # Single port string
    if not port_spec.isdigit():
        error_msg = f"Invalid port specification '{port_spec}': must be an integer."
        logging.error(error_msg)
        raise ValueError(error_msg)
    port_num = int(port_spec)
    validate_single_port(port_num, field_name="(single)")
    logging.debug("Port specification is a digit: %d", port_num)
    return [port_num]


def validate_port_value(port_value: Optional[Union[int, str]], field_name: str) -> None:
    """Validates that port_value is either a valid int, a valid port range string, or not None.

    Args:
        port_value: The port value to validate.
        field_name: The name of the field being validated.

    Raises:
        ValueError: If the port value is invalid (None, out of range, malformed range).
        TypeError: If port_value is not int or str.
    """
    if port_value is None:
        error_msg = f"'{field_name}' port cannot be None."
        logging.error(error_msg)
        raise ValueError(error_msg)

    if isinstance(port_value, int):
        validate_single_port(port_value, field_name)
    elif isinstance(port_value, str):
        if "-" in port_value:
            validate_port_range(port_value, field_name)
        else:
            # Single port string
            if not port_value.isdigit():
                error_msg = f"'{field_name}' port must be an integer."
                logging.error(error_msg)
                raise ValueError(error_msg)
            validate_single_port(int(port_value), field_name)
    else:
        error_msg = f"'{field_name}' port must be an integer or a string."
        logging.error(error_msg)
        raise TypeError(error_msg)


# ---------------------------------------------------------
#                   Task Management
# ---------------------------------------------------------


class TaskManagement(BaseModel):
    """Configuration for task management settings.

    Attributes:
        num_instances: Number of instances required.
        priority: Priority level of the task.
        utility_threshold_price: Utility threshold price per GPU.
    """

    num_instances: Optional[int] = None
    priority: Optional[str] = None
    utility_threshold_price: Optional[float] = None

    @field_validator("priority")
    def validate_priority(cls, priority_value: Optional[str]) -> Optional[str]:
        """Validates the priority field.

        Args:
            priority_value: The priority value to validate.

        Raises:
            ValueError: If the provided priority is invalid.

        Returns:
            The validated priority value or None if not provided.
        """
        if priority_value is not None:
            valid_priorities = {"critical", "high", "standard", "low"}
            if priority_value not in valid_priorities:
                raise ValueError(
                    f"Invalid priority '{priority_value}'. Valid options are: "
                    f"{', '.join(valid_priorities)}."
                )
        return priority_value


# ---------------------------------------------------------
#                  Advanced Specifications
# ---------------------------------------------------------


class AdvancedSpec(BaseModel):
    """Advanced specifications for resource optimization.

    Attributes:
        optimize: Optimization criterion.
        nearest_estimated_duration: Estimated duration in hours.
    """

    optimize: Optional[str] = None
    nearest_estimated_duration: Optional[int] = None

    @field_validator("optimize")
    def validate_optimize(cls, optimize_value: Optional[str]) -> Optional[str]:
        """Validates the optimize field.

        Args:
            optimize_value: The optimization criterion to validate.

        Raises:
            ValueError: If the optimization criterion is invalid.

        Returns:
            The validated optimize value or None if not provided.
        """
        if optimize_value is not None:
            valid_options = {"budget", "job_completion_time"}
            if optimize_value not in valid_options:
                raise ValueError(
                    f"Invalid optimize '{optimize_value}'. Valid options are: "
                    f"{', '.join(valid_options)}."
                )
        return optimize_value

    @field_validator("nearest_estimated_duration")
    def validate_nearest_estimated_duration(
        cls, duration_value: Optional[int]
    ) -> Optional[int]:
        """Validates the nearest_estimated_duration field.

        Args:
            duration_value: The estimated duration.

        Raises:
            ValueError: If the estimated duration is negative.

        Returns:
            The validated duration value or None if not provided.
        """
        if duration_value is not None and duration_value < 0:
            raise ValueError("nearest_estimated_duration must be non-negative.")
        return duration_value


# ---------------------------------------------------------
#                 Resources Specification
# ---------------------------------------------------------


class ResourcesSpecification(BaseModel):
    """Configuration for resources specification.

    Attributes:
        fcp_instance: FCP instance type.
        num_instances: Number of instances.
        gpu_type: Type of GPU required.
        num_gpus: Number of GPUs required.
        intranode_interconnect: Intra-node interconnect type.
        internode_interconnect: Inter-node interconnect type.
        advanced: Advanced specifications for resource optimization.
    """

    fcp_instance: Optional[str] = None
    num_instances: Optional[int] = None
    gpu_type: Optional[str] = None
    num_gpus: Optional[int] = None
    intranode_interconnect: Optional[str] = None
    internode_interconnect: Optional[str] = None
    advanced: Optional[AdvancedSpec] = None


# ---------------------------------------------------------
#                          Port
# ---------------------------------------------------------


class Port(BaseModel):
    """Represents a port configuration with optional external and internal mappings.

    Attributes:
        external: The external port number or range.
        internal: The internal port number or range.
        protocol: The protocol type, either 'tcp' or 'udp'.
    """

    external: Optional[Union[int, str]] = None
    internal: Optional[Union[int, str]] = None
    protocol: Literal["tcp", "udp"] = "tcp"

    model_config = ConfigDict(arbitrary_types_allowed=True)

    @classmethod
    def model_validate(cls, value: Any) -> "Port":
        """Performs custom validation logic for the Port model.

        This is called during model parsing and handles cases where the input is a
        single integer or string, corresponding to the same external and internal port.

        Args:
            value: The input value to validate.

        Raises:
            TypeError: If the input type is invalid.

        Returns:
            A Port instance with the corresponding attributes set.
        """
        if isinstance(value, dict):
            return super().model_validate(value)
        if isinstance(value, (int, str)):
            new_values = {"external": value, "internal": value}
            logging.debug(
                "Parsed raw input into external and internal ports: %s", new_values
            )
            return super().model_validate(new_values)

        error_msg = (
            f"Invalid type for port specification: {type(value)}. "
            "Expected int, str, or dict."
        )
        logging.error(error_msg)
        raise TypeError(error_msg)

    @field_validator("external", "internal")
    def validate_port_field(
        cls, port_value: Optional[Union[int, str]], field_info
    ) -> Optional[Union[int, str]]:
        """Validates the 'external' and 'internal' fields.

        Args:
            port_value: The port value to validate.
            field_info: Field validation information.

        Raises:
            ValueError: If the port value is invalid.

        Returns:
            The validated port value or None if not provided.
        """
        field_name = field_info.field_name
        try:
            validate_port_value(port_value, field_name=field_name)
        except (ValueError, TypeError) as exc:
            logging.error("Validation failed for '%s' port: %s", field_name, exc)
            raise
        return port_value

    def get_port_mappings(self) -> List[Tuple[int, int]]:
        """Expands and pairs external and internal ports.

        Raises:
            ValueError: If the port ranges do not match in length.

        Returns:
            A list of tuples with (external_port, internal_port).
        """
        external_ports = expand_port_spec(self.external)
        internal_ports = expand_port_spec(self.internal)

        if len(external_ports) != len(internal_ports):
            error_msg = (
                "Port ranges do not match in length for external "
                f"({self.external}) and internal ({self.internal}) ports."
            )
            logging.error(error_msg)
            raise ValueError(error_msg)

        port_mappings = list(zip(external_ports, internal_ports))
        logging.debug("Generated port mappings: %s", port_mappings)
        return port_mappings

    def __repr__(self) -> str:
        """Returns a string representation of the Port."""
        repr_str = (
            f"Port(external={self.external}, internal={self.internal}, "
            f"protocol='{self.protocol}')"
        )
        logging.debug("Generated __repr__: %s", repr_str)
        return repr_str

    def __eq__(self, other: Any) -> bool:
        """Checks equality with another Port instance.

        Args:
            other: The other object to compare.

        Returns:
            True if equal, False otherwise.
        """
        if not isinstance(other, Port):
            return NotImplemented
        is_equal = (
            self.external == other.external
            and self.internal == other.internal
            and self.protocol == other.protocol
        )
        logging.debug(
            "Comparing Ports: self=%s, other=%s, equal=%s", self, other, is_equal
        )
        return is_equal


# ---------------------------------------------------------
#                 Ephemeral Storage Config
# ---------------------------------------------------------


class EphemeralStorageConfig(BaseModel):
    """Ephemeral storage configuration.

    Attributes:
        type: Type of ephemeral storage.
        mounts: Mount points from local to remote directories.
    """

    type: Optional[str] = None
    mounts: Optional[Dict[str, str]] = None


# ---------------------------------------------------------
#                 Persistent Storage
# ---------------------------------------------------------


class PersistentStorageCreate(BaseModel):
    """Configuration for creating persistent storage.

    Attributes:
        volume_name: Name of the volume to create.
        size: Disk size in GB. Must be greater than 0.
        region_id: Region ID where storage is to be created.
        disk_interface: Disk interface type (e.g., 'Block', 'SCSI', etc.).
    """

    volume_name: Optional[str] = Field(
        default=None, description="Name of the volume to create."
    )
    size: Optional[int] = Field(
        default=None, description="Disk size in GB. Must be greater than 0."
    )
    region_id: Optional[str] = Field(
        default=None, description="Region ID where storage is to be created."
    )
    disk_interface: Optional[str] = Field(
        default=None, description="Disk interface type (e.g., 'Block', 'SCSI', etc.)."
    )


class PersistentStorageAttach(BaseModel):
    """Configuration for attaching existing persistent storage.

    Attributes:
        volume_name: Name of the volume to attach.
        region_id: Region ID where the storage exists.
    """

    volume_name: str = Field(..., description="Name of the volume to attach.")
    region_id: str = Field(..., description="Region ID where the storage exists.")


class PersistentStorage(BaseModel):
    """Persistent storage configuration.

    Attributes:
        mount_dir: Directory to mount the storage.
        attach: Details to attach an existing volume.
        create: Details to create a new volume.
    """

    mount_dir: Optional[str] = Field(
        default=None, description="Directory to mount the storage."
    )
    attach: Optional[PersistentStorageAttach] = Field(
        default=None, description="Details to attach an existing volume."
    )
    create: Optional[PersistentStorageCreate] = Field(
        default=None, description="Details to create a new volume."
    )


# ---------------------------------------------------------
#                       Networking
# ---------------------------------------------------------


class Networking(BaseModel):
    """Networking configuration.

    Attributes:
        dc_network_class: Data center network class.
    """

    dc_network_class: Optional[str] = None


# ---------------------------------------------------------
#                        Resources
# ---------------------------------------------------------


class Resources(BaseModel):
    """Resources configuration.

    Attributes:
        vCPU: Number of virtual CPUs.
        RAM: Amount of RAM in GB.
    """

    vCPU: Optional[int] = None
    RAM: Optional[int] = None


# ---------------------------------------------------------
#                 Container Image Config
# ---------------------------------------------------------


class ContainerImageConfig(BaseModel):
    """
    Container image configuration for specifying a Docker image to run on the VM instance.

    Attributes:
        image_name: Name of the Docker image (e.g. 'nginx:latest' or a local image tag).
        build_context: Optional local path for building the Docker image (if provided, the image is built rather than pulled).
        run_options: Optional command-line options for running the Docker container.
    """

    image_name: str
    build_context: Optional[str] = None
    run_options: Optional[str] = None


# ---------------------------------------------------------
#                     Config Model
# ---------------------------------------------------------


class ConfigModel(BaseModel):
    """Overall configuration model.

    Attributes:
        name: The name of the task.
        num_instances: Number of instances.
        task_management: Task management settings.
        resources_specification: Resources specification.
        ports: List of ports to expose.
        ephemeral_storage_config: Ephemeral storage settings.
        persistent_storage: Persistent storage settings.
        networking: Networking settings.
        resources: Resource allocations.
        startup_script: Script to run on startup.
        project_name: Foundry project name.
        ssh_key_name: Foundry SSH key name.
    """

    name: str
    num_instances: Optional[int] = None
    task_management: Optional[TaskManagement] = None
    resources_specification: ResourcesSpecification
    ports: Optional[List[Port]] = None
    ephemeral_storage_config: Optional[EphemeralStorageConfig] = None
    persistent_storage: Optional[PersistentStorage] = None
    networking: Optional[Networking] = None
    resources: Optional[Resources] = None
    startup_script: Optional[str] = None
    container_image: Optional[ContainerImageConfig] = None
    project_name: Optional[str] = None
    ssh_key_name: Optional[str] = None

    @field_validator("ports", mode="before")
    def validate_ports(cls, ports_value: Any) -> List[Dict[str, Any]]:
        """Validates and preprocesses the 'ports' field.

        Args:
            ports_value: The raw value of the 'ports' field.

        Raises:
            ValueError: If the input is invalid.

        Returns:
            A list of dictionaries representing the ports.
        """
        if ports_value is None:
            return []
        if not isinstance(ports_value, list):
            raise ValueError("Ports must be a list.")
        new_ports = []
        for item in ports_value:
            if isinstance(item, (int, str)):
                new_ports.append({"external": item, "internal": item})
            elif isinstance(item, dict):
                new_ports.append(item)
            else:
                raise ValueError(f"Invalid port specification: {item}")
        return new_ports

    @field_validator("name")
    def validate_name(cls, name_value: str) -> str:
        """Validates the name field.

        Args:
            name_value: The name value to validate.

        Raises:
            ValueError: If the name is empty or consists only of whitespace.

        Returns:
            The validated name.
        """
        if not name_value or not name_value.strip():
            raise ValueError("Name is a required field.")
        return name_value

    @model_validator(mode="after")
    def validate_model(cls, model_values: Dict[str, Any]) -> Dict[str, Any]:
        """Performs additional validations after model initialization.

        Args:
            model_values: The initialized model values.

        Returns:
            The validated model values.
        """
        # Additional model-level validations can be added here.
        return model_values
