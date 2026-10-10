
import os
import json
import subprocess
import urllib.request
import xml.etree.ElementTree as ET
from pathlib import Path
from datetime import datetime, timezone
from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent
load_dotenv(BASE_DIR / ".env")

WEBHOOK = os.getenv("DISCORD_WEBHOOK", "").strip()
STATE_FILE = BASE_DIR / "seen.json"



FEEDS = [
    "https://www.cisa.gov/cybersecurity-advisories/alerts.xml",
]
SITE = "https://klineme.fr"
MAX_ALERTS_PER_RUN = 5


def send_discord(message):
    if not WEBHOOK:
        raise RuntimeError("Secret DISCORD_WEBHOOK absent.")

    payload = json.dumps(
        {"content": message[:1900]},
        ensure_ascii=False,
    )

    result = subprocess.run(
        [
            "curl", "-sS", "-o", "/dev/null",
            "-w", "%{http_code}",
            "--max-time", "20",
            "-H", "Content-Type: application/json",
            "-d", payload,
            WEBHOOK,
        ],
        capture_output=True,
        text=True,
        timeout=25,
    )

    status = result.stdout.strip()
    if result.returncode != 0 or status not in ("200", "204"):
        raise RuntimeError(
            f"Erreur Discord HTTP {status or '?'}: "
            f"{result.stderr.strip()}"
        )


def fetch(url):
    request = urllib.request.Request(
        url,
        headers={"User-Agent": "DiscordSentinel/1.0"},
    )
    with urllib.request.urlopen(request, timeout=20) as response:
        return response.read()


def get_items(xml_data):
    root = ET.fromstring(xml_data)
    items = []

    for item in root.findall(".//item"):
        title = item.findtext("title", default="Sans titre").strip()
        link = item.findtext("link", default="").strip()
        guid = item.findtext("guid", default=link or title).strip()
        description = item.findtext("description", default="")
        items.append((guid, title, link, description))

    namespace = {"a": "http://www.w3.org/2005/Atom"}

    if not items:
        for item in root.findall(".//a:entry", namespace):
            title = item.findtext(
                "a:title", default="Sans titre",
                namespaces=namespace,
            ).strip()
            link_element = item.find("a:link", namespace)
            link = (
                link_element.get("href", "")
                if link_element is not None else ""
            )
            guid = item.findtext(
                "a:id", default=link or title,
                namespaces=namespace,
            ).strip()
            description = item.findtext(
                "a:summary", default="",
                namespaces=namespace,
            )
            items.append((guid, title, link, description))

    return items


def main():
    now = datetime.now(timezone.utc).strftime("%d/%m/%Y %H:%M UTC")
    print(f"Début de la vérification : {now}")

    try:
        seen = json.loads(STATE_FILE.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        seen = {"items": [], "site_down": False}

    old_items = set(seen.get("items", []))
    is_first_run = not old_items
    new_items = []
    feed_results = []
    all_guids = set(old_items)

    # Récupération des actualités de sécurité
    for feed in FEEDS:
        try:
            items = get_items(fetch(feed))
            feed_results.append(
                f"✅ Flux CISA accessible : {len(items)} article(s) trouvé(s)"
            )

            for guid, title, link, description in items:
                if guid:
                    all_guids.add(guid)
                    if guid not in old_items:
                        new_items.append(
                            (guid, title, link, description)
                        )

        except Exception as exc:
            feed_results.append(
                f"⚠️ Flux indisponible : {str(exc)[:250]}"
            )

    # Au premier lancement, enregistrer les articles existants
    # sans les présenter comme des nouveautés.
    if is_first_run:
        new_items = []
        print("Premier lancement : historique initialisé.")

    sent_count = 0

    for guid, title, link, description in new_items[:MAX_ALERTS_PER_RUN]:
        summary = " ".join(description.split())
        if len(summary) > 500:
            summary = summary[:497] + "..."

        message = (
            f"🛡️ **Nouvelle actualité de cybersécurité**\n"
            f"**{title}**\n"
        )
        if summary:
            message += f"{summary}\n"
        if link:
            message += link

        send_discord(message)
        sent_count += 1
        print(f"Actualité envoyée : {title}")

    # Vérification de la disponibilité du site
    site_down = seen.get("site_down", False)

    try:
        fetch(SITE)
        site_status = f"✅ Site accessible : {SITE}"

        if site_down:
            send_discord(f"✅ Le site **{SITE}** répond de nouveau.")

        site_down = False

    except Exception as exc:
        site_status = f"🚨 Site inaccessible : {str(exc)[:200]}"

        if not site_down:
            send_discord(
                f"🚨 **Le site {SITE} semble inaccessible**\n"
                f"Erreur : {str(exc)[:250]}"
            )

        site_down = True

    # Bilan systématique à chaque exécution
    if is_first_run:
        news_status = (
            f"Initialisation : {len(all_guids)} article(s) "
            "historique(s) mémorisé(s), sans anciennes alertes."
        )
    elif new_items:
        news_status = (
            f"📰 {len(new_items)} nouvelle(s) actualité(s) détectée(s), "
            f"{sent_count} envoyée(s) cette fois."
        )
    else:
        news_status = "📰 Aucune nouvelle actualité détectée."

    report = [
        f"📋 **Bilan Discord Sentinel — {now}**",
        news_status,
        *feed_results,
        site_status,
    ]
    send_discord("\n".join(report))

    state = {
        "items": list(all_guids)[-500:],
        "site_down": site_down,
    }

    STATE_FILE.write_text(
        json.dumps(state, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    print("Vérification terminée.")


if __name__ == "__main__":
    main()