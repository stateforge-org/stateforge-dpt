import io
import sys

import pytest

from tools.smoke_test_package import print_server_output


@pytest.mark.parametrize("encoding", ["utf-8", "cp1252", "ascii"])
def test_utf8_server_output_can_be_replayed_on_any_console(monkeypatch, encoding):
    buffer = io.BytesIO()
    stream = io.TextIOWrapper(buffer, encoding=encoding)
    monkeypatch.setattr(sys, "stdout", stream)
    output = "  ◆ StateForge DPT\n  → http://127.0.0.1:8463\n  Workspace: 用户\n"

    print_server_output(output)
    stream.flush()

    replayed = buffer.getvalue().decode(encoding)
    assert "StateForge DPT" in replayed
    assert "http://127.0.0.1:8463" in replayed
    if encoding == "utf-8":
        assert replayed == output + "\n"
    else:
        assert r"\u25c6" in replayed
        assert r"\u2192" in replayed
        assert r"\u7528\u6237" in replayed
