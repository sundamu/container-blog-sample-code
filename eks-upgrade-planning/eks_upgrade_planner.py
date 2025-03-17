#!/usr/bin/env python3
import argparse
import json
import os
import sys
import requests
import datetime
import time
from typing import Dict, List, Tuple, Any, Optional
import boto3
import logging

# 导入eks_cluster_info模块中的函数
from eks_cluster_info import (
    get_cluster_info, 
    get_current_version, 
    get_health_issues, 
    get_compatibility_issues,
    get_addon_compatibility_issues,
    get_deprecated_api_versions,
    get_nodegroups,
    get_fargate_profiles,
    get_installed_addons,
    get_addon_upgrade_info,
    connect_to_cluster,
    get_node_versions,
    get_opensource_addons,
    get_core_components_version
)

# 自定义JSON编码器，处理datetime对象
class DateTimeEncoder(json.JSONEncoder):
    def default(self, obj):
        if isinstance(obj, datetime.datetime):
            return obj.isoformat()
        return super().default(obj)

# 全局参数设置
MAX_RETRIES = 3  # 最大重试次数
RETRY_INTERVAL = 30  # 重试间隔（秒）
RETRY_BACKOFF_FACTOR = 2  # 重试间隔递增因子

# 配置日志
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger("eks_upgrade_planner")

# 设置AWS Bedrock客户端
def get_bedrock_client(region_name="us-west-2", role_arn=None):
    """获取AWS Bedrock客户端
    
    Args:
        region_name: AWS区域名称
        role_arn: 要扮演的IAM角色ARN
        
    Returns:
        boto3.client: Bedrock运行时客户端
    """
    try:
        if role_arn:
            # 使用STS假设角色
            logger.info(f"使用角色 {role_arn} 创建Bedrock客户端")
            sts_client = boto3.client('sts')
            assumed_role = sts_client.assume_role(
                RoleArn=role_arn,
                RoleSessionName="EksUpgradePlannerSession"
            )
            credentials = assumed_role['Credentials']
            
            # 使用临时凭证创建Bedrock客户端
            bedrock_runtime = boto3.client(
                service_name="bedrock-runtime",
                region_name=region_name,
                aws_access_key_id=credentials['AccessKeyId'],
                aws_secret_access_key=credentials['SecretAccessKey'],
                aws_session_token=credentials['SessionToken']
            )
        else:
            # 使用默认凭证创建Bedrock客户端
            bedrock_runtime = boto3.client(
                service_name="bedrock-runtime",
                region_name=region_name
            )
        return bedrock_runtime
    except Exception as e:
        logger.error(f"创建Bedrock客户端失败: {e}")
        return None

# 文档URL常量
DOCS = {
    "troubleshooting": "https://docs.aws.amazon.com/eks/latest/userguide/troubleshooting.html",
    "update_addon": "https://docs.aws.amazon.com/eks/latest/userguide/updating-an-add-on.html",
    "standard_versions": "https://docs.aws.amazon.com/eks/latest/userguide/kubernetes-versions-standard.html",
    "extended_versions": "https://docs.aws.amazon.com/eks/latest/userguide/kubernetes-versions-extended.html",
    "best_practices": "https://docs.aws.amazon.com/eks/latest/best-practices/cluster-upgrades.html",
    "api_migration": "https://kubernetes.io/docs/reference/using-api/deprecation-guide/",
    "update_nodegroup": "https://docs.aws.amazon.com/eks/latest/userguide/update-managed-node-group.html",
    "update_kubernetes": "https://docs.aws.amazon.com/eks/latest/userguide/update-cluster.html"
}

def fetch_document(url: str) -> str:
    """获取文档内容
    
    Args:
        url: 文档URL
        
    Returns:
        str: 文档内容
    """
    try:
        response = requests.get(url, timeout=30)
        response.raise_for_status()
        return response.text
    except requests.RequestException as e:
        logger.error(f"获取文档失败: {url}, 错误: {e}")
        return f"获取文档失败: {e}"

def validate_versions(current_version: str, target_version: str) -> Tuple[bool, str, List[str]]:
    """验证版本并生成中间版本列表
    
    Args:
        current_version: 当前版本
        target_version: 目标版本
        
    Returns:
        Tuple[bool, str, List[str]]: (是否有效, 错误信息, 中间版本列表)
    """
    try:
        # 转换版本字符串为整数元组
        current = tuple(map(int, current_version.split('.')[:2]))
        target = tuple(map(int, target_version.split('.')[:2]))
        
        # 验证版本
        if len(current) != 2 or len(target) != 2:
            return False, "版本必须是'x.y'格式", []
        
        if current >= target:
            return False, "目标版本必须大于当前版本", []
        
        # 生成版本列表
        versions = []
        major, minor = current
        target_major, target_minor = target
        
        while (major, minor) < (target_major, target_minor):
            minor += 1
            if minor > 99:  # 假设次要版本不超过99
                major += 1
                minor = 0
            versions.append(f"{major}.{minor}")
        
        return True, "", versions
    except Exception as e:
        return False, f"验证版本时出错: {e}", []

