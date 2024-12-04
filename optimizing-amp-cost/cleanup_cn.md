# 清理资源

请及时清理您的实验环境，避免产生非必要的费用。

删除示例应用程序。

``` bash
cd <your working dir>/container-blog-sample-code/optimizing-amp-cost
./deleteApp.sh 60
```

删除应用程序节点组。

``` bash
eksctl delete nodegroup \
  --cluster <your cluster name> \
  --name app
```

清理由 terraform 创建的资源。

``` bash
cd <your working dir>/terraform-aws-observability-accelerator/examples/existing-cluster-with-base-and-infra

export TF_VAR_aws_region=<Your AWS Region> # e.g. us-west-2
export TF_VAR_eks_cluster_id=<Your Cluster Name>
export TF_VAR_managed_grafana_workspace_id=<Your Grafana Workspace ID> # e.g. g-d73e6ed3d6
export TF_VAR_grafana_api_key=<Your Grafana API Key>

terraform destroy
```

删除 Grafana 服务账户。

``` bash
SA_ID=$(aws grafana list-workspace-service-accounts \
  --workspace-id $TF_VAR_managed_grafana_workspace_id \
  --query 'serviceAccounts[?name==`mimirtool-sa`].id' \
  --output text)

aws grafana delete-workspace-service-account \
  --workspace-id $TF_VAR_managed_grafana_workspace_id \
  --service-account-id $SA_ID
  
SA_ID=$(aws grafana list-workspace-service-accounts \
  --workspace-id $TF_VAR_managed_grafana_workspace_id \
  --query 'serviceAccounts[?name==`terraform-accelerator-eks`].id' \
  --output text)

aws grafana delete-workspace-service-account \
  --workspace-id $TF_VAR_managed_grafana_workspace_id \
  --service-account-id $SA_ID
```