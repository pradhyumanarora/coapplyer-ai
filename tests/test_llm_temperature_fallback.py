import pytest
import types
import sys

from src.coapplyer_ai.llm.llm_manager import LLMLogger, LoggerChatModel, GPTAnswerer
from src.job import Job


class _DummyLLMWithDisable:
    def __init__(self):
        self.disabled = False
        self.invoke_calls = 0
        self.disable_calls = 0

    def invoke(self, _messages):
        self.invoke_calls += 1
        if not self.disabled:
            raise Exception(
                "Unsupported value:'temperature' does not support 0.4 with this model. unsupported_value"
            )
        return "ok"

    def disable_temperature(self):
        self.disable_calls += 1
        self.disabled = True
        return True


class _DummyLLMDisableFails:
    def invoke(self, _messages):
        raise Exception(
            "Unsupported value:'temperature' does not support 0.4 with this model. unsupported_value"
        )

    def disable_temperature(self):
        return False


def test_logger_chat_model_retries_without_temperature(monkeypatch):
    monkeypatch.setattr(LLMLogger, "log_request", staticmethod(lambda *args, **kwargs: None))
    monkeypatch.setattr(LoggerChatModel, "parse_llmresult", lambda self, reply: {"content": reply})

    llm = _DummyLLMWithDisable()
    chat_model = LoggerChatModel(llm)

    result = chat_model([{"role": "user", "content": "hello"}])

    assert result == "ok"
    assert llm.disable_calls == 1
    assert llm.invoke_calls == 2
    assert chat_model._temperature_disabled is True


def test_logger_chat_model_fails_fast_if_temperature_not_disableable(monkeypatch):
    monkeypatch.setattr(LLMLogger, "log_request", staticmethod(lambda *args, **kwargs: None))
    monkeypatch.setattr(LoggerChatModel, "parse_llmresult", lambda self, reply: {"content": reply})

    chat_model = LoggerChatModel(_DummyLLMDisableFails())

    with pytest.raises(RuntimeError, match="Unable to disable temperature"):
        chat_model([{"role": "user", "content": "hello"}])


def test_azure_model_uses_temperature_one_on_init_and_fallback(monkeypatch):
    captured_kwargs = []

    class _FakeAzureChatOpenAI:
        def __init__(self, **kwargs):
            captured_kwargs.append(kwargs)

    fake_module = types.SimpleNamespace(AzureChatOpenAI=_FakeAzureChatOpenAI)
    monkeypatch.setitem(sys.modules, "langchain_openai", fake_module)

    from src.coapplyer_ai.llm.llm_manager import AzureOpenAIModel

    model = AzureOpenAIModel(
        api_key="k",
        azure_endpoint="https://example.openai.azure.com",
        azure_deployment="dep",
        api_version="2025-04-01-preview",
    )
    model.disable_temperature()

    assert len(captured_kwargs) == 2
    assert captured_kwargs[0]["temperature"] == 1
    assert captured_kwargs[1]["temperature"] == 1


def test_set_job_skips_summarization_when_description_empty(monkeypatch):
    answerer = GPTAnswerer.__new__(GPTAnswerer)

    def _raise_if_called(_text):
        raise AssertionError("summarize_job_description should not be called")

    answerer.summarize_job_description = _raise_if_called
    job = Job(description="   ")

    answerer.set_job(job)

    assert answerer.job.summarize_job_description == ""
