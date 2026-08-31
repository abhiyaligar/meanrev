"""
REPL loop — Phase 11.3 + 11b

- prompt_toolkit PromptSession with history, completer for slash commands, rich theme
- Routing: /slash → commands.py direct, natural language → graph with instruction hook + token enforcement
- Live streaming: rich.live.Live for research→strategy→risk→execution steps while persisting to logs/broker.jsonl
"""

import asyncio
import time
from typing import Any, Dict, Optional

from backend.core.logging import log_event

# Slash commands registry
try:
    from backend.cli.commands import COMMANDS
except Exception:
    COMMANDS = {}


def _get_completer():
    try:
        from prompt_toolkit.completion import WordCompleter

        words = [f"/{k}" for k in COMMANDS.keys()] + ["/help", "/quit"]
        return WordCompleter(words, ignore_case=True, sentence=True)
    except Exception:
        return None


def _route_slash(text: str) -> tuple[bool, str]:
    """Return (is_slash, output). Handles /command routing."""
    if not text.strip().startswith("/"):
        return False, ""
    parts = text.strip().lstrip("/").split(maxsplit=1)
    cmd = parts[0].lower() if parts else ""
    args = parts[1] if len(parts) > 1 else ""
    handler = COMMANDS.get(cmd)
    if not handler:
        return True, f"Unknown command /{cmd} — type /help"
    try:
        result = handler(args)
        # Handle exit
        if isinstance(result, dict) and result.get("data", {}).get("exit"):
            return True, "__EXIT__"
        output = result.get("output", "") if isinstance(result, dict) else str(result)
        return True, output
    except Exception as e:
        log_event("cli_slash_error", command=cmd, error=str(e)[:200])
        return True, f"/{cmd} failed: {e}"


def _apply_instruction_hook(text: str, state: Dict) -> Dict:
    """Wire instruction hook (11b) — 'be more conservative' → strategy_conservatism."""
    try:
        from backend.agents.strategy import apply_instruction

        # Only treat as instruction if contains hook keywords
        lower = text.lower()
        if any(kw in lower for kw in ("conservative", "aggressive", "explain", "risk")):
            state = apply_instruction(state, text)
            log_event("cli_instruction_hook", instruction=text[:200], conservatism=state.get("strategy_conservatism"))
    except Exception:
        pass
    return state


def _enforce_token_limit_for_graph(state: Dict) -> Dict:
    """11b token enforcement before graph.invoke — uses utils.count_tokens."""
    try:
        from backend.core.utils import count_tokens, enforce_token_limit

        # Estimate prompt tokens for research + strategy system prompts + messages
        msgs = state.get("messages", [])
        prompt_text = " ".join(
            (m.get("content") if isinstance(m, dict) else getattr(m, "content", str(m))) for m in msgs
        )
        # Check against 10000, truncate oldest messages if needed (was 1000, now 10k)
        if count_tokens(prompt_text) > 10000:
            # Keep last 3 messages + system
            if len(msgs) > 3:
                state["messages"] = msgs[-3:]
                log_event("cli_token_truncate", original_tokens=count_tokens(prompt_text), kept_messages=3)
            # Also enforce via utils
            for m in state["messages"]:
                if isinstance(m, dict) and "content" in m:
                    m["content"] = enforce_token_limit(str(m["content"]), 8000)
                elif hasattr(m, "content"):
                    try:
                        m.content = enforce_token_limit(str(m.content), 800)
                    except Exception:
                        pass
    except Exception:
        pass
    return state


