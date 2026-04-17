import argparse
import sys
import uuid

import boto3


def find_matching_zone(client, domain: str) -> list[dict]:
    fqdn = domain if domain.endswith(".") else domain + "."
    response = client.list_hosted_zones_by_name(DNSName=domain)
    return [zone for zone in response["HostedZones"] if zone["Name"] == fqdn]


def main() -> int:
    parser = argparse.ArgumentParser(description="Create a Route53 hosted zone.")
    parser.add_argument("domain", help="Domain name for the hosted zone")
    visibility = parser.add_mutually_exclusive_group()
    visibility.add_argument("--public", dest="private", action="store_false")
    visibility.add_argument("--private", dest="private", action="store_true")
    parser.set_defaults(private=False)
    args = parser.parse_args()

    if args.private:
        parser.error(
            "--private zones require VPC association (id + region) which is not "
            "yet implemented; add those flags when a use case exists."
        )

    client = boto3.client("route53")
    existing = find_matching_zone(client, args.domain)

    for zone in existing:
        is_private = zone.get("Config", {}).get("PrivateZone", False)
        if is_private == args.private:
            print(zone["Id"])
            return 0
        sys.stderr.write(
            f"Zone {zone['Id']} for {zone['Name']} exists with "
            f"PrivateZone={is_private}, but --{'private' if args.private else 'public'} "
            "was requested.\n"
        )
        return 1

    response = client.create_hosted_zone(
        Name=args.domain,
        CallerReference=str(uuid.uuid4()),
        HostedZoneConfig={"PrivateZone": args.private},
    )
    print(response["HostedZone"]["Id"])
    return 0


if __name__ == "__main__":
    sys.exit(main())