def invoke_llm(
    client, 
    model_id: str, 
    system_prompt: str, 
    user_prompt: str, 
    temperature: float = 0.1,
    debug: bool = False
) -> str:
    """调用LLM生成内容，支持重试逻辑
    
    Args:
        client: Bedrock客户端
        model_id: 模型ID
        system_prompt: 系统提示
        user_prompt: 用户提示
        temperature: 温度参数
        debug: 是否打印请求和响应详情
        
    Returns:
        str: LLM生成的内容
    """
    if not client:
        return "无法连接到LLM服务"
    
    # 记录当前使用的模型ID
    logger.info(f"使用模型ID: {model_id}")
    
    # 准备请求体
    def prepare_request_body():
        # 根据模型ID选择不同的请求格式
        if "anthropic" in model_id.lower() or "claude" in model_id.lower():
            # Claude模型
            messages = [
                # {"role": "system", "content": system_prompt},
                # {"role": "user", "content": user_prompt}
                {
                    "role": "user",  
                    "content": f"{system_prompt}\n\n{user_prompt}"
                }
            ]
            
            # Claude模型的基本请求体
            return {
                "anthropic_version": "bedrock-2023-05-31",
                "max_tokens": 8191,
                "messages": messages,
                "temperature": temperature
            }
        
        elif "deepseek" in model_id.lower():
            # DeepSeek模型
            messages = [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt}
            ]
            
            # DeepSeek模型的基本请求体
            return {
                "messages": messages,
                "temperature": temperature,
                "max_tokens": 4096
            }
        
        # elif "amazon" in model_id.lower() or "titan" in model_id.lower():
        #     # Amazon Titan模型
        #     # 使用messages格式，不使用inputText
        #     messages = [
        #         {"role": "system", "content": system_prompt},
        #         {"role": "user", "content": user_prompt}
        #     ]
            
        #     return {
        #         "messages": messages,
        #         "textGenerationConfig": {
        #             "temperature": temperature,
        #             "maxTokenCount": 4096
        #         }
        #     }
        
        # elif "cohere" in model_id.lower():
        #     # Cohere模型
        #     prompt = f"System: {system_prompt}\n\nHuman: {user_prompt}"
            
        #     # 确保包含messages字段
        #     messages = [
        #         {"role": "system", "content": system_prompt},
        #         {"role": "user", "content": user_prompt}
        #     ]
            
        #     return {
        #         "prompt": prompt,
        #         "messages": messages,  # 添加messages字段以满足API要求
        #         "temperature": temperature,
        #         "max_tokens": 4096
        #     }
        
        # elif "meta" in model_id.lower() or "llama" in model_id.lower():
        #     # Meta/Llama模型
        #     prompt = f"System: {system_prompt}\n\nHuman: {user_prompt}"
            
        #     # 确保包含messages字段
        #     messages = [
        #         {"role": "system", "content": system_prompt},
        #         {"role": "user", "content": user_prompt}
        #     ]
            
        #     return {
        #         "prompt": prompt,
        #         "messages": messages,  # 添加messages字段以满足API要求
        #         "temperature": temperature,
        #         "max_gen_len": 4096
        #     }
        
        # elif "ai21" in model_id.lower():
        #     # AI21模型
        #     prompt = f"System: {system_prompt}\n\nHuman: {user_prompt}"
            
        #     # 确保包含messages字段
        #     messages = [
        #         {"role": "system", "content": system_prompt},
        #         {"role": "user", "content": user_prompt}
        #     ]
            
        #     return {
        #         "prompt": prompt,
        #         "messages": messages,  # 添加messages字段以满足API要求
        #         "temperature": temperature,
        #         "maxTokens": 4096
        #     }
        
        else:
            # 默认使用Claude格式，因为它是最常用的
            logger.warning(f"未知模型类型: {model_id}，使用Claude请求格式")
            messages = [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt}
            ]
            
            return {
                "anthropic_version": "bedrock-2023-05-31",
                "messages": messages,
                "temperature": temperature
            }
    
    # 解析响应
    def parse_response(response_body):
        if 'content' in response_body and isinstance(response_body.get('content'), list):
            return response_body.get('content')[0].get('text')
        elif 'choices' in response_body and isinstance(response_body.get('choices'), list):
            return response_body.get('choices')[0].get('message').get('content')
        elif 'results' in response_body and isinstance(response_body.get('results'), list):
            return response_body.get('results')[0].get('outputText')
        elif 'generations' in response_body and isinstance(response_body.get('generations'), list):
            return response_body.get('generations')[0].get('text')
        elif 'generation' in response_body:
            return response_body.get('generation')
        elif 'completions' in response_body and isinstance(response_body.get('completions'), list):
            return response_body.get('completions')[0].get('data').get('text')
        elif 'completion' in response_body:
            return response_body.get('completion')
        elif 'generated_text' in response_body:
            return response_body.get('generated_text')
        else:
            logger.warning(f"未知响应格式: {response_body}")
            return str(response_body)
    
    # 实现重试逻辑
    retry_count = 0
    wait_time = RETRY_INTERVAL
    
    while retry_count <= MAX_RETRIES:
        try:
            request_body = prepare_request_body()
            
            # 如果在debug模式下，打印请求体
            if debug:
                logger.info(f"{model_id}模型请求体: {json.dumps(request_body)}")
            else:
                logger.info(f"正在调用 {model_id} 模型...")
            
            # 计算请求令牌数（简单估算）
            system_tokens = len(system_prompt.split())
            user_tokens = len(user_prompt.split())
            request_tokens = system_tokens + user_tokens
            
            # 记录开始时间
            start_time = time.time()
            
            # 调用模型
            response = client.invoke_model(
                modelId=model_id,
                body=json.dumps(request_body)
            )
            
            # 计算延迟
            latency = time.time() - start_time
            
            # 解析响应
            response_body = json.loads(response.get('body').read())
            result = parse_response(response_body)
            
            # 计算响应令牌数（简单估算）
            response_tokens = len(result.split())
            
            # 计算费用（简单估算，实际费用需要根据具体模型和定价计算）
            # 这里使用一个简单的估算方法，实际应用中应该根据不同模型的定价策略计算
            if "claude" in model_id.lower():
                # Claude 模型的估算费用（示例值）
                request_cost = request_tokens * 0.000003  # 每个输入令牌 $0.000003
                response_cost = response_tokens * 0.000015  # 每个输出令牌 $0.000015
            elif "deepseek" in model_id.lower():
                # DeepSeek 模型的估算费用（示例值）
                request_cost = request_tokens * 0.00000135  # 每个输入令牌 $0.00000135
                response_cost = response_tokens * 0.0000054  # 每个输出令牌 $0.0000054
            # else:
            #     # 默认估算费用
            #     request_cost = request_tokens * 0.0000005
            #     response_cost = response_tokens * 0.0000015
            
            total_cost = request_cost + response_cost
            
            # 记录指标
            logger.info(f"LLM请求指标 - 延迟: {latency:.2f}秒, 请求令牌: {request_tokens}, 响应令牌: {response_tokens}, 估算费用: ${total_cost:.6f}")
            
            # 如果在debug模式下，打印响应体
            if debug:
                logger.info(f"{model_id}模型响应体: {json.dumps(response_body)}")
            
            return result
            
        except Exception as e:
            error_message = str(e)
            logger.error(f"调用LLM失败 (尝试 {retry_count+1}/{MAX_RETRIES+1}): {error_message}")
            
            # 检查是否是"too many requests"错误
            if "TooManyRequestsException" in error_message or "too many requests" in error_message.lower() or "throttling" in error_message.lower():
                if retry_count < MAX_RETRIES:
                    logger.info(f"遇到请求限制，等待 {wait_time} 秒后重试...")
                    time.sleep(wait_time)
                    retry_count += 1
                    wait_time *= RETRY_BACKOFF_FACTOR  # 指数退避
                    continue
            
            # 对于其他错误或已达到最大重试次数，记录详细错误并返回
            import traceback
            logger.error(f"错误详情: {error_message}")
            logger.error(f"堆栈跟踪: {traceback.format_exc()}")
            return f"调用LLM失败: {error_message}"
    
    return f"调用LLM失败: 达到最大重试次数 ({MAX_RETRIES})"

