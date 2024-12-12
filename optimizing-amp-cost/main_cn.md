# 优化 Amazon EKS 集群的 Amazon Managed Service for Prometheus 成本

## 简介

[Amazon Managed Service for Prometheus (AMP)](https://aws.amazon.com/prometheus/) 是一项托管的监控和报警服务，为大规模部署的容器环境提供数据和深入的洞察。客户可以从其高可用性和安全性功能中获益，而无需在运营方面有太多投入。尽管与自管理解决方案相比，AMP 降低了总体拥有成本，但您仍有机会通过优化配置来进一步优化 AMP 的成本。

在本文中，我们将通过分析用于警报和仪表板的指标的摄取和消费情况，探讨优化 AMP 成本的策略。这将帮助客户了解他们的 AMP 使用情况，并识别降低成本的机会，而不会影响他们的监控能力。

## **方案概述**

在这份指南中，我们将引导您使用亚马逊云科技的托管服务为 Amazon EKS 构建一个健壮的可观测性解决方案。我们将涵盖以下内容：

* 设置环境：
    * 在 Amazon EKS 集群上部署示例应用程序
    * 部署一套基于 Amazon Managed Service for Prometheus (AMP) 和 Amazon Managed Grafana (AMG) 的可观测性系统
* 成本分析：
    * 深入分析此环境中与 AMP 相关的成本
* 优化策略：
    * 识别成本优化机会
    * 应用和验证成本优化措施

在本实验步骤结束时，您将清楚地了解如何使用亚马逊云科技的托管服务来实现、分析和优化 EKS 可观测性解决方案，帮助您在基础架构中实现性能和成本效益。

<p align="center">
  <img src="./images/figure%201.jpg" />
</p>

*<p align="center">图 1：总体架构</p>*

## 动手实践

在这个动手演练中，我们将引导您完成设置可观测性解决方案、部署示例应用程序、分析 AMP 成本和实施成本优化策略的过程。

**提示：**

* 步骤 1 是为了模拟一个已部署了工作负载和可观测性的 EKS 集群，若您已经有一个可用的环境，您可以跳过此步骤。
* 执行步骤 1 的实验会产生费用，请您在实验后**及时清理环境**，避免非必要费用的产生。

### 先决条件

* 一个 Amazon EKS 集群 (本例中为 1.30 版本)
* 为您的 EKS 集群启用 IAM OIDC provider association;
* 一个 Amazon Managed Grafana 工作区;
* Terraform [CLI](https://developer.hashicorp.com/terraform/install);
* AWS CLI (>=2.15.51 or >=1.32.106);
* [awscurl](https://github.com/okigan/awscurl);
* git CLI;
* python;
* yq 命令;

### **步骤 1： 配置您的可观测性系统并部署样例应用 （可选）**

在此步骤中，我们将在已有的 EKS 集群中构建一套基于 ADOT + AMP + AMG 的可观测性系统，并在集群中部署样例应用，以模拟一个接近生产环境规模的 EKS 集群。

**注意：** 如果您已经有一个运行中的环境并配置了基于 AMP 的可观测系统，则无需执行此步骤。

请您按照[步骤 1](./envSetup_cn.md)完成环境的准备。

在我们的实验中，我们在 UTC 时间 10 月 18 日 13：00 开始摄取指标，然后在 UTC 时间 10 月 21 日 02：00 检查费用。这个时间跨度允许我们收集足够的数据来分析 AMP 成本并识别优化机会。

### **步骤 2： 了解 AMP 的成本**

在亚马逊云科技控制台上，转到 [**Billing and Cost Management**](https://us-east-1.console.aws.amazon.com/costmanagement/home#/home) > Bills > Charges by service，按服务名称 “Managed Service for Prometheus” 过滤，您可以看到当月 AMP 的成本。

在本实验中，我的 AMP 的大部分成本来自于 **metrics ingested**。

<p align="center">
  <img src="./images/figure%205.jpg" />
</p>

*<p align="center">图 5：AMP 成本细分</p>*

在亚马逊云科技控制台上，转到 [**Billing and Cost Management**](https://us-east-1.console.aws.amazon.com/costmanagement/home#/home) > Cost Explorer，我们可以看到按天的成本细分。

<p align="center">
  <img src="./images/figure%206.jpg" />
</p>

*<p align="center">图 6：AMP 的每日成本</p>*

### **步骤 3： 如何优化我们的 AMP 成本？**

#### **分析和优化未使用的指标。**

我们将借助工具来分析正在 Grafana 仪表板和 Prometheus 规则中被使用的指标，并将其与全量指标进行对比，从而发现被摄取到 AMP 但未被使用到的指标。我们可以过滤掉这些指标，从而降低 AMP 的费用。

请您按照[步骤 3](./optimize_cn.md)分析并优化采集到 AMP 的指标。

在优化步骤完成后，请让该环境运行 1~2 天以观测成本变化。

在我们的实验中，我们在 10 月 21 日过滤了指标。从下面的屏幕截图可以看出，AMP 的成本从 39.91 美元下降了 64%。

|	|Cost before optimization (20th Oct)	|Cost after optimization (22nd Oct)	|Cost reduction	|
|---	|---	|---	|---	|
|Daily cost	|39.96	|14.3	|64%	|

<p align="center">
  <img src="./images/figure%2014.jpg" />
</p>

*<p align="center">图 14：优化后 AMP 的每日成本</p>*

#### **考虑其他降低成本的选项**

在前面的步骤中，我们已经演示了通过过滤不需要的指标可以显著降低 AMP 的成本。您还可以考虑一些其他的选项：

* 查看您的 prometheus [scrap interval](https://prometheus.io/docs/prometheus/latest/configuration/configuration/#scrape_config)，是否有增加间隔的空间？
* [优化](https://grafana.com/blog/2022/10/20/how-to-manage-high-cardinality-metrics-in-prometheus-and-kubernetes/)高基数指标。
* 从源头消除指标。例如，您可以[禁用 Prometheus node exporter 的收集器](https://github.com/prometheus/node_exporter?tab=readme-ov-file#collectors)。
* [优化](https://docs.aws.amazon.com/prometheus/latest/userguide/AMP-costs.html#AMP-costs-FAQ-alertquery)您的查询成本。

## 清理资源

请您按照[清理步骤](./cleanup_cn.md)删除您在本实验中部署的资源。

## **总结**

在本文中，我们探讨了优化使用 Amazon Managed Service for Prometheus (AMP) 监控 Amazon EKS 集群成本的策略。演示了如何部署一套完备的可观测性解决方案、分析 AMP 使用情况以及实施节省成本的措施，例如过滤未使用的指标。

通过遵循这些步骤，我们在示例场景中实现了 **64%** 的显著成本降低。这种优化不仅降低了开支，而且通过关注最相关的指标来提高了监控设置的效率。

优化您的 AMP 成本是一个持续的过程。定期审查您的指标使用情况，根据您的应用程序的变化调整您的过滤规则，并在适当时考虑其他优化技术，如调整抓取间隔和降低基数。

通过利用 AMP 的托管服务功能并实施这些成本优化策略，您可以为您的 EKS 集群维护一个健壮的监控解决方案，同时控制您的可观测性成本。

## **采取行动**

将您的 Prometheus 监控迁移到 Amazon Managed Service for Prometheus (AMP)，以减少运营负担并优化成本。通过过渡到 AMP，您可以释放团队的时间和资源，让他们专注于更高价值的工作。一旦在 AMP 上，分析您的指标使用情况，识别优化机会，并利用 AMP 的成本管理功能来控制您的支出。