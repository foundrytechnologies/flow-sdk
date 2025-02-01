from __future__ import annotations
import os
import logging
from typing import Any, Dict, List, Optional, Tuple

import yaml
from jinja2 import Environment, BaseLoader, TemplateError
import base64
import gzip
from io import BytesIO

# Add imports from models.py
from flow.task_config.models import Port, PersistentStorage, EphemeralStorageConfig


# -------------------------------------------------------------
# Custom Exceptions for Startup Script Builder
# -------------------------------------------------------------
class StartupScriptBuilderError(Exception):
    """Base exception class for startup script builder errors."""


class TemplatesFileNotFoundError(StartupScriptBuilderError):
    """Exception raised when the templates YAML file cannot be found."""


class TemplateKeyNotFoundError(StartupScriptBuilderError):
    """Exception raised when a requested template key is missing in the YAML data."""


# -------------------------------------------------------------
# A no-op logger to avoid spamming logs if none is provided
# -------------------------------------------------------------
class NoOpLogger:
    def debug(self, msg: str, *args, **kwargs):
        pass

    def info(self, msg: str, *args, **kwargs):
        pass

    def warning(self, msg: str, *args, **kwargs):
        pass

    def error(self, msg: str, *args, **kwargs):
        pass


# -------------------------------------------------------------
# ScriptSegmentBuilder: Base class for building script segments
# -------------------------------------------------------------
class ScriptSegmentBuilder:
    """Base class for building script segments."""

    def render_segment(self) -> str:
        """Returns a string containing shell commands or script text for the segment."""
        raise NotImplementedError("Subclasses must implement this method.")


# -------------------------------------------------------------
# JinjaTemplateSegmentBuilder: Renders a Jinja template segment
# -------------------------------------------------------------
class JinjaTemplateSegmentBuilder(ScriptSegmentBuilder):
    """
    Builds a script segment by rendering a Jinja template string with a given context.
    """

    def __init__(
        self,
        template_str: str,
        template_context: Dict[str, Any],
        logger: Optional[logging.Logger] = None,
    ) -> None:
        """Initialize a JinjaTemplateSegmentBuilder.

        Args:
            template_str: The raw Jinja template string.
            template_context: Dictionary of variables to substitute in the template.
            logger: Optional logger instance. If omitted, a NoOpLogger is used.
        """
        self.template_str = template_str
        self.template_context = template_context
        self.logger = logger if logger else NoOpLogger()

    def render_segment(self) -> str:
        """Renders the Jinja template and returns the final string."""
        self.logger.debug("Starting render_segment in JinjaTemplateSegmentBuilder.")
        try:
            jinja_env = Environment(loader=BaseLoader(), autoescape=False)
            template = jinja_env.from_string(self.template_str)
            rendered = template.render(**self.template_context)
            self.logger.debug(
                "Template successfully rendered. Context: %s", self.template_context
            )
            self.logger.debug("Rendered output:\n%s", rendered)
            return rendered
        except TemplateError as te:
            error_msg = f"Error rendering Jinja template with context {self.template_context}: {te}"
            self.logger.error(error_msg)
            raise StartupScriptBuilderError(error_msg) from te


