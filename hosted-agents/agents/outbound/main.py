"""Outbound-to-private test agent (scenario S22): no model call.

Reads a synthetic blob from a storage account whose public access is disabled, with the agent's OWN identity
(FOUNDRY_AGENT_INSTANCE_CLIENT_ID), and returns its content. Input: "READ <account>/<container>/<blob>".
Built on the hosted-agent Responses protocol library (Learn quickstart "deploy your own code" pattern).
"""
import asyncio
import os

from azure.ai.agentserver.responses import CreateResponse, ResponseContext, ResponsesAgentServerHost, TextResponse

app = ResponsesAgentServerHost()


@app.response_handler
async def handler(request: CreateResponse, context: ResponseContext, _cancellation_signal: asyncio.Event):
    text = (await context.get_input_text() or "").strip()
    try:
        account, container, blob = text.split(" ", 1)[1].split("/", 2)
        from azure.identity.aio import AzureCliCredential, ManagedIdentityCredential
        from azure.storage.blob.aio import BlobClient
        instance = os.environ.get("FOUNDRY_AGENT_INSTANCE_CLIENT_ID")  # set by the platform when hosted
        async with (ManagedIdentityCredential(client_id=instance) if instance else AzureCliCredential()) as cred:
            async with BlobClient(f"https://{account}.blob.core.windows.net", container, blob, credential=cred) as client:
                data = await (await client.download_blob()).readall()
        reply = "BLOB:" + data.decode("utf-8", "replace")[:200]
    except Exception as exc:  # reported verbatim (type + message) so the cause is attributable
        reply = f"ERROR:{type(exc).__name__}: {str(exc)[:300]}"
    return TextResponse(context, request, text=reply)


app.run()