def process_cluster_health_issues(client, docs: Dict[str, str], health_issues: List[Dict], model_id: str, debug: bool = False) -> str:
    """处理集群健康问题
    
    Args:
        client: Bedrock客户端
        docs: 文档内容字典
        health_issues: 健康问题列表
        model_id: 模型ID
        
    Returns:
        str: 健康问题分析结果
    """
    if not health_issues:
        return "集群没有健康问题。"
    
    system_prompt = """你是一位Kubernetes和Amazon EKS专家，请根据用户提供的的Cluster Health Issues信息，逐步思考并提供详细的，可执行的修复建议。

<参考文档>
- EKS troubleshooting：{troubleshooting}
- AWS VPC 子网在创建之后无法调整CIDR大小
</参考文档>

<风格>严谨，专业客观</风格>

<要求>
- 如果集群没有Health Issues，则直接返回无issue
- 不要提供未经证实的解决办法
- 除了专业名称、代码、命令行之外，请使用简体中文输出
</要求>

输出模版（Markdown）：
### 问题1
#### 问题描述：{{问题1描述}}
#### 解决办法：{{问题1解决办法}}

### 问题2
#### 问题描述：{{问题2描述}}
#### 解决办法：{{问题2解决办法}}

### 问题3
...
""".format(troubleshooting=docs.get("troubleshooting", ""))
    
    user_prompt = f"Cluster Health Issues:{json.dumps(health_issues, indent=2, cls=DateTimeEncoder)}"

    logger.info("正在分析集群健康问题。。。")
    
    return invoke_llm(
        client=client,
        model_id=model_id,
        system_prompt=system_prompt,
        user_prompt=user_prompt,
        debug=debug
    )

def process_addon_compatibility(client, docs: Dict[str, str], addon_comp_issues: List[Dict], model_id: str, debug: bool = False) -> str:
    """处理插件兼容性问题
    
    Args:
        client: Bedrock客户端
        docs: 文档内容字典
        addon_comp_issues: 插件兼容性问题列表
        model_id: 模型ID
        
    Returns:
        str: 插件兼容性分析结果
    """
    if not addon_comp_issues:
        return "集群没有插件兼容性问题。"
    
    system_prompt = """你是一位Kubernetes和Amazon EKS专家，请根据用户提供的的EKS addons compatibility信息，逐步思考并提供详细的，可执行的升级建议。

<参考文档>
- EKS Addon升级：{update_addon}
</参考文档>

<风格>严谨，专业客观</风格>

<要求>
- 如果集群没有addon compatibility issue，则直接返回无issue
- 不要提供移除addon的建议
- 不要提供没有切确来源的解决办法
- 除了专业名称、代码、命令行之外，请使用简体中文输出
</要求>

输出模版（Markdown）：
### 不兼容的EKS addons
...

### xx addon 需要升级到 xx 版本
备份：
参考AWS CLI命令：
回退参考命令：
注意事项（若有）：

### xx addon 需要升级到 xx 版本
备份：
参考AWS CLI命令：
回退参考命令：
注意事项（若有）：
...

### addon 升级的最佳实践
建议您把自定义配置配置到EKS Addon的Advanced configuration，避免被覆盖；
若升级时发生字段冲突，可选择OVERWRITE模式，但请确保您已经把自定义配置同步到Advanced configuration；
...
""".format(update_addon=docs.get("update_addon", ""))
    
    user_prompt = f"EKS addons compatibility信息：{json.dumps(addon_comp_issues, indent=2, cls=DateTimeEncoder)}"

    logger.info("正在分析EKS插件兼容性问题。。。")
    
    return invoke_llm(
        client=client,
        model_id=model_id,
        system_prompt=system_prompt,
        user_prompt=user_prompt,
        debug=debug
    )

