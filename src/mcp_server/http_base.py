"""
HTTP 基础客户端模块
httpx AsyncClient 单例（连接池、超时），封装 GET/POST/PUT/PATCH/DELETE
"""
import httpx
import json
from typing import Any, Optional
from .server_config import ERP_BASE_URL, HTTP_TIMEOUT, HTTP_MAX_CONNECTIONS


class ERPHttpClient:
    """ERP 后端 HTTP 客户端单例"""
    """
    HTTP 客户端采用单例模式，因为其维护昂贵的连接池资源，全局共享可复用 TCP 连接、
    显著提升性能并统一超时/并发配置；而 LLM 实例不适合单例，因为不同任务需要独立的模型、温度、API Key 等参数配置，
    且调用本身是无状态的，按需创建更灵活、更经济。
    """
    _instance: Optional['ERPHttpClient'] = None
    _client: Optional[httpx.AsyncClient] = None

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance

    @property
    def client(self) -> httpx.AsyncClient:
        if self._client is None or self._client.is_closed:
            self._client = httpx.AsyncClient(
                base_url=ERP_BASE_URL,
                timeout=httpx.Timeout(HTTP_TIMEOUT),
                limits=httpx.Limits(
                    max_connections=HTTP_MAX_CONNECTIONS,
                    max_keepalive_connections=10,
                ),
                headers={"Content-Type": "application/json"},
            )
        return self._client

    async def _request(self, method: str, path: str, **kwargs) -> dict:
        """统一发起请求，并把网络层异常转成结构化错误（不向上抛）。

        ERP 后端（:8081）未启动时，工具不应让整条 Agent 回答崩溃，
        而应返回错误信息，让模型能告知用户“ERP 服务未启动”。
        """
        try:
            response = await self.client.request(method, path, **kwargs)
        except httpx.ConnectError as e:
            return {
                "code": 503,
                "message": (
                    f"ERP 后端不可用（{ERP_BASE_URL} 未启动或无法连接）。"
                    f"请先启动 Java ERP 服务后重试。详情: {e}"
                ),
                "data": None,
            }
        except httpx.TimeoutException as e:
            return {
                "code": 504,
                "message": f"ERP 后端请求超时，请稍后重试。详情: {e}",
                "data": None,
            }
        except httpx.HTTPError as e:
            return {
                "code": 502,
                "message": f"ERP 后端请求失败: {e}",
                "data": None,
            }
        return self._handle_response(response)

    async def get(self, path: str, params: Optional[dict] = None) -> dict:
        """GET 请求"""
        # 过滤掉 None 值的参数
        if params:
            params = {k: v for k, v in params.items() if v is not None}
        return await self._request("GET", path, params=params)

    async def post(self, path: str, json_data: Optional[dict] = None, params: Optional[dict] = None) -> dict:
        """POST 请求"""
        if params:
            params = {k: v for k, v in params.items() if v is not None}
        return await self._request("POST", path, json=json_data, params=params)

    async def put(self, path: str, json_data: Optional[dict] = None, params: Optional[dict] = None) -> dict:
        """PUT 请求"""
        if params:
            params = {k: v for k, v in params.items() if v is not None}
        return await self._request("PUT", path, json=json_data, params=params)

    async def patch(self, path: str, params: Optional[dict] = None) -> dict:
        """PATCH 请求"""
        if params:
            params = {k: v for k, v in params.items() if v is not None}
        return await self._request("PATCH", path, params=params)

    async def delete(self, path: str) -> dict:
        """DELETE 请求"""
        return await self._request("DELETE", path)

    def _handle_response(self, response: httpx.Response) -> dict:
        """统一响应处理"""
        try:
            data = response.json()
            if response.status_code == 200:
                return data
            else:
                return {
                    "code": response.status_code,
                    "message": f"HTTP Error: {response.status_code}",
                    "data": data,
                }
        except json.JSONDecodeError:
            return {
                "code": response.status_code,
                "message": f"Response not JSON: {response.text[:500]}",
                "data": None,
            }

    async def close(self):
        """关闭客户端"""
        if self._client and not self._client.is_closed:
            await self._client.aclose()


# 全局单例
erp_client = ERPHttpClient()
