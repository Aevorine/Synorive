"""依赖清单体检：清单里登记的和实际装的必须对得上。"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from synorive.doctor.service import Doctor  # noqa: E402

MODEL_DIR = Path(__file__).resolve().parent.parent.parent / "data" / "models"

MARK = {"ok": "✓", "missing": "·", "failed": "✗", "installing": "…"}


def main() -> int:
    doc = Doctor(MODEL_DIR)
    rows = doc.check_all(deep=True)

    print(f"{'':<3}{'类别':<12}{'必需':<6}{'名称':<44}状态")
    print("-" * 92)
    bad = 0
    for s in rows:
        m = MARK.get(s["state"], "?")
        need = "必需" if not s["optional"] else "可选"
        print(f"{m:<3}{s['kind']:<12}{need:<6}{s['name'][:42]:<44}{s['state']}")
        if s.get("note"):
            print(f"{'':<21}{s['note']}")
        if s.get("error"):
            print(f"{'':<21}错误：{s['error']}")
        if s["state"] != "ok" and not s["optional"]:
            bad += 1

    print("-" * 92)
    ok = sum(1 for s in rows if s["state"] == "ok")
    print(f"共 {len(rows)} 项：可用 {ok}，未装 {sum(1 for s in rows if s['state'] == 'missing')}，"
          f"异常 {sum(1 for s in rows if s['state'] == 'failed')}")

    # 清单和实现脱节是很难发现的一类错：界面提示装一个装了也没用的包，
    # 或者真正需要的包压根不在清单里。这里做一道交叉核对。
    print()
    print("交叉核对：清单里登记的探针能不能真的 import")
    from synorive.doctor.registry import IMPORT_PROBES

    import importlib

    problems = 0
    for dep_id, probes in IMPORT_PROBES.items():
        for mod in probes:
            try:
                importlib.import_module(mod)
                state = "可导入"
            except ImportError:
                state = "未安装"
            except Exception as e:  # noqa: BLE001
                state = f"导入报错 {type(e).__name__}"
                problems += 1
            print(f"  {dep_id:16} {mod:24} {state}")

    if bad:
        print(f"\n✗ 有 {bad} 个**必需**依赖不可用")
        return 1
    if problems:
        print(f"\n✗ 有 {problems} 个探针导入时报错（不是没装，是装坏了）")
        return 1
    print("\n✓ 必需依赖全部可用，探针与实现对得上")
    return 0


if __name__ == "__main__":
    sys.exit(main())
