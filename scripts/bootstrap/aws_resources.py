import argparse
import getpass
import json
import pathlib
import secrets
import string
import subprocess
import sys
from collections.abc import Callable
from dataclasses import dataclass


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


@dataclass
class Inputs:
    ghcr_username: str
    ghcr_access_token: str
    dockerhub_username: str
    dockerhub_access_token: str
    data_database_username: str
    data_database_password: str | None
    authentik_secret_key: str | None
    authentik_bootstrap_email: str
    authentik_bootstrap_password: str | None
    authentik_smtp_username: str
    authentik_smtp_password: str | None
    tailscale_oidc_client_id: str
    tailscale_oidc_client_secret: str
    headscale_oidc_client_id: str
    headscale_oidc_client_secret: str | None
    headplane_oidc_client_id: str
    headplane_oidc_client_secret: str | None
    vaultwarden_admin_token: str | None
    vaultwarden_smtp_username: str
    vaultwarden_smtp_password: str | None


def collect_inputs(args: argparse.Namespace) -> Inputs:
    return Inputs(
        ghcr_username=resolve_required(
            args, "ghcr_username", "GitHub username (for ghcr.io pull-through cache)"
        ),
        ghcr_access_token=resolve_secret(
            args, "ghcr_access_token", "GitHub PAT with read:packages scope"
        ),
        dockerhub_username=resolve_required(
            args,
            "dockerhub_username",
            "Docker Hub username (for docker.io pull-through cache)",
        ),
        dockerhub_access_token=resolve_secret(
            args, "dockerhub_access_token", "Docker Hub PAT"
        ),
        data_database_username=resolve_required(
            args,
            "data_database_username",
            "Data database master username",
            default="postgres",
        ),
        data_database_password=resolve_optional_password(
            args, "data_database_password", "Data database master password"
        ),
        authentik_secret_key=resolve_optional_password(
            args, "authentik_secret_key", "Authentik secret key"
        ),
        authentik_bootstrap_email=resolve_required(
            args, "authentik_bootstrap_email", "Authentik bootstrap email"
        ),
        authentik_bootstrap_password=resolve_optional_password(
            args, "authentik_bootstrap_password", "Authentik bootstrap password"
        ),
        authentik_smtp_username=resolve_required(
            args,
            "authentik_smtp_username",
            "Authentik SMTP username",
            default="authentik",
        ),
        authentik_smtp_password=resolve_optional_password(
            args, "authentik_smtp_password", "Authentik SMTP password"
        ),
        tailscale_oidc_client_id=resolve_required(
            args,
            "tailscale_oidc_client_id",
            "Tailscale OIDC client ID (from Authentik)",
        ),
        tailscale_oidc_client_secret=resolve_secret(
            args,
            "tailscale_oidc_client_secret",
            "Tailscale OIDC client secret (from Authentik)",
        ),
        headscale_oidc_client_id=resolve_or_generate(
            args, "headscale_oidc_client_id", generate_oidc_client_id
        ),
        headscale_oidc_client_secret=resolve_arg(args.headscale_oidc_client_secret),
        headplane_oidc_client_id=resolve_or_generate(
            args, "headplane_oidc_client_id", generate_oidc_client_id
        ),
        headplane_oidc_client_secret=resolve_arg(args.headplane_oidc_client_secret),
        vaultwarden_admin_token=resolve_optional_password(
            args, "vaultwarden_admin_token", "Vaultwarden admin token"
        ),
        vaultwarden_smtp_username=resolve_required(
            args,
            "vaultwarden_smtp_username",
            "Vaultwarden SMTP username",
            default="vaultwarden",
        ),
        vaultwarden_smtp_password=resolve_optional_password(
            args, "vaultwarden_smtp_password", "Vaultwarden SMTP password"
        ),
    )


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
            "from the named file."
        )
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
    public_domain = cfg.foundation.public_domain
    private_domain = cfg.foundation.private_domain
    inputs = collect_inputs(args)

    run([str(CREATE_HOSTED_ZONE), public_domain], "create-hosted-zone (public)")
    run([str(CREATE_HOSTED_ZONE), private_domain], "create-hosted-zone (private)")

    write_secret(
        "ecr-pullthroughcache/ghcr",
        template={"username": inputs.ghcr_username},
        key="accessToken",
        provided=inputs.ghcr_access_token,
    )
    write_secret(
        "ecr-pullthroughcache/dockerhub",
        template={"username": inputs.dockerhub_username},
        key="accessToken",
        provided=inputs.dockerhub_access_token,
    )

    write_secret(
        "data/database",
        template={"username": inputs.data_database_username},
        key="password",
        provided=inputs.data_database_password,
        length=32,
        exclude_punctuation=True,
    )

    for service in ("authentik", "headscale", "vaultwarden"):
        write_secret(
            f"{service}/database",
            template={"username": service},
            key="password",
            length=32,
            exclude_punctuation=True,
        )

    write_secret(
        "authentik/secret-key",
        provided=inputs.authentik_secret_key,
        length=50,
        exclude_punctuation=True,
    )

    write_secret(
        "authentik/bootstrap",
        template={
            "email": inputs.authentik_bootstrap_email,
            "username": "akadmin",
        },
        key="password",
        provided=inputs.authentik_bootstrap_password,
        length=32,
    )

    write_secret(
        "authentik/smtp",
        template={"username": inputs.authentik_smtp_username},
        key="password",
        provided=inputs.authentik_smtp_password,
        length=32,
        exclude_punctuation=True,
    )

    write_secret(
        "authentik/oidc/tailscale",
        template={"client_id": inputs.tailscale_oidc_client_id},
        key="client_secret",
        provided=inputs.tailscale_oidc_client_secret,
    )

    for slug, client_id, client_secret in (
        (
            "headscale",
            inputs.headscale_oidc_client_id,
            inputs.headscale_oidc_client_secret,
        ),
        (
            "headplane",
            inputs.headplane_oidc_client_id,
            inputs.headplane_oidc_client_secret,
        ),
    ):
        write_secret(
            f"authentik/oidc/{slug}",
            template={"client_id": client_id},
            key="client_secret",
            provided=client_secret,
            length=128,
            exclude_punctuation=True,
        )

    write_secret("headplane/cookie-secret", bytes_=32)
    write_secret("headscale/noise-private-key", bytes_=32)
    # Empty placeholder - the HeadscaleStack custom resource populates this
    # with the real API key after Headscale is up.
    write_secret("headscale/admin-api-key", provided="")

    write_secret(
        "vaultwarden/admin-token",
        provided=inputs.vaultwarden_admin_token,
        length=64,
        exclude_punctuation=True,
    )

    write_secret(
        "vaultwarden/smtp",
        template={"username": inputs.vaultwarden_smtp_username},
        key="password",
        provided=inputs.vaultwarden_smtp_password,
        length=32,
        exclude_punctuation=True,
    )

    return 0


if __name__ == "__main__":
    sys.exit(main())
