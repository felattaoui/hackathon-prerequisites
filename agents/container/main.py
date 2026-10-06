"""Minimal hosted agent (Responses protocol), optionally with a Foundry toolbox.

Without toolbox (first trial): follows the Learn "Agent Framework hosted agents" sample — one Agent instance
passed to ResponsesHostServer.
With toolbox (TOOLBOX_ENDPOINT or TOOLBOX_NAME set; scenarios S07/S12, not yet run on Azure): per-request agent
factory from the "Use a toolbox with a hosted agent" sample. The installed hosting package rejects
history_source="agent_server" for a factory (observed 2026-10-01), so the factory uses history_source="agent".
Synthetic prompts only. The agent repeats the nonce it receives so that answers are checkable.
"""
import asyncio
import os
from contextlib import AsyncExitStack

from agent_framework import Agent
from agent_framework.foundry import FoundryChatClient
from agent_framework_foundry_hosting import ResponsesHostServer
from azure.ai.agentserver.core import AgentConfig

INSTRUCTIONS = (
    "You are a test agent. Always start your answer with the exact token that follows 'NONCE:' in the user "
    "message. If the user message contains 'CALL_WHOAMI', call the whoami tool with that nonce and report the "
    "tool result or the exact error you received. Keep answers under 60 words."
)


def sync_credential():
    from azure.identity import DefaultAzureCredential, ManagedIdentityCredential
    if AgentConfig.from_env().is_hosted:
        return ManagedIdentityCredential(client_id=os.environ.get("FOUNDRY_AGENT_INSTANCE_CLIENT_ID"))
    return DefaultAzureCredential()


def build_plain_agent() -> Agent:
    client = FoundryChatClient(project_endpoint=os.environ["FOUNDRY_PROJECT_ENDPOINT"],
                               model=os.environ["AZURE_AI_MODEL_DEPLOYMENT_NAME"], credential=sync_credential())
    return Agent(client=client, instructions=INSTRUCTIONS, default_options={"store": False})


def create_toolbox_agent() -> Agent:
    from agent_framework.foundry import FoundryToolbox
    from azure.ai.agentserver.core import get_request_context
    from azure.identity.aio import AzureCliCredential, ManagedIdentityCredential

    credential = (ManagedIdentityCredential(client_id=os.environ.get("FOUNDRY_AGENT_INSTANCE_CLIENT_ID"))
                  if AgentConfig.from_env().is_hosted else AzureCliCredential())

    class RequestClient(FoundryChatClient):
        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc_value, traceback):
            async with AsyncExitStack() as cleanup:
                cleanup.push_async_callback(credential.close)
                cleanup.push_async_callback(self.project_client.close)
                cleanup.push_async_callback(self.client.close)

    client = RequestClient(project_endpoint=os.environ["FOUNDRY_PROJECT_ENDPOINT"],
                           model=os.environ["AZURE_AI_MODEL_DEPLOYMENT_NAME"], credential=credential,
                           default_headers=get_request_context().platform_headers())
    return Agent(client=client, instructions=INSTRUCTIONS, tools=FoundryToolbox(credential))


def main() -> None:
    if os.environ.get("TOOLBOX_ENDPOINT") or os.environ.get("TOOLBOX_NAME"):
        server = ResponsesHostServer(agent=create_toolbox_agent, history_source="agent")
        asyncio.run(server.run_async())
    else:
        ResponsesHostServer(build_plain_agent()).run()


if __name__ == "__main__":
    main()
