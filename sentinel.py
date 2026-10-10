
import os
import json
import subprocess
import urllib.request
import xml.etree.ElementTree as ET
from pathlib import Path
from dotenv import load_dotenv

# Charger le fichier .env situé dans le dossier du projet
load_dotenv(dotenv_path=Path(__file__).resolve().parent / ".env")

WEBHOOK = os.getenv("DISCORD_WEBHOOK", "").strip()
STATE_FILE = Path(__file__).resolve().parent / "seen.json"

FEEDS = [
    "https://www.cisa.gov/uscert/ncas/all.xml",
]

SITE = "https://klineme.fr"
MAX_ALERTS_PER_RUN = 5


def send_discord(message):
    """Envoyer un message Discord via curl."""

    if not WEBHOOK:
        raise RuntimeError(
            "DISCORD_WEBHOOK absent. Vérifie le fichier .env."
        )

    payload = json.dumps(
        {"content": message[:1900]},
        ensure_ascii=False,
    )

    result = subprocess.run(
        [
            "curl",
            "-sS",
            "-o", "/dev/null",
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
            f"Échec envoi Discord : HTTP {status or 'inconnu'} "
            f"{result.stderr.strip()}"
        )


def fetch(url):
    """Télécharger une page ou un flux RSS."""

    request = urllib.request.Request(
        url,
        headers={"User-Agent": "DiscordSentinel/1.0"},
    )

    with urllib.request.urlopen(request, timeout=20) as response:
        return response.read()


def get_items(xml_data):
    """Extraire les articles d'un flux RSS ou Atom."""

    root = ET.fromstring(xml_data)
    items = []

    # Format RSS
    for item in root.findall(".//item"):
        title = item.findtext("title", default="Sans titre")
        link = item.findtext("link", default="")
        guid = item.findtext("guid", default=link or title)
        items.append((guid, title, link))

    # Format Atom
    namespace = {"a": "http://www.w3.org/2005/Atom"}

    if not items:
        for item in root.findall(".//a:entry", namespace):
            title = item.findtext(
                "a:title",
                default="Sans titre",
                namespaces=namespace,
            )

            link_element = item.find("a:link", namespace)
            link = (
                link_element.get("href", "")
                if link_element is not None
                else ""
            )

            guid = item.findtext(
                "a:id",
                default=link or title,
                namespaces=namespace,
            )

            items.append((guid, title, link))

    return items


def main():
    print("🤖 Démarrage de la sentinelle...")

    # Vérifier immédiatement que Discord fonctionne.
    send_discord(
        "🤖 **Sentinelle démarrée**\n"
        "Surveillance de klineme.fr et des alertes de sécurité."
    )
    print("✅ Message de démarrage envoyé à Discord.")

    try:
        seen = json.loads(STATE_FILE.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        seen = {"items": [], "site_down": False}

    old_items = set(seen.get("items", []))
    new_items = []

    # Lire les flux de sécurité.
    for feed in FEEDS:
        try:
            items = get_items(fetch(feed))

            for guid, title, link in items:
                if guid and guid not in old_items:
                    new_items.append((guid, title, link))

            print(f"✅ Flux vérifié : {feed}")

        except Exception as exc:
            print(f"⚠️ Flux indisponible : {feed} : {exc}")

    # Au premier lancement, mémoriser les anciens articles
    # sans envoyer toutes les anciennes alertes.
    if not old_items:
        old_items.update(guid for guid, _, _ in new_items)
        new_items = []
        print("ℹ️ Premier lancement : historique initialisé.")

    # Envoyer les nouveaux articles.
    for guid, title, link in new_items[:MAX_ALERTS_PER_RUN]:
        send_discord(
            f"🛡️ **Nouvelle alerte de sécurité**\n"
            f"{title}\n{link}"
        )

        old_items.add(guid)
        print(f"📨 Alerte envoyée : {title}")

    # Vérifier le site.
    site_down = seen.get("site_down", False)

    try:
        fetch(SITE)

        print(f"✅ Site accessible : {SITE}")

        if site_down:
            send_discord(
                f"✅ Le site **{SITE}** répond de nouveau."
            )

        site_down = False

    except Exception as exc:
        print(f"⚠️ Site inaccessible : {exc}")

        if not site_down:
            send_discord(
                f"🚨 **Le site {SITE} ne répond pas correctement.**\n"
                f"Erreur : {str(exc)[:250]}"
            )

        site_down = True

    # Enregistrer l'historique.
    state = {
        "items": list(old_items)[-500:],
        "site_down": site_down,
    }

    STATE_FILE.write_text(
        json.dumps(state, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    print("✅ Vérification terminée.")


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        print(f"❌ Erreur de la sentinelle : {exc}")
        raise

