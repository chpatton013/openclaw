import argparse
import getpass
import json
import pathlib
import secrets
import string
import subprocess
import sys
from collections.abc import Callable

import boto3


def find_repo_root(start: pathlib.Path) -> pathlib.Path:
    output = subprocess.run(
        ["git", "-C", start, "rev-parse", "--show-toplevel"],
        check=True,
        capture_output=True,
        text=True,
    )

    return pathlib.Path(output.stdout.strip())


HERE = pathlib.Path(__file__).parent
REPO_ROOT = find_repo_root(HERE)
BIN_DIR = REPO_ROOT / "bin"
CONFIG_PATH = REPO_ROOT / "config.toml"
CREATE_HOSTED_ZONE = BIN_DIR / "aws-create-hosted-zone"
WRITE_SECRET = BIN_DIR / "aws-write-secret"

sys.path.insert(0, str(REPO_ROOT))
from infra.models.app_config import load_config  # noqa: E402


def resolve_arg(value: str | None) -> str | None:
    """Resolve a flag value, handling the `@path` file-reference convention."""
    if value is None:
        return None
    if value.startswith("@"):
        with open(value[1:]) as f:
            return f.read().rstrip("\r\n")
    return value


def prompt_required(label: str, default: str | None = None) -> str:
    suffix = f" [{default}]" if default else ""
    while True:
        value = input(f"{label}{suffix}: ").strip()
        if value:
            return value
        if default is not None:
            return default
        print("A value is required.", file=sys.stderr)


def prompt_password_or_default(label: str) -> str | None:
    """Ask for a password twice. Empty input means 'use the auto-generated default'."""
    while True:
        first = getpass.getpass(f"{label} (leave blank to auto-generate): ")
        if first == "":
            return None
        second = getpass.getpass(f"{label} (confirm): ")
        if first == second:
            return first
        print("Passwords did not match; try again.", file=sys.stderr)


def generate_oidc_client_id() -> str:
    alphabet = string.ascii_letters + string.digits
    return "".join(secrets.choice(alphabet) for _ in range(40))


def resolve_required(
    args: argparse.Namespace,
    flag: str,
    label: str,
    *,
    default: str | None = None,
) -> str:
    return resolve_arg(getattr(args, flag)) or prompt_required(label, default=default)


def resolve_secret(args: argparse.Namespace, flag: str, label: str) -> str:
    return resolve_arg(getattr(args, flag)) or getpass.getpass(f"{label}: ")


def resolve_optional_password(
    args: argparse.Namespace, flag: str, label: str
) -> str | None:
    # Re-prompt only when the raw flag is None, so `--flag @empty.file` still
    # produces "" without interactively prompting.
    if getattr(args, flag) is None:
        return prompt_password_or_default(label)
    return resolve_arg(getattr(args, flag))


def resolve_or_generate(
    args: argparse.Namespace, flag: str, gen: Callable[[], str]
) -> str:
    return resolve_arg(getattr(args, flag)) or gen()


def fetch_existing_secrets() -> set[str]:
    client = boto3.client("secretsmanager")
    paginator = client.get_paginator("list_secrets")
    names: set[str] = set()
    for page in paginator.paginate():
        for entry in page.get("SecretList", []):
            names.add(entry["Name"])
    return names


def needs_write(name: str, existing: set[str]) -> bool:
    """True if the secret is missing. If it exists, log the skip and return False."""
    if name in existing:
        print(f"secret '{name}' already exists; skipping", file=sys.stderr)
        return False
    return True


def run(cmd: list[str], label: str, stdin_value: str | None = None) -> None:
    result = subprocess.run(
        cmd,
        input=stdin_value if stdin_value is not None else None,
        text=True,
    )
    if result.returncode != 0:
        sys.stderr.write(f"Failed: {label}\n")
        sys.exit(result.returncode)


def _write_secret_cmd(
    secret_name: str,
    *,
    template: dict | None = None,
    key: str | None = None,
    length: int | None = None,
    bytes_: int | None = None,
    exclude_punctuation: bool = False,
    use_stdin: bool = False,
) -> list[str]:
    cmd = [str(WRITE_SECRET), secret_name, "--skip-if-exists"]
    if template is not None:
        assert key is not None
        cmd.extend(["--template", json.dumps(template), "--key", key])
    if exclude_punctuation:
        cmd.append("--exclude-punctuation")
    if use_stdin:
        cmd.append("-")
    elif bytes_ is not None:
        cmd.extend([f"--bytes={bytes_}"])
    elif length is not None:
        cmd.extend([f"--length={length}"])
    return cmd


