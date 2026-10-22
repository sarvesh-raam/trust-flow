"""Request the free Oracle Cloud Ampere server until capacity frees up.

Run every few minutes by .github/workflows/oracle-retry.yml. Each run:
  - does nothing if an instance named "trustflow" already exists;
  - otherwise tries to launch one (2 OCPU / 12 GB, then 1 OCPU / 6 GB) in the
    public subnet made by the VCN wizard, and stops quietly on "out of capacity".

Only Always-Free-eligible resources are requested (VM.Standard.A1.Flex within
the free 4 OCPU / 24 GB allowance, default boot volume).

Environment (GitHub secrets): OCI_USER_OCID, OCI_TENANCY_OCID, OCI_FINGERPRINT,
OCI_REGION, OCI_PRIVATE_KEY (the .pem contents), SSH_PUBLIC_KEY.
"""
from __future__ import annotations

import os
import sys
import time

import oci

INSTANCE_NAME = "trustflow"
VCN_NAME = "trustflow-vcn"
SUBNET_NAME = "public subnet-trustflow-vcn"
SHAPE = "VM.Standard.A1.Flex"
SIZES = [(2, 12), (1, 6)]  # (OCPUs, GB memory), best first


def _config() -> dict:
    config = {
        "user": os.environ["OCI_USER_OCID"],
        "tenancy": os.environ["OCI_TENANCY_OCID"],
        "fingerprint": os.environ["OCI_FINGERPRINT"],
        "region": os.environ["OCI_REGION"],
        "key_content": os.environ["OCI_PRIVATE_KEY"],
    }
    oci.config.validate_config(config)
    return config


def _output(**values: str) -> None:
    """Expose values to later workflow steps."""
    path = os.getenv("GITHUB_OUTPUT")
    if path:
        with open(path, "a", encoding="utf-8") as fh:
            for key, value in values.items():
                fh.write(f"{key}={value}\n")


def _existing_instance(compute: oci.core.ComputeClient, compartment: str):
    for inst in compute.list_instances(compartment, display_name=INSTANCE_NAME).data:
        if inst.lifecycle_state not in ("TERMINATED", "TERMINATING"):
            return inst
    return None


def _subnet_id(network: oci.core.VirtualNetworkClient, compartment: str) -> str:
    vcns = network.list_vcns(compartment, display_name=VCN_NAME).data
    if not vcns:
        sys.exit(f"No VCN named {VCN_NAME!r} — create it with the VCN wizard first")
    subnets = network.list_subnets(compartment, vcn_id=vcns[0].id, display_name=SUBNET_NAME).data
    if not subnets:
        sys.exit(f"No subnet named {SUBNET_NAME!r} in {VCN_NAME}")
    return subnets[0].id


def _image_id(compute: oci.core.ComputeClient, compartment: str) -> str:
    images = compute.list_images(
        compartment,
        operating_system="Canonical Ubuntu",
        operating_system_version="24.04",
        shape=SHAPE,
        sort_by="TIMECREATED",
        sort_order="DESC",
    ).data
    if not images:
        sys.exit("No Ubuntu 24.04 image found for VM.Standard.A1.Flex")
    return images[0].id


def _public_ip(compute, network, compartment: str, instance_id: str) -> str:
    """Wait for the instance to run and return its public IP ("" if not yet known)."""
    for _ in range(30):
        inst = compute.get_instance(instance_id).data
        if inst.lifecycle_state == "RUNNING":
            for att in compute.list_vnic_attachments(compartment, instance_id=instance_id).data:
                vnic = network.get_vnic(att.vnic_id).data
                if vnic.public_ip:
                    return vnic.public_ip
        time.sleep(10)
    return ""


def main() -> None:
    config = _config()
    compartment = config["tenancy"]  # root compartment
    compute = oci.core.ComputeClient(config)
    network = oci.core.VirtualNetworkClient(config)
    identity = oci.identity.IdentityClient(config)

    existing = _existing_instance(compute, compartment)
    if existing:
        print(f"Instance {INSTANCE_NAME!r} already exists ({existing.lifecycle_state}) — nothing to do.")
        _output(created="false")
        return

    subnet_id = _subnet_id(network, compartment)
    image_id = _image_id(compute, compartment)
    domains = [ad.name for ad in identity.list_availability_domains(compartment).data]

    attempts = 0
    for ocpus, memory in SIZES:
        for ad in domains:
            if attempts:
                time.sleep(30)  # back-to-back launches get HTTP 429
            attempts += 1
            details = oci.core.models.LaunchInstanceDetails(
                availability_domain=ad,
                compartment_id=compartment,
                display_name=INSTANCE_NAME,
                shape=SHAPE,
                shape_config=oci.core.models.LaunchInstanceShapeConfigDetails(
                    ocpus=float(ocpus), memory_in_gbs=float(memory),
                ),
                source_details=oci.core.models.InstanceSourceViaImageDetails(image_id=image_id),
                create_vnic_details=oci.core.models.CreateVnicDetails(
                    subnet_id=subnet_id, assign_public_ip=True,
                ),
                metadata={"ssh_authorized_keys": os.environ["SSH_PUBLIC_KEY"].strip()},
            )
            try:
                instance = compute.launch_instance(details).data
            except oci.exceptions.ServiceError as exc:
                if "capacity" in (exc.message or "").lower():
                    print(f"Out of capacity: {ocpus} OCPU / {memory} GB in {ad}")
                    continue
                if exc.status == 429:
                    # Oracle also throttles launches this way while capacity is short.
                    print(f"Rate limited: {ocpus} OCPU / {memory} GB in {ad}")
                    continue
                raise
            print(f"Launched {ocpus} OCPU / {memory} GB in {ad}: {instance.id}")
            ip = _public_ip(compute, network, compartment, instance.id)
            print(f"Public IP: {ip or 'not assigned yet — check the Oracle console'}")
            _output(created="true", ip=ip, size=f"{ocpus} OCPU / {memory} GB")
            return

    print("No capacity anywhere this run — will try again next run.")
    _output(created="false")


if __name__ == "__main__":
    main()
