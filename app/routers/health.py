from fastapi import APIRouter

router = APIRouter(tags=["health"])


@router.get("/health")
async def health() -> dict:
    """服务健康检查。

    功能：
      供本地调试、部署探针或负载均衡器判断 API 进程是否存活。

    输入输出：
      无输入；返回 `{"status": "ok"}`。

    实现说明：
      阶段一只检查应用进程；生产可扩展为数据库、队列、模型服务和搜索 API 检查。
    """
    return {"status": "ok"}
