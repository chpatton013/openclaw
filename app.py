#!/usr/bin/env python3
import os
import pathlib

import aws_cdk as cdk

from infra.models.app_config import load_config
from infra.stacks.authentik_stack import AuthentikStack
from infra.stacks.foundation_stack import FoundationStack
from infra.stacks.openclaw_stack import OpenClawStack


app = cdk.App()

cfg = load_config(pathlib.Path(__file__).parent / "config.toml")
env = env=cdk.Environment(account=os.getenv('CDK_DEFAULT_ACCOUNT'), region=os.getenv('CDK_DEFAULT_REGION'))

shared = FoundationStack(app, "FoundationStack", cfg=cfg, env=env).exports
AuthentikStack(app, "AuthentikStack", cfg=cfg.authentik, shared=shared, env=env)
OpenClawStack(app, "OpenClawStack", env=env)

app.synth()
