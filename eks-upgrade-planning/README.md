# EKS 升级规划工具

这个工具集包含两个主要脚本，用于帮助您规划 Amazon EKS 集群的版本升级。通过这些工具，您可以获取集群信息、分析升级兼容性问题，并生成详细的升级计划文档。

## 功能概述

- **eks_cluster_info.py**: 收集 EKS 集群的详细信息，包括版本、节点组、插件、健康状态等
- **eks_upgrade_planner.py**: 基于集群信息生成详细的升级规划文档，包括升级前检查、控制面升级、插件升级、数据面升级等步骤

## 前提条件

- Python 3.6+
- 配置了适当EKS访问权限的AWS API客户端环境
- 调用Bedrock模型的权限

## 使用方法

### 方法一：直接生成升级计划

您可以直接运行 `eks_upgrade_planner.py`，它会自动连接到 EKS 集群获取信息并生成升级文档：

```bash
python eks_upgrade_planner.py <集群名称> <目标版本> --region <AWS区域> [其他选项]
```

例如：

```bash
python eks_upgrade_planner.py my-cluster 1.30 --region us-west-2 --connect-k8s --output upgrade-plan.md
```

#### 参数说明：

- `<集群名称>`: EKS 集群的名称
- `<目标版本>`: 要升级到的 Kubernetes 版本（例如 1.30）
- `--region`: AWS 区域（如果未指定，将使用默认配置）
- `--profile`: AWS 配置文件名称（可选）
- `--connect-k8s`: 连接到 Kubernetes API 服务器以收集额外信息（可选）
- `--output` 或 `-o`: 输出文件路径（可选，默认输出到控制台）
- `--bedrock-region`: AWS Bedrock 服务区域（默认为 us-west-2）
- `--model-id`: 使用的 Bedrock 模型 ID（默认为 us.deepseek.r1-v1:0）
- `--role-arn`: 用于调用 Bedrock API 的 IAM 角色 ARN（可选）
- `--debug`: 启用调试模式，打印请求和响应详情（可选）

### 方法二：分步执行（先收集信息，再生成计划）

#### 步骤 1：使用 eks_cluster_info.py 收集集群信息

```bash
python eks_cluster_info.py <集群名称> <目标版本> --region <AWS区域> [--connect-k8s] --output-file cluster-info.json
```

例如：

```bash
python eks_cluster_info.py my-cluster 1.30 --region us-west-2 --connect-k8s --output-file cluster-info.json
```

#### 参数说明：

- `<集群名称>`: EKS 集群的名称
- `<目标版本>`: 要升级到的 Kubernetes 版本（例如 1.30）
- `--region`: AWS 区域（如果未指定，将使用默认配置）
- `--profile`: AWS 配置文件名称（可选）
- `--connect-k8s`: 连接到 Kubernetes API 服务器以收集额外信息（可选）
- `--output-file`: 输出 JSON 文件路径（可选，默认输出到控制台）

#### 步骤 2：使用 eks_upgrade_planner.py 生成升级计划

```bash
python eks_upgrade_planner.py --cluster-info-file cluster-info.json [其他选项]
```

例如：

```bash
python eks_upgrade_planner.py --cluster-info-file cluster-info.json --output upgrade-plan.md
```

#### 参数说明：

- `--cluster-info-file`: 包含集群信息的 JSON 文件路径（由 eks_cluster_info.py 生成）
- `--output` 或 `-o`: 输出文件路径（可选，默认输出到控制台）
- `--bedrock-region`: AWS Bedrock 服务区域（默认为 us-west-2）
- `--model-id`: 使用的 Bedrock 模型 ID（默认为 us.deepseek.r1-v1:0）
- `--role-arn`: 用于调用 Bedrock API 的 IAM 角色 ARN（可选）
- `--debug`: 启用调试模式，打印请求和响应详情（可选）

## 两种方法的区别

### 方法一（直接使用 eks_upgrade_planner.py）

- **优点**：一步完成，操作简单
- **缺点**：您需要确保执行此脚本的用户/角色同时拥有连接EKS集群和调用LLM的权限

### 方法二（分步执行）

- **优点**：
  - 可以在不同时间或不同环境中分别执行信息收集和升级计划生成
  - 可以保存集群信息以便重复使用或比较
  - 适合离线分析或在无法直接连接集群的环境中使用
- **缺点**：需要执行两个命令，操作稍复杂

## 注意事项

1. 使用 `--connect-k8s` 参数时，脚本将连接到 Kubernetes API 服务器以获取更详细的信息，包括自管理节点、Karpenter 节点和开源插件等
2. 生成的升级计划是基于 AI 分析的建议，在执行实际升级操作前，请务必在测试环境中验证
3. 确保您的 AWS 凭证具有足够的权限来访问 EKS 集群和 Bedrock 服务
4. 如果使用 Bedrock 服务，请确保您的 AWS 账户已启用相应的模型访问权限

## 依赖项

请确保已安装以下 Python 包：

```bash
pip install boto3 kubernetes requests
```

## 示例输出

生成的升级计划文档包含以下主要部分：

1. 集群信息概览
2. 升级前检查（版本变更、版本偏差、插件兼容性、API 兼容性、集群健康）
3. 控制面升级步骤
4. 插件升级建议
5. 数据面升级步骤
6. 测试验证建议

每个部分都包含详细的说明和可执行的命令，帮助您安全地完成 EKS 集群升级。
