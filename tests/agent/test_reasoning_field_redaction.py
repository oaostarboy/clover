"""Assistant reasoning fields are redacted at the storage boundary, like content."""
from agent.chat_completion_helpers import build_assistant_message

_SECRET = "ghp_" + "A1b2C3d4E5f6G7h8I9j0K1l2M3n4O5p6Q7r8"
_THOUGHT = f"I will use the token {_SECRET} to call the API."


class _Agent:
    stream_delta_callback = None
    _stream_callback = None
    reasoning_callback = None
    verbose_logging = False

    def _extract_reasoning(self, msg):
        return getattr(msg, "reasoning", None)

    def _strip_think_blocks(self, text):
        return text

    def _needs_thinking_reasoning_pad(self):
        return False


class _Msg:
    def __init__(self, **f):
        self.content = "done"
        self.tool_calls = None
        self.__dict__.update(f)

    def __getattr__(self, _n):
        return None


def test_reasoning_fields_redacted_signed_blocks_untouched():
    signed = {"type": "reasoning.text", "text": _THOUGHT, "signature": "sig-opaque"}
    m = _Msg(reasoning=_THOUGHT, reasoning_content=_THOUGHT, reasoning_details=[
        {"type": "reasoning.summary", "summary": _THOUGHT}, dict(signed),
        {"type": "reasoning.encrypted", "data": "opaque-blob"}])
    out = build_assistant_message(_Agent(), m, "stop")
    assert _SECRET not in out["reasoning"]
    assert _SECRET not in out["reasoning_content"]
    assert _SECRET not in out["reasoning_details"][0]["summary"]
    assert out["reasoning_details"][1] == signed  # signed replay material byte-exact
    assert out["reasoning_details"][2]["data"] == "opaque-blob"


def test_plain_reasoning_unchanged():
    out = build_assistant_message(_Agent(), _Msg(reasoning="think about the plan"), "stop")
    assert out["reasoning"] == "think about the plan"