def write_secret(
    name: str,
    *,
    template: dict | None = None,
    key: str | None = None,
    provided: str | None = None,
    length: int | None = None,
    bytes_: int | None = None,
    exclude_punctuation: bool = False,
) -> None:
    """
    Write a Secrets Manager secret.

    If `provided` is not None, that value is written via stdin (including
    empty string). Otherwise the secret is generated using `length` / `bytes_`
    / `exclude_punctuation`, which act as a fallback spec and are ignored when
    `provided` is set.
    """
    label = f"write-secret {name}"
    if provided is not None:
        cmd = _write_secret_cmd(name, template=template, key=key, use_stdin=True)
        run(cmd, label, stdin_value=provided)
        return
    cmd = _write_secret_cmd(
        name,
        template=template,
        key=key,
        length=length,
        bytes_=bytes_,
        exclude_punctuation=exclude_punctuation,
    )
    run(cmd, label)


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Interactive bootstrap: creates the hosted zone and seeds Authentik secrets. "
            "Domain is read from config.toml. Flag values prefixed with '@' are read "
            "from the named file. Pass --skip-if-exists to avoid prompting for inputs "
            "whose secrets already exist in Secrets Manager."
        )
    )
    parser.add_argument(
        "--skip-if-exists",
        action="store_true",
        help="Skip prompting and writing for secrets that already exist.",
    )
    parser.add_argument("--ghcr-username")
    parser.add_argument("--ghcr-access-token")
    parser.add_argument("--dockerhub-username")
    parser.add_argument("--dockerhub-access-token")
    parser.add_argument("--data-database-username")
    parser.add_argument("--data-database-password")
    parser.add_argument("--authentik-secret-key")
    parser.add_argument("--authentik-bootstrap-email")
    parser.add_argument("--authentik-bootstrap-password")
    parser.add_argument("--authentik-smtp-username")
    parser.add_argument("--authentik-smtp-password")
    parser.add_argument("--tailscale-oidc-client-id")
    parser.add_argument("--tailscale-oidc-client-secret")
    parser.add_argument("--headscale-oidc-client-id")
    parser.add_argument("--headscale-oidc-client-secret")
    parser.add_argument("--headplane-oidc-client-id")
    parser.add_argument("--headplane-oidc-client-secret")
    parser.add_argument("--vaultwarden-admin-token")
    parser.add_argument("--vaultwarden-smtp-username")
    parser.add_argument("--vaultwarden-smtp-password")
    args = parser.parse_args()

    cfg = load_config(CONFIG_PATH)
    existing = fetch_existing_secrets() if args.skip_if_exists else set()

    run(
        [str(CREATE_HOSTED_ZONE), cfg.foundation.public_domain],
        "create-hosted-zone (public)",
    )
    run(
        [str(CREATE_HOSTED_ZONE), cfg.foundation.private_domain],
        "create-hosted-zone (private)",
    )

    if needs_write("ecr-pullthroughcache/ghcr", existing):
        write_secret(
            "ecr-pullthroughcache/ghcr",
            template={
                "username": resolve_required(
                    args,
                    "ghcr_username",
                    "GitHub username (for ghcr.io pull-through cache)",
                )
            },
            key="accessToken",
            provided=resolve_secret(
                args, "ghcr_access_token", "GitHub PAT with read:packages scope"
            ),
        )

    if needs_write("ecr-pullthroughcache/dockerhub", existing):
        write_secret(
            "ecr-pullthroughcache/dockerhub",
            template={
                "username": resolve_required(
                    args,
                    "dockerhub_username",
                    "Docker Hub username (for docker.io pull-through cache)",
                )
            },
            key="accessToken",
            provided=resolve_secret(args, "dockerhub_access_token", "Docker Hub PAT"),
        )

    if needs_write("data/database", existing):
        write_secret(
            "data/database",
            template={
                "username": resolve_required(
                    args,
                    "data_database_username",
                    "Data database master username",
                    default="postgres",
                )
            },
            key="password",
            provided=resolve_optional_password(
                args, "data_database_password", "Data database master password"
            ),
            length=32,
            exclude_punctuation=True,
        )

    for service in ("authentik", "headscale", "vaultwarden"):
        name = f"{service}/database"
        if needs_write(name, existing):
            write_secret(
                name,
                template={"username": service},
                key="password",
                length=32,
                exclude_punctuation=True,
            )

    if needs_write("authentik/secret-key", existing):
        write_secret(
            "authentik/secret-key",
            template={},
            key="secret",
            provided=resolve_optional_password(
                args, "authentik_secret_key", "Authentik secret key"
            ),
            length=50,
            exclude_punctuation=True,
        )

    if needs_write("authentik/bootstrap", existing):
        write_secret(
            "authentik/bootstrap",
            template={
                "email": resolve_required(
                    args, "authentik_bootstrap_email", "Authentik bootstrap email"
                ),
                "username": "akadmin",
            },
            key="password",
            provided=resolve_optional_password(
                args, "authentik_bootstrap_password", "Authentik bootstrap password"
            ),
            length=32,
        )

    if needs_write("authentik/smtp", existing):
        write_secret(
            "authentik/smtp",
            template={
                "username": resolve_required(
                    args,
                    "authentik_smtp_username",
                    "Authentik SMTP username",
                    default="authentik",
                )
            },
            key="password",
            provided=resolve_optional_password(
                args, "authentik_smtp_password", "Authentik SMTP password"
            ),
            length=32,
            exclude_punctuation=True,
        )

    if needs_write("authentik/oidc/tailscale", existing):
        write_secret(
            "authentik/oidc/tailscale",
            template={
                "client_id": resolve_required(
                    args,
                    "tailscale_oidc_client_id",
                    "Tailscale OIDC client ID (from Authentik)",
                )
            },
            key="client_secret",
            provided=resolve_secret(
                args,
                "tailscale_oidc_client_secret",
                "Tailscale OIDC client secret (from Authentik)",
            ),
        )

    for slug in ("headscale", "headplane"):
        name = f"authentik/oidc/{slug}"
        if needs_write(name, existing):
            write_secret(
                name,
                template={
                    "client_id": resolve_or_generate(
                        args, f"{slug}_oidc_client_id", generate_oidc_client_id
                    )
                },
                key="client_secret",
                provided=resolve_arg(getattr(args, f"{slug}_oidc_client_secret")),
                length=128,
                exclude_punctuation=True,
            )

    if needs_write("headplane/cookie-secret", existing):
        write_secret(
            "headplane/cookie-secret",
            template={},
            key="secret",
            length=32,
            exclude_punctuation=True,
        )
    if needs_write("headscale/noise-private-key", existing):
        write_secret(
            "headscale/noise-private-key", template={}, key="secret", bytes_=32
        )
    # Placeholder - the HeadscaleStack custom resource replaces this with the
    # real API key after Headscale is up. Secrets Manager rejects empty
    # strings, so we write a sentinel that the lambda recognizes.
    if needs_write("headscale/exit-node/preauthkey", existing):
        write_secret(
            "headscale/exit-node/preauthkey",
            template={},
            key="secret",
            provided="pending",
        )
    if needs_write("headscale/admin-api-key", existing):
        write_secret(
            "headscale/admin-api-key", template={}, key="secret", provided="pending"
        )

    if needs_write("vaultwarden/admin-token", existing):
        write_secret(
            "vaultwarden/admin-token",
            template={},
            key="secret",
            provided=resolve_optional_password(
                args, "vaultwarden_admin_token", "Vaultwarden admin token"
            ),
            length=64,
            exclude_punctuation=True,
        )

    if needs_write("vaultwarden/smtp", existing):
        write_secret(
            "vaultwarden/smtp",
            template={
                "username": resolve_required(
                    args,
                    "vaultwarden_smtp_username",
                    "Vaultwarden SMTP username",
                    default="vaultwarden",
                )
            },
            key="password",
            provided=resolve_optional_password(
                args, "vaultwarden_smtp_password", "Vaultwarden SMTP password"
            ),
            length=32,
            exclude_punctuation=True,
        )

    return 0


if __name__ == "__main__":
    sys.exit(main())
