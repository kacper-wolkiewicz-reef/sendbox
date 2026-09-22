"""Turning an untrusted ssh request from the container into a safe host ssh call.

The container's git hands the shim the arguments it would give ssh, and the shim
forwards them verbatim, so everything parsed here is attacker-controlled.
Nothing is passed through: the request must have the exact shape git produces,
and the host command line is rebuilt from the parsed values, with options that
keep a hostile destination from reaching anything but a git transport.
"""

import re
from dataclasses import dataclass
from typing import Optional

from .errors import RequestRejected

MAX_HEADER_BYTES = 8192

_SEND_ENV = "SendEnv=GIT_PROTOCOL"
_HARDENING = (
    "BatchMode=yes",
    "ForwardAgent=no",
    "ForwardX11=no",
    "ClearAllForwardings=yes",
    "PermitLocalCommand=no",
    "GSSAPIDelegateCredentials=no",
    "Tunnel=no",
    "RequestTTY=no",
)
_ADDRESS_FAMILIES = ("-4", "-6")
_DESTINATION = re.compile(
    r"(?:[A-Za-z0-9_][A-Za-z0-9._+-]{0,63}@)?[A-Za-z0-9_:][A-Za-z0-9._:-]{0,252}"
)
_COMMAND = re.compile(
    r"(?:git-upload-pack|git-receive-pack|git-upload-archive) "
    r"'([^'\\!\x00-\x1f\x7f]{1,4096})'"
)
_PORT = re.compile(r"[0-9]{1,5}")
_PROTOCOL = re.compile(r"version=[0-2]")


@dataclass(frozen=True)
class SshRequest:
    """A validated ssh connection that carries exactly one git transport command."""

    destination: str
    command: str
    port: Optional[str] = None
    address_family: Optional[str] = None
    protocol: Optional[str] = None

    def argv(self, binary="ssh"):
        """Return the hardened host ssh command line for this request."""
        argv = [binary]
        for option in _HARDENING:
            argv += ["-o", option]
        if self.address_family:
            argv.append(self.address_family)
        if self.port:
            argv += ["-p", self.port]
        if self.protocol:
            argv += ["-o", _SEND_ENV]
        return [*argv, "--", self.destination, self.command]

    def environment(self, base):
        """Return ``base`` with ``GIT_PROTOCOL`` set to the validated value only."""
        env = {key: value for key, value in base.items() if key != "GIT_PROTOCOL"}
        if self.protocol:
            env["GIT_PROTOCOL"] = self.protocol
        return env

    def describe(self):
        """Return a one-line, terminal-safe description of the connection."""
        port = f":{self.port}" if self.port else ""
        return f"{self.destination}{port} {self.command}"


def parse_header(header):
    """Parse the shim's header into an :class:`SshRequest`.

    The header is ``GIT_PROTOCOL`` followed by the ssh arguments, each
    terminated by a NUL byte, and ends with a newline.
    """
    if len(header) > MAX_HEADER_BYTES or not header.endswith(b"\0\n"):
        raise RequestRejected("malformed request header")
    try:
        fields = header[:-2].decode("utf-8").split("\0")
    except UnicodeDecodeError as exc:
        raise RequestRejected("request header is not valid UTF-8") from exc
    return _parse_arguments(fields[0], fields[1:])


def _parse_arguments(protocol, arguments):
    """Match ssh ``arguments`` against the exact shape git gives its ssh."""
    remaining = list(arguments)
    send_env = remaining[:2] == ["-o", _SEND_ENV]
    if send_env:
        del remaining[:2]
    address_family = None
    if remaining[:1] and remaining[0] in _ADDRESS_FAMILIES:
        address_family = remaining.pop(0)
    port = None
    if remaining[:1] == ["-p"]:
        port = _validated_port(remaining[1:2])
        del remaining[:2]
    if len(remaining) != 2:
        raise RequestRejected(f"unexpected ssh arguments {_shorten(arguments)}")
    destination, command = remaining
    if not _DESTINATION.fullmatch(destination):
        raise RequestRejected(f"refusing ssh destination {_shorten(destination)}")
    match = _COMMAND.fullmatch(command)
    if match is None or match.group(1).startswith("-"):
        raise RequestRejected(f"refusing remote command {_shorten(command)}")
    if send_env and not _PROTOCOL.fullmatch(protocol):
        raise RequestRejected(f"refusing GIT_PROTOCOL {_shorten(protocol)}")
    return SshRequest(
        destination=destination,
        command=command,
        port=port,
        address_family=address_family,
        protocol=protocol if send_env else None,
    )


def _validated_port(value):
    """Return the single port argument if it is a valid TCP port number."""
    if len(value) != 1 or not _PORT.fullmatch(value[0]) or not 0 < int(value[0]) < 65536:
        raise RequestRejected(f"refusing ssh port {_shorten(value)}")
    return value[0]


def _shorten(value, limit=120):
    """Render an untrusted value safely (escaped, truncated) for an error message."""
    text = repr(value)
    return text if len(text) <= limit else text[: limit - 3] + "..."
