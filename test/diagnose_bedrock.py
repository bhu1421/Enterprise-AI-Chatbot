
import os
import boto3
from dotenv import load_dotenv
from pathlib import Path

# Load .env from project root
env_path = Path(__file__).resolve().parent.parent / ".env"
load_dotenv(env_path)

# Create AWS session using .env credentials
session = boto3.Session(
    aws_access_key_id=os.getenv("AWS_ACCESS_KEY_ID"),
    aws_secret_access_key=os.getenv("AWS_SECRET_ACCESS_KEY"),
    region_name=os.getenv("AWS_REGION", "ap-south-1")
)

# Verify AWS identity
identity = session.client("sts").get_caller_identity()
print("AWS identity:", identity["Arn"])

# Create Bedrock runtime client
client = session.client("bedrock-runtime")






import json
from botocore.exceptions import ClientError

models = [
    "amazon.titan-embed-text-v2:0",
    "apac.amazon.nova-lite-v1:0"
]

for model in models:
    print(f"\nTesting: {model}")

    try:
        if "titan" in model:
            body = {
                "inputText": "Hello world",
                "dimensions": 1024,
                "normalize": True
            }
        else:
            body = {
                "messages": [
                    {
                        "role": "user",
                        "content": [{"text": "Say hello"}]
                    }
                ],
                "inferenceConfig": {"maxTokens": 100}
            }

        response = client.invoke_model(
            modelId=model,
            body=json.dumps(body),
            contentType="application/json",
            accept="application/json"
        )

        result = json.loads(response["body"].read())
        print("SUCCESS")

        if "embedding" in result:
            print("Embedding dimensions:", len(result["embedding"]))
        else:
            print("Response:", result.get("output", {}))

    except ClientError as e:
        print("ERROR:", e.response["Error"]["Code"])
        print("MESSAGE:", e.response["Error"]["Message"])
        print("REQUEST ID:", e.response["ResponseMetadata"].get("RequestId"))
