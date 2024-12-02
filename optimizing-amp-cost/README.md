# Optimizing AMP Cost for Amazon EKS Clusters

This repository contains sample code and resources demonstrating how to optimize the cost of Amazon Managed Service for Prometheus (AMP) when used with Amazon EKS clusters.

## Overview

This project provides practical examples and strategies for:
- Analyzing metrics being ingested into AMP
- Optimizing metric collection and storage
- Reducing costs while maintaining monitoring effectiveness
- Implementing best practices for Prometheus metric management

Please always follow the principle of least privilege. To complete the experiment in this blog, you need to have at least the minimum IAM permissions as listed in [iam-policy.json](./iam-policy.json). 

## Prerequisites

- An AWS account
- Amazon EKS cluster
- Amazon Managed Service for Prometheus (AMP) workspace
- kubectl installed and configured
- AWS CLI installed and configured
- Terraform CLI installed and configured
- Python installed and configured
- yq command installed

## Getting Started

1. Clone this repository:
```bash
git clone https://github.com/your-repo/optimizing-amp-cost.git
cd optimizing-amp-cost