# -------------------------------------------------------------
# StartupScriptBuilder: Orchestrates all script segments
# -------------------------------------------------------------
class StartupScriptBuilder:
    """
    A flexible builder that assembles a final startup script by:
      1. Loading Jinja templates from a YAML file.
      2. Injecting segments for port forwarding, ephemeral storage, etc.
      3. Combining them into a single script string.
    """

    def __init__(
        self,
        base_script: str = "#!/bin/bash\nset -ex\n",
        templates_file_path: Optional[str] = None,
        logger: Optional[logging.Logger] = None,
    ) -> None:
        """
        Args:
            base_script: Initial script text (e.g., shebang + shell flags).
            templates_file_path: Path to the YAML file containing Jinja templates.
            logger: Optional logger instance.
        """
        self.logger: logging.Logger = logger if logger else NoOpLogger()
        self.logger.debug(
            msg="Initializing StartupScriptBuilder with base_script=%r, templates_file_path=%r",
            base_script=base_script,
            templates_file_path=templates_file_path,
        )

        self.base_script = base_script
        self.segments: List[ScriptSegmentBuilder] = []
        self.templates: Dict[str, str] = {}

        self._load_templates(templates_file_path)
        self.logger.debug(
            "StartupScriptBuilder initialized successfully with %d templates loaded.",
            len(self.templates),
        )

    def _load_templates(self, templates_file_path: Optional[str]) -> None:
        """
        Loads Jinja templates from a YAML file. Populates self.templates dict.

        Raises:
            TemplatesFileNotFoundError: If the specified file does not exist.
            StartupScriptBuilderError: If templates cannot be loaded or no 'templates' key is found.
        """
        if not templates_file_path:
            current_dir = os.path.dirname(os.path.abspath(__file__))
            templates_file_path = os.path.join(
                current_dir, "startup_script_templates.yaml"
            )
            self.logger.debug(
                "No templates_file_path provided, using default path: %s",
                templates_file_path,
            )

        if not os.path.exists(templates_file_path):
            error_msg = f"Templates file not found: {templates_file_path}"
            self.logger.error(error_msg)
            raise TemplatesFileNotFoundError(error_msg)

        self.logger.debug("Loading templates from file: %s", templates_file_path)
        try:
            with open(templates_file_path, "r", encoding="utf-8") as f:
                data = yaml.safe_load(f) or {}
        except OSError as e:
            error_msg = f"Failed to read template file '{templates_file_path}': {e}"
            self.logger.error(error_msg)
            raise StartupScriptBuilderError(error_msg) from e
        except yaml.YAMLError as ye:
            error_msg = f"YAML syntax error in file '{templates_file_path}': {ye}"
            self.logger.error(error_msg)
            raise StartupScriptBuilderError(error_msg) from ye

        if "templates" not in data:
            error_msg = (
                f"No 'templates' key found in the YAML file '{templates_file_path}'"
            )
            self.logger.error(error_msg)
            raise StartupScriptBuilderError(error_msg)

        # Populate self.templates dict
        for key, val in data["templates"].items():
            if isinstance(val, str):
                self.templates[key] = val
                self.logger.debug("Loaded template for key '%s'", key)
            else:
                self.logger.warning("Skipping non-string template for key '%s'", key)

        self.logger.info("Successfully loaded templates from: %s", templates_file_path)

    # ----------------------------------
    # Methods to inject each segment
    # ----------------------------------

    def inject_ports(self, ports: List[Port]) -> None:
        """
        Injects port forwarding logic using the 'port_forwarding_segment' template.

        Args:
            ports: A list of Port objects representing external/internal ports.
        """
        self.logger.debug("inject_ports called with %d ports.", len(ports))
        if not ports:
            self.logger.info("No ports provided; skipping port forwarding injection.")
            return

        template_key = "port_forwarding_segment"
        if template_key not in self.templates:
            warning_msg = f"Template '{template_key}' not found in loaded templates."
            self.logger.warning(warning_msg)
            return

        all_mappings: List[Tuple[int, int]] = []
        for p in ports:
            pm = p.get_port_mappings()
            self.logger.debug("Port object %s expanded to mappings: %s", p, pm)
            all_mappings.extend(pm)

        if not all_mappings:
            self.logger.info(
                "No valid port mappings found; skipping port forwarding injection."
            )
            return

        segment_builder = JinjaTemplateSegmentBuilder(
            template_str=self.templates[template_key],
            template_context={"port_mappings": all_mappings},
            logger=self.logger,
        )
        self.segments.append(segment_builder)
        self.logger.info(
            "Port forwarding segment successfully injected. Mappings: %s", all_mappings
        )

    def inject_ephemeral_storage(
        self, ephemeral_config: Optional[EphemeralStorageConfig]
    ) -> None:
        """
        Inject ephemeral storage logic using the 'ephemeral_storage_segment' template.

        Args:
            ephemeral_config: Configuration detailing ephemeral storage mounts.
        """
        self.logger.debug("inject_ephemeral_storage called with %s", ephemeral_config)
        if not ephemeral_config or not ephemeral_config.mounts:
            self.logger.info(
                "No ephemeral storage config or mounts found; skipping ephemeral injection."
            )
            return

        template_key = "ephemeral_storage_segment"
        if template_key not in self.templates:
            warning_msg = f"Template '{template_key}' not found in loaded templates."
            self.logger.warning(warning_msg)
            return

        segment_builder = JinjaTemplateSegmentBuilder(
            template_str=self.templates[template_key],
            template_context={"ephemeral_mounts": ephemeral_config.mounts},
            logger=self.logger,
        )
        self.segments.append(segment_builder)
        self.logger.info(
            "Ephemeral storage segment successfully injected with mounts: %s",
            ephemeral_config.mounts,
        )

    def inject_persistent_storage(
        self, persistent_storage: Optional[PersistentStorage]
    ) -> None:
        """
        Inject persistent storage logic using the 'persistent_storage_segment' template.
    
        Args:
            persistent_storage: Configuration for persistent storage, e.g. block or file share.
        """
        self.logger.debug(
            "inject_persistent_storage called with %s", persistent_storage
        )
        if not persistent_storage:
            self.logger.info(
                "No persistent storage config provided; skipping injection."
            )
            return
    
        # Support backward compatibility:
        # If persistent_storage has a 'mount_points' attribute, use it.
        # Otherwise, if it has a 'mount_dir' attribute, wrap it into a list.
        mount_points = getattr(persistent_storage, "mount_points", None)
        if mount_points is None:
            mount_dir = getattr(persistent_storage, "mount_dir", None)
            if mount_dir is not None:
                mount_points = [mount_dir]
        if not mount_points:
            self.logger.info(
                "No persistent storage mount_points provided; skipping injection."
            )
            return
    
        template_key = "persistent_storage_segment"
        if template_key not in self.templates:
            warning_msg = f"Template '{template_key}' not found in loaded templates."
            self.logger.warning(warning_msg)
            return
    
        self.logger.debug("Using mount_points: %s", mount_points)
        segment_builder = JinjaTemplateSegmentBuilder(
            template_str=self.templates[template_key],
            template_context={"mount_points": mount_points},
            logger=self.logger,
        )
        self.segments.append(segment_builder)
        self.logger.info(
            "Persistent storage segment injected for mount_points='%s'.",
            mount_points,
        )

    def inject_custom_script(self, custom_script: Optional[str]) -> None:
        """
        Injects a user-provided script (verbatim) at the end of the final startup script.

        Args:
            custom_script: Shell commands or script content as a string.
        """
        self.logger.debug(
            "inject_custom_script called with script length=%s",
            len(custom_script or ""),
        )
        if not custom_script:
            self.logger.info("No custom script provided; skipping injection.")
            return

        class CustomScriptSegment(ScriptSegmentBuilder):
            def __init__(self, script: str, logger: logging.Logger):
                self.script = script
                self.logger = logger

            def render_segment(self) -> str:
                self.logger.debug("Rendering custom script segment.")
                return f"# --- Custom Startup Script ---\n{self.script}\n"

        self.segments.append(CustomScriptSegment(custom_script, self.logger))
        self.logger.info("Custom script segment injected.")

    def inject_bootstrap_script(self, full_script: str) -> None:
        """
        Compresses, base64-encodes the full script, and injects it into the bootstrap template.

        Args:
            full_script: The complete startup script generated by other segments.
        """
        self.logger.debug("inject_bootstrap_script called.")

        template_key = "bootstrap_script_segment"
        if template_key not in self.templates:
            error_msg = f"Template '{template_key}' not found in loaded templates."
            self.logger.error(error_msg)
            raise TemplateKeyNotFoundError(error_msg)

        self.logger.debug("Compressing and encoding the full startup script.")
        with BytesIO() as compressed_io:
            with gzip.GzipFile(fileobj=compressed_io, mode="wb") as gz_file:
                gz_file.write(full_script.encode("utf-8"))
            compressed_data = compressed_io.getvalue()

        encoded_script = base64.b64encode(compressed_data).decode("utf-8")
        self.logger.debug("Full script compressed and base64-encoded.")

        segment_builder = JinjaTemplateSegmentBuilder(
            template_str=self.templates[template_key],
            template_context={"encoded_script": encoded_script},
            logger=self.logger,
        )
        self.segments.append(segment_builder)
        self.logger.info("Bootstrap script segment injected.")

    # ----------------------------------
    # Build final startup script
    # ----------------------------------
    def build_script(self) -> str:
        """Combine the base script and all injected segments into a single startup script.

        Returns:
            The complete bash startup script as a string.
        """
        self.logger.debug("Beginning build_script.")
        final_script_lines = [self.base_script]

        for idx, segment in enumerate(self.segments):
            self.logger.debug(
                "Rendering segment #%d: %s", idx, segment.__class__.__name__
            )
            try:
                segment_text = segment.render_segment().rstrip("\n")
            except StartupScriptBuilderError as e:
                error_msg = f"Error rendering segment #{idx} ({segment.__class__.__name__}): {e}"
                self.logger.error(error_msg)
                raise
            final_script_lines.append(segment_text)

        combined_script = "\n".join(final_script_lines)
        self.logger.info(
            "Startup script successfully built. Total segments: %d", len(self.segments)
        )
        return combined_script


