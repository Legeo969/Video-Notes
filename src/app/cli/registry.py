from typing import Protocol, List
import argparse


class CliCommand(Protocol):
    name: str

    def add_arguments(self, parser: argparse.ArgumentParser) -> None: ...

    def matches(self, args: argparse.Namespace) -> bool: ...

    def run(self, args: argparse.Namespace) -> int: ...


class CommandRegistry:
    def __init__(self):
        self._commands: List[CliCommand] = []

    def register(self, command: CliCommand) -> None:
        self._commands.append(command)

    def list_commands(self) -> List[CliCommand]:
        return list(self._commands)

    def dispatch(self, args: argparse.Namespace) -> int:
        for cmd in self._commands:
            if cmd.matches(args):
                return cmd.run(args)
        return 1
