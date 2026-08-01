"""
依赖医生 —— 检测、下载、安装、验证
====================================================================
用户原话：「可以自动配置需要的工具与内容」。

它做四件事：
  ① 体检：每一项到底装没装、能不能真的用（不是查版本号，是真的 import 一次）
  ② 下载：模型带断点续传和多源择优
  ③ 安装：Python 包自动切国内镜像
  ④ **装完实调验证**：pip 说成功不算数，import 得进来才算

第 ④ 条是有意强调的：只看 pip 退出码会漏掉"装上了但和现有版本冲突、
一 import 就炸"这类问题，而那种错要等到用户真的去分析文件时才暴露。
"""

from __future__ import annotations

import asyncio
import importlib
import logging
import shutil
import subprocess
import sys
from collections.abc import Callable
from pathlib import Path
from typing import Any

import httpx

from .downloader import DownloadError, Progress, download_file, verify_onnx
from .registry import (
    BY_ID,
    IMPORT_PROBES,
    PIP_MIRRORS,
    PIP_PACKAGES,
    REGISTRY,
    DepKind,
    Dependency,
)

log = logging.getLogger("synorive.doctor")

StatusCb = Callable[[dict[str, Any]], None]


class Doctor:
    def __init__(self, model_dir: Path, on_status: StatusCb | None = None) -> None:
        self.model_dir = model_dir
        self.on_status = on_status
        self._installing: set[str] = set()

    # ── 体检 ────────────────────────────────────────────────

    def check(self, dep: Dependency, *, deep: bool = True) -> dict[str, Any]:
        """
        单项体检（给界面用）。正在装的项直接回 "installing"，
        免得进度条和体检结果打架。

        deep=False 是**快速档**：判断 Python 包时只查模块找不找得到
        （importlib.util.find_spec），不真的 import。

        为什么要分档：真 import 一遍 fitz / trafilatura / rapidocr 要 200~400ms 一个，
        引擎启动时体检 9 个依赖就要多花半秒多，直接顶破 A1「冷启动 ≤2s」。
        启动和 /health 用快档，用户打开依赖面板和装完自检用深档。

        ⚠️ 装完之后的自检**不能**走这个函数 —— 那时候 id 还在 _installing 里，
        会被这个短路拦下，返回 "installing" 而不是真实状态，
        结果就是"文件明明下好了，安装函数却报失败"。踩过一次，走 _check_raw。
        """
        if dep.id in self._installing:
            return {**self._describe(dep), "state": "installing"}
        return self._check_raw(dep, deep=deep)

    def _describe(self, dep: Dependency) -> dict[str, Any]:
        return {
            "id": dep.id,
            "kind": dep.kind.value,
            "name": dep.name,
            "purpose": dep.purpose,
            "requiredBy": list(dep.required_by),
            "degradesTo": dep.degrades_to,
            "optional": dep.optional,
        }

    def _check_raw(self, dep: Dependency, *, deep: bool = True) -> dict[str, Any]:
        """真体检，不管在不在装。装完的自检用这个（必须 deep=True）。"""
        base = self._describe(dep)

        if dep.kind is DepKind.MODEL:
            return {**base, **self._check_model(dep)}
        if dep.kind is DepKind.BINARY:
            return {**base, **self._check_binary(dep)}
        if dep.kind is DepKind.PY_PACKAGE:
            return {**base, **(self._check_package(dep) if deep else self._check_package_fast(dep))}
        return {**base, "state": "missing"}

    def check_all(self, *, deep: bool = True) -> list[dict[str, Any]]:
        return [self.check(d, deep=deep) for d in REGISTRY]

    def _check_model(self, dep: Dependency) -> dict[str, Any]:
        d = self.model_dir / dep.subdir
        missing: list[str] = []
        broken: list[str] = []
        total = 0

        for f in dep.files:
            p = d / f.filename
            if not p.exists() or p.stat().st_size == 0:
                missing.append(f.filename)
                continue
            total += p.stat().st_size
            if p.suffix == ".onnx":
                ok, why = verify_onnx(p)
                if not ok:
                    broken.append(f"{f.filename}（{why}）")

        if broken:
            return {"state": "failed", "error": "文件损坏：" + "；".join(broken), "sizeBytes": total}
        if missing:
            return {
                "state": "missing",
                "error": None,
                "missingFiles": missing,
                "sizeBytes": total,
            }
        return {"state": "ok", "error": None, "sizeBytes": total, "path": str(d)}

    def _check_binary(self, dep: Dependency) -> dict[str, Any]:
        exe = shutil.which(dep.id)
        if not exe:
            # 常见的自定义安装位置也找一下 —— 这台机器 D:\APPS 下装了不少东西
            for guess in (
                Path(r"D:\Files\VideoEditing\ffmpeg\bin") / f"{dep.id}.exe",
                Path(r"D:\APPS") / dep.id / "bin" / f"{dep.id}.exe",
            ):
                if guess.exists():
                    exe = str(guess)
                    break
        if not exe:
            return {"state": "missing", "error": None}

        version = None
        try:
            r = subprocess.run(
                [exe, "-version"], capture_output=True, text=True, timeout=8, errors="replace"
            )
            first = (r.stdout or r.stderr).splitlines()
            version = first[0][:80] if first else None
        except Exception:  # noqa: BLE001
            pass
        return {"state": "ok", "error": None, "path": exe, "installedVersion": version}

    def _check_package_fast(self, dep: Dependency) -> dict[str, Any]:
        """
        快速档：只查模块找不找得到，不执行它。

        find_spec 只翻文件系统和 sys.path，微秒级；
        真 import 会执行模块顶层代码，PyMuPDF/trafilatura 那种要几百毫秒。

        代价：查不出"装了但一 import 就炸"的情况。所以这一档只用于
        启动和 /health 这种要快的地方，用户打开依赖面板时会走深档复查。
        """
        import importlib.util

        probes = IMPORT_PROBES.get(dep.id, ())
        if not probes:
            return {"state": "missing", "error": None}

        found = 0
        for mod in probes:
            try:
                if importlib.util.find_spec(mod) is not None:
                    found += 1
            except (ImportError, ValueError, ModuleNotFoundError):
                pass

        if found == 0:
            return {"state": "missing", "error": None}
        if found < len(probes):
            return {"state": "failed", "error": f"只找到 {found}/{len(probes)} 个模块，装了一半"}

        if dep.id == "gpu-directml":
            # DirectML 必须看执行器列表，find_spec 分辨不出 CPU 版和 DML 版
            return self._check_package(dep)

        return {"state": "ok", "error": None, "installedVersion": None}

    def _check_package(self, dep: Dependency) -> dict[str, Any]:
        """深度档：真的 import 一次。慢但准，能查出"装了但用不了"。"""
        probes = IMPORT_PROBES.get(dep.id, ())
        if not probes:
            return {"state": "missing", "error": "没有登记 import 探针"}

        failed: list[str] = []
        versions: list[str] = []
        for mod in probes:
            try:
                m = importlib.import_module(mod)
                versions.append(f"{mod}={getattr(m, '__version__', 'ok')}")
            except Exception as e:  # noqa: BLE001
                failed.append(f"{mod}({type(e).__name__})")

        if failed and len(failed) == len(probes):
            return {"state": "missing", "error": None}
        if failed:
            # 装了一半：比完全没装更危险，因为代码路径会走进去然后炸
            return {"state": "failed", "error": "部分模块导入失败：" + "、".join(failed)}

        # DirectML 特判：import onnxruntime 成功不等于装了 DirectML 版
        # （CPU 版和 DirectML 版共用 onnxruntime 这个模块名，互斥）。
        # 判据得看执行器列表里有没有 Dml。
        #
        # ⚠️ 没有 Dml 时要报 "missing" 不是 "failed"：
        #    这是个可选加速项，从没装过就该显示"未安装"。
        #    报 failed 会让用户以为哪儿坏了，白紧张一场。
        if dep.id == "gpu-directml":
            try:
                import onnxruntime as ort

                if "DmlExecutionProvider" not in ort.get_available_providers():
                    return {
                        "state": "missing",
                        "error": None,
                        "note": f"当前是 CPU 版 onnxruntime {ort.__version__}，装了才能用核显",
                    }
            except Exception as e:  # noqa: BLE001
                return {"state": "failed", "error": str(e)}

        return {"state": "ok", "error": None, "installedVersion": "、".join(versions)}

    # ── 安装 ────────────────────────────────────────────────

    async def install(self, dep_id: str) -> dict[str, Any]:
        dep = BY_ID.get(dep_id)
        if dep is None:
            return {"ok": False, "error": f"没有登记的依赖：{dep_id}"}
        if dep_id in self._installing:
            return {"ok": False, "error": "正在安装中"}

        self._installing.add(dep_id)
        self._emit(dep_id, "installing", 0.0)
        try:
            if dep.kind is DepKind.MODEL:
                return await self._install_model(dep)
            if dep.kind is DepKind.PY_PACKAGE:
                return await self._install_package(dep)
            return {
                "ok": False,
                "error": f"{dep.name} 需要你手动安装 —— 自动装外部程序属于要先问你的操作",
            }
        finally:
            self._installing.discard(dep_id)

    async def _install_model(self, dep: Dependency) -> dict[str, Any]:
        target = self.model_dir / dep.subdir
        target.mkdir(parents=True, exist_ok=True)

        done_bytes = 0
        # 进度按**字节**加权，不按文件个数。
        # 这个模型是 24MB 的 model.onnx + 4 个几百字节的 json，
        # 按个数算的话 config.json 下完进度就跳 20%，进度条乱蹦。
        # 大小未知时先按已知的估，边下边修正。
        known_total = sum(f.size_bytes or 0 for f in dep.files)
        est_total = known_total or 25_000_000

        async with httpx.AsyncClient(
            timeout=httpx.Timeout(30.0, read=120.0),
            follow_redirects=True,
            headers={"User-Agent": "Synorive/0.1"},
        ) as client:
            for f in dep.files:

                def progress(p: Progress, base_done: int = done_bytes) -> None:
                    total = max(est_total, base_done + p.total)
                    overall = (base_done + p.downloaded) / total if total else 0.0
                    self._emit(
                        dep.id,
                        "downloading",
                        min(overall, 0.999),
                        downloaded=base_done + p.downloaded,
                        totalBytes=total,
                        speed=p.speed_bps,
                        detail=f"{p.filename}（源 {p.source}）",
                    )

                try:
                    p = await download_file(
                        client,
                        f.urls,
                        target / f.filename,
                        expected_sha256=f.sha256,
                        on_progress=progress,
                    )
                    done_bytes += p.stat().st_size
                except DownloadError as e:
                    self._emit(dep.id, "failed", 0.0, error=str(e))
                    return {"ok": False, "error": str(e)}

        # 装完立刻复查，不信"下载函数没报错"。
        # 必须走 _check_raw：这时 dep.id 还在 _installing 里，走 check() 会被短路。
        status = self._check_raw(dep)
        if status["state"] != "ok":
            self._emit(dep.id, "failed", 0.0, error=status.get("error"))
            return {"ok": False, "error": status.get("error") or "下载完了但复查不通过"}

        self._emit(dep.id, "ok", 1.0)
        return {"ok": True, "bytes": done_bytes, "path": str(target)}

    async def _install_package(self, dep: Dependency) -> dict[str, Any]:
        pkgs = PIP_PACKAGES.get(dep.id, ())
        if not pkgs:
            return {"ok": False, "error": f"{dep.id} 没登记 pip 包名"}

        # DirectML 和 CPU 版 onnxruntime 互斥，装之前得先卸掉
        pre: list[list[str]] = []
        if dep.id == "gpu-directml":
            pre.append([sys.executable, "-m", "pip", "uninstall", "-y", "onnxruntime"])

        last_err = ""
        for mirror in PIP_MIRRORS:
            for cmd in pre:
                await _run(cmd)

            cmd = [
                sys.executable,
                "-m",
                "pip",
                "install",
                "--index-url",
                mirror,
                "--disable-pip-version-check",
                *pkgs,
            ]
            self._emit(dep.id, "installing", 0.3, detail=f"从 {mirror.split('/')[2]} 安装")
            rc, out = await _run(cmd)
            if rc == 0:
                break
            last_err = out[-600:]
            log.warning("镜像 %s 安装失败，换下一个", mirror)
        else:
            self._emit(dep.id, "failed", 0.0, error=last_err)
            return {"ok": False, "error": f"三个镜像都装不上：{last_err}"}

        # ⚠️ pip 返回 0 不等于能用。必须真的 import 一次。
        # 同样走 _check_raw，理由见 check() 的注释。
        importlib.invalidate_caches()
        status = self._check_raw(dep)
        if status["state"] != "ok":
            self._emit(dep.id, "failed", 0.0, error=status.get("error"))
            return {
                "ok": False,
                "error": f"pip 报成功但导入失败：{status.get('error')}",
            }

        self._emit(dep.id, "ok", 1.0)
        return {"ok": True, "installedVersion": status.get("installedVersion")}

    # ── 事件 ────────────────────────────────────────────────

    def _emit(self, dep_id: str, state: str, progress: float, **extra: Any) -> None:
        if not self.on_status:
            return
        self.on_status(
            {
                "id": dep_id,
                "state": state,
                "progress": round(progress, 4),
                **{k: v for k, v in extra.items() if v is not None},
            }
        )


async def _run(cmd: list[str]) -> tuple[int, str]:
    proc = await asyncio.create_subprocess_exec(
        *cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT
    )
    out, _ = await proc.communicate()
    return proc.returncode or 0, out.decode("utf-8", errors="replace")