# -------------------------------------------------------------
# Example usage
# -------------------------------------------------------------
# if __name__ == "__main__":
#     logging.basicConfig(level=logging.DEBUG)
#     logger = logging.getLogger("startup_script_builder_example")
#
#     # Suppose we put startup_script_templates.yaml in the same directory:
#     current_dir = os.path.dirname(os.path.abspath(__file__))
#     template_path = os.path.join(current_dir, "startup_script_templates.yaml")
#
#     # Instantiate the builder
#     builder = StartupScriptBuilder(
#         base_script="#!/bin/bash\nset -e\n",
#         templates_file_path=template_path,
#         logger=logger,
#     )
#
#     # Example 1: inject ports
#     ports = [
#         Port(external=8080, internal=80),
#         Port(external=6006, internal=6006),
#     ]
#     builder.inject_ports(ports)
#
#     # Example 2: ephemeral storage config
#     ephemeral_config = EphemeralStorageConfig(mounts={
#         "/local/tmp": "/mnt/scratch",
#         "/data/cache": "/mnt/cache-ephemeral"
#     })
#     builder.inject_ephemeral_storage(ephemeral_config)
#
#     # Example 3: persistent storage config
#     persistent_storage = PersistentStorage(mount_dir="/mnt/my-block-volume")
#     builder.inject_persistent_storage(persistent_storage)
#
#     # Example 4: optional custom script
#     builder.inject_custom_script("echo 'Hello from a custom script!'")
#
#     # Generate final script
#     startup_script = builder.build_script()
#     print("==== FINAL STARTUP SCRIPT ====")
#     print(startup_script)
