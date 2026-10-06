"""LangGraph hosted agent on the Responses protocol (scenario S08).

Adapted from the Learn page "LangChain/LangGraph hosted agents" (langchain-azure-ai[hosting]).
The model deployment name comes from FOUNDRY_MODEL_NAME (set in the version's environment variables).
"""
import os

from azure.ai.projects import AIProjectClient
from azure.identity import DefaultAzureCredential, ManagedIdentityCredential, get_bearer_token_provider
from langchain.agents import create_agent
from langchain_openai import ChatOpenAI

from langchain_azure_ai.agents.hosting import ResponsesHostServer

_AZURE_AI_SCOPE = "https://ai.azure.com/.default"
SYSTEM_PROMPT = ("You are a test agent. Always start your answer with the exact token that follows 'NONCE:' in "
                 "the user message. Keep answers under 40 words.")


def build_chat_model() -> ChatOpenAI:
    project_endpoint = os.environ["FOUNDRY_PROJECT_ENDPOINT"].rstrip("/")
    deployment = os.environ["FOUNDRY_MODEL_NAME"]
    # Observed 2026-10-02: in the hosted container, DefaultAzureCredential (as in the Learn sample) obtained a token
    # for a principal without inference permission (401 PermissionDenied). The agent's own identity
    # (FOUNDRY_AGENT_INSTANCE_CLIENT_ID), used by the working minimal agent, has implicit inference access.
    instance = os.environ.get("FOUNDRY_AGENT_INSTANCE_CLIENT_ID")
    credential = ManagedIdentityCredential(client_id=instance) if instance else DefaultAzureCredential()
    project = AIProjectClient(endpoint=project_endpoint, credential=credential)
    openai_client = project.get_openai_client()
    token_provider = get_bearer_token_provider(credential, _AZURE_AI_SCOPE)
    # Observed 2026-10-02: with the agent identity, /openai/v1/chat/completions -> 401 "Principal does not have access
    # to API/Operation", while /openai/v1/responses -> 200 (reference agent). Use the Responses API.
    return ChatOpenAI(model=deployment, base_url=str(openai_client.base_url), api_key=token_provider,
                      use_responses_api=True)


def log_versions() -> None:
    """One stdout line with the installed versions: makes the platform's remote build auditable in container logs."""
    from importlib.metadata import PackageNotFoundError, version
    found = {}
    for pkg in ("langchain-azure-ai", "langchain", "langgraph", "langchain-openai", "azure-ai-agentserver-responses",
                "azure-ai-projects", "azure-identity", "openai"):
        try:
            found[pkg] = version(pkg)
        except PackageNotFoundError:
            found[pkg] = "absent"
    print("HAPQ_VERSIONS " + " ".join(f"{k}={v}" for k, v in found.items()), flush=True)


def main() -> None:
    log_versions()
    graph = create_agent(build_chat_model(), tools=[], system_prompt=SYSTEM_PROMPT)
    port = int(os.environ.get("PORT", "8088"))
    ResponsesHostServer(graph).run(port=port)


if __name__ == "__main__":
    main()
