import argparse
import json
import sys

import boto3
from botocore.exceptions import ClientError


def read_input(path: str) -> str:
    if path == "-":
        data = sys.stdin.read()
    else:
        with open(path) as f:
            data = f.read()
    return data.rstrip("\r\n")


def generate_password(
    client, length: int, exclude_punctuation: bool, exclude_characters: str | None
) -> str:
    kwargs = {
        "PasswordLength": length,
        "RequireEachIncludedType": True,
    }
    if exclude_punctuation:
        kwargs["ExcludePunctuation"] = True
    if exclude_characters is not None:
        kwargs["ExcludeCharacters"] = exclude_characters
    return client.get_random_password(**kwargs)["RandomPassword"]


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Create or overwrite a Secrets Manager secret."
    )
    parser.add_argument("secret_name")
    parser.add_argument("--overwrite", action="store_true")

    exclude = parser.add_mutually_exclusive_group()
    exclude.add_argument("--exclude-punctuation", action="store_true")
    exclude.add_argument("--exclude-characters", metavar="CHARSET")

    parser.add_argument("--template", metavar="TEMPLATE")
    parser.add_argument("--key", metavar="KEY")

    # Sentinel lets us distinguish "user passed --length" from "defaulted".
    parser.add_argument("--length", type=int, default=None)
    parser.add_argument("input", nargs="?", default=None, metavar="INPUT")
    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()

    if (args.template is None) != (args.key is None):
        parser.error("--template and --key must be used together or not at all")

    template_dict: dict | None = None
    if args.template is not None:
        try:
            template_dict = json.loads(args.template)
        except json.JSONDecodeError as e:
            parser.error(f"--template must be valid JSON: {e}")
        if not isinstance(template_dict, dict):
            parser.error("--template must parse to a JSON object")
        if args.key in template_dict:
            parser.error(f"--key '{args.key}' already present in --template")

    if args.length is not None and args.input is not None:
        parser.error("--length and INPUT are mutually exclusive")

    length = args.length if args.length is not None else 32

    client = boto3.client("secretsmanager")

    if args.input is not None:
        value = read_input(args.input)
    else:
        value = generate_password(
            client, length, args.exclude_punctuation, args.exclude_characters
        )

    if template_dict is not None:
        template_dict[args.key] = value
        secret_string = json.dumps(template_dict)
    else:
        secret_string = value

    try:
        client.create_secret(Name=args.secret_name, SecretString=secret_string)
        return 0
    except ClientError as e:
        if e.response.get("Error", {}).get("Code") != "ResourceExistsException":
            raise
        if not args.overwrite:
            sys.stderr.write(
                f"Secret '{args.secret_name}' already exists. Pass --overwrite to replace.\n"
            )
            return 1
        client.put_secret_value(SecretId=args.secret_name, SecretString=secret_string)
        return 0


if __name__ == "__main__":
    sys.exit(main())
