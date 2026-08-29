"""
CLI entrypoint — Phase 11.1

Usage:
  python -m backend.cli
  python -m backend.cli --mode hitl --thread-id my-run --symbol AAPL
  python -m backend.cli --help

Parses args, checks core/config (LLM_PROVIDER, LLM_MODEL_*, RISK_MAX_*, EXECUTION_MODE),
prints banner from System_Prompt, and starts repl.
"""

import argparse
import sys
from pathlib import Path

from backend.core.config import get_settings
from backend.core.logging import log_event


def _print_banner():
    try:
        from rich.console import Console
        from rich.panel import Panel

        console = Console()
        banner = Panel.fit(
            "[bold cyan]Meanrev Autonomous Agent[/bold cyan] — Alpaca Paper\n"
            "CLI-only, 5-agent loop: research → strategy → risk → execution → report\n"
            "Type /help for commands, natural language for instructions (e.g., 'be more conservative')",
            title="₳ CLI",
            border_style="cyan",
        )
        console.print(banner)
    except Exception:
        print("Meanrev Autonomous Agent — Alpaca Paper — CLI ready. Type /help")


def parse_args(argv=None):
    p = argparse.ArgumentParser(description="Meanrev CLI — autonomous trading agent")
    p.add_argument("--mode", choices=["auto", "hitl"], default=None, help="Execution mode override (auto|hitl), default from EXECUTION_MODE env")
    p.add_argument("--thread-id", dest="thread_id", default="cli", help="LangGraph thread_id for checkpointer persistence")
    p.add_argument("--symbol", default="AAPL", help="Default symbol for quick market checks")
    p.add_argument("--dry-run", action="store_true", help="Dry-run: do not submit orders, only log")
    return p.parse_args(argv)


def main(argv=None):
    args = parse_args(argv)

    # Load settings and validate
    try:
        s = get_settings()
        # Override via CLI if provided
        if args.mode:
            # Monkey-patch for this run (not persisted to .env)
            s.execution_mode = args.mode  # type: ignore
            s.hitl_enabled = args.mode == "hitl"  # type: ignore
    except Exception as e:
        print(f"Config error: {e} — check backend/.env via .env.example", file=sys.stderr)
        sys.exit(1)

    # Validate .env has required keys (warn, not exit, to allow offline)
    try:
        s = get_settings()
        missing = []
        if not s.get_key():
            missing.append("ALPACA_API_KEY")
        if not s.get_secret():
            missing.append("ALPACA_API_SECRET")
        if missing:
            print(f"Warning: missing {', '.join(missing)} in .env — broker calls will be 401 until set (see .env.example).", file=sys.stderr)
        # LLM check
        if not s.is_llm_configured():
            print("Warning: LLM not configured (no OPENROUTER_API_KEY/GROQ_API_KEY for LLM_PROVIDER) — agents will run in stub mode (deterministic fallback). Set LLM_MODEL_* in .env for full LLM.", file=sys.stderr)
        else:
            try:
                # Trigger compulsory check for model selectors
                s.get_model("research")
                s.get_model("strategy")
                s.get_model("reporting")
            except Exception as e:
                print(f"Warning: LLM model selector missing: {e}", file=sys.stderr)
    except Exception:
        pass

    _print_banner()
    try:
        s = get_settings()
        print(f"LLM: {s.llm_provider}:{s.get_model('strategy')[:30] if s.llm_model_strategy else 'missing'} | Risk: {s.risk_max_position_pct:.0%} pos/{s.risk_max_exposure_pct:.0%} exp/{s.risk_daily_drawdown_pct:.0%} DD | Exec: {getattr(s, 'execution_mode', 'auto')}/hitl={getattr(s, 'hitl_enabled', False)} | Thread: {args.thread_id}")
    except Exception as e:
        print(f"Settings: {e}")

    log_event("cli_started", thread_id=args.thread_id, mode=args.mode or getattr(get_settings(), "execution_mode", "auto"), symbol=args.symbol)

    # Start REPL
    try:
        from backend.cli.repl import run_repl

        run_repl(thread_id=args.thread_id, default_symbol=args.symbol, dry_run=args.dry_run)
    except KeyboardInterrupt:
        print("\nCLI interrupted — exporting report...")
        try:
            from backend.agents.reporting import reporting_agent

            res = reporting_agent({}, export_path="reports/report.md")
            print(f"Report exported to {res.get('exported_to')}")
        except Exception as e:
            print(f"Report export failed: {e}")
        sys.exit(0)
    except Exception as e:
        print(f"REPL failed: {e}", file=sys.stderr)
        import traceback

        traceback.print_exc()
        sys.exit(1)


if __name__ == "__main__":
    main()
