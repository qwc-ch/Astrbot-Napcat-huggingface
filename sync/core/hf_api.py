"""Hugging Face Hub LFS 后端（替代 GitHub Release）

职责：
- 提供与 `GitHubReleaseAPI` 完全相同的接口，把大文件存到 HF 仓库（model/dataset），
  绕开 GitHub Release asset 2GB/文件的硬上限与仓库存储软限制。
- 文件布局：`{prefix}/{release_tag}/{asset_name}`，默认 `lfs/<tag>/<asset>`。

大文件上传遵循官方 git-lfs batch 协议（>5MB 文件 PUT resolve 端点会 404）：
1. POST `https://huggingface.co/{repo}.git/info/lfs/objects/batch`
   携带 `{"operation":"upload","transfers":["basic"],"objects":[{"oid":sha256,"size":N}]}`
2. 响应无 `actions` → 内容已存在（内容寻址去重，跳过上传）；
   有 `actions.upload.href` → PUT 签名 URL（S3 直传）后 `actions.verify` 校验。
- 存在性：HEAD `https://huggingface.co/api/{type}s/{repo}/resolve/{rev}/{path}`
- 下载：GET  resolve URL（重定向到 CDN 签名链接，私有仓库也免额外鉴权）
- 删除：DELETE 上述 API resolve URL

相关环境变量（config.py 读取）：
- LFS_BACKEND=hf 启用本后端
- HF_REPO：仓库 ID（owner/name）
- HF_TOKEN：写 Token（HF Spaces 通常已自动注入）
- HF_REVISION：分支/版本，默认 main
- HF_REPO_TYPE：model（默认）或 dataset
- HF_LFS_PREFIX：文件存放子目录，默认 lfs
"""

from __future__ import annotations

import hashlib
import json
import os
from typing import Optional, List, Dict, Any, Callable
from urllib.parse import quote

try:
    import httpx
except ImportError:
    httpx = None

from sync.utils.logging import log, err


