import os
import click
import boto3
from concurrent.futures import ThreadPoolExecutor, as_completed
import shutil
import subprocess
import logging
from rich.logging import RichHandler
import time


from rich.console import Console
from rich.traceback import Traceback


from .providers.aws.Aws import Aws
from .utils.auth import auth
from .utils.tf_plan import count_resources_by_action_and_collect_changes, print_summary, print_detailed_changes

from rich.progress import Progress
from rich.progress import TimeElapsedColumn
from rich.progress import SpinnerColumn
from rich.progress import MofNCompleteColumn
from rich.progress import BarColumn
from rich.progress import TextColumn
from rich.progress import TaskProgressColumn

console = Console()
ftstacks = set()


def execute_provider_method(provider, method_name):
    try:
        if method_name == "iam":
            # Special handling for IAM module
            original_region = provider.aws_region
            provider.region = "global"
            method = getattr(provider, method_name)
            result = method()
            provider.region = original_region
        else:
            # Regular execution for other modules
            method = getattr(provider, method_name)
            result = method()
        return result
    except Exception as e:
        # Log fail status
        console.log(
            f"[bold red]Error executing {method_name}[/bold red]: {str(e)}", style="bold red")
        console.print(Traceback())
        return set()


def execute_terraform_plan(output_dir, ftstack):
    # Define the working directory for this ftstack
    cwd = os.path.join(output_dir, "tf_code", ftstack)

    max_retries = 1  # Maximum number of retries
    retry_count = 0  # Initial retry count

    while retry_count <= max_retries:
        try:
            console.print(
                f"[cyan]Running Terraform init and plan for {ftstack}...[/cyan]")
            # Run terraform init with the specified working directory
            subprocess.run(["terragrunt", "init"], cwd=cwd, check=True,
                           stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            # Run terraform plan with the specified working directory
            plan_file_name = os.path.join(cwd, f"{ftstack}_plan")
            subprocess.run(["terragrunt", "plan", "-out", plan_file_name],
                           cwd=cwd, check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            # Run terraform show with the specified working directory
            json_file_name = os.path.join(cwd, f"{ftstack}_plan.json")
            subprocess.run(f"terragrunt show -json {plan_file_name} > {json_file_name}",
                           shell=True, cwd=cwd, check=True, stderr=subprocess.PIPE)
            # Read and process the Terraform plan JSON
            with open(json_file_name) as f:
                counts, updates = count_resources_by_action_and_collect_changes(
                    f.read())
            # Optionally, clean up the plan file
            os.remove(plan_file_name)
            # os.remove(json_file_name)
            return (counts, updates, ftstack)
        except subprocess.CalledProcessError as e:
            console.print(
                f"[red]Error in Terraform operation for {ftstack}: {e.stderr.decode('utf-8')}[/red]")
            if retry_count < max_retries:
                retry_count += 1
                console.print(
                    f"[yellow]Retrying Terraform init and plan for {ftstack} in 10 seconds...[/yellow]")
                time.sleep(10)  # Wait for 10 seconds before retrying
            else:
                return None


@click.command()
@click.option('--provider', '-p', default="aws", help='Provider name')
@click.option('--module', '-m', required=True, help='Module name(s), separated by commas or "all" for all modules')
@click.option('--output_dir', '-o', default=os.getcwd(), help='Output directory')
@click.option('--process_dependencies', '-d', default=True, help='Process dependencies')
@click.option('--run-plan', '-r', default=True, help='Run plan')
def main(provider, module, output_dir, process_dependencies, run_plan):
    if not os.environ.get('PROCESS_DEPENDENCIES'):
        os.environ['PROCESS_DEPENDENCIES'] = str(process_dependencies)

    setup_logger()
    logger = logging.getLogger('finisterra')

    if provider == "aws":
        aws_access_key_id = os.getenv('AWS_ACCESS_KEY_ID')
        aws_secret_access_key = os.getenv('AWS_SECRET_ACCESS_KEY')
        aws_session_token = os.getenv('AWS_SESSION_TOKEN')
        aws_profile = os.getenv('AWS_PROFILE')
        aws_region = os.getenv('AWS_REGION')
        if not aws_region:
            logger.error("AWS_REGION environment variable is not defined.")
            exit()

        if aws_profile:
            session = boto3.Session(profile_name=aws_profile)
        else:
            session = boto3.Session(
                aws_access_key_id=aws_access_key_id,
                aws_secret_access_key=aws_secret_access_key,
                aws_session_token=aws_session_token,
                region_name=aws_region
            )

        sts = session.client('sts')
        aws_account_id = sts.get_caller_identity()['Account']

        auth_payload = {
            "provider": provider,
            "module": module,
            "account_id": aws_account_id,
            "region": aws_region
        }
        auth(auth_payload)

        progress = Progress(
            SpinnerColumn(spinner_name="dots"),
            BarColumn(),
            TaskProgressColumn(),
            MofNCompleteColumn(),
            TimeElapsedColumn(),
            TextColumn(
                "[progress.description]{task.description}"),
            console=console
        )
        with progress:
            logger.info("Fetching AWS resources...")

            script_dir = os.path.dirname(os.path.abspath(__file__))
            s3Bucket = f'ft-{aws_account_id}-{aws_region}-tfstate'
            dynamoDBTable = f'ft-{aws_account_id}-{aws_region}-tfstate-lock'
            stateKey = f'finisterra/generated/aws/{aws_account_id}/{aws_region}/{module}'

            provider_instance = Aws(progress, script_dir, s3Bucket, dynamoDBTable,
                                    stateKey, aws_account_id, aws_region, output_dir)

            # Define all provider methods for execution
            all_provider_methods = [
                'vpc',
                'acm',
                'apigateway',
                'autoscaling',
                'cloudmap',
                'cloudfront',
                'logs',
                'docdb',
                'dynamodb',
                'ec2',
                'ecr',
                'ecs',
                'eks',
                'elbv2',
                'elasticache_redis',
                'elasticbeanstalk',
                'iam',
                'kms',
                'aws_lambda',
                'rds',
                's3',
                'sns',
                'sqs',
                'wafv2',
                'stepfunction',
                'msk',
                'aurora',
                'security_group',
                'vpc_endpoint',
                'target_group',
                'elasticsearch',
                'codeartifact',
                'launchtemplate',
            ]

            # Check for invalid modules
            modules_to_execute = module.split(',')
            invalid_modules = [mod.strip() for mod in modules_to_execute if mod.strip(
            ) not in all_provider_methods and mod.lower() != 'all']
            if invalid_modules:
                logger.error(
                    f"Error: Invalid module(s) specified: {', '.join(invalid_modules)}")
                exit()

            # Handling for 'all' module
            if module.lower() == "all":
                modules_to_execute = all_provider_methods
            else:
                modules_to_execute = [mod.strip()
                                      for mod in modules_to_execute]

            max_parallel = int(os.getenv('MAX_PARALLEL', 5))
            results = []
            with ThreadPoolExecutor(max_workers=max_parallel) as executor:
                futures = [executor.submit(
                    execute_provider_method, provider_instance, method) for method in modules_to_execute]
                for future in as_completed(futures):
                    results.append(future.result())

            # After collecting all results, update ftstacks once
            global ftstacks
            for result in results:
                ftstacks = ftstacks.union(result)

        if run_plan:
            os.chdir(os.path.join(output_dir, "tf_code"))
            shutil.copyfile("./terragrunt.hcl",
                            "./terragrunt.hcl.remote-state")
            shutil.copyfile("./terragrunt.hcl.local-state", "./terragrunt.hcl")

            results = []  # Initialize a list to store results
            with ThreadPoolExecutor(max_workers=max_parallel) as executor:
                future_to_ftstack = {executor.submit(
                    execute_terraform_plan, output_dir, ftstack): ftstack for ftstack in ftstacks}
                for future in as_completed(future_to_ftstack):
                    result = future.result()
                    if result:
                        # Collect results for later processing
                        results.append(result)

            # Restore original terragrunt.hcl files after all plans have been executed
            os.chdir(os.path.join(output_dir, "tf_code"))
            shutil.copyfile("./terragrunt.hcl", "./terragrunt.hcl.local-state")
            shutil.copyfile("./terragrunt.hcl.remote-state",
                            "./terragrunt.hcl")

            # Process the results after all plans are done
            for counts, updates, ftstack in results:
                console.print(
                    f"\n[bold]Terraform Plan for {ftstack}[/bold]")
                print_detailed_changes(updates)
                print_summary(counts, ftstack)
                console.print('-' * 50)

        for ftstack in ftstacks:
            generated_path = os.path.join(output_dir, "tf_code", ftstack)
            logger.info(f"Terraform code created at: {generated_path}")


def setup_logger():
    # Set the log level for the root logger to NOTSET (this is required to allow handlers to control the logging level)
    logging.root.setLevel(logging.NOTSET)

    # Configure your application's logger
    log_level_name = os.getenv('FT_LOG_LEVEL', 'INFO').upper()
    app_log_level = getattr(logging, log_level_name, logging.INFO)

    # Setup the 'finisterra' logger to use RichHandler with the shared console instance
    logger = logging.getLogger('finisterra')
    logger.setLevel(app_log_level)
    rich_handler = RichHandler(
        console=console, show_time=False, show_level=True, show_path=False)
    rich_handler.setLevel(app_log_level)
    # Replace any default handlers with just the RichHandler
    logger.handlers = [rich_handler]

    # Set higher logging level for noisy libraries
    logging.getLogger('boto3').setLevel(logging.INFO)
    logging.getLogger('botocore').setLevel(logging.INFO)
    logging.getLogger('urllib3').setLevel(logging.INFO)


if __name__ == "__main__":
    main()
