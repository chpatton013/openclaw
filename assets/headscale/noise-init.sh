#!/bin/sh
# Seeds the headscale noise key + minimal config into the shared
# volume. Run by SharedVolumeInit (the aws-cli image, so /bin/sh is
# busybox-ash). Inputs:
#   NOISE_SECRET_NAME      Secrets Manager name; secret JSON has a
#                          base64-encoded raw key in `.secret`.
#   NOISE_KEY_PATH         where to write the binary key file.
#   NOISE_CONFIG_PATH      where to write the headscale config.yaml.
set -eu

touch "${NOISE_KEY_PATH}"
chmod 600 "${NOISE_KEY_PATH}"
aws secretsmanager get-secret-value \
  --secret-id "${NOISE_SECRET_NAME}" \
  --query SecretString --output text |
  jq -r .secret |
  base64 -d |
  od -An -v -t x1 |
  tr -d "[:space:]" |
  awk '{print "privkey:" $0}' >"${NOISE_KEY_PATH}"
printf 'noise:\n  private_key_path: %s\n' "${NOISE_KEY_PATH}" >"${NOISE_CONFIG_PATH}"
