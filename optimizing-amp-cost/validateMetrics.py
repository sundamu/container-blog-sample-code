import json
import os
import sys
import boto3
from botocore.auth import SigV4Auth
from botocore.awsrequest import AWSRequest
import requests
import re

# Paths to your JSON files
grafana_metrics_file = "metrics-in-grafana.json"
rule_metrics_file = "metrics-in-ruler.json"

def is_valid_metric_name(metric: str) -> bool:
    return bool(re.match(r'^[a-zA-Z0-9_:]+$', metric))

def query_aps(region: str, workspace_id: str, metric: str) -> dict:
    query = f"absent_over_time({metric}[5m])"
    url = f"https://aps-workspaces.{region}.amazonaws.com/workspaces/{workspace_id}/api/v1/query"
    headers = {
        'Content-Type': 'application/x-www-form-urlencoded'
    }
    data = {
        'query': query
    }
    
    session = boto3.Session(region_name=region)
    credentials = session.get_credentials()
    
    # Create the request
    request = AWSRequest(method='POST', url=url, data=data, headers=headers)
    
    # Sign the request
    SigV4Auth(credentials, 'aps', region).add_auth(request)
    
    # Send the request with a timeout of 30 seconds
    response = requests.request(method=request.method, url=request.url, 
                                headers=dict(request.headers), data=request.data,
                                timeout=30)

    # Raise an exception for HTTP errors
    response.raise_for_status()
    
    return response.json()

# Determine whether a metric is histogram
def is_histogram_metric(metric: str) -> bool:
    suffix = '_bucket'
    return metric.endswith(suffix)

if len(sys.argv) != 4 or sys.argv[3] not in ["before", "after"]:
    print("Error: Incorrect number of arguments or invalid mode.")
    print("Usage: python script.py <region> <workspace_id> <mode>")
    print("region: AWS region")
    print("workspace_id: AMP workspace ID")
    print("mode: 'before' or 'after' metrics filtering")
    sys.exit(1)

REGION = sys.argv[1]
AMP_WP_ID = sys.argv[2]
MODE = sys.argv[3]

# Validate inputs
if not re.match(r'^[a-z0-9-]+$', REGION) or not re.match(r'^ws-[a-f0-9-]+$', AMP_WP_ID):
    print("Error: Invalid region or workspace ID format.")
    sys.exit(1)

# Merge metrics from Grafana and rules
all_inuse_metrics = set()
for file_path in [grafana_metrics_file, rule_metrics_file]:
    try:
        with open(file_path, 'rb') as f:
            metrics = json.load(f).get('metricsUsed', [])
            for metric in metrics:
                if is_valid_metric_name(metric):
                    all_inuse_metrics.add(metric)
                    if is_histogram_metric(metric):
                        # Don't remove _count and _sum metrics if it's histogram
                        base_metric = metric.rsplit('_', 1)[0]
                        all_inuse_metrics.add(f"{base_metric}_count")
                        all_inuse_metrics.add(f"{base_metric}_sum")
                else:
                    print(f"Warning: Invalid metric name {metric}. Skipping.")
    except (json.JSONDecodeError, FileNotFoundError) as e:
        print(f"Error reading {file_path}: {str(e)}")
        sys.exit(1)

print(f"Number of inuse metrics: {len(all_inuse_metrics)}")

# Query APS for each used metric
missing_metrics_count = 0
missing_metrics = []
for metric in all_inuse_metrics:
    try:
        results = query_aps(REGION, AMP_WP_ID, metric)
        if results.get('data', {}).get('result'):
            value = results['data']['result'][0]['value'][1]
            if value == "1":
                if MODE == "before":
                    print(f"Warning: Metric {metric} not found!")
                missing_metrics_count += 1
                missing_metrics.append(metric)
    except requests.exceptions.RequestException as e:
        print(f"Error querying metric {metric}: {str(e)}")

if MODE == "before":
    if missing_metrics_count != 0:
        print(f"There are total {missing_metrics_count} metrics missing!")
    else:
        print("Congratulations! All metrics in use are present in Prometheus!")
    json_string = json.dumps(missing_metrics, indent=2)
    with open('missing_metrics_before.json', 'w', encoding='utf-8') as file:
        file.write(json_string)
elif MODE == "after":
    try:
        with open('missing_metrics_before.json', 'rb') as file:
            missing_metrics_before = set(json.load(file))
            missing_metrics_after = set(missing_metrics) - missing_metrics_before
            if missing_metrics_after:
                print(f"There are total {len(missing_metrics_after)} metrics missing after metrics filtering!")
                print("Missing metrics:")
                for metric in missing_metrics_after:
                    print(metric)
            else:
                print("Congratulations! No metrics missing due to false dropping!")
    except (json.JSONDecodeError, FileNotFoundError) as e:
        print(f"Error reading missing_metrics_before.json: {str(e)}")
        sys.exit(1)
