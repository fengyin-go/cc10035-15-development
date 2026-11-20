"""业务路由汇总：所有模块共用一套资源路由，由领域服务完成分派。"""
from __future__ import annotations

from app.routers import evidence, resource

# evidence 必须先注册：/api/{module}/{entry_id} 会吞掉 /api/evidence/profiles
# 这类固定路径，先注册让固定路径优先命中。
ROUTERS = [evidence, resource]