def process_version_changes(client, docs: Dict[str, str], current_version: str, target_version: str, model_id: str, debug: bool = False) -> str:
    """处理版本变更
    
    Args:
        client: Bedrock客户端
        docs: 文档内容字典
        current_version: 当前版本
        target_version: 目标版本
        model_id: 模型ID
        
    Returns:
        str: 版本变更分析结果
    """
    system_prompt = """你是一位Kubernetes和Amazon EKS技术专家，请根据用户提供的当前及目标EKS版本信息，逐步思考并制定一份升级前检查建议。

<要求>
- 提供对注意事项的解析
- 提供详细的，可操作的检查方法
- 提供详细的，可操作的应对措施
- 使用kubent或pluto命令检查API Version
- 除了专业名称、代码、命令行之外，请使用简体中文输出
- 不要考虑<=当前版本，或者>目标版本的变更
- 不要考虑新增功能或特性
- 不要使用kubectl get命令检查API Version
- 无需提供备份，升级或回退操作步骤
</要求>

<参考文档>
- Standard version版本信息: {standard_versions}
- Extended support version版本信息: {extended_versions}
</参考文档>

<风格>严谨，专业客观</风格>

输出模版（Markdown）：
好的，我将输出EKS版本变更影响评估，但不会包含新增的功能或特性。

当前EKS集群版本：

目标EKS集群版本：

### EKS 1.x 升级至 1.x 版本变更影响评估

1 EKS 1.x 关键变更
...

2 EKS 1.x 关键变更
...
""".format(
        standard_versions=docs.get("standard_versions", ""),
        extended_versions=docs.get("extended_versions", "")
    )
    
    user_prompt = f"""当前EKS集群版本：{current_version}
目标EKS集群版本：{target_version}"""

    logger.info("正在分析特点版本变更的影响。。。")
    
    return invoke_llm(
        client=client,
        model_id=model_id,
        system_prompt=system_prompt,
        user_prompt=user_prompt,
        debug=debug
    )

def process_deprecated_apis(client, docs: Dict[str, str], deprecated_apis: List[Dict], model_id: str, debug: bool = False) -> str:
    """处理废弃的API
    
    Args:
        client: Bedrock客户端
        docs: 文档内容字典
        deprecated_apis: 废弃的API列表
        model_id: 模型ID
        
    Returns:
        str: 废弃API分析结果
    """
    if not deprecated_apis:
        return "集群没有使用废弃的API。"
    
    system_prompt = """你是一位Kubernetes和Amazon EKS技术专家，请根据用户提供的当前及目标EKS版本信息，以及正在使用的Deprecated API versions信息，逐步思考并制定一份API versions更新建议。

<要求>
- 如果集群没有使用deprecated API，则直接返回无issue
- API versions迁移的步骤
- 使用自动化转换工具
- 不要考虑不相关版本的信息，包括当前版本
- 不要使用kubectl get命令检查或验证API versions
- 只需要提供API versions更新建议，不要提供集群版本的升级步骤
- 除了专业名称、代码、命令行之外，请使用简体中文输出
</要求>

<参考文档>
- Kubernetes API migration guide: {api_migration}
</参考文档>

<风格>严谨，专业客观</风格>

输出模版（Markdown）：
### 正在使用的deprecated API version
### 使用deprecated API version的client agent
### 迁移方法
""".format(api_migration=docs.get("api_migration", ""))
    
    user_prompt = f"正在使用的Deprecated API versions信息：{json.dumps(deprecated_apis, indent=2, cls=DateTimeEncoder)}"

    logger.info("正在分析弃用API的影响。。。")
    
    return invoke_llm(
        client=client,
        model_id=model_id,
        system_prompt=system_prompt,
        user_prompt=user_prompt,
        debug=debug
    )

def process_nodegroups(
    client, 
    docs: Dict[str, str], 
    ng_list: List[Dict], 
    fg_profile_list: List[str],
    self_managed_nodes: Optional[str],
    karpenter_nodes: Optional[str],
    model_id: str,
    debug: bool = False
) -> str:
    """处理节点组信息
    
    Args:
        client: Bedrock客户端
        docs: 文档内容字典
        ng_list: 节点组列表
        fg_profile_list: Fargate配置文件列表
        self_managed_nodes: 自管理节点信息
        karpenter_nodes: Karpenter节点信息
        model_id: 模型ID
        
    Returns:
        str: 节点组分析结果
    """
    if not ng_list and not fg_profile_list and not self_managed_nodes and not karpenter_nodes:
        return "集群没有节点组或Fargate配置文件。"
    
    system_prompt = """你是一位Kubernetes和Amazon EKS技术专家，请根据用户提供的EKS节点组及Fargate Profile信息，逐步思考并制定一份节点组及Fargate升级步骤。

<要求>
- 若集群存在节点组，则为每个节点组提供升级命令
- 若节点组数量超过3个，可以提供指导步骤而无需穷举所有节点组
- 若集群不存在Fargate profile，则无需提供Fargate升级方法
- 若集群不存在自管理节点或Kapenter节点，则无需提供这两种节点的升级方法
- 除了专业名称、代码、命令行之外，请使用简体中文输出
</要求>

<参考文档>
- 更新集群的托管式节点组: {update_nodegroup}
</参考文档>

<风格>严谨，专业客观</风格>

输出模版（Markdown）：
### 集群托管节点组列表
1 ...
2 ...

### 托管节点组升级方法
蓝绿方式升级（推荐）
...
原节点组升级
...

### 自管理节点升级方法

### Karpenter节点升级方法

### 节点升级注意事项
节点升级时所有节点会被替换，请确保您没有对节点的依赖配置（例如IP地址）；
删除旧节点组时需注意...
...

### Fargate Pod升级方法

### Fargate Pod升级注意事项
""".format(update_nodegroup=docs.get("update_nodegroup", ""))
    
    user_prompt = f"""托管节点组列表：{json.dumps(ng_list, indent=2, cls=DateTimeEncoder)}
Fargate Profile列表：{json.dumps(fg_profile_list, indent=2, cls=DateTimeEncoder)}
自管理节点信息：{self_managed_nodes or "无"}
Karpenter节点信息：{karpenter_nodes or "无"}"""

    logger.info("正在分析数据面升级情况。。。")
    
    return invoke_llm(
        client=client,
        model_id=model_id,
        system_prompt=system_prompt,
        user_prompt=user_prompt,
        debug=debug
    )

