import json
import os
import boto3
from botocore.auth import SigV4Auth
from botocore.awsrequest import AWSRequest
import requests
from typing import List
import re

# Env variables
REGION = os.environ['TF_VAR_aws_region']
AMP_WP_ID = os.environ['AMP_WP_ID']

# Paths to your JSON files
all_metrics_file = "metrics-prometheus-all.json"
grafana_metrics_file = "metrics-in-grafana.json"
rule_metrics_file = "metrics-in-ruler.json"

# Create a boto3 session
session = boto3.Session(region_name=REGION)
credentials = session.get_credentials()

def is_valid_metric_name(metric: str) -> bool:
    # Validate metric name: only allow alphanumeric characters, underscores, and colons
    return bool(re.match(r'^[a-zA-Z0-9_:]+$', metric))

def query_aps(metric: str) -> dict:
    query = f"sum({metric}) by (job)"
    url = f"https://aps-workspaces.{REGION}.amazonaws.com/workspaces/{AMP_WP_ID}/api/v1/query"
    headers = {
        'Content-Type': 'application/x-www-form-urlencoded'
    }
    data = {
        'query': query
    }
    
    # Create the request
    request = AWSRequest(method='POST', url=url, data=data, headers=headers)
    
    # Sign the request
    SigV4Auth(credentials, 'aps', REGION).add_auth(request)
    
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

# Merge metrics from Grafana and rules
all_inuse_metrics = set()
for file_path in [grafana_metrics_file, rule_metrics_file]:
    with open(file_path, 'rb') as f:
        metrics = json.load(f).get('metricsUsed', [])
        for metric in metrics:
            all_inuse_metrics.add(metric)
            if is_histogram_metric(metric):
                # Don't remove _count and _sum metrics if it's histogram
                base_metric = metric.rsplit('_', 1)[0]
                all_inuse_metrics.add(f"{base_metric}_count")
                all_inuse_metrics.add(f"{base_metric}_sum")
print(f"Number of inuse metrics: {len(all_inuse_metrics)}")

# Extract all metrics from the full list
with open(all_metrics_file, 'rb') as f:
    all_metrics = set(json.load(f)['data'])
print(f"Number of ingested metrics: {len(all_metrics)}")

# Find unused metrics
unused_metrics = all_metrics - all_inuse_metrics
print(f"Number of unused metrics: {len(unused_metrics)}")

# Query APS for each unused metric
labeled_metrics = []
for metric in unused_metrics:
    if not is_valid_metric_name(metric):
        print(f"Warning: Invalid metric name {metric}. Skipping.")
        continue

    try:
        result = query_aps(metric)
        #print(result)
        jobs = result.get('data', {}).get('result', [])
        if len(jobs) <= 0:
            print(f"Warning: Metric {metric} not found!")
        for item in jobs:
            try:
                job = item['metric'].get('job', 'Unknown')
                labeled_metrics.append(f"{job} {metric}")
            except KeyError as e:
                print(f"Warning: KeyError encountered - {e}. Skipping item: {item}")
    except requests.exceptions.RequestException as e:
        print(f"Error querying APS for metric {metric}: {e}")

# Sort the labeled_metrics list by the first column (job)
sorted_labeled_metrics = sorted(labeled_metrics, key=lambda x: x.split()[0])

# Process sorted labeled metrics
job = ""
previous_job = ""
metrics = ""
print("Metrics to drop:")
for line in sorted_labeled_metrics:
    job, metric = line.split(' ', 1)
    if previous_job == "" or job != previous_job:
        if metrics:
            print(metrics)
        metrics = ""
        previous_job = job
        print(f"Job: {job}")
        metrics = metric
    else:
        metrics += f"|{metric}"

# Print the last collected metrics
if metrics:
    print(metrics)
