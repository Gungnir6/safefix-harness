from __future__ import annotations

import argparse
import asyncio
import getpass
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Any


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="safefix")
    commands = parser.add_subparsers(dest="command", required=True)

    run = commands.add_parser("run")
    run.add_argument("project")
    run.add_argument("--task", required=True)
    run.add_argument("--provider", default="openai-compatible")

    serve = commands.add_parser("serve")
    serve.add_argument("--host", default="127.0.0.1")
    serve.add_argument("--port", type=int, default=8000)
    serve.add_argument("--public-demo", action="store_true")

    config = commands.add_parser("config").add_subparsers(
        dest="config_command", required=True
    )
    config.add_parser("init").add_argument("path", nargs="?", default="safefix.yaml")
    config.add_parser("validate").add_argument(
        "path", nargs="?", default="safefix.yaml"
    )

    credentials = commands.add_parser("credentials").add_subparsers(
        dest="credentials_command", required=True
    )
    for name in ("set", "status", "clear"):
        command = credentials.add_parser(name)
        command.add_argument("--provider", default="openai-compatible")
        if name == "clear":
            command.add_argument("--yes", action="store_true")

    commands.add_parser("demo")
    return parser


def _default_credentials() -> Any:
    import keyring

    from safefix.credentials import CredentialService

    return CredentialService(keyring)


def main(
    argv: Sequence[str] | None = None,
    *,
    credential_service: Any | None = None,
    task_service: Any | None = None,
    serve: Callable[[str, int, bool], None] | None = None,
) -> int:
    args = build_parser().parse_args(argv)
    if args.command == "credentials":
        credentials = credential_service or _default_credentials()
        provider = args.provider
        if args.credentials_command == "set":
            credentials.set(provider, getpass.getpass("API key: "))
            print("credential stored")
        elif args.credentials_command == "status":
            status = credentials.status(provider)
            print(f"configured: {'yes' if status.configured else 'no'}")
            print(f"source: {status.source or 'none'}")
            if status.warning:
                print(f"warning: {status.warning}")
        else:
            confirmed = args.yes or input("Clear credential? [y/N] ").lower() == "y"
            if not confirmed:
                print("cancelled")
                return 1
            credentials.clear(provider)
            print("credential cleared")
        return 0

    if args.command == "serve":
        if serve is None:
            raise RuntimeError("server dependencies are not configured")
        serve(args.host, args.port, args.public_demo)
        return 0

    if args.command == "run":
        if task_service is None:
            raise RuntimeError("task service is not configured")
        snapshot = asyncio.run(
            task_service.create(
                task=args.task,
                project_path=args.project,
                provider=args.provider,
            )
        )
        print(f"{snapshot.run_id}: {snapshot.status.value}")
        return 0

    if args.command == "config":
        path = Path(args.path)
        if args.config_command == "init":
            path.write_text(
                "llm:\n  endpoint: https://example.invalid/v1\n", encoding="utf-8"
            )
        else:
            from safefix.config import load_settings

            load_settings(path)
        print(str(path))
        return 0

    from safefix.demo import main as demo_main

    return int(demo_main([]) or 0)


if __name__ == "__main__":
    raise SystemExit(main())
