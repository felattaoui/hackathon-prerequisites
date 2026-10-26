"""Hosted agent with a Foundry toolbox (scenario S07, test agent hapq-mcp).

Supported combination used here (verified 2026-10-02):
  agent-framework-foundry-hosting 1.0.0b260918 (latest on PyPI) -> pattern from the package's own FoundryToolbox
  docstring: ONE Agent instance with tools=FoundryToolbox(credential), passed to ResponsesHostServer(agent).
  The package injects x-agent-foundry-call-id on every outbound toolbox request from the request context.
Documentation divergence (kept on record): Learn "Microsoft Foundry Toolbox" (agent-framework, Python) shows a
per-request factory ResponsesHostServer(agent=create_agent, history_source="agent_server"); versions examined: 1.0.0a260709, 1.0.0b260730, 1.0.0b260827, 1.0.0b260910 and 1.0.0b260918 (latest on 2026-10-02); 
none accepts a factory. The other 19 published versions were not examined: observed "'function' object has no attribute 'create_session'".
"""
import os

from agent_framework import Agent
from agent_framework.foundry import FoundryChatClient, FoundryToolbox
from agent_framework_foundry_hosting import ResponsesHostServer
from azure.ai.agentserver.core import AgentConfig

INSTRUCTIONS = (
    "You are a test agent. Always start your answer with the exact token that follows 'NONCE:' in the user "
    "message. If the user message contains 'CALL_WHOAMI', call the whoami tool with that nonce and report the "
    "tool result or the exact error you received. Keep answers under 60 words."
)


def credential():
    from azure.identity import DefaultAzureCredential, ManagedIdentityCredential
    if AgentConfig.from_env().is_hosted:  # the agent's own identity, as in the working reference agent
        return ManagedIdentityCredential(client_id=os.environ.get("FOUNDRY_AGENT_INSTANCE_CLIENT_ID"))
    return DefaultAzureCredential()


def main() -> None:
    cred = credential()
    agent = Agent(
        client=FoundryChatClient(project_endpoint=os.environ["FOUNDRY_PROJECT_ENDPOINT"],
                                 model=os.environ["AZURE_AI_MODEL_DEPLOYMENT_NAME"], credential=cred),
        instructions=INSTRUCTIONS,
        tools=FoundryToolbox(cred),  # resolves TOOLBOX_ENDPOINT or FOUNDRY_PROJECT_ENDPOINT + TOOLBOX_NAME
        default_options={"store": False},
    )
    ResponsesHostServer(agent).run()


if __name__ == "__main__":
    main()
