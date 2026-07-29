from __future__ import annotations

import argparse
import asyncio
import getpass
import sys
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Any


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="safefix")
    commands = parser.add_subparsers(dest="command")

    chat = commands.add_parser("chat")
    chat.add_argument("project", nargs="?", type=Path, default=Path("."))
    chat.add_argument("--config", type=Path)
    chat.add_argument("--data-dir", type=Path)
    chat.add_argument("--provider", default="openai-compatible")

    run = commands.add_parser("run")
    run.add_argument("project", type=Path)
    run.add_argument("--task", required=True)
    run.add_argument("--config", type=Path, default=Path("safefix.yaml"))
    run.add_argument("--data-dir", type=Path)
    run.add_argument("--provider", default="openai-compatible")
    run.add_argument("--in-place", action="store_true")
    run.add_argument("--mock-script", type=Path)
    run.add_argument("--non-interactive", action="store_true")
    run.add_argument("--json", action="store_true")

    setup = commands.add_parser("setup")
    setup.add_argument("project", nargs="?", type=Path, default=Path("."))
    setup.add_argument("--config", type=Path)
    setup.add_argument("--provider", default="openai-compatible")

    serve = commands.add_parser("serve")
    serve.add_argument("--host", default="127.0.0.1")
    serve.add_argument("--port", type=int, default=8000)
    serve.add_argument("--public-demo", action="store_true")

    config = commands.add_parser("config").add_subparsers(
        dest="config_command", required=True
    )
    config.add_parser("init").add_argument(
        "path", nargs="?", type=Path, default=Path("safefix.yaml")
    )
    config.add_parser("validate").add_argument(
        "path", nargs="?", type=Path, default=Path("safefix.yaml")
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
    parser.set_defaults(
        command="chat",
        project=Path("."),
        config=None,
        data_dir=None,
        provider="openai-compatible",
    )
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
    if args.command == "chat":
        from safefix.cli_chat import ChatOptions, run_chat

        return run_chat(
            ChatOptions(
                project=args.project,
                config=args.config,
                data_dir=args.data_dir,
                provider=args.provider,
            ),
            credential_service=credential_service or _default_credentials(),
            input_fn=input,
            secret_input_fn=getpass.getpass,
            stdout=sys.stdout,
            stderr=sys.stderr,
        )

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
            import uvicorn

            from safefix.demo import PublicDemoService
            from safefix.web.app import AppDependencies, create_app

            app = create_app(
                AppDependencies(
                    service=PublicDemoService(), public_demo=args.public_demo
                )
            )
            uvicorn.run(app, host=args.host, port=args.port)
        else:
            serve(args.host, args.port, args.public_demo)
        return 0

    if args.command == "setup":
        from safefix.cli_setup import SetupOptions, run_setup

        return run_setup(
            SetupOptions(
                project=args.project,
                config=args.config,
                provider=args.provider,
            ),
            credential_service=credential_service or _default_credentials(),
            input_fn=input,
            secret_input_fn=getpass.getpass,
            stdout=sys.stdout,
        )

    if args.command == "run":
        if task_service is not None:
            snapshot = asyncio.run(
                task_service.create(
                    task=args.task,
                    project_path=str(args.project),
                    provider=args.provider,
                )
            )
            print(f"{snapshot.run_id}: {snapshot.status.value}")
            return 0

        from safefix.cli_runner import CliRunOptions, run_cli

        return run_cli(
            CliRunOptions(
                project=args.project,
                task=args.task,
                config=args.config,
                data_dir=args.data_dir,
                provider=args.provider,
                in_place=args.in_place,
                mock_script=args.mock_script,
                non_interactive=args.non_interactive,
                json_output=args.json,
            )
        )

    if args.command == "config":
        from safefix.config import ConfigError, default_settings_yaml, load_settings

        path = args.path
        if args.config_command == "init":
            try:
                with path.open("x", encoding="utf-8", newline="\n") as config_file:
                    config_file.write(default_settings_yaml())
            except FileExistsError:
                print(f"configuration already exists: {path}")
                return 2
            except OSError:
                print(f"cannot write configuration: {path}")
                return 2
        else:
            try:
                load_settings(path)
            except ConfigError as exc:
                print(f"configuration error: {exc}")
                return 2
        print(str(path))
        return 0

    from safefix.demo import main as demo_main

    return int(demo_main([]) or 0)


def public_demo_main() -> int:
    return main(["serve", "--public-demo", "--host", "0.0.0.0"])


if __name__ == "__main__":
    raise SystemExit(main())