def process_cluster_summary(
    client,
    cluster_name: str,
    current_version: str,
    target_version: str,
    ng_list: List[Dict],
    self_managed_nodes: Optional[str],
    karpenter_nodes: Optional[str],
    fg_profile_list: List[str],
    version_skew: Optional[str],
    kube_proxy: Optional[str],
    model_id: str, 
    debug: bool = False
) -> str:
    """处理集群概要信息
    
    Args:
        client: Bedrock客户端
        cluster_name: 集群名称
        current_version: 当前版本
        target_version: 目标版本
        ng_list: 节点组列表
        self_managed_nodes: 自管理节点信息
        karpenter_nodes: Karpenter节点信息
        fg_profile_list: Fargate配置文件列表
        version_skew: 版本偏差信息
        kube_proxy: Kube-proxy版本信息
        
    Returns:
        str: 集群概要信息
    """
    system_prompt = """你是一位Kubernetes和Amazon EKS技术专家，请根据用户提供的EKS版本信息，整理集群的概要信息。

<要求>
- 内容清晰，格式规范，可读性强
- 除了专业名称、代码、命令行之外，请使用简体中文输出
</要求>

<风格>严谨，专业客观</风格>

输出模版（Markdown）：
## 节点信息

集群名称：...

当前版本：...

目标版本：...

托管节点组：
1 ...
2 ...

自管理节点：...

Karpenter节点：...

Fargate profile：
1 ...
2 ...

### 总览
1. 版本偏差风险：...

2. 关键升级约束：...

3. 升级策略：...

### 备注
本升级计划由AI助手基于提供的集群信息自动生成。由于自动生成内容可能存在不完整或不准确的情况，强烈建议您在执行正式升级之前，先在测试环境中完整验证本升级计划的可行性与安全性。在测试环境验证通过后，再根据实际情况调整并在生产环境实施升级操作。
"""
    
    user_prompt = f"""集群名称：{cluster_name}
当前EKS集群版本：{current_version}
目标EKS集群版本：{target_version}
托管节点组列表：{json.dumps(ng_list, indent=2, cls=DateTimeEncoder)}
自管理节点信息：{self_managed_nodes or "无"}
Karpenter节点信息：{karpenter_nodes or "无"}
Fargate profile列表：{json.dumps(fg_profile_list, indent=2, cls=DateTimeEncoder)}
版本偏差信息：{version_skew or "无"}
Kube-proxy版本信息：{kube_proxy or "无"}"""

    logger.info("正在生成整体升级概览。。。")
    
    return invoke_llm(
        client=client,
        model_id=model_id,
        system_prompt=system_prompt,
        user_prompt=user_prompt,
        debug=debug
    )

def process_control_plane_upgrade(client, docs: Dict[str, str], current_version: str, target_version: str, model_id: str, debug: bool = False) -> str:
    """处理控制平面升级
    
    Args:
        client: Bedrock客户端
        docs: 文档内容字典
        current_version: 当前版本
        target_version: 目标版本
        model_id: 模型ID
        
    Returns:
        str: 控制平面升级建议
    """
    system_prompt = """你是一位Kubernetes和Amazon EKS技术专家，请根据用户提供的当前及目标EKS版本信息，逐步思考并为用户定制一个EKS控制平面版本升级步骤。

<参考文档>
- 更新EKS Kubernetes版本：{update_kubernetes}
- Kubernetes控制平面升级后无法回退
</参考文档>

<要求>
- 如果当前及目标EKS版本跨多个次要版本，请提供连续升级步骤
- 请提供可操作的命令行或控制台操作步骤，命令行请提供参考AWS CLI命令
- 除了专业名称、代码、命令行之外，请使用简体中文输出
- 无需提供升级前检查的步骤
- 无需为每个版本提供重复的步骤，简略说明即可
- 无需提供数据面，插件等其它组件的升级步骤
</要求>

<风格>严谨，专业客观</风格>

输出模版（Markdown）：
### 升级步骤
...

### 注意事项
Kubernetes 控制面升级成功后无法回退；
...
""".format(update_kubernetes=docs.get("update_kubernetes", ""))
    
    user_prompt = f"""当前EKS集群版本：{current_version}
目标EKS集群版本：{target_version}"""
    
    logger.info("正在分析控制面升级情况。。。")

    return invoke_llm(
        client=client,
        model_id=model_id,
        system_prompt=system_prompt,
        user_prompt=user_prompt,
        debug=debug
    )

def process_addon_upgrade(
    client,
    eks_addon_list: List[Dict],
    opensource_addons: List[Dict],
    self_managed_core_addons: List[Dict],
    current_version: str,
    target_version: str,
    model_id: str, 
    debug: bool = False
) -> str:
    """处理插件升级
    
    Args:
        client: Bedrock客户端
        eks_addon_list: EKS插件列表
        opensource_addons: 开源插件列表
        self_managed_core_addons: 自管理核心插件列表
        current_version: 当前版本
        target_version: 目标版本
        model_id: 模型ID
        
    Returns:
        str: 插件升级建议
    """
    system_prompt = """你是一位Kubernetes和Amazon EKS技术专家，请根据用户提供的EKS addon、OpenSource addon、自管理核心addon列表及版本信息，建议用户更新addon版本并提供参考文档链接。

<要求>
- Kube-proxy需要与目标控制面版本一致
- 其它addons建议更新版本，但不是强制要求
- 针对EKS addons，提供兼容版本的AWS CLI检查命令
- 请谨慎思考，不要提供错误的建议版本
- 请提供参考资料的原文链接
- 除了专业名称、代码、命令行之外，请使用简体中文输出
</要求>

<风格>严谨，专业客观</风格>

输出模版（Markdown）：
建议您更新当前集群中的插件到更新的版本...
"""
    
    user_prompt = f"""EKS addon 信息：{json.dumps(eks_addon_list, indent=2, cls=DateTimeEncoder)}
Opensource addon 信息：{json.dumps(opensource_addons, indent=2, cls=DateTimeEncoder)}
自管理核心addon信息：{json.dumps(self_managed_core_addons, indent=2, cls=DateTimeEncoder)}
当前集群版本：{current_version}
目标集群版本：{target_version}"""

    logger.info("正在分析插件升级情况。。。")
    
    return invoke_llm(
        client=client,
        model_id=model_id,
        system_prompt=system_prompt,
        user_prompt=user_prompt,
        debug=debug
    )

