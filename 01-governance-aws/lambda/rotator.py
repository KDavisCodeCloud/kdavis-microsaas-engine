import boto3
import json
import string
import secrets
import logging

logger = logging.getLogger()
logger.setLevel(logging.INFO)

def lambda_handler(event, context):
    arn   = event['SecretId']
    token = event['ClientRequestToken']
    step  = event['Step']

    client   = boto3.client('secretsmanager')
    metadata = client.describe_secret(SecretId=arn)

    logger.info(f"Rotation step: {step} for secret: {arn}")

    if step == "createSecret":
        create_secret(client, arn, token)
    elif step == "setSecret":
        logger.info("setSecret: no external system to update in this demo")
    elif step == "testSecret":
        test_secret(client, arn, token)
    elif step == "finishSecret":
        finish_secret(client, arn, token, metadata)

def create_secret(client, arn, token):
    try:
        client.get_secret_value(SecretId=arn, VersionId=token, VersionStage="AWSPENDING")
        logger.info("Secret already exists in PENDING stage")
        return
    except client.exceptions.ResourceNotFoundException:
        pass

    current = json.loads(
        client.get_secret_value(SecretId=arn, VersionStage="AWSCURRENT")['SecretString']
    )
    new_password = ''.join(secrets.choice(string.ascii_letters + string.digits) for _ in range(32))
    current['password'] = new_password

    client.put_secret_value(
        SecretId=arn,
        ClientRequestToken=token,
        SecretString=json.dumps(current),
        VersionStages=["AWSPENDING"]
    )
    logger.info("Created new secret version in PENDING stage")

def test_secret(client, arn, token):
    secret = json.loads(
        client.get_secret_value(SecretId=arn, VersionId=token, VersionStage="AWSPENDING")['SecretString']
    )
    assert len(secret['password']) == 32, "Password length check failed"
    logger.info("Secret validation passed")

def finish_secret(client, arn, token, metadata):
    current_version = next(
        v for v, stages in metadata['VersionIdsToStages'].items()
        if 'AWSCURRENT' in stages
    )
    client.update_secret_version_stage(
        SecretId=arn,
        VersionStage="AWSCURRENT",
        MoveToVersionId=token,
        RemoveFromVersionId=current_version
    )
    logger.info("Rotation complete — new version is now AWSCURRENT")
