# 计算节点 Docker

本仓库独立维护计算节点镜像及运行库，不构建管理平台。默认 base 支持两种 CPU 架构；cpu、uvr 同样支持 amd64/arm64，CUDA、ROCm、OpenVINO 为 amd64，RKNN 为 arm64。

执行 make docker-context-check docker-test 验证；make docker-build COMPUTE_RUNTIME=base 构建；make docker-up 启动预构建镜像；make docker-down 停止并保留卷。

源码构建使用 compose.compute.yaml 加 compose.compute-build.yaml；预构建镜像只用前者。Compose 项目名保持 singverse-compute-node，命名卷保留。默认只监听本机；跨主机部署显式设置受限网络地址、凭据、TLS及媒体访问。Go 发起配对和任务，计算节点不直接访问业务数据库。

单独选择计算镜像版本：COMPUTE_VERSION；COMPUTE_RUNTIME 选择运行库。源构建产品版本只读取根 VERSION。

正式 GitHub Release 触发 singverse-compute-images 工作流，按实际架构构建并验证一次 OCI 产物，再将同一内容发布到 GHCR、Docker Hub 和阿里云。配置 Actions Variables：GHCR_NAMESPACE、DOCKERHUB_NAMESPACE、ACR_REGISTRY、ACR_NAMESPACE；配置 Secrets：DOCKERHUB_USERNAME、DOCKERHUB_TOKEN、ACR_USERNAME、ACR_PASSWORD。GHCR 使用 GITHUB_TOKEN。标签必须等于 v + 根 VERSION。固定标签不同内容会拒绝覆盖，所有固定版本成功后更新 latest。运行库镜像不内置模型权重。

正式发布在 GitHub Release 保存每个运行库和架构的已验收 OCI 归档。逻辑名为 `${runtime}-${arch}.oci.tar`；实际使用 1 GiB 内容寻址分片，最后发布 `.oci.tar.json` 清单，包含整包及每片的长度和 SHA-256，适配 GitHub 单资产大小限制。重试先恢复同一归档并重新执行版本、revision、运行库和 OCI 校验，再发布到镜像仓库。中断上传的分片可复用，固定内容从不覆盖；查询失败不会被当成缓存缺失。脚本入口：`python3 infra/docker/release-artifact.py restore|save --name base-amd64.oci.tar --archive /tmp/image.tar`，需要 GH_TOKEN、GITHUB_REPOSITORY 与 RELEASE_TAG，标签必须匹配根 VERSION。
