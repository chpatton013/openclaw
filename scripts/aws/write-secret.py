import argparse

import boto3

# TODO:
#   usage: SECRET_NAME [ --overwrite] \
#           [ --exclude-punctuation | --exclude-characters=CHARSET ] \
#           [ --template=TEMPLATE --key=KEY ] \
#           [ --length=LEN | INPUT ]
#   overwrite defaults to false
#   mutually-exclusive group for exclude options; defaults to none
#   template and key must be used together or not at all
#   template must be a json dict
#   key must be a string
#   key must not already be in parsed template dict
#   length defaults to 32
#   INPUT is a filepath containing the password data, or - for stdin
#   if INPUT is unset, then generate a secure secret string
#   length and INPUT are mutually exclusive


if __name__ == "__main__":
    pass
