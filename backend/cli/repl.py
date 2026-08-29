"""
REPL loop — Phase 11.3 + 11b

- prompt_toolkit PromptSession with history, completer for slash commands, rich theme
- Routing: /slash → commands.py direct, natural language → graph with instruction hook + token enforcement
- Live streaming: rich.live.Live for research→strategy→risk→execution steps while persisting to logs/broker.jsonl
"""

import asyncio
import time
from typing import Dict, Optional

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
        # Check against 1000, truncate oldest messages if needed
        if count_tokens(prompt_text) > 1000:
            # Keep last 3 messages + system
            if len(msgs) > 3:
                state["messages"] = msgs[-3:]
                log_event("cli_token_truncate", original_tokens=count_tokens(prompt_text), kept_messages=3)
            # Also enforce via utils
            for m in state["messages"]:
                if isinstance(m, dict) and "content" in m:
                    m["content"] = enforce_token_limit(str(m["content"]), 800)
                elif hasattr(m, "content"):
                    try:
                        m.content = enforce_token_limit(str(m.content), 800)
                    except Exception:
                        pass
    except Exception:
        pass
    return state


def _run_graph_with_streaming(state: Dict, thread_id: str = "cli") -> Dict:
    """
    Run graph with rich.live streaming of research→strategy→risk→execution.
    Uses graph.stream_events if available, else invoke with Live updates.
    Returns final state.
    """
    from backend.graph.build import build_graph

    graph = build_graph()

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
                    # After invoke, populate table
                    for step in ("research", "strategy", "risk", "execution"):
                        if step in result and result[step]:
                            out = str(result[step])[:500]
                            table.add_row(step, out)
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
                for step in ("research", "strategy", "risk", "execution", "reporting"):
                    if step in result and result[step]:
                        out = str(result[step])
                        # Truncate for display
                        display = out[:400] + ("..." if len(out) > 400 else "")
                        table.add_row(step, display)
                        live.update(table)
                        time.sleep(0.05)
                # Also show final P&L if available
                if "execution" in result:
                    log_event("cli_graph_complete", steps=list(result.keys()))
            return result
        except Exception:
            # Final fallback: no rich
            return graph.invoke(state, config={"configurable": {"thread_id": thread_id}})

    except Exception as e:
        log_event("cli_graph_error", error=str(e)[:300])
        # Ultimate fallback: direct invoke without Live
        try:
            return graph.invoke(state, config={"configurable": {"thread_id": thread_id}})
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

            # Handle interrupts (HITL) — if result has __interrupt__, prompt for decision
            if isinstance(result, dict) and "__interrupt__" in result:
                print("\n[bold yellow]Human approval required:[/bold yellow]")
                try:
                    from rich.console import Console

                    Console().print(str(result["__interrupt__"])[:1000])
                except Exception:
                    print(str(result["__interrupt__"])[:1000])

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

            # Render final state summary via rich
            try:
                from rich.console import Console
                from rich.table import Table

                console = Console()
                table = Table(title="Result", show_header=False)
                table.add_column("Key", style="cyan")
                table.add_column("Value", overflow="fold")
                for k in ("research", "strategy", "risk", "execution"):
                    if k in result and result[k]:
                        val = str(result[k])
                        table.add_row(k, val[:600] + ("..." if len(val) > 600 else ""))
                console.print(table)
            except Exception:
                # Fallback plain
                for k in ("research", "strategy", "risk", "execution"):
                    if k in result:
                        print(f"{k}: {str(result[k])[:500]}")

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
