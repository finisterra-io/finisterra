import os
from ...utils.hcl import HCL
import logging

logger = logging.getLogger('finisterra')


class SNS:
    def __init__(self, progress, aws_clients, script_dir, provider_name, schema_data, region, s3Bucket,
                 dynamoDBTable, state_key, workspace_id, modules, aws_account_id, output_dir, hcl=None):
        self.progress = progress

        self.aws_clients = aws_clients
        self.transform_rules = {
        }
        self.provider_name = provider_name
        self.script_dir = script_dir
        self.schema_data = schema_data
        self.region = region
        self.aws_account_id = aws_account_id

        self.workspace_id = workspace_id
        self.modules = modules
        self.hcl = HCL(self.schema_data, self.provider_name)

        self.hcl.region = region
        self.hcl.output_dir = output_dir
        self.hcl.account_id = aws_account_id

    def sns(self):
        self.hcl.prepare_folder(os.path.join("generated"))

        self.aws_sns_topic()
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

    def aws_sns_platform_application(self):
        logger.debug("Processing SNS Platform Applications...")

        paginator = self.aws_clients.sns_client.get_paginator(
            "list_platform_applications")
        for page in paginator.paginate():
            for platform_application in page.get("PlatformApplications", []):
                arn = platform_application["PlatformApplicationArn"]
                name = arn.split(":")[-1]
                logger.debug(f"Processing SNS Platform Application: {name}")

                attributes = {
                    "id": arn,
                    "name": name,
                }
                self.hcl.process_resource(
                    "aws_sns_platform_application", name.replace("-", "_"), attributes)

    def aws_sns_sms_preferences(self):
        logger.debug("Processing SNS SMS Preferences...")

        try:
            preferences = self.aws_clients.sns_client.get_sms_attributes()[
                "attributes"]
            attributes = {key: value for key, value in preferences.items()}

            self.hcl.process_resource(
                "aws_sns_sms_preferences", "sns_sms_preferences", attributes)
        except Exception as e:
            logger.error(f"Error retrieving SNS SMS Preferences: {str(e)}")

    def aws_sns_topic(self):
        resource_type = "aws_sns_topic"
        logger.debug("Processing SNS Topics...")

        paginator = self.aws_clients.sns_client.get_paginator("list_topics")
        total = 0
        for page in paginator.paginate():
            total += len(page.get("Topics", []))
        if total > 0:
            self.task = self.progress.add_task(
                f"[cyan]Processing {self.__class__.__name__}...", total=total)
        for page in paginator.paginate():
            for topic in page.get("Topics", []):
                arn = topic["TopicArn"]
                name = arn.split(":")[-1]
                self.progress.update(
                    self.task, advance=1, description=f"[cyan]{self.__class__.__name__} [bold]{name}[/]")

                # if name != 'slack-devops-monitors-devqa':
                #     continue

                logger.debug(f"Processing SNS Topic: {name}")
                id = arn

                ftstack = "sns"
                try:
                    tags_response = self.aws_clients.sns_client.list_tags_for_resource(
                        ResourceArn=arn)
                    tags = tags_response.get('Tags', [])
                    for tag in tags:
                        if tag['Key'] == 'ftstack':
                            if tag['Value'] != 'sns':
                                ftstack = "stack_"+tag['Value']
                            break
                except Exception as e:
                    logger.error("Error occurred: ", e)

                attributes = {
                    "id": id,
                    "name": name,
                }
                self.hcl.process_resource(
                    resource_type, id, attributes)
                self.hcl.add_stack(resource_type, id, ftstack)

                self.aws_sns_topic_policy(arn)
                self.aws_sns_topic_data_protection_policy(arn)
                self.aws_sns_topic_subscription(arn)

    def aws_sns_topic_policy(self, arn):
        logger.debug("Processing SNS Topic Policies...")

        name = arn.split(":")[-1]

        try:
            policy = self.aws_clients.sns_client.get_topic_attributes(
                TopicArn=arn)["Attributes"].get("Policy")
            if policy:
                attributes = {
                    "id": arn,
                    "arn": arn,
                    "policy": policy,
                }
                self.hcl.process_resource(
                    "aws_sns_topic_policy", f"{name}_policy".replace("-", "_"), attributes)
        except Exception as e:
            logger.error(
                f"Error retrieving SNS Topic Policy for {name}: {str(e)}")

    def aws_sns_topic_data_protection_policy(self, arn):
        logger.debug("Processing SNS Topic Data Protection Policies...")

        name = arn.split(":")[-1]

        try:
            policy = self.aws_clients.sns_client.get_topic_attributes(
                TopicArn=arn)["Attributes"].get("DataProtectionPolicy")
            if policy:
                attributes = {
                    "id": arn,
                    "arn": arn,
                    "data_protection_policy": policy,
                }
                self.hcl.process_resource(
                    "aws_sns_topic_data_protection_policy", f"{name}_data_protection_policy".replace("-", "_"), attributes)
        except Exception as e:
            logger.error(
                f"Error retrieving SNS Topic Data Protection Policy for {name}: {str(e)}")

    def aws_sns_topic_subscription(self, topic_arn):
        logger.debug("Processing SNS Topic Subscriptions...")

        paginator = self.aws_clients.sns_client.get_paginator(
            "list_subscriptions")
        for page in paginator.paginate():
            for subscription in page.get("Subscriptions", []):
                # Process only subscriptions for the given topic ARN
                if subscription["TopicArn"] == topic_arn:
                    arn = subscription["SubscriptionArn"]
                    if arn == "PendingConfirmation":
                        continue
                    name = arn.split(":")[-1]
                    logger.debug(f"Processing SNS Topic Subscription: {name}")

                    attributes = {
                        "id": arn,
                        "arn": arn,
                        "topic_arn": subscription["TopicArn"],
                        "protocol": subscription["Protocol"],
                        "endpoint": subscription["Endpoint"],
                    }

                    if subscription.get("FilterPolicy"):
                        attributes["filter_policy"] = subscription["FilterPolicy"]

                    self.hcl.process_resource(
                        "aws_sns_topic_subscription", name.replace("-", "_"), attributes)