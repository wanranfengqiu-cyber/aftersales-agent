"""中文电商售后领域:zh_aftersales(政策里含法规条款)和 zh_aftersales_rag(条款要用工具按条查)。"""
from pathlib import Path
from typing import Optional

from tau2.data_model.tasks import Task
from tau2.domains.zh_aftersales.data_model import ZhAftersalesDB
from tau2.domains.zh_aftersales.tools import ZhAftersalesTools
from tau2.domains.zh_aftersales.utils import ZH_DB_PATH, ZH_POLICY_PATH, ZH_POLICY_RAG_PATH, ZH_TASK_SET_PATH
from tau2.environment.environment import Environment
from tau2.utils import load_file


def _env(policy_path, name, db: Optional[ZhAftersalesDB] = None, solo_mode: bool = False) -> Environment:
    if solo_mode:
        raise ValueError("zh_aftersales 不支持 solo 模式")
    if db is None:
        db = ZhAftersalesDB.load(ZH_DB_PATH)
    with open(policy_path, "r", encoding="utf-8") as fp:
        policy = fp.read()
    return Environment(domain_name=name, policy=policy, tools=ZhAftersalesTools(db))


def get_environment(db: Optional[ZhAftersalesDB] = None, solo_mode: bool = False) -> Environment:
    return _env(ZH_POLICY_PATH, "zh_aftersales", db, solo_mode)


def get_environment_rag(db: Optional[ZhAftersalesDB] = None, solo_mode: bool = False) -> Environment:
    return _env(ZH_POLICY_RAG_PATH, "zh_aftersales_rag", db, solo_mode)


def get_tasks(task_split_name: Optional[str] = "base") -> list[Task]:
    tasks = [Task.model_validate(t) for t in load_file(ZH_TASK_SET_PATH)]
    if task_split_name is None:
        return tasks
    splits = get_tasks_split()
    if task_split_name not in splits:
        raise ValueError(f"Invalid task split name: {task_split_name}. Valid splits are: {splits.keys()}")
    return [t for t in tasks if t.id in splits[task_split_name]]


def get_tasks_split() -> dict[str, list[str]]:
    return load_file(Path(ZH_TASK_SET_PATH).parent / f"split_{Path(ZH_TASK_SET_PATH).stem}.json")