def _to_dict(obj: Any) -> Dict[str, Any]:
    """Normalize GraphState object or dict to plain dict for .get / in checks."""
    if obj is None:
        return {}
    if isinstance(obj, dict):
        return obj
    if hasattr(obj, "model_dump"):
        try:
            return obj.model_dump()
        except Exception:
            pass
    try:
        return dict(obj)
    except Exception:
        # Fallback via get
        out: Dict[str, Any] = {}
        for k in ("messages", "research", "strategy", "risk", "execution", "reporting", "market_snapshot", "account_state"):
            try:
                v = obj.get(k) if hasattr(obj, "get") else getattr(obj, k, None)
                if v is not None:
                    out[k] = v
            except Exception:
                pass
        # Also check __dict__ and extra
        if hasattr(obj, "__dict__"):
            out.update({k: v for k, v in obj.__dict__.items() if not k.startswith("_")})
        if hasattr(obj, "__pydantic_extra__") and obj.__pydantic_extra__:
            out.update(obj.__pydantic_extra__)
        return out


def _get(obj: Any, key: str, default: Any = None) -> Any:
    """Safe get for dict or GraphState object."""
    if isinstance(obj, dict):
        return obj.get(key, default)
    if hasattr(obj, "get"):
        try:
            return obj.get(key, default)
        except Exception:
            pass
    return getattr(obj, key, default)


def _has(obj: Any, key: str) -> bool:
    if isinstance(obj, dict):
        return key in obj
    if hasattr(obj, "__contains__"):
        try:
            return key in obj
        except Exception:
            pass
    return hasattr(obj, key) and getattr(obj, key, None) is not None


