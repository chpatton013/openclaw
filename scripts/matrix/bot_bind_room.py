"""Bind the OpenClaw Matrix bot to a control room.

Two-step bring-up after the OpenClaw EC2 instance is deployed:

1. Write the room ID to SSM Parameter Store at the path the bot's
   prestart helper reads from.
2. Send an SSM `RunShellScript` command to the OpenClaw instance to
   restart the bot's user systemd service so it picks up the new
   value.

Idempotent: rerunning with the same room ID is a no-op (`Overwrite=
True` on the parameter, plain restart of an already-running unit).

Manual prerequisites: a room must already exist in Matrix and the
bot account `@openclaw-bot:<public_domain>` must have been (or
about to be) invited to it. The bot's auto-join handler accepts
the invite as soon as the SSM param matches the room.
"""

import argparse
import re
import sys
import time

import boto3

ROOM_ID_RE = re.compile(r"^![A-Za-z0-9_=/+\-.]+:[A-Za-z0-9.\-]+$")
SSM_PARAM_NAME = "/openclaw-matrix-bot/control-room-id"
STACK_NAME = "OpenClawStack"
RESTART_CMD = (
    'sudo -iu ubuntu XDG_RUNTIME_DIR="/run/user/$(id -u ubuntu)" '
    "systemctl --user restart openclaw-matrix-bot.service"
)


def _instance_id(cfn) -> str:
    stacks = cfn.describe_stacks(StackName=STACK_NAME)
    outputs = {
        o["OutputKey"]: o["OutputValue"] for o in stacks["Stacks"][0].get("Outputs", [])
    }
    instance_id = outputs.get("InstanceId")
    if not instance_id:
        raise RuntimeError(f"{STACK_NAME} has no InstanceId output")
    return instance_id


def _wait_for_command(ssm, command_id: str, instance_id: str) -> dict:
    for _ in range(30):
        time.sleep(2)
        try:
            inv = ssm.get_command_invocation(
                CommandId=command_id, InstanceId=instance_id
            )
        except ssm.exceptions.InvocationDoesNotExist:
            continue
        if inv["Status"] in ("InProgress", "Pending", "Delayed"):
            continue
        return inv
    raise RuntimeError(f"timed out waiting for SSM command {command_id}")


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Bind the OpenClaw Matrix bot to a control room."
    )
    parser.add_argument(
        "room_id",
        help="Matrix room ID, e.g. '!abc:example.com'",
    )
    args = parser.parse_args()

    if not ROOM_ID_RE.match(args.room_id):
        sys.stderr.write(
            f"invalid room id {args.room_id!r}; expected '!<localpart>:<domain>'\n"
        )
        return 2

    ssm = boto3.client("ssm")
    cfn = boto3.client("cloudformation")

    sys.stderr.write(f"setting {SSM_PARAM_NAME} = {args.room_id}\n")
    ssm.put_parameter(
        Name=SSM_PARAM_NAME,
        Value=args.room_id,
        Type="String",
        Overwrite=True,
    )

    instance_id = _instance_id(cfn)
    sys.stderr.write(f"restarting bot on {instance_id}\n")

    response = ssm.send_command(
        InstanceIds=[instance_id],
        DocumentName="AWS-RunShellScript",
        Parameters={"commands": [RESTART_CMD]},
    )
    command_id = response["Command"]["CommandId"]

    inv = _wait_for_command(ssm, command_id, instance_id)
    if inv["Status"] != "Success":
        sys.stderr.write(
            f"restart failed: status={inv['Status']}\n"
            f"stdout: {inv.get('StandardOutputContent', '')}\n"
            f"stderr: {inv.get('StandardErrorContent', '')}\n"
        )
        return 3

    sys.stderr.write("bot restarted; tailing journal for 10s...\n")
    tail_response = ssm.send_command(
        InstanceIds=[instance_id],
        DocumentName="AWS-RunShellScript",
        Parameters={
            "commands": [
                'sudo -iu ubuntu XDG_RUNTIME_DIR="/run/user/$(id -u ubuntu)" '
                "journalctl --user -u openclaw-matrix-bot.service --no-pager -n 30"
            ]
        },
    )
    tail_inv = _wait_for_command(
        ssm, tail_response["Command"]["CommandId"], instance_id
    )
    sys.stdout.write(tail_inv.get("StandardOutputContent", ""))
    return 0


if __name__ == "__main__":
    sys.exit(main())
