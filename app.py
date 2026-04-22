#!/usr/bin/env python3
import os
import pathlib

import aws_cdk as cdk

from infra.app_builder import build_app
from infra.models.app_config import load_config
from infra.models.asset_loader import AssetLoader

REPO_ROOT = pathlib.Path(__file__).parent

app = cdk.App()
build_app(
    app,
    cfg=load_config(REPO_ROOT / "config.toml"),
    assets=AssetLoader(REPO_ROOT),
    env=cdk.Environment(
        account=os.getenv("CDK_DEFAULT_ACCOUNT"),
        region=os.getenv("CDK_DEFAULT_REGION"),
    ),
)
app.synth()