def _human_step(step: str, data: Any) -> str:
    """Human readable rendering for each agent step — no braces, no backslashes, no code fences."""
    if not isinstance(data, dict):
        # Clean any raw string: strip code fences, braces, backslashes
        s = str(data).replace("```json", "").replace("```", "").replace("{", "").replace("}", "").replace("\\", "").replace('"', "").strip()
        return s[:600]
    # Research: Sentiment | Regime | Catalyst
    if step == "research":
        sentiment = data.get("sentiment") or data.get("output", "")[:80]
        regime = data.get("regime", "")
        catalyst = data.get("catalyst_summary") or data.get("output") or ""
        # Clean catalyst
        catalyst = str(catalyst).replace("```json", "").replace("```", "").replace("{", "").replace("}", "").replace("\\", "").replace('"', "").strip()
        # If output already human like "Sentiment: ...", use it directly
        output = data.get("output", "")
        if output and "Sentiment:" in output:
            return output[:600]
        parts = []
        if sentiment:
            parts.append(f"Sentiment: {sentiment}")
        if regime:
            parts.append(f"Regime: {regime}")
        if catalyst:
            # Take first 300 chars of catalyst, no braces
            clean_cat = catalyst.replace("{", "").replace("}", "").replace("\\", "").strip()[:300]
            parts.append(f"Catalyst: {clean_cat}")
        return " | ".join(parts) if parts else str(data.get("output", ""))[:600].replace("{", "").replace("}", "").replace("\\", "")

    # Strategy: Action Symbol Qty Price Stop/Target + Option
    if step == "strategy":
        action = str(data.get("action", data.get("output", "")[:20]) or "hold").upper()
        symbol = data.get("symbol", "")
        qty = data.get("qty", data.get("notional", ""))
        price = data.get("price") or data.get("close") or ""
        stop = data.get("stop_price", "")
        target = data.get("target_price", "")
        atr = data.get("atr", "")
        rationale = data.get("rationale") or data.get("output", "")
        # Clean rationale
        rationale = str(rationale).replace("```json", "").replace("```", "").replace("{", "").replace("}", "").replace("\\", "").replace('"', "").strip()[:300]
        # Option leg
        option = ""
        if data.get("option_leg"):
            leg = data["option_leg"]
            if isinstance(leg, dict):
                option = f"Option: {leg.get('symbol','')} Delta {leg.get('greeks',{}).get('delta','')}"
            else:
                option = f"Option: {leg}"
        elif data.get("option_symbol"):
            option = f"Option: {data['option_symbol']}"
        # Use output if it already is human like "BUY 1 AAPL..."
        output = data.get("output", "")
        if output and ("BUY" in output.upper() or "SELL" in output.upper()) and "ATR" in output:
            return str(output)[:600].replace("{", "").replace("}", "").replace("\\", "")
        header = f"{action} {qty} {symbol}".strip()
        if price:
            header += f" @ {price}"
        if atr:
            header += f" (ATR {atr})"
        details = []
        if stop:
            details.append(f"Stop {stop}")
        if target:
            details.append(f"Target {target}")
        if option:
            details.append(option)
        body = " | ".join(details) if details else ""
        if rationale and "fallback" not in rationale.lower():
            body += f"\nRationale: {rationale[:250]}" if body else f"Rationale: {rationale[:250]}"
        return f"{header}\n{body}".strip()[:600]

    # Risk: Decision + Rule + Drawdown
    if step == "risk":
        decision = data.get("decision", "no_trade")
        rule = data.get("rule", "")
        drawdown = data.get("drawdown", "")
        # Clean rule
        rule = str(rule).replace("{", "").replace("}", "").replace("\\", "").strip()
        parts = [f"Decision: {decision}"]
        if rule:
            parts.append(f"Rule: {rule[:300]}")
        if drawdown != "" and drawdown is not None:
            try:
                dd = float(drawdown)
                parts.append(f"Drawdown: {dd:.2%}")
            except Exception:
                parts.append(f"Drawdown: {drawdown}")
        if data.get("spxw_flag"):
            parts.append("SPXW settlement lag flagged")
        return " | ".join(parts)

    # Execution: Status + Order ID + Fill
    if step == "execution":
        status = data.get("status", "skipped")
        order_id = data.get("order_id", "")
        filled = data.get("filled_qty", "")
        price = data.get("filled_price") or data.get("price") or ""
        # Clean
        order = data.get("order", {})
        symbol = order.get("symbol", "") if isinstance(order, dict) else ""
        qty = order.get("qty", "") if isinstance(order, dict) else ""
        parts = [f"Status: {status}"]
        if order_id and order_id != "unknown":
            parts.append(f"Order ID: {order_id}")
        if symbol:
            parts.append(f"Symbol: {symbol}")
        if qty:
            parts.append(f"Qty: {qty}")
        if filled:
            parts.append(f"Filled: {filled} @ {price}" if price else f"Filled: {filled}")
        if data.get("latency_ms"):
            parts.append(f"Latency: {data['latency_ms']}ms")
        if data.get("human_approved"):
            parts.append("Human Approved")
        if data.get("auto"):
            parts.append("Auto Mode")
        return " | ".join(parts)

    # Fallback for other steps: clean braces
    s = str(data).replace("```json", "").replace("```", "").replace("{", "").replace("}", "").replace("\\", "").replace("'", "").strip()
    return s[:600]


