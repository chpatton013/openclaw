#!/usr/bin/env python3
import os
import pathlib

import aws_cdk as cdk

from infra.models.app_config import load_config
from infra.models.asset_loader import AssetLoader
from infra.stacks.authentik_stack import AuthentikImports, AuthentikStack
from infra.stacks.data_stack import DataImports, DataStack
from infra.stacks.foundation_stack import FoundationImports, FoundationStack
from infra.stacks.headscale_stack import HeadscaleImports, HeadscaleStack
from infra.stacks.openclaw_stack import OpenClawStack
from infra.stacks.webfinger_stack import WebFingerImports, WebFingerStack

REPO_ROOT = pathlib.Path(__file__).parent

app = cdk.App()

cfg = load_config(REPO_ROOT / "config.toml")
assets = AssetLoader(REPO_ROOT)
env = cdk.Environment(
    account=os.getenv("CDK_DEFAULT_ACCOUNT"), region=os.getenv("CDK_DEFAULT_REGION")
)

authentik_issuer_base = (
    f"https://{cfg.authentik.subdomain}.{cfg.foundation.public_domain}/application/o"
)
headscale_fqdn = f"{cfg.headscale.headscale_subdomain}.{cfg.foundation.public_domain}"
headplane_fqdn = f"{cfg.headscale.headplane_subdomain}.{cfg.foundation.public_domain}"

shared = FoundationStack(
    app,
    "FoundationStack",
    imports=FoundationImports(cfg=cfg.foundation),
    env=env,
).exports
data = DataStack(
    app,
    "DataStack",
    imports=DataImports(
        cfg=cfg.data,
        shared=shared,
        databases=[cfg.authentik.db, cfg.headscale.db],
        assets=assets,
    ),
    env=env,
).exports
AuthentikStack(
    app,
    "AuthentikStack",
    imports=AuthentikImports(
        cfg=cfg.authentik,
        shared=shared,
        data=data,
        assets=assets,
        tailscale_redirect_uri="https://login.tailscale.com/a/oauth_response",
        headscale_redirect_uri=f"https://{headscale_fqdn}/oidc/callback",
        headplane_redirect_uri=f"https://{headplane_fqdn}/oidc/callback",
    ),
    env=env,
)
WebFingerStack(
    app,
    "WebFingerStack",
    imports=WebFingerImports(
        cfg=cfg.webfinger,
        shared=shared,
        assets=assets,
        authentik_issuer_base=authentik_issuer_base,
    ),
    env=env,
)
HeadscaleStack(
    app,
    "HeadscaleStack",
    imports=HeadscaleImports(
        cfg=cfg.headscale,
        shared=shared,
        data=data,
        assets=assets,
        authentik_issuer_base=authentik_issuer_base,
    ),
    env=env,
)
OpenClawStack(app, "OpenClawStack", env=env)

app.synth()
