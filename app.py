#!/usr/bin/env python3
import os
import pathlib

import aws_cdk as cdk

from infra.models.app_config import load_config
from infra.stacks.authentik_stack import AuthentikStack
from infra.stacks.data_stack import DataStack
from infra.stacks.foundation_stack import FoundationStack
from infra.stacks.headscale_stack import HeadscaleStack
from infra.stacks.openclaw_stack import OpenClawStack
from infra.stacks.webfinger_stack import WebFingerStack

app = cdk.App()

cfg = load_config(pathlib.Path(__file__).parent / "config.toml")
env = env = cdk.Environment(
    account=os.getenv("CDK_DEFAULT_ACCOUNT"), region=os.getenv("CDK_DEFAULT_REGION")
)

shared = FoundationStack(app, "FoundationStack", cfg=cfg.foundation, env=env).exports
data = DataStack(
    app,
    "DataStack",
    cfg=cfg.data,
    shared=shared,
    databases=[cfg.authentik.db.name, cfg.headscale.db.name],
    env=env,
).exports
AuthentikStack(
    app, "AuthentikStack", cfg=cfg.authentik, shared=shared, data=data, env=env
)
WebFingerStack(app, "WebFingerStack", cfg=cfg.webfinger, shared=shared, env=env)
HeadscaleStack(
    app, "HeadscaleStack", cfg=cfg.headscale, shared=shared, data=data, env=env
)
OpenClawStack(app, "OpenClawStack", env=env)

app.synth()