def process_version_skew(
    client,
    current_version: str,
    target_version: str,
    self_managed_nodes: Optional[str],
    karpenter_nodes: Optional[str],
    ng_list: List[Dict],
    kube_proxy: Optional[str],
    model_id: str, 
    debug: bool = False
) -> str:
    """处理版本偏差
    
    Args:
        client: Bedrock客户端
        current_version: 当前版本
        target_version: 目标版本
        self_managed_nodes: 自管理节点信息
        karpenter_nodes: Karpenter节点信息
        ng_list: 节点组列表
        kube_proxy: Kube-proxy版本信息
        
    Returns:
        str: 版本偏差分析结果
    """
    system_prompt = """你是一位Kubernetes和Amazon EKS专家，请根据用户提供的节点和kube-proxy版本信息，逐步思考并提供节点和kube-proxy的升级建议。

<风格>严谨，专业客观</风格>

<参考文档>
Kubernetes 版本偏差策略：
- kubelet must not be newer than kube-apiserver.
- kubelet may be up to three minor versions older than kube-apiserver (kubelet < 1.25 may only be up to two minor versions older than kube-apiserver).
- kube-proxy must not be newer than kube-apiserver.
- kube-proxy may be up to three minor versions older than kube-apiserver (kube-proxy < 1.25 may only be up to two minor versions older than kube-apiserver).
</参考文档>

<要求>
- 检查当前节点和kube-proxy版本是否与目标Kubernetes版本兼容，若不兼容请建议升级到与当前控制面版本一致
- 请遵循Kubernetes版本偏差策略
- 请遵循EKS升级最佳实践
- 无需提供具体的升级方法
- 无需提供除了节点和kube-proxy之外的其它组件的建议
- 除了专业名称、代码、命令行之外，请使用简体中文输出
</要求>

输出模版（Markdown）：
### Version Skew

当前存在与目标版本不兼容的节点，建议在升级控制面之前更新工作节点到当前控制面版本。。。

当前 kube-proxy  与目标版本不兼容，建议在升级控制面之前更新 kube-proxy 到当前控制面版本。。。
"""
    
    user_prompt = f"""当前控制面版本：{current_version}
目标EKS版本：{target_version}
自管理节点版本信息：{self_managed_nodes or "无"}
Karpenter节点版本信息：{karpenter_nodes or "无"}
托管节点组版本信息：{json.dumps(ng_list, indent=2, cls=DateTimeEncoder)}
Kube-proxy版本信息：{kube_proxy or "无"}"""
    
    logger.info("正在分析版本偏差问题。。。")

    return invoke_llm(
        client=client,
        model_id=model_id,
        system_prompt=system_prompt,
        user_prompt=user_prompt,
        debug=debug
    )

def process_test_validation(
    client,
    model_id: str, 
    debug: bool = False
) -> str:
    """处理测试验证
    
    Args:
        client: Bedrock客户端
        
    Returns:
        str: 测试验证建议
    """
    system_prompt = """你是一位Kubernetes和Amazon EKS技术专家，请根据EKS升级最佳实践，为用户提供一个简略的EKS版本升级后的测试建议。

<要求>
- 请遵循EKS升级最佳实践
- EKS控制面版本无法回退
- 除了专业名称、代码、命令行之外，请使用简体中文输出
</要求>

<风格>严谨，专业客观</风格>

输出模版（Markdown）：
## 测试验证
"""
    
    user_prompt = "好的，我将生成一份针对EKS版本升级的测试建议。"

    logger.info("正在生成测试验证建议。。。")
    
    return invoke_llm(
        client=client,
        model_id=model_id,
        system_prompt=system_prompt,
        user_prompt=user_prompt,
        debug=debug
    )

