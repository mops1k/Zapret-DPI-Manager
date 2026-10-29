# Исходники goodbyeDPI-UI

Скопированы из репозитория [Storik4pro/goodbyeDPI-UI-configs](https://github.com/Storik4pro/goodbyeDPI-UI-configs)
(`zapret/configs/*.json`), лицензия Apache-2.0. Это избранные стратегии
zapret/winws, которых нет у нас по содержимому: 1 (Discord: udplen/tamper),
3 (Amazon/Cloudflare: dup, ipfrag2), 12/14/18 (fake-tls-mod rndsni/padencap),
17 (WireGuard, ipset-ip, dpi-desync-start), 22/23/25 (autottl, свой TLS ClientHello).

Пересоздание файлов из этих исходников: `python3 tools/import_goodbyedpi_ui.py --write`,
проверка в CI: `python3 tools/import_goodbyedpi_ui.py --check`.
