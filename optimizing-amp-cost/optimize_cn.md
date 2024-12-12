# 步骤 3: 分析并优化 AMP 成本

在此步骤中，我们将借助工具来分析正在 Grafana 仪表板和 Prometheus 规则中被使用的指标，并将其与全量指标进行对比，从而发现被摄取到 AMP 但未被使用到的指标。我们可以过滤掉这些指标，从而降低 AMP 的费用。

安装 [Grafana Mimirtool](https://grafana.com/docs/mimir/latest/manage/tools/mimirtool/) 命令行工具来分析在 Grafana 仪表板和 Prometheus 规则中使用的指标。Mimirtool是一个可以用来检查，校验，配置 Grafana 仪表板，Prometheus 规则等的命令行工具。

``` bash
curl -fLo mimirtool https://github.com/grafana/mimir/releases/latest/download/mimirtool-linux-amd64
chmod +x mimirtool
sudo mv mimirtool /usr/local/bin/
```

为 `mimirtool` 创建一个 Grafana 令牌，此令牌的有效期限被设置为 24 小时，您可以在其过期后重新创建。

``` bash
export TF_VAR_managed_grafana_workspace_id=<Your grafana workspace id>

aws grafana create-workspace-service-account \
  --workspace-id $TF_VAR_managed_grafana_workspace_id \
  --grafana-role VIEWER \
  --name mimirtool-sa

SA_ID=$(aws grafana list-workspace-service-accounts \
  --workspace-id $TF_VAR_managed_grafana_workspace_id \
  --query 'serviceAccounts[?name==`mimirtool-sa`].id' \
  --output text)

# This token is expire after 24 hours.
GRAFANA_API_TOKEN=$(aws grafana create-workspace-service-account-token \
  --workspace-id $TF_VAR_managed_grafana_workspace_id \
  --service-account-id $SA_ID \
  --seconds-to-live 86400 \
  --name mimirtool-token \
  --query 'serviceAccountToken.key' \
  --output text)
  
```

分析 Grafana 仪表板。此步骤将生成一个 JSON 文件 `metrics-in-grafana.json`，此文件包含了所有在 Grafana dashboards 中被使用到的指标列表。

``` bash
export TF_VAR_managed_grafana_workspace_id=<Your AMG workspace ID>
export TF_VAR_aws_region=<Your region>

cd <your working dir>/container-blog-sample-code/optimizing-amp-cost
mimirtool analyze grafana \
  --address=https://${TF_VAR_managed_grafana_workspace_id}.grafana-workspace.${TF_VAR_aws_region}.amazonaws.com \
  --key="${GRAFANA_API_TOKEN}" \
  --output="metrics-in-grafana.json"
```

分析 Prometheus 规则。此步骤将生成一个 JSON 文件 `metrics-in-ruler.json`，此文件包含了所有在 Prometheus 规则中被使用到的指标列表。

``` bash
export AMP_WP_ID=<Your AMP workspace ID>

# Download prometheus rule files
export namespaces=$(aws amp list-rule-groups-namespaces \
  --workspace-id ${AMP_WP_ID} \
  --query 'ruleGroupsNamespaces[*].name' \
  --out text)
  
for ns in $namespaces
do
  aws amp describe-rule-groups-namespace \
    --workspace-id ${AMP_WP_ID} \
    --name $ns \
    --query 'ruleGroupsNamespace.data' \
    --output text \
    | base64 --decode > $ns.json
done

# Analyze rule files
mimirtool analyze rule-file \
  accelerator-infra-alerting.json accelerator-infra-rules.json \
  --output="metrics-in-ruler.json"
```

列出您的 AMP 工作区中的所有指标。

``` bash
awscurl -X GET --region ${TF_VAR_aws_region} \
  --service aps https://aps-workspaces.${TF_VAR_aws_region}.amazonaws.com/workspaces/${AMP_WP_ID}/api/v1/label/__name__/values \
  --header 'Content-Type: application/x-www-form-urlencoded' \
  > metrics-prometheus-all.json
```

运行一个脚本来提取未使用的指标。此脚本会把被使用到的指标和全量的指标进行对比，并输出未被使用的指标列表。

``` bash
cd <your working dir>/container-blog-sample-code/optimizing-amp-cost
pip3 install -r requirements.txt
echo "" > unused-metrics.txt
python3 extractUnusedMetrics.py | tee -a unused-metrics.txt
```

如果您看到任何类似下面的警告消息，可能是因为在您查询时该指标已不存在。例如，`certificatesigningrequest` 的生命周期很短。在 `certificatesigningrequest` 被删除后，它的指标将消失。

``` bash
Warning: Metric kube_certificatesigningrequest_condition not found!
Warning: Metric kube_certificatesigningrequest_created not found!
```

此脚本大概需要运行几分钟。一旦完成，您应该会得到如下屏幕所示的输出。

![](./images/figure%207.jpg)

 *<p align="center">图 7：extractUnusedMetrics.py 的输出</p>*

请忽略 job `Unknown` 的指标。这些指标并非由采集器摄取到 AMP，而是由 Prometheus Recording Rules 生成的。Recording Rules 可以预先计算频繁使用或计算复杂的查询表达式并保存为新的指标，达到优化查询性能和资源使用的目的。

对于其它 job 的未使用的指标，您可以选择：

* 从源头消除指标，例如 node exporter，这样可以完全避免采集器处理非必要指标的额外开销。
* 从 ADOT 采集器中删除指标，采集器仍需要进行匹配和删除处理。

在过滤未使用的指标之前，请运行以下脚本来验证是否有任何在 AMP 中缺失的但正在使用指标。我们将在过滤指标后再次运行相同的脚本，以确保我们不会错误地删除必要的指标样本。

``` bash
python3 validateMetrics.py ${TF_VAR_aws_region} ${AMP_WP_ID} before
```

您可能会看到如下屏幕截图所示的警告消息。这意味着一些指标在 AMP 中没有样本数据。这可能是由于 Grafana 或 Prometheus 规则配置错误，或者是因为没有数据被摄取到 AMP，例如，指标 `etcd_disk_backend_commit_duration_seconds_bucket` 在 EKS 中不可用。

![](./images/figure%208.jpg)

 *<p align="center">图 8： validateMetrics.py 的输出</p>*

在本实验中，我们将通过 [Prometheus metric relabeling](https://prometheus.io/docs/prometheus/latest/configuration/configuration/#metric_relabel_configs) 来删除未使用的指标。在以下的配置中，我们通过正则表达式匹配指标名称，并指定 action 为 drop。采集器在爬取指标样本之后，在往 AMP 发送数据之前，会丢弃匹配到规则的指标样本。

配置解析：

``` yaml
              metric_relabel_configs:
              - source_labels: [ __name__ ] # 匹配指标名称
                action: drop # 匹配成功则丢弃指标样本
                regex: # 正则表达式表示的指标列表
```


复制并粘贴 `metric_relabel_configs` 到您的 prometheus 配置中。在这种情况下，请更新 `modules/eks-monitoring/otel-config/templates/opentelemetrycollector.yaml` 并再次部署您的 terraform。

``` bash
cd <your working dir>/terraform-aws-observability-accelerator/modules/eks-monitoring/otel-config/templates
# 编辑 opentelemetrycollector.yaml
# 对于在 prometheus.config.scrape_configs 下的每一个 job_name 配置项, 
# 添加或更新 metric_relabel_configs 以丢弃不需要的 metrics.
# 把脚本输出的 metrics list 作为 regex 的参数值.
## 例如 job_name: kubelet
            - job_name: 'kubelet'
            ...
              relabel_configs
              ...
              metric_relabel_configs:
              - source_labels: [ __name__ ]
                action: drop
                regex: container_fs_inodes_free|container_fs_sector_writes_total|container_tasks_state # 此字符串已被截断以节省空间

## 对其它 jobs 执行同样的步骤:
## Job apiserver
## Job kube-proxy
## Job kube-state-metrics
## Job kubernetes-kubelet
## Job node-exporter
## Job otel-collector-metrics
```

例如：

![](./images/figure%209.jpg)

 *<p align="center">图 9：Prometheus 配置样例</p>*

更新 otel-config 的 helm chart 版本。

``` bash
cd <your working dir>/terraform-aws-observability-accelerator/modules/eks-monitoring/otel-config/
# 编辑 Chart.yaml
# 更新 version number
version: 0.8.1
```

再次部署 terraform。

``` bash
cd <your working dir>/terraform-aws-observability-accelerator/examples/existing-cluster-with-base-and-infra

export TF_VAR_aws_region=<Your AWS Region> # e.g. us-west-2
export TF_VAR_eks_cluster_id=<Your Cluster Name>
export TF_VAR_managed_grafana_workspace_id=<Your Grafana Workspace ID> # e.g. g-d73e6ed3d6
export TF_VAR_grafana_api_key=<Your Grafana API Key>

terraform plan

# You will see that "adot-collector-kubeprometheus" will be updated
  # module.eks_monitoring.module.helm_addon.helm_release.addon[0] will be updated in-place
  ~ resource "helm_release" "addon" {
        id                         = "adot-collector-kubeprometheus"
        name                       = "adot-collector-kubeprometheus"
      ~ version                    = "0.8.0" -> "0.8.1"
        # (31 unchanged attributes hidden)

        # (31 unchanged blocks hidden)
    }

# Apply
terraform apply -auto-approve
```

验证 ADOT collector pod 是否已成功重新启动。

``` bash
$ kubectl -n adot-collector-kubeprometheus get pod                                            
NAME                              READY   STATUS    RESTARTS   AGE
adot-collector-7d94d44f8b-mf4jj   1/1     Running   0          103s
```

再次从 Grafana "Explore" 页面验证 "active series" 的数量。我们可以看到在优化后 "active series" 的数量下降了。

![](./images/figure%2010.jpg)

 *<p align="center">图 10：优化后的 Prometheus active series 数量</p>*

我们也可以在 CloudWatch 中检查 `activeseries` 指标。但它只会在指标数据消失 2 小时后才更新。

![](./images/figure%2011.jpg)

 *<p align="center">图 11：CloudWatch activeseries 指标</p>*

验证所有 Grafana 仪表板与之前一样正常工作。

![](./images/figure%2012.jpg)

 *<p align="center">图 12：优化后的 Grafana 仪表板</p>*

运行一个脚本来验证在 Prometheus 规则中使用的指标是否有任何缺失。

``` bash
cd <your working dir>/container-blog-sample-code/optimizing-amp-cost
python3 validateMetrics.py ${TF_VAR_aws_region} ${AMP_WP_ID} after
```

![](./images/figure%2013.jpg)

 *<p align="center">图 13：validateMetrics.py 的输出</p>*