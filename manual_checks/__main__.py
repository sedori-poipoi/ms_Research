import asyncio
import sys

from manual_checks.amazon_keyword_probe import main as amazon_main
from manual_checks.yodobashi_jan_probe import run_from_cli as run_jan_cli


def print_help():
    print("Usage: python3 -m manual_checks [amazon|jan] [args]")
    print("  amazon")
    print("  jan [URL] [--headless] [--wait SECONDS]")


def run():
    if len(sys.argv) < 2:
        print_help()
        return 1

    probe_name = sys.argv[1].strip().lower()
    probe_args = sys.argv[2:]

    if probe_name == "amazon":
        if probe_args:
            print("amazon probe does not accept extra arguments")
            print_help()
            return 1
        asyncio.run(amazon_main())
        return 0

    if probe_name == "jan":
        return run_jan_cli(probe_args)

    print(f"Unknown probe: {probe_name}")
    print_help()
    return 1


if __name__ == "__main__":
    raise SystemExit(run())
