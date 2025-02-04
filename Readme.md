# Installation and Quickstart

NOTE: Flow is currently in preview. This git repo is under active development and may be subject to frequent changes. 
If you are using Flow, please send a quick note to **contact@mlfoundry.com** so we can keep you updated on the latest changes. 

## Installation

1. Ensure you have Python 3.11 or higher installed.  
2. Install dependencies either by:  
   - Using pdm (preferred):  
     » pdm install  
   - Or manually with pip:  
     » pip install -r requirements.txt

## Quick-Start

1. **Authentication via Environment Variables:**  
   Configure Foundry authentication variables which will be pulled in via the settings in `src/flow/config/base_settings.py`:

   **Option 1 (Recommended): API Key**  
     - `export FOUNDRY_API_KEY='your_api_key'`  (Found in FCP Console → Settings → API Keys tab)

   **Option 2: Email/Password**  
     - `export FOUNDRY_EMAIL='your_email@example.com'`  
     - `export FOUNDRY_PASSWORD='your_password'`

   - Option 1 (Recommended): API Key

     - export FOUNDRY_API_KEY='your_api_key' # Found in FCP Console → Settings → API Keys tab

   - Option 2: Email/Password
     - export FOUNDRY_EMAIL='your_email@example.com'
     - export FOUNDRY_PASSWORD='your_password'


2. Get familiary with the flow_example.yaml file, which contains a comprehensive example of a Foundry task definition via the CLI. 

3. Submit an example task using flow_example.yaml:
   » `flow submit flow_example.yaml --project-name your_project_name --ssh-key-name your_ssh_key_name)`

4. Check the status of your submitted task:
   » `flow status --project-name your_project_name --ssh-key-name your_ssh_key_name`

Note, we have yet to tune the CLI to be more ergonomic and stylish. More work to do on the UI. Placeholder UI logic is in the 'formatters' folder.

5. Look through and experiment with the flow_sdk_quickstart.ipynb file, which contains an interactive notebookquickstart guide to using the Foundry SDK.

---

# FCP Hierarchical Structures

## FCP convenience taxonomy

A **task** is an atomic, irreducible unit that maps to an all-or-nothing _request_ for infrastructure from FCP. 

Most users think of their experiments not in terms of tasks, per se, but rather in terms of **jobs**. A **job** is a meta-concept which subsumes that of tasks. A job can be composed of: 

* Logic around a <u>single task</u>. A single task request specifies a single <u>fcp_instance</u>, or explicit GPU type and networking config. However, when multiple types can be viable, but with a preferred order or optimization objective (minimal cost, fastest job completion time), advanced users can add metadata to give FCP the leverage to optimize on the user's behalf. 
* Specification of <u>multiple tasks</u> to be executed independently or in succession. 


### Tasks

A **task** specification maps to a requests for infrastructure from FCP. 

FCP's scheduling mechanism is inspired by economic concepts of **utility** and **surplus maximation**. 

We internally leverage a **second-price auction** mechanism, which helps ensure that infrastructure is put to <u>best-use</u> whilst allowing users to state their job's *utility* <u>truthfully</u>, since they won't be charged their **utility_threshold_price**, but rather the **second_price** of the job their job is displacing when there is overlapping demand for all resources (or the minimum threshold price, when excess nodes are available.) 

Under this mechanism, the game-theoretically optimal approach is to state one's utility_threshold_price honestly. 

This all allows Foundry to <u>maximize global utility across all users and jobs</u>, and can be more efficient than a fixed_price system. 

### Attributes of Tasks

Foundry **tasks** are defined by the following attributes:

