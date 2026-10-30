"""文件放在哪里。

- 随代码发布的资料（知识库、车程表、演示行程）在仓库根目录的 data/ 下；
- 运行时产生的数据（数据库、上传的凭证、各种缓存、生成的报告）放在 DATA_DIR：
  本地开发默认是 services/api/data/，上线时设成云端持久硬盘（Render 上是 /data），重新部署也不会丢。
"""

import os
from pathlib import Path

from dotenv import load_dotenv

API_DIR = Path(__file__).resolve().parents[1]     # services/api
load_dotenv(API_DIR / ".env")                      # 本地开发写在 .env 里的 DATA_DIR 也要生效（上线时用环境变量）
REPO_DIR = API_DIR.parents[1]                      # 仓库根目录
KB_DATA = REPO_DIR / "data"                        # 知识库、车程表、演示行程

_data_env = os.getenv("DATA_DIR")
DATA_DIR = Path(_data_env) if _data_env else API_DIR / "data"
UPLOADS = DATA_DIR / "uploads"
# 生成的 Word 报告：本地开发放在仓库的 output/ 方便打开；上线时放在数据目录里
OUTPUT = Path(os.getenv("OUTPUT_DIR") or (DATA_DIR / "output" if _data_env else REPO_DIR / "output"))
