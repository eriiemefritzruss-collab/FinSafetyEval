"""统一模型调用客户端"""
import os
import time
import logging
from typing import Optional, List, Dict
from dataclasses import dataclass

logger = logging.getLogger(__name__)


@dataclass
class ModelResponse:
    content: str
    model: str
    usage: Optional[Dict] = None


class ModelClient:
    """统一的模型调用客户端，支持 aliyun/openai/deepseek/custom 四种 provider"""

    def __init__(self, provider: str, model: str, api_key: Optional[str] = None,
                 api_key_env: Optional[str] = None,
                 base_url: Optional[str] = None,
                 temperature: float = 0.7, max_tokens: int = 2048,
                 max_retries: int = 3, retry_delay: float = 2.0):
        """
        Args:
            provider: 模型提供商 (aliyun | openai | deepseek | custom)
            model: 模型名称
            api_key: 直接提供 API Key（优先级最高）
            api_key_env: 从该环境变量名读取 API Key（次优先）
            base_url: 自定义 API 端点（用于 custom provider 或私有部署）
            temperature: 生成温度
            max_tokens: 最大生成 token 数
            max_retries: 最大重试次数
            retry_delay: 重试基础延迟（秒，实际使用指数退避）
        """
        self.provider = provider.lower()
        self.model = model
        self.temperature = temperature
        self.max_tokens = max_tokens
        self.max_retries = max_retries
        self.retry_delay = retry_delay

        # API Key 优先级：直接传入 > api_key_env 指定的变量 > provider 默认变量
        resolved_key = self._resolve_api_key(api_key, api_key_env)
        self._init_client(resolved_key, base_url)

    def _resolve_api_key(self, api_key: Optional[str], api_key_env: Optional[str]) -> Optional[str]:
        """解析 API Key"""
        if api_key:
            return api_key
        # 从配置文件中指定的环境变量名读取
        if api_key_env:
            key = os.getenv(api_key_env)
            if key:
                return key
            logger.warning(f"环境变量 {api_key_env} 未设置")
        # 从 provider 默认的环境变量读取
        default_env_map = {
            "aliyun": "DASHSCOPE_API_KEY",
            "openai": "OPENAI_API_KEY",
            "deepseek": "DEEPSEEK_API_KEY",
        }
        default_env = default_env_map.get(self.provider)
        if default_env:
            return os.getenv(default_env)
        return None

    def _init_client(self, api_key: Optional[str], base_url: Optional[str]):
        """初始化对应 provider 的客户端"""
        try:
            from openai import OpenAI
        except ImportError:
            raise ImportError("请安装 openai: pip install openai>=1.0.0")

        provider_base_urls = {
            "aliyun": "https://dashscope.aliyuncs.com/compatible-mode/v1",
            "deepseek": "https://api.deepseek.com/v1",
        }

        import httpx
        timeout = httpx.Timeout(600.0)  # 强制设置 10 分钟长超时，防止深度思考模型被断开

        if self.provider in ("openai",):
            # openai 官方不需要 base_url
            self.client = OpenAI(api_key=api_key, timeout=timeout)
        elif self.provider in provider_base_urls:
            self.client = OpenAI(
                api_key=api_key,
                base_url=base_url or provider_base_urls[self.provider],
                timeout=timeout
            )
        elif self.provider == "custom":
            if not base_url:
                raise ValueError("custom provider 需要提供 base_url 参数")
            self.client = OpenAI(api_key=api_key or "EMPTY", base_url=base_url, timeout=timeout)
        else:
            raise ValueError(
                f"不支持的 provider: {self.provider}，"
                f"可用选项: aliyun, openai, deepseek, custom"
            )

        if not api_key and self.provider != "custom":
            logger.warning(f"Provider [{self.provider}] 未检测到 API Key，请检查环境变量配置")

    def chat(self, messages: List[Dict[str, str]], **kwargs) -> ModelResponse:
        """发送对话请求（带指数退避重试）"""
        last_error = None

        for attempt in range(self.max_retries):
            try:
                response = self.client.chat.completions.create(
                    model=self.model,
                    messages=messages,
                    temperature=kwargs.get("temperature", self.temperature),
                    max_tokens=kwargs.get("max_tokens", self.max_tokens)
                )

                content = response.choices[0].message.content or ""
                usage = None
                if response.usage:
                    usage = {
                        "prompt_tokens": response.usage.prompt_tokens,
                        "completion_tokens": response.usage.completion_tokens,
                        "total_tokens": response.usage.total_tokens
                    }

                return ModelResponse(content=content, model=self.model, usage=usage)

            except Exception as e:
                last_error = e
                wait_time = self.retry_delay * (2 ** attempt)  # 指数退避
                logger.warning(
                    f"[{self.provider}/{self.model}] 请求失败 "
                    f"(尝试 {attempt + 1}/{self.max_retries}): {e}，"
                    f"{wait_time:.1f}s 后重试"
                )
                if attempt < self.max_retries - 1:
                    time.sleep(wait_time)

        raise RuntimeError(
            f"[{self.provider}/{self.model}] 请求失败，已重试 {self.max_retries} 次"
        ) from last_error

    def simple_query(self, system_prompt: str, user_prompt: str, **kwargs) -> str:
        """简化接口：传入 system+user，返回文本内容"""
        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt}
        ]
        response = self.chat(messages, **kwargs)
        return response.content
