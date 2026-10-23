import httpx
import pytest

from agentic_editor import hooks, pipeline
from agentic_editor.edl import EDL, Clip
from agentic_editor.llm import LLMError, OllamaClient
from agentic_editor.presets import Preset
from tests.test_planner import FakeLLM, sentence


def client(handler) -> OllamaClient:
    llm = OllamaClient("http://ollama.test", "m")
    llm.http = httpx.Client(transport=httpx.MockTransport(handler))
    return llm


def talk(make_analysis, sentences: int = 10):
    words = [w for k in range(sentences) for w in sentence(k * 4, f"this is sentence {k} here.")]
    return make_analysis(words, sentences * 4.0)


def test_an_unreachable_server_is_a_model_error():
    def refuse(request):
        raise httpx.ConnectError("connection refused", request=request)

    with pytest.raises(LLMError, match="could not be reached"):
        client(refuse).chat_json([{"role": "user", "content": "hi"}], {})


def test_a_reply_without_choices_is_a_model_error():
    llm = client(lambda request: httpx.Response(200, json={"error": "busy"}))
    with pytest.raises(LLMError, match="unexpected reply"):
        llm.chat_json([{"role": "user", "content": "hi"}], {})


def test_a_server_gone_mid_job_keeps_the_planned_opening(make_analysis):
    def refuse(request):
        raise httpx.ConnectError("connection refused", request=request)

    preset = Preset(name="t", label="t", min_duration=10, max_duration=30)
    edl = EDL(clips=[Clip(start=0, end=39)])
    assert hooks.sharpen(client(refuse), edl, talk(make_analysis), preset, None) == edl


def test_a_score_outside_the_schema_falls_back_to_the_heuristic(make_analysis):
    reply = {"start_line": 0, "end_line": 2, "hook": 11, "completeness": 7, "peak": 7}
    llm = FakeLLM({"candidates": [reply | {"reason": "x"}]}, {"option": 0, "reason": "x"})
    llm.model = "fake"
    preset = Preset(name="t", label="t", min_duration=6, max_duration=12)
    (_, planner), *_ = pipeline.plan(llm, talk(make_analysis), preset, None)
    assert planner == "heuristic"