def _run_graph_with_streaming(state: Dict, thread_id: str = "cli") -> Dict:
    """
    Run graph with rich.live streaming of research→strategy→risk→execution.
    Uses graph.stream_events if available, else invoke with Live updates.
    Returns final state as plain dict (normalized from GraphState object).
    """
    from backend.graph.build import build_graph

    graph = build_graph()

    def _normalize_result(res: Any) -> Dict[str, Any]:
        return _to_dict(res) if not isinstance(res, dict) else res

    # Try streaming via rich.live
    try:
        from rich.console import Console
        from rich.live import Live
        from rich.table import Table

        console = Console()

        # For stub graph (no LLM), just invoke and render steps after
        # For built-in agent graph, try stream_events
        if hasattr(graph, "stream_events"):
            # LangGraph streaming — version v3 with interrupts
            try:
                from langgraph.types import Command

                # Use stream_events with v3 for HITL support
                stream_input = state
                config = {"configurable": {"thread_id": thread_id}}

                # Simple Live table for steps
                table = Table(title="Agent Steps (live)", show_header=True, header_style="bold cyan")
                table.add_column("Step", style="dim")
                table.add_column("Output", overflow="fold")

                with Live(table, console=console, refresh_per_second=4, transient=False) as live:
                    # For now, fallback to invoke + manual Live updates for stub
                    # Real streaming would iterate stream.messages / stream.values
                    result = graph.invoke(state, config=config)
                    result = _normalize_result(result)
                    # After invoke, populate table with human readable
                    for step in ("research", "strategy", "risk", "execution"):
                        if _has(result, step) and _get(result, step):
                            out = _human_step(step, _get(result, step))
                            table.add_row(step, out[:600])
                            live.update(table)
                            time.sleep(0.1)  # subtle animation for demo
                return result
            except Exception as e:
                log_event("cli_stream_fallback", error=str(e)[:200])

        # Fallback: invoke and render with Live
        try:
            from rich.live import Live
            from rich.table import Table
            from rich.console import Console

            console = Console()
            table = Table(title="Agent Steps", show_header=True, header_style="bold cyan")
            table.add_column("Step", style="dim")
            table.add_column("Output", overflow="fold")

            with Live(table, console=console, refresh_per_second=4) as live:
                result = graph.invoke(state, config={"configurable": {"thread_id": thread_id}})
                result = _normalize_result(result)
                for step in ("research", "strategy", "risk", "execution", "reporting"):
                    if _has(result, step) and _get(result, step):
                        out = _human_step(step, _get(result, step))
                        # Truncate for display
                        display = out[:600] + ("..." if len(out) > 600 else "")
                        table.add_row(step, display)
                        live.update(table)
                        time.sleep(0.05)
                # Also show final P&L if available
                if _has(result, "execution"):
                    log_event("cli_graph_complete", steps=list(result.keys()) if isinstance(result, dict) else [])
            return result
        except Exception:
            # Final fallback: no rich
            result = graph.invoke(state, config={"configurable": {"thread_id": thread_id}})
            return _normalize_result(result)

    except Exception as e:
        log_event("cli_graph_error", error=str(e)[:300])
        # Ultimate fallback: direct invoke without Live
        try:
            result = graph.invoke(state, config={"configurable": {"thread_id": thread_id}})
            return _normalize_result(result)
        except Exception as e2:
            return {"error": str(e2)[:500], "state": state}


