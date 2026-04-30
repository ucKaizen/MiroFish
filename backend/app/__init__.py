"""
MiroFish Backend - Flask应用工厂
"""

import os
import warnings

# 抑制 multiprocessing resource_tracker 的警告（来自第三方库如 transformers）
# 需要在所有其他导入之前设置
warnings.filterwarnings("ignore", message=".*resource_tracker.*")

from flask import Flask, request, send_from_directory
from flask_cors import CORS

from .config import Config
from .utils.logger import setup_logger, get_logger

# Path to the pre-built Vue/Vite bundle (populated by the Docker build stage).
# In local dev, this directory may be empty; Flask simply won't serve it.
FRONTEND_DIST_DIR = os.path.abspath(
    os.path.join(os.path.dirname(__file__), '..', '..', 'frontend', 'dist')
)


def create_app(config_class=Config):
    """Flask应用工厂函数"""
    app = Flask(__name__)
    app.config.from_object(config_class)
    
    # 设置JSON编码：确保中文直接显示（而不是 \uXXXX 格式）
    # Flask >= 2.3 使用 app.json.ensure_ascii，旧版本使用 JSON_AS_ASCII 配置
    if hasattr(app, 'json') and hasattr(app.json, 'ensure_ascii'):
        app.json.ensure_ascii = False
    
    # 设置日志
    logger = setup_logger('mirofish')
    
    # 只在 reloader 子进程中打印启动信息（避免 debug 模式下打印两次）
    is_reloader_process = os.environ.get('WERKZEUG_RUN_MAIN') == 'true'
    debug_mode = app.config.get('DEBUG', False)
    should_log_startup = not debug_mode or is_reloader_process
    
    if should_log_startup:
        logger.info("=" * 50)
        logger.info("MiroFish Backend 启动中...")
        logger.info("=" * 50)
    
    # 启用CORS
    CORS(app, resources={r"/api/*": {"origins": "*"}})
    
    # 注册模拟进程清理函数（确保服务器关闭时终止所有模拟进程）
    from .services.simulation_runner import SimulationRunner
    SimulationRunner.register_cleanup()
    if should_log_startup:
        logger.info("已注册模拟进程清理函数")
    
    # 请求日志中间件
    @app.before_request
    def log_request():
        logger = get_logger('mirofish.request')
        logger.debug(f"请求: {request.method} {request.path}")
        if request.content_type and 'json' in request.content_type:
            logger.debug(f"请求体: {request.get_json(silent=True)}")
    
    @app.after_request
    def log_response(response):
        logger = get_logger('mirofish.request')
        logger.debug(f"响应: {response.status_code}")
        return response
    
    # 注册蓝图
    from .api import graph_bp, simulation_bp, report_bp
    app.register_blueprint(graph_bp, url_prefix='/api/graph')
    app.register_blueprint(simulation_bp, url_prefix='/api/simulation')
    app.register_blueprint(report_bp, url_prefix='/api/report')

    # v2 — schema-direct, no-fork pipeline. Mounts under /api/v2/*.
    from .v2.api import v2_bp
    app.register_blueprint(v2_bp, url_prefix='/api/v2')
    
    # 健康检查
    @app.route('/health')
    def health():
        return {'status': 'ok', 'service': 'MiroFish Backend'}

    # Version / commit info — proves which build is actually running.
    # Reads RAILWAY_GIT_* env vars that Railway injects automatically at
    # build/deploy time. Falls back to a local `git rev-parse` for dev.
    @app.route('/version')
    def version():
        import subprocess
        sha = os.environ.get("RAILWAY_GIT_COMMIT_SHA") or ""
        if not sha:
            try:
                sha = subprocess.check_output(
                    ["git", "rev-parse", "HEAD"],
                    stderr=subprocess.DEVNULL,
                    cwd=os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")),
                ).decode().strip()
            except Exception:
                sha = "unknown"
        return {
            "commit": sha[:12] if sha else "unknown",
            "commit_full": sha or "unknown",
            "branch": os.environ.get("RAILWAY_GIT_BRANCH", "local"),
            "message": (os.environ.get("RAILWAY_GIT_COMMIT_MESSAGE", "") or "")[:200],
            "deployment_id": os.environ.get("RAILWAY_DEPLOYMENT_ID", "local"),
            "service": os.environ.get("RAILWAY_SERVICE_NAME", "MiroFish"),
        }

    # Serve the built Vue SPA. API routes above take precedence thanks to
    # Flask's specificity-based matching, so /api/* and /health still win.
    if os.path.isdir(FRONTEND_DIST_DIR):
        @app.route('/', defaults={'path': ''})
        @app.route('/<path:path>')
        def serve_spa(path):
            candidate = os.path.join(FRONTEND_DIST_DIR, path)
            if path and os.path.isfile(candidate):
                return send_from_directory(FRONTEND_DIST_DIR, path)
            return send_from_directory(FRONTEND_DIST_DIR, 'index.html')

    if should_log_startup:
        logger.info("MiroFish Backend 启动完成")

    return app

