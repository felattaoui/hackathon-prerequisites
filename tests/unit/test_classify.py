from hapq.lab import classify as c
from hapq.results import Status


def test_expected_refusal():
    assert c.expected_refusal(401, 401)[0] is Status.PASS
    assert c.expected_refusal(200, 401)[0] is Status.FAIL
    assert c.expected_refusal(404, 401)[0] is Status.NOT_TESTED
    assert c.expected_refusal(None, 401)[0] is Status.BLOCKED


def test_network_refusal_requires_network_evidence():
    assert c.network_refusal(403, '{"error":{"message":"Access denied due to Virtual Network/Firewall rules."}}')[0] is Status.PASS
    assert c.network_refusal(403, '{"error":"Forbidden"}')[0] is Status.NOT_TESTED
    assert c.network_refusal(401, "")[0] is Status.FAIL     # 401 says nothing about the network
    assert c.network_refusal(200, "")[0] is Status.FAIL
    assert c.network_refusal(None, "")[0] is Status.NOT_TESTED  # silence is not proof of a closed path


def test_inventory_and_version_outcome():
    assert c.inventory({"p1": ["a"], "p2": ["b"]}, {"p1": ["a"], "p2": ["b"]})[0] is Status.PASS
    assert c.inventory({"p1": ["a"]}, {"p1": ["a"], "p2": ["b"]})[0] is Status.FAIL
    assert c.version_outcome(["creating", "active"], "active", None)[0] is Status.PASS
    s, msg = c.version_outcome(["creating", "failed"], "failed", "Feature HostedAgents not registered")
    assert s is Status.FAIL and "preview/feature" in msg
    assert c.version_outcome(["creating"], "creating", None)[0] is Status.NOT_TESTED


def test_technical_refusals_ignores_numbers_and_other_messages_in_a_chunk():
    from hapq.lab import classify
    chunk = ('{"timestamp":"2026-10-02T19:38:05.599075827+00:00","message":"INFO Foundry storage POST /storage/responses -> 201"}\n'
             '{"message":"await self._async_exit_stack.enter_async_context(context_manager) /toolboxes/hapq-tools/ 4031"}')
    assert classify.technical_refusals([chunk], "/toolboxes/hapq-tools/") == []
    real = ('{"message":"INFO httpx: HTTP Request: POST https://x/api/projects/proj1/toolboxes/hapq-tools/mcp?api-version=v1 '
            '\u0022HTTP/1.1 403 Forbidden\u0022"}')
    assert len(classify.technical_refusals([real], "/toolboxes/hapq-tools/")) == 1
