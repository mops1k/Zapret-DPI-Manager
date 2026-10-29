GITHUB_BASE_URL = "https://github.com/mops1k/Zapret-DPI-Manager"
GITHUB_API_REPO = "mops1k/Zapret-DPI-Manager"
GITHUB_RAW_URL = "https://raw.githubusercontent.com/mops1k/Zapret-DPI-Manager/main"

RELEASES_URL = "https://github.com/mops1k/Zapret-DPI-Manager/releases/latest/download/"

# Зеркало для raw-файлов: последний провайдер в core/github_fetch.py.
# {owner}, {repo}, {ref}, {path}, {url} — подстановки; шаблон без «{» считается
# префиксом (`https://ghproxy.net/` → префикс + raw-URL). Переопределяется
# переменной окружения ZAPRET_GH_MIRROR (значение `off` выключает зеркало).
GITHUB_MIRROR_TEMPLATE = "https://cdn.jsdelivr.net/gh/{owner}/{repo}@{ref}/{path}"

# Единый манифест полного пакета (менеджер + служба) на GitHub (raw)
VERSION_CONFIG = {
    "version_url": f"{GITHUB_RAW_URL}/version.txt",
    "current_version": "2.13.1",
}
