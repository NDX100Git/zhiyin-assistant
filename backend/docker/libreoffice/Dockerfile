# LibreOffice headless 转换容器（阶段 5-A 使用）
# 用途：把 .doc/.docx 转成 PDF。容器常驻，后端调用：
#   docker exec zhiyin-libreoffice soffice --headless --convert-to pdf --outdir /workspace/runtime <file>
FROM debian:bookworm-slim

ENV DEBIAN_FRONTEND=noninteractive

# 镜像源可换：国内网络直连官方源经常断，构建时用 APT_MIRROR 指到镜像站即可
ARG APT_MIRROR=deb.debian.org
RUN if [ "$APT_MIRROR" != "deb.debian.org" ]; then \
        sed -i "s|deb.debian.org|${APT_MIRROR}|g" /etc/apt/sources.list.d/debian.sources; \
    fi \
    && apt-get update \
    && apt-get install -y --no-install-recommends \
        libreoffice-writer \
        libreoffice-calc \
        fonts-noto-cjk \
        fontconfig \
        ca-certificates \
    && fc-cache -fv \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /workspace

CMD ["sleep", "infinity"]