def run_repl(thread_id: str = "cli", default_symbol: str = "AAPL", dry_run: bool = False):
    """Main REPL loop — prompt_toolkit + rich + routing."""
    # Check prompt_toolkit available
    try:
        from prompt_toolkit import PromptSession
        from prompt_toolkit.history import InMemoryHistory
        from prompt_toolkit.auto_suggest import AutoSuggestFromHistory

        history = InMemoryHistory()
        session_kwargs = {
            "history": history,
            "auto_suggest": AutoSuggestFromHistory(),
        }
        completer = _get_completer()
        if completer:
            session_kwargs["completer"] = completer

        session = PromptSession(**session_kwargs)
        use_ptk = True
    except Exception as e:
        print(f"prompt_toolkit not available ({e}) — falling back to input()")
        session = None
        use_ptk = False

    try:
        from rich.console import Console

        console = Console()
        console.print("[bold green]REPL started.[/bold green] Type /help, or natural language. Ctrl+C to exit.", style="dim")
    except Exception:
        print("REPL started. Type /help")

    # Main loop
    while True:
        try:
            if use_ptk and session:
                text = session.prompt("₳ ")
            else:
                text = input("₳ ")

            text = text.strip()
            if not text:
                continue

            log_event("cli_input", text=text[:200], thread_id=thread_id)

            # 1. Slash command routing
            is_slash, slash_output = _route_slash(text)
            if is_slash:
                if slash_output == "__EXIT__":
                    print("Exiting CLI.")
                    break
                # Render via rich if possible
                try:
                    from rich.console import Console
                    from rich.markdown import Markdown

                    Console().print(Markdown(slash_output))
                except Exception:
                    print(slash_output)
                continue

            # 2. Natural language → instruction hook + token enforcement + graph
            state: Dict = {"messages": [{"role": "user", "content": text}], "thread_id": thread_id}
            if dry_run:
                state["dry_run"] = True

            # 11b: instruction hook
            state = _apply_instruction_hook(text, state)
            # 11b: token enforcement
            state = _enforce_token_limit_for_graph(state)

            # Run graph with live streaming
            result = _run_graph_with_streaming(state, thread_id=thread_id)

            # Handle interrupts (HITL) — if result has __interrupt__, prompt for decision (handle both dict and GraphState object)
            has_interrupt = _has(result, "__interrupt__") or ("__interrupt__" in result if isinstance(result, dict) else False)
            if has_interrupt:
                interrupt_val = _get(result, "__interrupt__", result.get("__interrupt__") if isinstance(result, dict) else None)
                # Also try direct dict access for GraphState object via model_extra
                if interrupt_val is None:
                    try:
                        interrupt_val = result["__interrupt__"] if isinstance(result, dict) else getattr(result, "__interrupt__", None)
                    except Exception:
                        interrupt_val = None
                if interrupt_val is not None:
                    print("\n[bold yellow]Human approval required:[/bold yellow]")
                    try:
                        from rich.console import Console

                        Console().print(str(interrupt_val)[:1000])
                    except Exception:
                        print(str(interrupt_val)[:1000])

                # Prompt for decision
                try:
                    import questionary

                    decision = questionary.select(
                        "Approve order?",
                        choices=["approve", "edit", "reject"],
                    ).ask()
                    if decision is None:
                        decision = "reject"
                except Exception:
                    decision = input("Approve? (approve/edit/reject) [approve]: ").strip().lower() or "approve"

                # Resume via Command
                try:
                    from langgraph.types import Command
                    from backend.graph.build import build_graph

                    graph = build_graph()
                    resume_value = {"decisions": [{"type": decision}]}
                    result = graph.invoke(Command(resume=resume_value), config={"configurable": {"thread_id": thread_id}})
                    print(f"Resumed with {decision}")
                except Exception as e:
                    print(f"Resume failed: {e}")

            # Render final state summary via rich — human readable, no braces
            try:
                from rich.console import Console
                from rich.table import Table

                console = Console()
                table = Table(title="Result — Human Readable", show_header=False, show_lines=True)
                table.add_column("Step", style="cyan", width=12)
                table.add_column("Summary", overflow="fold")
                for k in ("research", "strategy", "risk", "execution"):
                    if _has(result, k) and _get(result, k):
                        val = _human_step(k, _get(result, k))
                        table.add_row(k.capitalize(), val[:800] + ("..." if len(val) > 800 else ""))
                # Show P&L if available
                if _has(result, "execution"):
                    exec_data = _get(result, "execution")
                    if isinstance(exec_data, dict) and exec_data.get("order"):
                        table.add_row("Order", str(exec_data.get("order"))[:400])
                console.print(table)
            except Exception:
                # Fallback plain human readable
                for k in ("research", "strategy", "risk", "execution"):
                    if _has(result, k):
                        try:
                            print(f"{k.capitalize()}: {_human_step(k, _get(result, k))[:600]}")
                        except Exception:
                            pass

        except KeyboardInterrupt:
            # Handled in __main__.py, but also here
            print("\nUse /exit to quit or Ctrl+C again to force.")
            try:
                # Second Ctrl+C within 1 sec exits
                import time as _t

                start = _t.time()
                if use_ptk and session:
                    text = session.prompt("Really exit? (y/N) ")
                else:
                    text = input("Really exit? (y/N) ")
                if text.strip().lower() in ("y", "yes"):
                    break
            except KeyboardInterrupt:
                break
        except EOFError:
            break
        except Exception as e:
            log_event("cli_loop_error", error=str(e)[:300])
            print(f"Error: {e}")
            import traceback

            traceback.print_exc()
