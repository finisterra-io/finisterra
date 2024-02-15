import os
from ...utils.hcl import HCL
from ...providers.aws.security_group import SECURITY_GROUP
import logging

logger = logging.getLogger('finisterra')


class VPCEndPoint:
    def __init__(self, progress, aws_clients, script_dir, provider_name, schema_data, region, s3Bucket,
                 dynamoDBTable, state_key, workspace_id, modules, aws_account_id, output_dir, hcl=None):
        self.progress = progress

        self.aws_clients = aws_clients
        self.transform_rules = {}
        self.provider_name = provider_name
        self.script_dir = script_dir
        self.schema_data = schema_data
        self.region = region
        self.aws_account_id = aws_account_id
        self.s3Bucket = s3Bucket
        self.dynamoDBTable = dynamoDBTable
        self.state_key = state_key

        self.workspace_id = workspace_id
        self.modules = modules

        if not hcl:
            self.hcl = HCL(self.schema_data, self.provider_name)
        else:
            self.hcl = hcl

        self.hcl.region = region
        self.hcl.output_dir = output_dir
        self.hcl.account_id = aws_account_id

        self.security_group_instance = SECURITY_GROUP(self.progress,  self.aws_clients, script_dir, provider_name, schema_data,
                                                      region, s3Bucket, dynamoDBTable, state_key, workspace_id, modules, aws_account_id, output_dir, self.hcl)

    def get_subnet_names(self, subnet_ids):
        subnet_names = []
        for subnet_id in subnet_ids:
            response = self.aws_clients.ec2_client.describe_subnets(SubnetIds=[
                                                                    subnet_id])

            # Check if 'Subnets' key exists and it's not empty
            if not response or 'Subnets' not in response or not response['Subnets']:
                logger.debug(
                    f"No subnet information found for Subnet ID: {subnet_id}")
                continue

            # Extract the 'Tags' key safely using get
            subnet_tags = response['Subnets'][0].get('Tags', [])

            # Extract the subnet name from the tags
            subnet_name = next(
                (tag['Value'] for tag in subnet_tags if tag['Key'] == 'Name'), None)

            if subnet_name:
                subnet_names.append(subnet_name)
            else:
                logger.debug(f"No 'Name' tag found for Subnet ID: {subnet_id}")

        return subnet_names

    def get_vpc_name(self, vpc_id):
        response = self.aws_clients.ec2_client.describe_vpcs(VpcIds=[vpc_id])

        if not response or 'Vpcs' not in response or not response['Vpcs']:
            # Handle this case as required, for example:
            logger.debug(f"No VPC information found for VPC ID: {vpc_id}")
            return None

        vpc_tags = response['Vpcs'][0].get('Tags', [])
        vpc_name = next((tag['Value']
                        for tag in vpc_tags if tag['Key'] == 'Name'), None)

        if vpc_name is None:
            logger.debug(f"No 'Name' tag found for VPC ID: {vpc_id}")

        return vpc_name

    def vpc_endpoint(self):
        self.hcl.prepare_folder(os.path.join("generated"))

        self.aws_vpc_endpoint()
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

    def aws_vpc_endpoint(self, vpce_id=None, ftstack=None):
        resource_type = "aws_vpc_endpoint"
        logger.debug(f"Processing VPC Endpoint: {vpce_id}...")
        try:
            if vpce_id:
                if ftstack and self.hcl.id_resource_processed(resource_type, vpce_id, ftstack):
                    logger.debug(
                        f"  Skipping VPC Endpoint: {vpce_id} - already processed")
                    return
            if vpce_id is None:
                endpoints = self.aws_clients.ec2_client.describe_vpc_endpoints()[
                    "VpcEndpoints"]
            else:
                endpoints = self.aws_clients.ec2_client.describe_vpc_endpoints(
                    VpcEndpointIds=[vpce_id])["VpcEndpoints"]

            if len(endpoints) > 0:
                self.task = self.progress.add_task(
                    f"[cyan]Processing {self.__class__.__name__}...", total=len(endpoints))
            for endpoint in endpoints:
                endpoint_id = endpoint["VpcEndpointId"]

                vpc_id = endpoint["VpcId"]
                service_name = endpoint["ServiceName"]
                logger.debug(
                    f"Processing VPC Endpoint: {endpoint_id} for VPC: {vpc_id}")
                attributes = {
                    "id": endpoint_id,
                    "vpc_id": vpc_id,
                    "service_name": service_name,
                }

                if not ftstack:
                    ftstack = "vpc_endpoint"
                    # Check if "ftstack" tag exists and set "ftstack" to its value
                    tags = endpoint.get("Tags", [])
                    for tag in tags:
                        if tag["Key"] == "ftstack":
                            ftstack = tag["Value"]
                            break

                self.hcl.process_resource(
                    resource_type, endpoint_id, attributes)
                self.hcl.add_stack(resource_type, endpoint_id, ftstack)

                security_groups = endpoint.get("Groups", [])
                for security_group in security_groups:
                    security_group_id = security_group["GroupId"]
                    self.security_group_instance.aws_security_group(
                        security_group_id, ftstack)

                vpc_id = endpoint["VpcId"]
                if vpc_id:
                    vpc_name = self.get_vpc_name(vpc_id)
                    if vpc_name:
                        self.hcl.add_additional_data(
                            resource_type, endpoint_id, "vpc_name", vpc_name)

                subnet_ids = endpoint.get("SubnetIds", [])
                if subnet_ids:
                    subnet_names = self.get_subnet_names(subnet_ids)
                    if subnet_names:
                        self.hcl.add_additional_data(
                            resource_type, endpoint_id, "subnet_names", subnet_names)

                self.progress.update(
                    self.task, advance=1, description=f"[cyan]{self.__class__.__name__} [bold]{endpoint_id}[/]")

            if not endpoints:
                logger.debug("No VPC Endpoints found.")
        except Exception as e:
            logger.error(f"An error occurred: {str(e)}")
            pass

    def aws_vpc_endpoint_connection_accepter(self):
        logger.debug("Processing VPC Endpoint Connection Accepters...")
        vpc_endpoints = self.aws_clients.ec2_client.describe_vpc_endpoints()[
            "VpcEndpoints"]

        for endpoint in vpc_endpoints:
            if endpoint["State"] == "pendingAcceptance":
                endpoint_id = endpoint["VpcEndpointId"]
                vpc_id = endpoint["VpcId"]
                service_name = endpoint["ServiceName"]
                logger.debug(
                    f"Processing VPC Endpoint Connection Accepter: {endpoint_id} for VPC: {vpc_id}")

                accepter_id = f"{vpc_id}-{endpoint_id}"
                attributes = {
                    "id": accepter_id,
                    "vpc_endpoint_id": endpoint_id,
                    "vpc_id": vpc_id,
                    "service_name": service_name,
                }
                self.hcl.process_resource(
                    "aws_vpc_endpoint_connection_accepter", accepter_id.replace("-", "_"), attributes)

    def aws_vpc_endpoint_connection_notification(self):
        logger.debug("Processing VPC Endpoint Connection Notifications...")
        connection_notifications = self.aws_clients.ec2_client.describe_vpc_endpoint_connection_notifications()[
            "ConnectionNotificationSet"]

        for notification in connection_notifications:
            notification_id = notification["ConnectionNotificationId"]
            vpc_endpoint_id = notification["VpcEndpointId"]
            service_id = notification["ServiceId"]
            sns_topic_arn = notification["ConnectionNotificationArn"]
            logger.debug(
                f"Processing VPC Endpoint Connection Notification: {notification_id}")

            attributes = {
                "id": notification_id,
                "vpc_endpoint_id": vpc_endpoint_id,
                "service_id": service_id,
                "sns_topic_arn": sns_topic_arn,
                "notification_type": notification["ConnectionNotificationType"],
                "state": notification["ConnectionNotificationState"],
            }
            self.hcl.process_resource(
                "aws_vpc_endpoint_connection_notification", notification_id.replace("-", "_"), attributes)

    def aws_vpc_endpoint_policy(self):
        logger.debug("Processing VPC Endpoint Policies...")
        vpc_endpoints = self.aws_clients.ec2_client.describe_vpc_endpoints()[
            "VpcEndpoints"]

        for endpoint in vpc_endpoints:
            endpoint_id = endpoint["VpcEndpointId"]
            vpc_id = endpoint["VpcId"]
            service_name = endpoint["ServiceName"]
            policy_document = endpoint["PolicyDocument"]
            logger.debug(
                f"Processing VPC Endpoint Policy: {endpoint_id} for VPC: {vpc_id}")

            attributes = {
                "id": endpoint_id,
                "vpc_endpoint_id": endpoint_id,
                "policy": policy_document,
            }
            self.hcl.process_resource(
                "aws_vpc_endpoint_policy", endpoint_id.replace("-", "_"), attributes)

    def aws_vpc_endpoint_route_table_association(self):
        logger.debug("Processing VPC Endpoint Route Table Associations...")
        vpc_endpoints = self.aws_clients.ec2_client.describe_vpc_endpoints()[
            "VpcEndpoints"]

        for endpoint in vpc_endpoints:
            endpoint_id = endpoint["VpcEndpointId"]
            route_table_ids = endpoint.get("RouteTableIds", [])

            for route_table_id in route_table_ids:
                logger.debug(
                    f"Processing VPC Endpoint Route Table Association: {endpoint_id} - {route_table_id}")

                assoc_id = f"{endpoint_id}-{route_table_id}"
                attributes = {
                    "id": assoc_id,
                    "vpc_endpoint_id": endpoint_id,
                    "route_table_id": route_table_id,
                }
                self.hcl.process_resource(
                    "aws_vpc_endpoint_route_table_association", assoc_id.replace("-", "_"), attributes)

    def aws_vpc_endpoint_security_group_association(self):
        logger.debug("Processing VPC Endpoint Security Group Associations...")
        vpc_endpoints = self.aws_clients.ec2_client.describe_vpc_endpoints()[
            "VpcEndpoints"]

        for endpoint in vpc_endpoints:
            endpoint_id = endpoint["VpcEndpointId"]
            security_group_ids = [group["GroupId"]
                                  for group in endpoint.get("Groups", [])]

            for security_group_id in security_group_ids:
                logger.debug(
                    f"Processing VPC Endpoint Security Group Association: {endpoint_id} - {security_group_id}")

                assoc_id = f"{endpoint_id}-{security_group_id}"
                attributes = {
                    "id": assoc_id,
                    "vpc_endpoint_id": endpoint_id,
                    "security_group_id": security_group_id,
                }
                self.hcl.process_resource(
                    "aws_vpc_endpoint_security_group_association", assoc_id.replace("-", "_"), attributes)

    def aws_vpc_endpoint_service(self):
        logger.debug("Processing VPC Endpoint Services...")
        vpc_endpoint_services = self.aws_clients.ec2_client.describe_vpc_endpoint_services()[
            "ServiceDetails"]

        for service in vpc_endpoint_services:
            service_id = service["ServiceId"]
            service_name = service["ServiceName"]

            # Skip default AWS services
            if service_name.startswith('com.amazonaws') or service_name.startswith('aws.'):
                continue

            logger.debug(
                f"Processing VPC Endpoint Service: {service_id} {service_name}")

            attributes = {
                "id": service_id,
                "service_name": service_name,
                "acceptance_required": service["AcceptanceRequired"],
                "availability_zones": service["AvailabilityZones"],
                "base_endpoint_dns_names": service["BaseEndpointDnsNames"],
                "service_type": service["ServiceType"][0]["ServiceType"],
            }
            self.hcl.process_resource(
                "aws_vpc_endpoint_service", service_id.replace("-", "_"), attributes)

    def aws_vpc_endpoint_service_allowed_principal(self):
        logger.debug("Processing VPC Endpoint Service Allowed Principals...")
        vpc_endpoint_services = self.aws_clients.ec2_client.describe_vpc_endpoint_service_configurations()[
            "ServiceConfigurations"]

        for service in vpc_endpoint_services:
            service_id = service["ServiceId"]
            allowed_principals = service.get("AllowedPrincipals", [])

            for principal in allowed_principals:
                logger.debug(
                    f"Processing VPC Endpoint Service Allowed Principal: {principal} for Service: {service_id}")

                assoc_id = f"{service_id}-{principal}"
                attributes = {
                    "id": assoc_id,
                    "vpc_endpoint_service_id": service_id,
                    "principal_arn": principal,
                }
                self.hcl.process_resource(
                    "aws_vpc_endpoint_service_allowed_principal", assoc_id.replace("-", "_"), attributes)

    def aws_vpc_endpoint_subnet_association(self):
        logger.debug("Processing VPC Endpoint Subnet Associations...")
        vpc_endpoints = self.aws_clients.ec2_client.describe_vpc_endpoints()[
            "VpcEndpoints"]

        for endpoint in vpc_endpoints:
            endpoint_id = endpoint["VpcEndpointId"]
            subnet_ids = endpoint["SubnetIds"]

            for subnet_id in subnet_ids:
                logger.debug(
                    f"Processing VPC Endpoint Subnet Association: {endpoint_id} - {subnet_id}")

                assoc_id = f"{endpoint_id}-{subnet_id}"
                attributes = {
                    "id": assoc_id,
                    "vpc_endpoint_id": endpoint_id,
                    "subnet_id": subnet_id,
                }
                self.hcl.process_resource(
                    "aws_vpc_endpoint_subnet_association", assoc_id.replace("-", "_"), attributes)