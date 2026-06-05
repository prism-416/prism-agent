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

Set these runtime configuration values in OCI, not GitHub Actions:

```text
APP_ENV=prod
STATE_BACKEND=object_storage
QUEUE_BACKEND=oci
LLM_PROVIDER=gemini
GEMINI_API_KEY_SECRET_OCID=<vault secret ocid>
DEFAULT_GEMINI_MODEL=gemini-2.5-pro
MAX_RECURSION_DEPTH=10
OCI_QUEUE_OCID=<queue ocid>
OCI_QUEUE_MESSAGES_ENDPOINT=<queue messages endpoint>
OCI_BUCKET_NAME=<state bucket>
OCI_NAMESPACE=<object storage namespace>
OCI_PAYLOAD_BUCKET_NAME=<payload bucket, optional>
OCI_PAYLOAD_NAMESPACE=<payload namespace, optional>
PRISM_API_BASE_URL=<api base url>
PRISM_API_TOKEN_SECRET_OCID=<vault secret ocid>
```

Runtime follow-up events are published with the OCI Queue Python SDK by
`src/infrastructure/queue/oci_queue.py`.

Program-specific secrets should live in OCI Vault, not GitHub Actions. Create OCI Vault
secrets for:

```text
GEMINI_API_KEY
PRISM_API_TOKEN
```

Then set the secret OCIDs in OCI Function Application config or OCI Function config:

```text
GEMINI_API_KEY_SECRET_OCID
PRISM_API_TOKEN_SECRET_OCID
```

At runtime, `Settings.from_env()` reads those OCIDs and resolves the current secret values
with the OCI Secrets API. Direct `GEMINI_API_KEY` and `PRISM_API_TOKEN` env vars still work
for local development and take precedence when present.

The OCI Function application must already exist. The function itself does not need to exist:
the workflow creates it on first deploy and updates the image on later deploys.

OCI IAM policies must allow the deploy user to manage functions, read the function
application, and manage the target OCIR repository. The deployed function's resource
principal must be allowed to read the configured Vault secrets and read/write the configured
queue and Object Storage buckets.

This workflow creates or updates the function resource only. Configure any external
invocation source, such as API Gateway, Events, Connector Hub, or another service integration,
outside this workflow unless that infrastructure is later moved into Terraform.
