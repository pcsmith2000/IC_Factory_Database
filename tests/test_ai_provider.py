"""The Vercel AI Gateway is the one AI provider. The first-party ANTHROPIC_API_KEY fallback was
removed on 2026-09-18: a run that lost the gateway key used to keep going against another provider
on a differently-spelled model id, with only the run record's `provider` field saying so."""
import pytest
from pipeline import ai_client_and_model, gateway_model_id


def test_a_missing_gateway_key_fails_loudly_rather_than_finding_another_provider(monkeypatch):
    monkeypatch.delenv("AI_GATEWAY_API_KEY", raising=False)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-should-not-be-consulted")
    with pytest.raises(RuntimeError) as e:
        ai_client_and_model("claude-haiku-4-5")
    assert "AI_GATEWAY_API_KEY" in str(e.value) and "IC_AI=off" in str(e.value)
    assert "ANTHROPIC_API_KEY" not in str(e.value)     # nothing points at the retired credential


def test_the_gateway_key_gives_a_gateway_client_and_a_gateway_model_id(monkeypatch):
    monkeypatch.setenv("AI_GATEWAY_API_KEY", "vck_test")
    client, model, provider = ai_client_and_model("claude-haiku-4-5")
    assert provider == "vercel_ai_gateway"
    assert model == "anthropic/claude-haiku-4.5"       # dotted and qualified, not the first-party id
    assert str(client.base_url).startswith("https://ai-gateway.vercel.sh")


def test_the_anthropic_SDK_is_still_the_client_because_the_gateway_speaks_its_API(monkeypatch):
    """Removing the key is not removing the package: the gateway serves the Messages API."""
    monkeypatch.setenv("AI_GATEWAY_API_KEY", "vck_test")
    client, _, _ = ai_client_and_model("claude-haiku-4-5")
    assert type(client).__module__.startswith("anthropic")
    assert client.api_key == "vck_test"                # the gateway key, never a first-party one


def test_an_already_qualified_id_is_left_alone_so_a_non_anthropic_model_can_be_pinned():
    assert gateway_model_id("inception/mercury-2.5") == "inception/mercury-2.5"
