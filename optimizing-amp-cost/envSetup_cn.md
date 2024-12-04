# 步骤 1: 配置您的可观测性系统并部署样例应用 （可选）

在此步骤中，我们将在已有的 EKS 集群中构建一套基于 ADOT + AMP + AMG 的可观测性系统，并在集群中部署样例应用，以模拟一个接近生产环境规模的 EKS 集群。

**注意：** 如果您已经有一个运行中的环境并配置了基于 AMP 的可观测系统，则无需执行此步骤。

## 步骤 1.1: 使用 ADOT + AMP + AMG 配置您的可观测性

在这一步中，我们将利用 [AWS observability accelerator](https://github.com/aws-observability/terraform-aws-observability-accelerator) 构建一个健壮的可观测性系统。这个设置将为使用 AMP 和 AMG 监控您的 EKS 集群提供一个坚实的基础。

``` bash
cd <your working dir>
git clone https://github.com/aws-observability/terraform-aws-observability-accelerator.git
cd terraform-aws-observability-accelerator/examples/existing-cluster-with-base-and-infra
terraform init

# Region
export TF_VAR_aws_region=<Your AWS Region> # e.g. us-west-2

# EKS cluster name
export TF_VAR_eks_cluster_id=<Your Cluster Name>

export TF_VAR_managed_grafana_workspace_id=<Your Grafana Workspace ID> # e.g. g-d73e6ed3d6

# Disable fluentbit
sed -i 's/enable_logs = true/enable_logs = false/' main.tf

# Create Grafana service account and token
GRAFANA_SA_ID=$(aws grafana create-workspace-service-account \
  --workspace-id $TF_VAR_managed_grafana_workspace_id \
  --grafana-role ADMIN \
  --name terraform-accelerator-eks \
  --query 'id' \
  --output text)
export TF_VAR_grafana_api_key=$(aws grafana create-workspace-service-account-token \
  --workspace-id $TF_VAR_managed_grafana_workspace_id \
  --name "observability-accelerator-$(date +%s)" \
  --seconds-to-live 7200 \
  --service-account-id $GRAFANA_SA_ID \
  --query 'serviceAccountToken.key' \
  --output text)

# Please take note of your TF_VAR_grafana_api_key

# Deploy
terraform plan

terraform apply
```

上述 terraform 部署了：

* 一个 Amazon Managed Service for Prometheus 工作区;
* 在您的 EKS 集群中的一个 ADOT 收集器，用于摄取指标；
* 在您的 EKS 集群中的一个 Grafana Operator，用于管理 Grafana CRDs；
* 在您的 Grafana 工作区中的 AMP 作为数据源；
* Grafana 仪表板；

登录您的 Grafana 页面，检查您是否能够看到您的 EKS 集群的指标。

![](./images/figure%202.jpg)

 *<p align="center">图 2：Grafana仪表板</p>*

 ## 步骤 1.2: 部署示例应用程序

 部署示例应用程序以模拟常见规模的 EKS 集群。

在本演练中，我们将使用以下规模作为测试示例。

**注意：** 若您需要在自己账户中进行试验，您可以减少节点及 Pod 数量，以减少实验的费用。

|Nodes	|Deployments	|Pods	|Services	|
|---	|---	|---	|---	|
|20	|60	|600	|60	|

为了提高每个节点的 pod 密度，请启用 [VPC CNI prefix delegation](https://docs.aws.amazon.com/eks/latest/userguide/cni-increase-ip-addresses.html)。

**注意：** 如果您已经为 VPC CNI EKS 插件配置了 [advanced configuration](https://aws.amazon.com/blogs/containers/amazon-eks-add-ons-advanced-configuration/)，请合并配置而不是替换它。

``` bash
aws eks update-addon \
  --addon-name vpc-cni \
  --cluster-name <your cluster name> \
  --region <your region> \
  --configuration-values "{\"env\":{\"ENABLE_PREFIX_DELEGATION\":\"true\", \
    \"WARM_IP_TARGET\": \"3\", \"MINIMUM_IP_TARGET\": \"16\"}}"
```

为示例应用程序创建一个新的 EKS 节点组。

``` bash
eksctl create nodegroup \
  --cluster <your cluster name> \
  --name app \
  --node-type t3.medium \
  --nodes 20 \
  --nodes-min 0 \
  --nodes-max 20 \
  --region <your region> \
  --node-private-networking \
  --node-labels app=nginx
```

部署示例应用程序。

``` bash
cd <your working dir>
git clone https://github.com/aws-samples/container-blog-sample-code.git
cd container-blog-sample-code/optimizing-amp-cost

./createApp.sh 60

kubectl -n sample-nginx get pod
```

一旦示例应用程序准备就绪，您可以在 Grafana dashboard 查看到工作负载的监控图表。

![](./images/figure%203.jpg)

 *<p align="center">图 3：工作负载监控图表</p>*

我们可以通过 PromQL 语句 `count({__name__=~".+"})` 从 Grafana "Explore" 页面查询我们集群的 Prometheus [active series](https://community.grafana.com/t/what-is-and-active-series/85194)，该指标反映了当前采集器发送到 AMP 的指标样本数量。这个查询比较耗时，请选择一个较短的时间范围 (例如 **5 分钟**) 以节省时间。

![](./images/figure%204.jpg)

 *<p align="center">图 4：Prometheus active series 数量</p>*