class HuggingFaceHubAPI:
    """Hugging Face Hub LFS 客户端（与 GitHubReleaseAPI 接口兼容）"""

    def __init__(
        self,
        repo: str,
        token: str,
        revision: str = "main",
        repo_type: str = "model",
        prefix: str = "lfs",
        timeout: int = 300,
    ):
        """初始化 HF Hub 客户端

        Args:
            repo: 仓库 ID，格式：owner/name
            token: HF 写 Token（HuggingFace token，带 write 权限）
            revision: 分支/commit/tag，默认 main
            repo_type: model 或 dataset
            prefix: 文件存放前缀目录，默认 lfs
            timeout: 请求超时时间（秒）
        """
        if not httpx:
            raise RuntimeError("httpx not installed, required for LFS")

        repo_type = (repo_type or "model").lower()
        if repo_type not in ("model", "dataset"):
            raise RuntimeError(f"Unsupported HF repo type: {repo_type}")

        self.repo = repo
        self.token = token
        self.revision = revision
        self.repo_type = repo_type
        self.prefix = prefix.strip("/")
        self.timeout = timeout

        # API 根路径（上传/删除/存在性查询）
        self.base_url = f"https://huggingface.co/api/{repo_type}s/{repo}"
        # 下载根路径（公开仓库可免 Token 下载）
        dl_prefix = f"/{repo_type}s" if repo_type == "dataset" else ""
        self.download_base = f"https://huggingface.co{dl_prefix}/{repo}"
        # git-lfs batch 端点（内容寻址上传）
        self.lfs_batch_url = f"https://huggingface.co/{repo}.git/info/lfs/objects/batch"

    # -------- 内部工具 --------
    def _auth(self) -> Dict[str, str]:
        return {"Authorization": f"Bearer {self.token}"}

    def _full_path(self, tag: str, asset_name: str) -> str:
        """构造仓库内相对路径：{prefix}/{tag}/{asset_name}"""
        parts = [p for p in (self.prefix, tag, asset_name) if p]
        return "/".join(parts)

    def _resolve_url(self, tag: str, asset_name: str) -> str:
        """网页版 resolve URL（存在性探测/下载用；
        注意 /api/models/.../resolve 对 LFS 文件返回 404，不能用于探测）"""
        path = self._full_path(tag, asset_name)
        return f"{self.download_base}/resolve/{quote(self.revision)}/{quote(path)}"

    def _delete_url(self, tag: str, asset_name: str) -> str:
        """删除用 API URL（官方文档：
        DELETE https://huggingface.co/api/repos/{repo}/resolve/{rev}/{path}）"""
        path = self._full_path(tag, asset_name)
        return f"https://huggingface.co/api/repos/{self.repo}/resolve/{quote(self.revision)}/{quote(path)}"

    def _download_url(self, tag: str, asset_name: str) -> str:
        path = self._full_path(tag, asset_name)
        return f"{self.download_base}/resolve/{quote(self.revision)}/{quote(path)}"

    def _make_asset(self, name: str, size: int, tag: str) -> Dict[str, Any]:
        """构造与 GitHub asset 兼容的 asset 字典"""
        return {
            "name": name,
            "size": size,
            "tag": tag,
            "url": self._download_url(tag, name),
            "api_url": self._delete_url(tag, name),
        }

    def _tag_of(self, release: Dict[str, Any]) -> str:
        return str(release.get("tag") or release.get("name") or "")

    @staticmethod
    def _file_sha256(file_path: str) -> str:
        """计算文件 sha256（分块读取）"""
        hasher = hashlib.sha256()
        with open(file_path, "rb") as f:
            while chunk := f.read(1024 * 1024):
                hasher.update(chunk)
        return hasher.hexdigest()

    # -------- Release 概念桩（HF 无 release，映射到分支/目录） --------
    def get_release(self, tag: str) -> Optional[Dict[str, Any]]:
        """HF 没有 Release 概念：任何 tag 都视为可用（对应仓库内同名子目录）。

        仓库本身是否存在的校验在首次上传时自然失败（404/401）。
        """
        return {"tag": tag, "name": tag, "prefix": self._full_path(tag, "")}

    def get_or_create_release(self, tag: str) -> Dict[str, Any]:
        """与 GitHubReleaseAPI.get_or_create_release 兼容的桩。"""
        return self.get_release(tag)

    def create_release(self, tag: str, name: str, body: str = "") -> Dict[str, Any]:
        """与 GitHubReleaseAPI.create_release 兼容的桩：HF 无需创建 Release。"""
        return self.get_release(tag)

    # -------- Asset 查询 --------
    def list_assets(self, release: Dict[str, Any]) -> List[Dict[str, Any]]:
        """列出 release（tag 目录）下所有文件（tree API，递归）。"""
        tag = self._tag_of(release)
        prefix = self._full_path(tag, "")
        url = f"{self.base_url}/tree/{quote(self.revision)}"
        params = {"recursive": "true", "path": prefix}
        try:
            with httpx.Client(timeout=self.timeout, follow_redirects=True) as client:
                resp = client.get(url, params=params, headers=self._auth())
                if resp.status_code == 404:
                    return []
                resp.raise_for_status()
                data = resp.json()
        except (httpx.RequestError, httpx.HTTPStatusError) as e:
            err(f"Failed to list HF assets: {e}")
            return []

        assets = []
        if isinstance(data, list):
            for entry in data:
                if entry.get("type") != "file":
                    continue
                entry_path = entry.get("path", "")
                # tree 的 path 参数可能不生效，客户端按前缀过滤
                if not entry_path.startswith(prefix + "/"):
                    continue
                size = entry.get("size") or entry.get("lfs", {}).get("size") or 0
                assets.append(self._make_asset(os.path.basename(entry_path), size, tag))
        return assets

    def get_asset_by_name(self, release: Dict[str, Any], name: str) -> Optional[Dict[str, Any]]:
        """判断文件是否存在，返回 asset 字典或 None。

        先 HEAD resolve URL；HEAD 在部分网络/CDN 下返回 404 而 GET 正常，
        故 404 时回退为 GET + Range(0-0) 探测（与 huggingface_hub 一致）。
        """
        tag = self._tag_of(release)
        url = self._resolve_url(tag, name)
        for probe in ("head", "range"):
            try:
                with httpx.Client(timeout=self.timeout, follow_redirects=True) as client:
                    if probe == "head":
                        resp = client.head(url, headers=self._auth())
                    else:
                        headers = self._auth()
                        headers["Range"] = "bytes=0-0"
                        resp = client.get(url, headers=headers)
                if resp.status_code == 404:
                    continue
                if resp.status_code == 200 or resp.status_code == 206:
                    size = int(
                        resp.headers.get("x-linked-size")
                        or resp.headers.get("x-lfs-size")
                        or resp.headers.get("content-length")
                        or 0
                    )
                    return self._make_asset(name, size, tag)
                resp.raise_for_status()
            except httpx.HTTPStatusError as e:
                if e.response.status_code == 404:
                    continue
                err(f"Failed to check HF asset {name}: {e}")
                return None
            except httpx.RequestError as e:
                err(f"Failed to check HF asset {name}: {e}")
                return None
        return None

    # -------- 上传（git-lfs batch 协议） --------
    def upload_asset(
        self,
        release: Dict[str, Any],
        file_path: str,
        asset_name: str,
        progress_callback: Optional[Callable[[int, int], None]] = None,
    ) -> Dict[str, Any]:
        """按官方 git-lfs 协议上传大文件。

        流程：batch 申请签名 URL →（内容已存在则跳过）→ PUT 直传 → verify。
        内容寻址：同一内容的文件只会上传一次。
        """
        tag = self._tag_of(release)
        oid = self._file_sha256(file_path)
        file_size = os.path.getsize(file_path)

        # 若仓库中该路径已存在同名 asset，先删旧版（与 GitHub 后端行为一致）
        existing = self.get_asset_by_name(release, asset_name)
        if existing:
            log(f"Asset {asset_name} already exists, deleting old version")
            self.delete_asset(existing)

        # 1. batch 申请上传地址
        payload = {
            "operation": "upload",
            "transfers": ["basic"],
            "objects": [{"oid": oid, "size": file_size}],
            "ref": {"name": self.revision},
        }
        try:
            with httpx.Client(timeout=self.timeout) as client:
                resp = client.post(
                    self.lfs_batch_url,
                    headers=self._auth(),
                    json=payload,
                )
                if resp.status_code == 404:
                    raise RuntimeError(
                        f"HF 仓库不存在: {self.repo} (HTTP 404，请确认已创建仓库)"
                    )
                if resp.status_code == 401 or resp.status_code == 403:
                    raise RuntimeError(
                        f"HF Token 无权限访问仓库 {self.repo} (HTTP {resp.status_code})"
                    )
                resp.raise_for_status()
                batch = resp.json()
        except RuntimeError:
            raise
        except httpx.HTTPStatusError as e:
            raise RuntimeError(f"HF LFS batch 请求失败: {e}") from e
        except httpx.RequestError as e:
            raise RuntimeError(f"HF LFS batch 网络请求失败: {e}") from e

        objects = batch.get("objects") or []
        if not objects:
            raise RuntimeError(f"HF LFS batch 响应异常: {batch}")
        obj = objects[0]
        if obj.get("error"):
            raise RuntimeError(f"HF LFS batch 错误: {obj['error']}")

        actions = obj.get("actions") or {}
        upload_action = actions.get("upload")

        if not upload_action:
            # 内容已存在于远端（内容寻址去重），跳过上传
            log(f"Content of {asset_name} already on HF, skip upload")
        else:
            upload_url = upload_action.get("href")
            if not upload_url:
                raise RuntimeError(f"HF LFS batch 缺少上传地址: {upload_action}")
            log(f"Uploading {asset_name} ({file_size} bytes) to HF...")

            def _iter():
                sent = 0
                with open(file_path, "rb") as f:
                    while True:
                        chunk = f.read(1024 * 1024)
                        if not chunk:
                            break
                        sent += len(chunk)
                        if progress_callback:
                            progress_callback(sent, file_size)
                        yield chunk

            # 2. PUT 直传签名 URL（S3，无需额外鉴权头；必须带 Content-Length）
            try:
                with httpx.Client(timeout=self.timeout) as client:
                    put_resp = client.put(
                        upload_url,
                        headers={
                            "Content-Type": "application/octet-stream",
                            "Content-Length": str(file_size),
                        },
                        content=_iter(),
                    )
                    put_resp.raise_for_status()
            except (httpx.RequestError, httpx.HTTPStatusError) as e:
                raise RuntimeError(f"HF 上传内容失败: {e}") from e

            # 3. verify 确认
            verify_action = actions.get("verify")
            if verify_action:
                verify_url = verify_action.get("href")
                verify_headers = dict(verify_action.get("header") or {})
                verify_headers.setdefault("Authorization", self._auth()["Authorization"])
                try:
                    with httpx.Client(timeout=self.timeout) as client:
                        v_resp = client.post(verify_url, headers=verify_headers, json={"oid": oid, "size": file_size})
                        v_resp.raise_for_status()
                except (httpx.RequestError, httpx.HTTPStatusError) as e:
                    raise RuntimeError(f"HF 上传校验失败: {e}") from e

        # 4. 不再通过 REST commit API 写入仓库（避免与 git push 并发抢分支）。
        #    标准 git-lfs 指针条目由 convert_to_lfs 写入本地仓库，
        #    随周期 git 提交统一推送。

        log(f"✓ Uploaded asset: {asset_name}")
        return self._make_asset(asset_name, file_size, tag)

    def _commit_pointer_file(self, tag: str, asset_name: str, oid: str, size: int) -> None:
        """用 commit API 把 LFS 指针条目提交进仓库（NDJSON：header + lfsFile）。"""
        path_in_repo = self._full_path(tag, asset_name)
        try:
            with httpx.Client(timeout=self.timeout) as client:
                # 取当前 HEAD 作为父提交
                info_resp = client.get(self.base_url, headers=self._auth())
                info_resp.raise_for_status()
                parent = (info_resp.json() or {}).get("sha", None)

                lines = [
                    json.dumps({
                        "key": "header",
                        "value": {
                            "summary": f"chore(sync): upload lfs asset {asset_name}",
                            "description": "",
                            **({"parentCommit": parent} if parent else {}),
                        },
                    }),
                    json.dumps({
                        "key": "lfsFile",
                        "value": {
                            "path": path_in_repo,
                            "algo": "sha256",
                            "oid": oid,
                            "size": size,
                        },
                    }),
                ]
                headers = self._auth()
                headers["Content-Type"] = "application/x-ndjson"
                commit_resp = client.post(
                    f"{self.base_url}/commit/{quote(self.revision)}",
                    headers=headers,
                    content=("\n".join(lines) + "\n").encode(),
                )
                commit_resp.raise_for_status()
        except httpx.HTTPStatusError as e:
            raise RuntimeError(f"HF 提交指针文件失败 (HTTP {e.response.status_code}): {e.response.text[:300]}") from e
        except httpx.RequestError as e:
            raise RuntimeError(f"HF 提交指针文件网络失败: {e}") from e

    # -------- 下载 --------
    def download_asset(
        self,
        asset: Dict[str, Any],
        save_path: str,
        progress_callback: Optional[Callable[[int, int], None]] = None,
    ) -> bool:
        """下载 asset 到本地文件（流式，跟随 CDN 重定向）。"""
        url = asset["url"]
        size = asset.get("size", 0)

        parent = os.path.dirname(save_path)
        if parent:
            os.makedirs(parent, exist_ok=True)

        headers = self._auth()
        headers["Accept"] = "application/octet-stream"

        try:
            with httpx.Client(timeout=self.timeout, follow_redirects=True) as client:
                with client.stream("GET", url, headers=headers) as resp:
                    resp.raise_for_status()
                    downloaded = 0
                    with open(save_path, "wb") as f:
                        for chunk in resp.iter_bytes(8192):
                            if not chunk:
                                continue
                            f.write(chunk)
                            downloaded += len(chunk)
                            if progress_callback:
                                progress_callback(downloaded, size)
        except httpx.HTTPStatusError as e:
            if e.response.status_code == 404:
                err(f"HF asset not found: {asset.get('name')}")
                return False
            err(f"Failed to download HF asset {asset.get('name')}: {e}")
            return False
        except httpx.RequestError as e:
            err(f"Failed to download HF asset {asset.get('name')}: {e}")
            return False

        log(f"✓ Downloaded: {asset.get('name', '<unknown>')}")
        return True

    # -------- 删除 --------
    def delete_asset(self, asset: Dict[str, Any]) -> bool:
        """用 commit API 的 deletedFile 操作删除仓库内文件。

        注：HF 的 DELETE resolve 端点对 LFS 文件返回 404，官方客户端同样走 commit API。
        """
        tag = self._tag_of(asset)
        path_in_repo = self._full_path(tag, asset.get("name", ""))
        try:
            with httpx.Client(timeout=self.timeout) as client:
                # 取当前 HEAD 作为父提交
                info_resp = client.get(self.base_url, headers=self._auth())
                info_resp.raise_for_status()
                parent = (info_resp.json() or {}).get("sha", None)

                lines = [
                    json.dumps({
                        "key": "header",
                        "value": {
                            "summary": f"chore(sync): delete lfs asset {asset.get('name')}",
                            "description": "",
                            **({"parentCommit": parent} if parent else {}),
                        },
                    }),
                    json.dumps({
                        "key": "deletedFile",
                        "value": {"path": path_in_repo},
                    }),
                ]
                headers = self._auth()
                headers["Content-Type"] = "application/x-ndjson"
                resp = client.post(
                    f"{self.base_url}/commit/{quote(self.revision)}",
                    headers=headers,
                    content=("\n".join(lines) + "\n").encode(),
                )
                resp.raise_for_status()
            log(f"✓ Deleted asset: {asset.get('name')}")
            return True
        except Exception as e:
            err(f"Failed to delete asset {asset.get('name')}: {e}")
            return False