def generate_upgrade_plan(
    cluster_name: str,
    region: str,
    target_version: str,
    profile: Optional[str] = None,
    connect_k8s: bool = False,
    bedrock_region: str = "us-west-2",
    model_id: str = "us.deepseek.r1-v1:0",
    role_arn: Optional[str] = None,
    debug: bool = False,
    cluster_info_file: Optional[str] = None
) -> str:
    """生成EKS升级计划
    
    Args:
        cluster_name: 集群名称
        region: AWS区域
        target_version: 目标EKS版本
        profile: AWS配置文件
        connect_k8s: 是否连接到Kubernetes API服务器
        bedrock_region: Bedrock服务区域
        model_id: 模型ID
        role_arn: 要扮演的IAM角色ARN
        debug: 是否启用调试模式
        cluster_info_file: 包含集群信息的JSON文件路径
        
    Returns:
        str: 升级计划
    """
    try:
        # 获取Bedrock客户端
        bedrock_client = get_bedrock_client(bedrock_region, role_arn)
        if not bedrock_client:
            return "无法连接到Bedrock服务，请检查配置。"
        
        # 初始化变量
        cluster_info = None
        current_version = None
        health_issues = []
        compatibility_issues = []
        nodegroups = []
        min_nodegroup_version = None
        fargate_profiles = []
        installed_addons = []
        addon_upgrade_info = []
        upgrade_recommended = False
        kube_proxy_version = None
        min_self_managed_version = None
        min_karpenter_version = None
        self_managed_count = 0
        karpenter_count = 0
        opensource_addons = []
        core_components = []
        deprecated_apis = []
        addon_comp_issues = []
        
        # 从文件读取集群信息或直接从集群获取
        if cluster_info_file:
            logger.info(f"从文件 {cluster_info_file} 读取集群信息...")
            try:
                with open(cluster_info_file, 'r', encoding='utf-8') as f:
                    cluster_data = json.load(f)
                
                # 提取集群信息
                cluster_name = cluster_data.get("cluster_name", cluster_name)
                region = cluster_data.get("region", region)
                current_version = cluster_data.get("current_version")
                target_version = cluster_data.get("target_version", target_version)
                health_issues = cluster_data.get("health_issues", [])
                compatibility_issues = cluster_data.get("compatibility_issues", [])
                nodegroups = cluster_data.get("nodegroups", [])
                min_nodegroup_version = cluster_data.get("min_nodegroup_version")
                fargate_profiles = cluster_data.get("fargate_profiles", [])
                installed_addons = cluster_data.get("installed_addons", [])
                addon_upgrade_info = cluster_data.get("addon_upgrade_info", [])
                upgrade_recommended = cluster_data.get("upgrade_recommended", False)
                kube_proxy_version = cluster_data.get("kube_proxy_version")
                min_self_managed_version = cluster_data.get("min_self_managed_version")
                min_karpenter_version = cluster_data.get("min_karpenter_version")
                self_managed_count = cluster_data.get("self_managed_count", 0)
                karpenter_count = cluster_data.get("karpenter_count", 0)
                opensource_addons = cluster_data.get("opensource_addons", [])
                core_components = cluster_data.get("core_components", [])
                
                # 如果kube_proxy_version为空，从core_components中检索
                if kube_proxy_version is None and core_components:
                    kube_proxy_version = next(
                        (component['version'] for component in core_components if component['name'] == 'kube-proxy'),
                        None
                    )
                deprecated_apis = cluster_data.get("deprecated_apis", [])
                addon_comp_issues = cluster_data.get("addon_compatibility_issues", [])
                
                if not current_version:
                    return "从文件中读取的集群信息不完整，缺少当前版本信息。"
                
                logger.info(f"成功从文件读取集群 {cluster_name} 的信息")
            except Exception as e:
                logger.error(f"读取集群信息文件时出错: {e}")
                return f"读取集群信息文件时出错: {e}"
        else:
            # 直接从集群获取信息
            logger.info(f"获取集群 {cluster_name} 信息...")
            cluster_info = get_cluster_info(cluster_name, region, profile)
            current_version = get_current_version(cluster_info)
            
            # 获取集群健康问题
            health_issues = get_health_issues(cluster_info)
            
            # 获取兼容性问题（中国区域不可用）
            if region not in ["cn-north-1", "cn-northwest-1"]:
                compatibility_issues = get_compatibility_issues(cluster_name, region, profile)
            
            # 获取节点组和最小节点组版本
            nodegroups, min_nodegroup_version = get_nodegroups(cluster_name, region, profile)
            
            # 获取Fargate配置文件
            fargate_profiles = get_fargate_profiles(cluster_name, region, profile)
            
            # 获取已安装的插件
            installed_addons = get_installed_addons(cluster_name, region, profile)
            
            # 获取插件升级信息
            addon_upgrade_info, upgrade_recommended = get_addon_upgrade_info(
                cluster_name, region, current_version, target_version, profile
            )
            
            # 获取kube-proxy插件版本
            kube_proxy_version = next(
                (addon['current_version'] for addon in addon_upgrade_info if addon['name'] == 'kube-proxy'), 
                None
            )
            
            # 如果kube_proxy_version为空，从core_components中检索
            if kube_proxy_version is None and core_components:
                kube_proxy_version = next(
                    (component['version'] for component in core_components if component['name'] == 'kube-proxy'),
                    None
                )
            
            # 连接到kube-apiserver获取更多信息
            if connect_k8s:
                api_client = connect_to_cluster(cluster_name, region, profile)
                
                if api_client:
                    # 获取自管理和Karpenter节点的最小版本
                    min_self_managed_version, min_karpenter_version, self_managed_count, karpenter_count = get_node_versions(api_client)
                    
                    # 获取开源插件信息
                    opensource_addons = get_opensource_addons(api_client, installed_addons)
                    
                    # 获取核心组件版本信息（如果不在EKS插件列表中）
                    core_components = get_core_components_version(api_client, installed_addons)
                else:
                    logger.warning("无法连接到kube-apiserver!")
            
            # 获取废弃的API版本
            if region not in ["cn-north-1", "cn-northwest-1"]:
                deprecated_apis = get_deprecated_api_versions(compatibility_issues)
            
            # 获取插件兼容性问题
            if region not in ["cn-north-1", "cn-northwest-1"]:
                addon_comp_issues = get_addon_compatibility_issues(compatibility_issues)
        
        # 验证版本
        is_valid, error_message, versions = validate_versions(current_version, target_version)
        if not is_valid:
            return f"版本验证失败: {error_message}"
        
        # 获取文档内容
        logger.info("获取参考文档...")
        docs = {}
        for key, url in DOCS.items():
            docs[key] = fetch_document(url)
        
        # 处理版本偏差
        version_skew_result = process_version_skew(
            bedrock_client,
            current_version,
            target_version,
            json.dumps({"version": min_self_managed_version, "count": self_managed_count}) if min_self_managed_version else None,
            json.dumps({"version": min_karpenter_version, "count": karpenter_count}) if min_karpenter_version else None,
            nodegroups,
            json.dumps({"version": kube_proxy_version}) if kube_proxy_version else None,
            model_id,
            debug
        )
        
        # 处理集群概要信息
        cluster_summary = process_cluster_summary(
            bedrock_client,
            cluster_name,
            current_version,
            target_version,
            nodegroups,
            json.dumps({"version": min_self_managed_version, "count": self_managed_count}) if min_self_managed_version else None,
            json.dumps({"version": min_karpenter_version, "count": karpenter_count}) if min_karpenter_version else None,
            fargate_profiles,
            version_skew_result,
            json.dumps({"version": kube_proxy_version}) if kube_proxy_version else None,
            model_id,
            debug
        )
        
        # 处理集群健康问题
        cluster_health = process_cluster_health_issues(bedrock_client, docs, health_issues, model_id, debug)
        
        # 处理插件兼容性问题
        addons_compatibility = process_addon_compatibility(bedrock_client, docs, addon_comp_issues, model_id, debug)
        
        # 处理版本变更
        cluster_version = process_version_changes(bedrock_client, docs, current_version, target_version, model_id, debug)
        
        # 处理废弃的API
        api_version = process_deprecated_apis(bedrock_client, docs, deprecated_apis, model_id, debug)
        
        # 处理控制平面升级
        control_plane = process_control_plane_upgrade(bedrock_client, docs, current_version, target_version, model_id, debug)
        
        # 处理插件升级
        addon_upgrade = process_addon_upgrade(
            bedrock_client,
            installed_addons,
            opensource_addons,
            core_components,
            current_version,
            target_version,
            model_id,
            debug
        )
        
        # 处理节点组升级
        nodegroup_upgrade = process_nodegroups(
            bedrock_client,
            docs,
            nodegroups,
            fargate_profiles,
            json.dumps({"version": min_self_managed_version, "count": self_managed_count}) if min_self_managed_version else None,
            json.dumps({"version": min_karpenter_version, "count": karpenter_count}) if min_karpenter_version else None,
            model_id,
            debug
        )
        
        # 处理测试验证
        test = process_test_validation(bedrock_client, model_id, debug)
        
        # 生成最终输出
        template = """# 集群信息

{ClusterSummary}

# 升级前检查

## 特定版本变更检查及建议

{ClusterVersion}

## Kubelet和kube-proxy版本对齐

{KubernetesSkew}

## 插件版本兼容性检查及建议

{Addons}

## Kubernetes API version与目标EKS版本的兼容性检查（若有）

{APIVersion}

## 集群健康检查及修复建议（若有）

{ClusterHealth}

# 控制面升级

{ControlPlane}

# 插件升级（更新建议）

{AddonUpgrade}

# 数据面升级

{NodegroupUpgrade}

# 测试验证

{Test}"""
        
        output = template.format(
            ClusterSummary=cluster_summary,
            ClusterVersion=cluster_version,
            KubernetesSkew=version_skew_result,
            Addons=addons_compatibility,
            APIVersion=api_version,
            ClusterHealth=cluster_health,
            ControlPlane=control_plane,
            AddonUpgrade=addon_upgrade,
            NodegroupUpgrade=nodegroup_upgrade,
            Test=test
        )
        
        return output
    except Exception as e:
        logger.error(f"生成升级计划时出错: {e}")
        import traceback
        return f"生成升级计划时出错: {e}\n{traceback.format_exc()}"

