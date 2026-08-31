"""Graph + CLI wiring — conditional risk branch, instruction routing."""

from unittest.mock import MagicMock, patch

import pytest


@pytest.mark.integration
def test_graph_conditional_risk_branch():
    from backend.graph.build import build_graph
    from backend.agents import risk as risk_mod

    # Use stub graph if LLM not configured (fast)
    with patch("backend.core.config.get_settings") as mock_cfg:
        mock_s = MagicMock()
        mock_s.is_llm_configured.return_value = False
        mock_cfg.return_value = mock_s
        g = build_graph()
        # Stub graph is callable
        # Risk rejected -> execution skipped
        state = {"strategy": {"action": "hold", "symbol": "AAPL", "qty": 0}}
        out = g.invoke(state)
        assert out.get("risk", {}).get("decision") in ("no_trade", "rejected")
        # Ensure execution not called when rejected (status skipped)
        assert out.get("execution", {}).get("status") in ("skipped", "dry_run_no_hitl", "rejected", None) or "execution" not in out or out["execution"]["status"] != "filled"


@pytest.mark.integration
def test_cli_instruction_routing():
    from backend.agents.strategy import apply_instruction

    s = {}
    s = apply_instruction(s, "be more conservative today")
    assert s["strategy_conservatism"] == 0.5
    s2 = apply_instruction({}, "go more aggressive")
    assert s2["strategy_conservatism"] == 1.5
    s3 = apply_instruction({"strategy": {"rationale": "bought AAPL"}}, "explain last trade")
    assert "strategy_explain" in s3


@pytest.mark.integration
def test_cli_to_graph_routing_via_repl():
    from backend.cli.repl import _apply_instruction_hook

    state: dict = {}
    out = _apply_instruction_hook("be more conservative", state)
    assert out.get("strategy_conservatism") == 0.5
    # Also test via messages path
    state2 = {"messages": [{"role": "user", "content": "go more aggressive"}]}
    out2 = _apply_instruction_hook("go more aggressive", state2)
    assert out2.get("strategy_conservatism") == 1.5
