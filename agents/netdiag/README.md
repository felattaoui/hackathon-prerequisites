# Third-party code: network diagnostic agent

Copied without functional change from microsoft-foundry/foundry-samples,
`samples/python/hosted-agents/bring-your-own/invocations/diagnostic-agent/src/diagnostic-agent-python-invocations`,
commit `d807d9a0b2e633040d896cd0ddd58c95e14ac6f1` (tests and Dockerfile not copied; `requirements.txt` reduced to the
runtime dependencies). Deployed in ZIP mode (no container registry, no model call) to probe DNS/TCP/TLS/HTTP from the
hosted-agent runtime in the private environment.

Licensed under the MIT License, Copyright (c) 2025 Microsoft Corporation: see `LICENSE-foundry-samples.txt`.
