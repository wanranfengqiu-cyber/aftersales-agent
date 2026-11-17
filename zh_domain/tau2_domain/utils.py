from tau2.utils.utils import DATA_DIR

ZH_DATA_DIR = DATA_DIR / "tau2" / "domains" / "zh_aftersales"
ZH_DB_PATH = ZH_DATA_DIR / "db.json"
ZH_POLICY_PATH = ZH_DATA_DIR / "policy.md"          # 法规条款全文放在政策里
ZH_POLICY_RAG_PATH = ZH_DATA_DIR / "policy_rag.md"  # 不放条款全文,要用 get_policy_article 按条查
ZH_TASK_SET_PATH = ZH_DATA_DIR / "tasks.json"
TODAY = "2026-11-17"  # 评测里固定的"今天"(写在政策里)
