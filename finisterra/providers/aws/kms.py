import os
from ...utils.hcl import HCL
import botocore
import logging

logger = logging.getLogger('finisterra')


class KMS:
    def __init__(self, progress, aws_clients, script_dir, provider_name, provider_name_short,
                 provider_source, provider_version, schema_data, region, s3Bucket,
                 dynamoDBTable, state_key, workspace_id, modules, aws_account_id, output_dir, hcl=None):
        self.progress = progress

        self.aws_clients = aws_clients
        self.transform_rules = {}
        self.provider_name = provider_name
        self.script_dir = script_dir
        self.schema_data = schema_data
        self.region = region
        self.aws_account_id = aws_account_id

        self.workspace_id = workspace_id
        self.modules = modules
        if not hcl:
            self.hcl = HCL(self.schema_data)
        else:
            self.hcl = hcl

        self.hcl.region = region
        self.hcl.output_dir = output_dir
        self.hcl.account_id = aws_account_id

        self.hcl.provider_name = provider_name
        self.hcl.provider_name_short = provider_name_short
        self.hcl.provider_source = provider_source
        self.hcl.provider_version = provider_version

        self.additional_data = {}

    def kms(self):
        self.hcl.prepare_folder()
        self.aws_kms_key()
        self.aws_kms_replica_key()
        self.aws_kms_external_key()
        self.aws_kms_replica_external_key()
        if self.hcl.count_state():
            self.progress.update(
                self.task, description=f"[cyan]{self.__class__.__name__} [bold]Refreshing state[/]", total=self.progress.tasks[self.task].total+1)
            self.hcl.refresh_state()
            self.hcl.request_tf_code()
            self.progress.update(
                self.task, advance=1, description=f"[green]{self.__class__.__name__} [bold]Code Generated[/]")
        else:
            self.task = self.progress.add_task(
                f"[orange3]{self.__class__.__name__} [bold]No resources found[/]", total=1)
            self.progress.update(self.task, advance=1)

    def aws_kms_key(self, key_arn=None, ftstack=None):
        logger.debug("Processing KMS Keys...")
        if not ftstack:
            ftstack = "kms"

        if key_arn:
            # Process only the key specified by the ARN
            try:
                key_metadata = self.aws_clients.kms_client.describe_key(KeyId=key_arn)[
                    "KeyMetadata"]
                if key_metadata["KeyManager"] == "CUSTOMER":
                    self.process_key(key_metadata, ftstack)
                else:
                    return "MANAGED"
            except botocore.exceptions.ClientError as e:
                logger.error(f"  Error processing KMS Key: {e}")
        else:
            # Process all customer-managed keys
            paginator = self.aws_clients.kms_client.get_paginator("list_keys")
            total = 0
            for page in paginator.paginate():
                for key in page["Keys"]:
                    total += 1

            if total > 0:
                self.task = self.progress.add_task(
                    f"[cyan]Processing {self.__class__.__name__}...", total=total)

            for page in paginator.paginate():
                for key in page["Keys"]:
                    key_id = key["KeyId"]
                    self.progress.update(
                        self.task, advance=1, description=f"[cyan]{self.__class__.__name__} [bold]{key_id}[/]")
                    try:
                        key_metadata = self.aws_clients.kms_client.describe_key(KeyId=key_id)[
                            "KeyMetadata"]
                        if key_metadata["KeyManager"] == "CUSTOMER":
                            self.process_key(key_metadata, ftstack)
                    except botocore.exceptions.ClientError as e:
                        logger.error(f"  Error processing KMS Key: {e}")

    def process_key(self, key_metadata, ftstack):
        resource_type = "aws_kms_key"
        key_id = key_metadata["KeyId"]
        logger.debug(f"Processing KMS Key: {key_id}")

        id = key_id

        attributes = {
            "id": id,
            "key_id": key_id,
            "arn": key_metadata["Arn"],
            "creation_date": key_metadata["CreationDate"].isoformat(),
            "enabled": key_metadata["Enabled"],
            "key_usage": key_metadata["KeyUsage"],
            "key_state": key_metadata["KeyState"],
        }
        self.hcl.process_resource(
            resource_type, key_id.replace("-", "_"), attributes)
        self.hcl.add_stack(resource_type, id, ftstack)

        self.aws_kms_key_policy(key_metadata["Arn"])
        self.aws_kms_alias(key_metadata["Arn"])
        self.aws_kms_grant(key_metadata["Arn"])

    def aws_kms_alias(self, kms_arn):
        logger.debug("Processing KMS Aliases...")
        try:
            # List aliases directly for the specified key ARN
            aliases = self.aws_clients.kms_client.list_aliases(KeyId=kms_arn)[
                "Aliases"]

            for alias in aliases:
                alias_name = alias["AliasName"]
                target_key_id = alias.get("TargetKeyId", "")
                if not target_key_id:
                    logger.debug(
                        f"Skipping {alias_name} due to empty TargetKeyId")
                    continue
                if alias_name == "alias/":
                    logger.debug(f"Skipping empty {alias_name}")
                    continue

                logger.debug(f"Processing KMS Alias: {alias_name}")

                attributes = {
                    "id": alias_name,
                    "name": alias_name,
                    "target_key_id": target_key_id,
                }
                self.hcl.process_resource(
                    "aws_kms_alias", alias_name.replace("-", "_"), attributes)

        except botocore.exceptions.ClientError as e:
            logger.debug(f"  Error processing KMS Aliases: {e}")

    # def aws_kms_ciphertext(self, key_arns, plaintext_data):
    #     logger.debug("Processing KMS Ciphertexts...")

    #     for key_arn in key_arns:
    #         for data in plaintext_data:
    #             ciphertext = self.aws_clients.kms_client.encrypt(KeyId=key_arn, Plaintext=data)[
    #                 "CiphertextBlob"]
    #             b64_ciphertext = base64.b64encode(ciphertext).decode("utf-8")
    #             logger.debug(f"Processing KMS Ciphertext for Key ARN: {key_arn}")

    #             attributes = {
    #                 "id": f"{key_arn}-{hashlib.sha1(data.encode('utf-8')).hexdigest()}",
    #                 "key_arn": key_arn,
    #                 "plaintext": data,
    #                 "ciphertext_base64": b64_ciphertext,
    #             }
    #             self.hcl.process_resource(
    #                 "aws_kms_ciphertext", f"kms_ciphertext_{attributes['id']}", attributes)

    # def aws_kms_custom_key_store(self):
    #     logger.debug("Processing KMS Custom Key Stores...")
    #     custom_key_stores = self.aws_clients.kms_client.describe_custom_key_stores()[
    #         "CustomKeyStores"]

    #     for cks in custom_key_stores:
    #         cks_id = cks["CustomKeyStoreId"]
    #         logger.debug(f"Processing KMS Custom Key Store: {cks_id}")

    #         attributes = {
    #             "id": cks_id,
    #             "custom_key_store_id": cks_id,
    #             "custom_key_store_name": cks["CustomKeyStoreName"],
    #             "cloudhsm_cluster_id": cks["CloudHsmClusterId"],
    #             "trust_anchor_certificate": cks["TrustAnchorCertificate"],
    #         }
    #         self.hcl.process_resource(
    #             "aws_kms_custom_key_store", cks_id.replace("-", "_"), attributes)

    # def aws_kms_grant(self, kms_arn):
    #     logger.debug("Processing KMS Grants...")
    #     try:
    #         # Directly list grants for the specified key ARN
    #         grants = self.aws_clients.kms_client.list_grants(KeyId=kms_arn)["Grants"]

    #         for grant in grants:
    #             grant_id = grant["GrantId"]
    #             logger.debug(f"Processing KMS Grant: {grant_id}")

    #             attributes = {
    #                 "id": kms_arn + ":" + grant_id,
    #                 # Additional attributes can be included as needed
    #             }
    #             self.hcl.process_resource(
    #                 "aws_kms_grant", grant_id.replace("-", "_"), attributes)

    #     except botocore.exceptions.ClientError as e:
    #         logger.debug(f"  Error processing KMS Grants: {e}")

    def check_iam_role_exists(self, role_name):
        try:
            self.aws_clients.iam_client.get_role(RoleName=role_name)
            return True  # Role exists
        except botocore.exceptions.ClientError as error:
            if error.response['Error']['Code'] == 'NoSuchEntity':
                return False  # Role does not exist
            else:
                raise  # Other AWS error

    def aws_kms_grant(self, kms_arn):
        logger.debug("Processing KMS Grants...")
        try:
            # Directly list grants for the specified key ARN
            grants = self.aws_clients.kms_client.list_grants(KeyId=kms_arn)[
                "Grants"]

            for grant in grants:
                grant_id = grant["GrantId"]
                grantee_principal = grant.get("GranteePrincipal", "")

                # Check if the GranteePrincipal is an IAM role
                if ':' in grantee_principal and 'assumed-role' not in grantee_principal:

                    role_name = grantee_principal.split(':')[-1]
                    if not self.check_iam_role_exists(role_name):
                        logger.debug(
                            f"  Skipping Grant ID: {grant_id} due to non-existent IAM role: {role_name}")
                        continue

                logger.debug(f"Processing KMS Grant: {grant_id}")
                attributes = {
                    "id": kms_arn + ":" + grant_id,
                    # Additional attributes can be included as needed
                }
                self.hcl.process_resource(
                    "aws_kms_grant", grant_id.replace("-", "_"), attributes)

        except botocore.exceptions.ClientError as e:
            pass
            # logger.error(f"  Error processing KMS Grants: {e}")

    def aws_kms_key_policy(self, kms_arn):
        logger.debug("Processing KMS Key Policies...")
        try:
            # Directly get the policy for the specified key ARN
            policy = self.aws_clients.kms_client.get_key_policy(
                KeyId=kms_arn, PolicyName="default")["Policy"]

            logger.debug(f"Processing KMS Key Policy for Key: {kms_arn}")

            attributes = {
                "id": kms_arn,
                "key_id": kms_arn,  # Using ARN as key ID for consistency
                "policy": policy,
            }
            self.hcl.process_resource(
                "aws_kms_key_policy", kms_arn.replace("-", "_"), attributes)

        except botocore.exceptions.ClientError as e:
            logger.debug(f"  Error processing KMS Key Policy: {e}")

    def aws_kms_replica_key(self):
        logger.debug("Processing KMS Replica Keys...")
        paginator = self.aws_clients.kms_client.get_paginator("list_keys")

        try:
            for page in paginator.paginate():
                for key in page["Keys"]:
                    key_metadata = self.aws_clients.kms_client.describe_key(KeyId=key["KeyId"])[
                        "KeyMetadata"]

                    # Check if the key is multi-region
                    if key_metadata.get("MultiRegion", False):
                        multi_region_config = key_metadata.get(
                            "MultiRegionConfiguration", {})
                        replica_keys = multi_region_config.get(
                            "ReplicaKeys", [])

                        # Check for replicas in the same region
                        for replica_key in replica_keys:
                            if replica_key["Region"] == self.region:
                                logger.debug(
                                    f"Processing Multi-Region KMS Replica Key: {replica_key['Arn']}")

                                attributes = {
                                    "id": key_metadata['KeyId'],
                                    "key_id": key_metadata['KeyId'],
                                    "arn": key_metadata['Arn'],
                                    "creation_date": key_metadata['CreationDate'].isoformat(),
                                    "enabled": key_metadata['Enabled'],
                                    "key_usage": key_metadata['KeyUsage'],
                                    "key_state": key_metadata['KeyState'],
                                    "multi_region": key_metadata['MultiRegion'],
                                    # Add more attributes as needed
                                }
                                self.hcl.process_resource(
                                    "aws_kms_replica_key", key_metadata['KeyId'].replace("-", "_"), attributes)

        except botocore.exceptions.ClientError as e:
            logger.error(f"  Error processing KMS Replica Key: {e}")

    def aws_kms_external_key(self):
        logger.debug("Processing KMS External Keys...")
        paginator = self.aws_clients.kms_client.get_paginator("list_keys")
        for page in paginator.paginate():
            for key in page["Keys"]:
                try:
                    key_id = key["KeyId"]
                    key_metadata = self.aws_clients.kms_client.describe_key(KeyId=key_id)[
                        "KeyMetadata"]

                    if key_metadata["Origin"] == "EXTERNAL":
                        logger.debug(f"Processing KMS External Key: {key_id}")

                        attributes = {
                            "id": key_id,
                            "key_id": key_id,
                            "arn": key_metadata["Arn"],
                            "creation_date": key_metadata["CreationDate"].isoformat(),
                            "enabled": key_metadata["Enabled"],
                        }
                        self.hcl.process_resource(
                            "aws_kms_external_key", key_id.replace("-", "_"), attributes)

                except botocore.exceptions.ClientError as e:
                    logger.error(f"  Error processing KMS Grant: {e}")

    def aws_kms_replica_external_key(self):
        logger.debug("Processing KMS Replica External Keys...")
        paginator = self.aws_clients.kms_client.get_paginator("list_keys")

        try:
            for page in paginator.paginate():
                for key in page["Keys"]:
                    key_metadata = self.aws_clients.kms_client.describe_key(KeyId=key["KeyId"])[
                        "KeyMetadata"]

                    # Check if the key's origin is external and it's a multi-region key
                    if key_metadata["Origin"] == "EXTERNAL" and key_metadata.get("MultiRegion", False):
                        multi_region_config = key_metadata.get(
                            "MultiRegionConfiguration", {})
                        replica_keys = multi_region_config.get(
                            "ReplicaKeys", [])

                        # Check for replicas in the same region
                        for replica_key in replica_keys:
                            if replica_key["Region"] == self.region:
                                logger.debug(
                                    f"Processing KMS Replica External Key: {replica_key['Arn']}")

                                attributes = {
                                    "id": key_metadata['KeyId'],
                                    "key_id": key_metadata['KeyId'],
                                    "arn": key_metadata['Arn'],
                                    "creation_date": key_metadata['CreationDate'].isoformat(),
                                    "enabled": key_metadata['Enabled'],
                                    "key_usage": key_metadata['KeyUsage'],
                                    "key_state": key_metadata['KeyState'],
                                    "multi_region": key_metadata['MultiRegion'],
                                    # Add more attributes as needed
                                }
                                self.hcl.process_resource(
                                    "aws_kms_replica_external_key", key_metadata['KeyId'].replace("-", "_"), attributes)

        except botocore.exceptions.ClientError as e:
            logger.error(f"  Error processing KMS Replica External Key: {e}")
