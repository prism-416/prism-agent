# OCI Function Deployment

The DEV deployment workflow builds the Prism agent as an OCI Functions image, pushes it to
OCIR, and creates or updates a function in an existing OCI Functions application.

Workflow file:

```text
.github/workflows/deploy-oci-function-dev.yml
```

Required GitHub secrets:

```text
OCI_CLI_USER
OCI_CLI_TENANCY
OCI_CLI_FINGERPRINT
OCI_CLI_KEY_CONTENT
OCI_CLI_REGION
OCI_AUTH_TOKEN
OCI_COMPARTMENT_OCID
OCI_FUNCTION_APPLICATION_OCID
```

Optional GitHub variables:

```text
OCI_FUNCTION_MEMORY_MB
OCI_FUNCTION_NAME
OCI_FUNCTION_PLATFORM
OCI_FUNCTION_TIMEOUT_SECONDS
OCIR_REPOSITORY_NAME
```

The deployment workflow intentionally does not write program-specific function config.
It only builds/pushes the image and creates or updates the function resource. Runtime
configuration belongs in OCI Function Application config or OCI Function config.
OCI exposes those configuration values to the function as environment variables.

Set these runtime configuration values in OCI, not GitHub Actions:

```text
APP_ENV=prod
STATE_BACKEND=prism_api
QUEUE_BACKEND=oci
LLM_PROVIDER=gemini
GEMINI_API_KEY=<gemini api key>
DEFAULT_GEMINI_MODEL=gemini-3.1-pro-preview
MAX_RECURSION_DEPTH=10
OCI_AUTH_MODE=instance_principal
OCI_QUEUE_OCID=<queue ocid>
OCI_QUEUE_MESSAGES_ENDPOINT=<queue messages endpoint>
OCI_OBJECT_STORAGE_BUCKET_NAME=<payload bucket>
OCI_OBJECT_STORAGE_NAMESPACE=<object storage namespace>
PRISM_API_BASE_URL=<api base url>
PRISM_API_TOKEN=<internal api token>
```

Runtime follow-up events are published with the OCI Queue Python SDK by
`src/infrastructure/queue/oci_queue.py`.

Program-specific runtime credentials should live in OCI Function Application config or
OCI Function config, not GitHub Actions. Configure these values directly:

```text
GEMINI_API_KEY
PRISM_API_TOKEN
```

At runtime, `Settings.from_env()` reads `GEMINI_API_KEY` and `PRISM_API_TOKEN` from the
function environment that OCI derives from the configured application/function values.
The same env vars still work for local development through an untracked `.env` file or
shell environment.

The OCI Function application must already exist. The function itself does not need to exist:
the workflow creates it on first deploy and updates the image on later deploys.

OCI IAM policies must allow the deploy user to manage functions, read the function
application, and manage the target OCIR repository. The deployed function's resource
principal must be allowed to read/write the configured queue and Object Storage buckets.

This workflow creates or updates the function resource only. Configure any external
invocation source, such as API Gateway, Events, Connector Hub, or another service integration,
outside this workflow unless that infrastructure is later moved into Terraform.