```YAML
# -------------------------------------------------------------------------
# Foundry Task Configuration
# -------------------------------------------------------------------------
# This file provides a comprehensive configuration for defining a Foundry task.
# Each section explains both required and optional fields, guiding you in
# tailoring your workflow. Update or uncomment fields according to your needs.

# -------------------------------------------------------------------------
# [1] Task Name (Optional)
# -------------------------------------------------------------------------
# A human-readable, descriptive name for this task. Appears in Foundry UIs.
# If omitted, a default or auto-generated name may be used.
name: flow-sdk-batch-task-example

# -------------------------------------------------------------------------
# [2] Authentication Parameters
# -------------------------------------------------------------------------
# Specify the relevant Foundry project name and SSH key name.
project_name: my-foundry-project
ssh_key_name: my-ssh-key

# -------------------------------------------------------------------------
# [3] Foundry Task Management
# -------------------------------------------------------------------------
# Parameters for how Foundry schedules, prices, and manages this task.
task_management:

  # -----------------------------------------------------------------------
  # 2.1 Priority (Optional)
  # -----------------------------------------------------------------------
  # Defines a shorthand for setting a utility price threshold.
  # Foundry uses these utility thresholds to optimize global utility across all tasks.
  # Valid options: [critical, high, standard, low].
  # If not specified, defaults to 'standard' or the configured default in the 'flow_config' file
  # Set values in the 'flow_config' file to override defaults.
  priority: low  # Example for lower priority 'batch' tasks

  # -----------------------------------------------------------------------
  # 2.2 Explicit Utility Threshold Price (Optional)
  # -----------------------------------------------------------------------
  # A direct per-GPU limit price (spot_bid). If left out, Foundry calculates
  # the spot_bid based on the 'priority' field above. 
  # Note, Foundry utility maximization is based on a second price auction, so users aren't charged their utility price but rather a guaranteed lower rate.
  # Thus, users can set the utility threshold price to their 'true utility threshold'.
  utility_threshold_price: 17.92 # Example low priority custom price for a batch task, 2.24/GPU/hour for H100 SXM5

# -------------------------------------------------------------------------
# [4] Resource Configuration
# -------------------------------------------------------------------------
# Specify the compute resources needed for the task.
resources_specification:

  # -----------------------------------------------------------------------
  # 3.1 Instance Type
  # -----------------------------------------------------------------------
  # The name of the GPU instance type. Adjust according to your project's
  # performance requirements or availability in a given Foundry cluster.
  fcp_instance: h100.8x.SXM5.IB

  # -----------------------------------------------------------------------
  # 3.2 Number of Instances
  # -----------------------------------------------------------------------
  # Total instances to request. By default, a single instance is allocated.
  # Increase for tasks that need multi-instance processing in an all-or-nothing
  # configuration (e.g., distributed training).
  num_instances: 1
  num_gpus: 8 

  # -----------------------------------------------------------------------
  # 3.3 Cluster ID (Optional)
  # -----------------------------------------------------------------------
  # Restrict task placement to a specific cluster or region if needed.
  # Uncomment and set the cluster_id to choose a region (e.g., "us-central1").
  cluster_id: us-central1-b  # Where H100 SXM5 instances are available

# -------------------------------------------------------------------------
# [5] Ports to Expose (Optional)
# -------------------------------------------------------------------------
# Define which ports or port ranges to expose for external access, such as for
# Jupyter or TensorBoard. You can specify single ports (e.g., "8080"), ranges
# (e.g., "6006-6010"), or pair external and internal ports explicitly.
ports:
  - 8080              # Single port exposed externally and internally
  - 6006-6010         # Expose port range externally and internally
  # You can also map external ports to different internal ports or ranges:
  - external: 8441
    internal: 8001
  - external: 8442
    internal: 8002
  - external: 8443-8445
    internal: 8003-8005

# -------------------------------------------------------------------------
# [6] Storage Configuration (Optional)
# -------------------------------------------------------------------------
# Configure persistent storage volumes to be mounted on the task instance.
# This is particularly useful for sharing data, storing checkpoints, or
# accessing large datasets. Uncomment and fill out the relevant fields.

persistent_storage:
  # -----------------------------------------------------------------------
  # 5.1 Create a New Volume (Optional)
  # -----------------------------------------------------------------------
  # Provide the desired volume name, size, type, region, and mount point.
  create:
    volume_name: my-new-volume           # Custom label for your volume
    storage_type: block                  # Options: 'block' or 'nfs'
    size: 1                              # Size in GB
    mount_point: /mnt/my-new-volume      # Where volume is mounted on the instance
    region_id: us-central1-b             # Region/zone for storage
    disk_interface: Block                # Usually 'Block' for block-based volumes

  # -----------------------------------------------------------------------
  # 5.2 Attach an Existing Volume (Optional)
  # -----------------------------------------------------------------------
  # Instead of creating a new volume, attach an existing one by name.
  # Uncomment and fill out to attach your pre-existing storage.
  # attach:
  #   volume_name: existing-volume-name
  #   mount_point: /mnt/existing-volume

# -------------------------------------------------------------------------
# [7] Startup Script (Optional)
# -------------------------------------------------------------------------
# Provide commands that will execute upon instance initialization, enabling
# you to install dependencies or run initial setup tasks automatically.
startup_script: |
  #!/bin/bash
  echo "Starting setup..."
  pip install -r requirements.txt
  echo "Setup complete."

# -------------------------------------------------------------------------
# [8] Versioning
# -------------------------------------------------------------------------
# Indicates the format version of this configuration file.
version: 1.2
```
