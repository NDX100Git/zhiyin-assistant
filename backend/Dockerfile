# 智印助手后端服务镜像（FastAPI + uvicorn）
#
# 构建与启动统一走编排：在 backend/ 目录执行 docker compose up -d --build
# 单独构建：docker compose build backend
#
# 为什么基础镜像选 Python 3.12：
#   requirements.txt 是 pip freeze 锁定的全量依赖，其中 numpy==2.5.4 声明
#   requires_python>=3.12，3.11 及以下直接装不上，这就是整套依赖对 Python 的下界。
#   3.12 是满足该下界、且所有二进制依赖（pydantic-core、pymupdf、cryptography、cffi、
#   opencv-python-headless、argon2-cffi-bindings、jiter、lxml）都有 cp312 或 abi3
#   预编译 wheel 的版本，镜像里不需要装编译器；3.13/3.14 虽然也能装，但部分依赖只剩
#   abi3 wheel、个别包要靠源码编译，国内构建更容易失败，因此取 3.12。
FROM python:3.12-slim

# pip 源可换：默认官方源。国内直连 PyPI 经常断流，构建时用 --build-arg 指到镜像站，
# 例如 PIP_INDEX_URL=https://pypi.tuna.tsinghua.edu.cn/simple
# docker compose build 会读 backend/.env 里的同名变量自动传入，见 docker-compose.yml。
ARG PIP_INDEX_URL=https://pypi.org/simple
# 使用 http 私有源时才需要填（https 镜像站留空即可）
ARG PIP_TRUSTED_HOST=

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

# 先只拷依赖清单：依赖没变时复用这一层缓存，改代码不必重装依赖
COPY requirements.txt ./

# 依赖全部有预编译 wheel，加 --no-cache-dir 让镜像不残留下载缓存
RUN pip install --no-cache-dir \
        ${PIP_TRUSTED_HOST:+--trusted-host "${PIP_TRUSTED_HOST}"} \
        --index-url "${PIP_INDEX_URL}" \
        -r requirements.txt

# Word / Excel / PPT 转 PDF 用的 LibreOffice 装进同一个镜像。
# 不挂 docker.sock、不装 docker CLI：挂 socket 等于把宿主机 root 交给一个会解析
# 用户上传文件的 Web 服务，代价远大于省下的镜像体积。装进来就没有这些风险，
# 也不依赖另一个容器的存活状态。只装 writer、calc 与 impress，不装整套套件。
ARG APT_MIRROR=deb.debian.org
RUN if [ "$APT_MIRROR" != "deb.debian.org" ]; then \
        sed -i "s|deb.debian.org|${APT_MIRROR}|g" /etc/apt/sources.list.d/debian.sources; \
    fi \
    && apt-get update \
    && apt-get install -y --no-install-recommends \
        libreoffice-writer \
        libreoffice-calc \
        libreoffice-impress \
        fonts-noto-cjk \
        fontconfig \
    && fc-cache -f \
    && rm -rf /var/lib/apt/lists/*

# 容器内直接用镜像里的 soffice；宿主机模式改用 .env 里的 SOFFICE_CONTAINER
ENV SOFFICE_PATH=/usr/bin/soffice

# 应用代码：只拷运行期需要的 app/ 与 config/。
# config/ 下的 JSON 是价格、格式规则、功能开关的唯一来源，缺了业务接口会报错。
COPY app ./app
COPY config ./config

# 降权运行：这个容器专职解析学生上传的 docx/xlsx/pptx，还装着 LibreOffice，
# 以 root 跑等于把「格式解析漏洞」和「整台宿主机」绑在一起。
# 固定 uid/gid 10001，方便挂载卷与 docker exec 共用同一个身份。
# 运行时临时目录也在这里建好并交给 app 用户。
RUN groupadd --system --gid 10001 app \
    && useradd --system --uid 10001 --gid app --home-dir /app --shell /usr/sbin/nologin app \
    && mkdir -p /app/runtime/tmp \
    && chown -R app:app /app

USER app

EXPOSE 8000

# 监听 0.0.0.0，容器外才能通过端口映射访问
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
