import argparse
import getpass
import json
import pathlib
import subprocess
import sys
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


@dataclass
class Inputs:
    public_domain: str
    private_domain: str
    data_database_username: str
    data_database_password: str | None
    authentik_secret_key: str | None
    authentik_bootstrap_email: str
    authentik_bootstrap_password: str | None
    authentik_smtp_username: str
    authentik_smtp_password: str | None
    headscale_oidc_client_id: str | None
    headscale_oidc_client_secret: str | None
    headplane_oidc_client_id: str | None
    headplane_oidc_client_secret: str | None


def collect_inputs(
    args: argparse.Namespace, public_domain: str, private_domain: str
) -> Inputs:
    data_database_username = resolve_arg(
        args.data_database_username
    ) or prompt_required("Data database master username", default="postgres")

    data_database_password = resolve_arg(args.data_database_password)
    if args.data_database_password is None:
        data_database_password = prompt_password_or_default(
            "Data database master password"
        )

    authentik_secret_key = resolve_arg(args.authentik_secret_key)
    if args.authentik_secret_key is None:
        authentik_secret_key = prompt_password_or_default("Authentik secret key")

    authentik_bootstrap_email = resolve_arg(
        args.authentik_bootstrap_email
    ) or prompt_required("Authentik bootstrap email")

    authentik_bootstrap_password = resolve_arg(args.authentik_bootstrap_password)
    if args.authentik_bootstrap_password is None:
        authentik_bootstrap_password = prompt_password_or_default(
            "Authentik bootstrap password"
        )

    authentik_smtp_username = resolve_arg(
        args.authentik_smtp_username
    ) or prompt_required("Authentik SMTP username", default="authentik")

    authentik_smtp_password = resolve_arg(args.authentik_smtp_password)
    if args.authentik_smtp_password is None:
        authentik_smtp_password = prompt_password_or_default("Authentik SMTP password")

    headscale_oidc_client_id = resolve_arg(args.headscale_oidc_client_id)
    headscale_oidc_client_secret = resolve_arg(args.headscale_oidc_client_secret)
    headplane_oidc_client_id = resolve_arg(args.headplane_oidc_client_id)
    headplane_oidc_client_secret = resolve_arg(args.headplane_oidc_client_secret)

    return Inputs(
        public_domain=public_domain,
        private_domain=private_domain,
        data_database_username=data_database_username,
        data_database_password=data_database_password,
        authentik_secret_key=authentik_secret_key,
        authentik_bootstrap_email=authentik_bootstrap_email,
        authentik_bootstrap_password=authentik_bootstrap_password,
        authentik_smtp_username=authentik_smtp_username,
        authentik_smtp_password=authentik_smtp_password,
        headscale_oidc_client_id=headscale_oidc_client_id,
        headscale_oidc_client_secret=headscale_oidc_client_secret,
        headplane_oidc_client_id=headplane_oidc_client_id,
        headplane_oidc_client_secret=headplane_oidc_client_secret,
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


def write_secret_cmd(
    secret_name: str,
    *,
    template: dict | None = None,
    key: str | None = None,
    length: int | None = None,
    bytes_: int | None = None,
    exclude_punctuation: bool = False,
    use_stdin: bool = False,
) -> list[str]:
    cmd = [str(WRITE_SECRET), secret_name]
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


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Interactive bootstrap: creates the hosted zone and seeds Authentik secrets. "
            "Domain is read from config.toml. Flag values prefixed with '@' are read "
            "from the named file."
        )
    )
    parser.add_argument("--data-database-username")
    parser.add_argument("--data-database-password")
    parser.add_argument("--authentik-secret-key")
    parser.add_argument("--authentik-bootstrap-email")
    parser.add_argument("--authentik-bootstrap-password")
    parser.add_argument("--authentik-smtp-username")
    parser.add_argument("--authentik-smtp-password")
    parser.add_argument("--headscale-oidc-client-id")
    parser.add_argument("--headscale-oidc-client-secret")
    parser.add_argument("--headplane-oidc-client-id")
    parser.add_argument("--headplane-oidc-client-secret")
    args = parser.parse_args()

    cfg = load_config(CONFIG_PATH)
    inputs = collect_inputs(
        args,
        public_domain=cfg.foundation.public_domain,
        private_domain=cfg.foundation.private_domain,
    )

    run(
        [str(CREATE_HOSTED_ZONE), inputs.public_domain],
        "create-hosted-zone (public)",
    )
    run(
        [str(CREATE_HOSTED_ZONE), inputs.private_domain],
        "create-hosted-zone (private)",
    )

    data_database_template = {"username": inputs.data_database_username}
    if inputs.data_database_password is not None:
        run(
            write_secret_cmd(
                "data/database",
                template=data_database_template,
                key="password",
                exclude_punctuation=True,
                use_stdin=True,
            ),
            "write-secret data/database",
            stdin_value=inputs.data_database_password,
        )
    else:
        run(
            write_secret_cmd(
                "data/database",
                template=data_database_template,
                key="password",
                length=32,
                exclude_punctuation=True,
            ),
            "write-secret data/database",
        )

    if inputs.authentik_secret_key is not None:
        run(
            write_secret_cmd("authentik/secret-key", use_stdin=True),
            "write-secret authentik/secret-key",
            stdin_value=inputs.authentik_secret_key,
        )
    else:
        run(
            write_secret_cmd(
                "authentik/secret-key", length=50, exclude_punctuation=True
            ),
            "write-secret authentik/secret-key",
        )

    authentik_bootstrap_template = {
        "email": inputs.authentik_bootstrap_email,
        "username": "akadmin",
    }
    if inputs.authentik_bootstrap_password is not None:
        run(
            write_secret_cmd(
                "authentik/bootstrap",
                template=authentik_bootstrap_template,
                key="password",
                use_stdin=True,
            ),
            "write-secret authentik/bootstrap",
            stdin_value=inputs.authentik_bootstrap_password,
        )
    else:
        run(
            write_secret_cmd(
                "authentik/bootstrap",
                template=authentik_bootstrap_template,
                key="password",
                length=32,
            ),
            "write-secret authentik/bootstrap",
        )

    authentik_smtp_template = {"username": inputs.authentik_smtp_username}
    if inputs.authentik_smtp_password is not None:
        run(
            write_secret_cmd(
                "authentik/smtp",
                template=authentik_smtp_template,
                key="password",
                use_stdin=True,
            ),
            "write-secret authentik/smtp",
            stdin_value=inputs.authentik_smtp_password,
        )
    else:
        run(
            write_secret_cmd(
                "authentik/smtp",
                template=authentik_smtp_template,
                key="password",
                length=32,
                exclude_punctuation=True,
            ),
            "write-secret authentik/smtp",
        )

    headscale_oidc_template = {"client_id": inputs.headscale_oidc_client_id or ""}
    headscale_oidc_value = inputs.headscale_oidc_client_secret or ""
    run(
        write_secret_cmd(
            "headscale/oidc",
            template=headscale_oidc_template,
            key="client_secret",
            use_stdin=True,
        ),
        "write-secret headscale/oidc",
        stdin_value=headscale_oidc_value,
    )

    headplane_oidc_template = {"client_id": inputs.headplane_oidc_client_id or ""}
    headplane_oidc_value = inputs.headplane_oidc_client_secret or ""
    run(
        write_secret_cmd(
            "headplane/oidc",
            template=headplane_oidc_template,
            key="client_secret",
            use_stdin=True,
        ),
        "write-secret headplane/oidc",
        stdin_value=headplane_oidc_value,
    )

    run(
        write_secret_cmd("headplane/cookie-secret", bytes_=32),
        "write-secret headplane/cookie-secret",
    )

    run(
        write_secret_cmd("headscale/noise-private-key", bytes_=32),
        "write-secret headscale/noise-private-key",
    )

    # Empty placeholder - the HeadscaleStack custom resource populates this
    # with the real API key after Headscale is up.
    run(
        write_secret_cmd("headscale/admin-api-key", use_stdin=True),
        "write-secret headscale/admin-api-key",
        stdin_value="",
    )

    return 0


if __name__ == "__main__":
    sys.exit(main())