def main():
    """主函数"""
    parser = argparse.ArgumentParser(description="EKS集群升级规划工具")
    
    # 创建互斥组
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--cluster-info-file", help="包含集群信息的JSON文件路径")
    
    # 当使用--cluster-info-file时，这些参数是可选的
    parser.add_argument("cluster_name", nargs="?", help="EKS集群名称（与--cluster-info-file互斥）")
    parser.add_argument("target_version", nargs="?", help="目标EKS版本（与--cluster-info-file互斥）")
    parser.add_argument("--region", help="EKS集群所在的AWS区域")
    parser.add_argument("--profile", help="AWS配置文件")
    parser.add_argument("--connect-k8s", action="store_true", 
                    help="连接到Kubernetes API服务器以收集额外信息（与--cluster-info-file互斥）")
    parser.add_argument("--bedrock-region", default="us-west-2",
                    help="AWS Bedrock服务区域")
    parser.add_argument("--model-id", default="us.deepseek.r1-v1:0",
                    choices=["us.deepseek.r1-v1:0", "us.anthropic.claude-3-5-sonnet-20241022-v2:0"],
                    help="Bedrock模型ID")
    parser.add_argument("--role-arn", help="用于调用Bedrock API的IAM角色ARN")
    parser.add_argument("--output", "-o", help="输出文件路径")
    parser.add_argument("--debug", action="store_true", 
                    help="启用调试模式，打印请求和响应详情")
    args = parser.parse_args()
    
    # 验证参数组合
    if args.cluster_info_file:
        if args.cluster_name or args.target_version or args.connect_k8s:
            parser.error("当提供--cluster-info-file参数时，不能同时提供cluster_name、target_version和--connect-k8s参数")
    else:
        # 当不使用--cluster-info-file时，cluster_name和target_version是必需的
        if not args.cluster_name or not args.target_version:
            parser.error("当不提供--cluster-info-file参数时，必须提供cluster_name和target_version参数")
        
        # 如果没有提供区域，尝试从配置文件获取
        if not args.region:
            session = boto3.Session(profile_name=args.profile) if args.profile else boto3.Session()
            args.region = session.region_name

            if not args.region:
                print("错误: 需要提供AWS区域。请使用--region参数或在AWS配置中设置。")
                sys.exit(1)

    # 生成升级计划
    upgrade_plan = generate_upgrade_plan(
        cluster_name=args.cluster_name,
        region=args.region,
        target_version=args.target_version,
        profile=args.profile,
        connect_k8s=args.connect_k8s,
        bedrock_region=args.bedrock_region,
        model_id=args.model_id,
        role_arn=args.role_arn,
        debug=args.debug,
        cluster_info_file=args.cluster_info_file
    )
    
    # 输出结果
    if args.output:
        with open(args.output, 'w', encoding='utf-8') as f:
            f.write(upgrade_plan)
        print(f"升级计划已保存到: {args.output}")
    else:
        print(upgrade_plan)

if __name__ == "__main__":
    main()
