"""Rebuilds source.json (the SideStore source) from every .ipa attached to this
repository's GitHub releases. Run by .github/workflows/update-store.yml.

Each IPA is read once; what's learned is kept in store-cache.json. Settings such as
the store name, and per-app descriptions or icons, live in store-config.json.
"""
import json
import os
import shutil
import sys
import tempfile
import urllib.request
import zipfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from ipa_tools import NotAnApp, inspect_ipa  # noqa: E402

REPO = os.environ.get("GITHUB_REPOSITORY", "sengarcia32-ux/noteplus-updates")
OWNER = REPO.split("/")[0]
TOKEN = os.environ.get("GH_TOKEN", "")
RAW = f"https://raw.githubusercontent.com/{REPO}/main"
MAX_VERSIONS = 10
CACHE_FILE = "store-cache.json"


def load_json(path, default):
    if os.path.exists(path):
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    return default


def api(path):
    headers = {"Accept": "application/vnd.github+json", "User-Agent": "store-builder"}
    if TOKEN:
        headers["Authorization"] = f"Bearer {TOKEN}"
    request = urllib.request.Request(f"https://api.github.com/repos/{REPO}/{path}", headers=headers)
    with urllib.request.urlopen(request, timeout=60) as response:
        return json.load(response)


def all_releases():
    releases, page = [], 1
    while True:
        batch = api(f"releases?per_page=100&page={page}")
        releases += batch
        if len(batch) < 100:
            break
        page += 1
    return [r for r in releases if not r.get("draft")]


def download(url, destination):
    # Public download links redirect to GitHub's file storage, which rejects auth headers.
    request = urllib.request.Request(url, headers={"User-Agent": "store-builder"})
    with urllib.request.urlopen(request, timeout=600) as response, open(destination, "wb") as f:
        shutil.copyfileobj(response, f, 1 << 20)


def absolute(url):
    if not url or url.startswith("http"):
        return url
    return f"{RAW}/{url.lstrip('/')}"


def release_date(release):
    return release.get("published_at") or release.get("created_at") or ""


def main():
    config = load_json("store-config.json", {})
    cache = load_json(CACHE_FILE, {})
    os.makedirs("icons", exist_ok=True)

    entries = []
    live_assets = set()
    for release in all_releases():
        assets = release.get("assets", [])
        icon_asset = next((a for a in assets if a["name"].lower() == "icon.png"), None)
        for asset in assets:
            if not asset["name"].lower().endswith(".ipa"):
                continue
            key = str(asset["id"])
            live_assets.add(key)
            meta = cache.get(key)
            if meta is None:
                print(f"Reading {asset['name']} ({asset['size']:,} bytes)…")
                with tempfile.TemporaryDirectory() as folder:
                    path = os.path.join(folder, "app.ipa")
                    download(asset["browser_download_url"], path)
                    try:
                        meta, icon = inspect_ipa(path)
                    except (NotAnApp, zipfile.BadZipFile, KeyError, ValueError) as error:
                        print(f"  skipped: {error}")
                        cache[key] = {"invalid": str(error)}
                        continue
                if icon:
                    with open(f"icons/{key}.png", "wb") as f:
                        f.write(icon)
                    meta["icon"] = f"icons/{key}.png"
                cache[key] = meta
            if meta.get("invalid"):
                continue
            entries.append({"meta": meta, "release": release, "asset": asset, "iconAsset": icon_asset})

    # Forget IPAs whose release or file was deleted.
    for key in list(cache):
        if key not in live_assets:
            del cache[key]
            if os.path.exists(f"icons/{key}.png"):
                os.remove(f"icons/{key}.png")

    # Group by app, newest release first.
    by_app = {}
    for entry in sorted(entries, key=lambda e: release_date(e["release"]), reverse=True):
        by_app.setdefault(entry["meta"]["bundleIdentifier"], []).append(entry)

    store_icon = absolute(config.get("iconURL", "icon.png"))
    tint = config.get("tintColor", "6366F1")
    apps = []
    for bundle_id, items in by_app.items():
        overrides = config.get("apps", {}).get(bundle_id, {})
        versions, seen = [], set()
        for entry in items:
            meta, release, asset = entry["meta"], entry["release"], entry["asset"]
            version_key = (meta["version"], meta["buildVersion"])
            if version_key in seen:
                continue
            seen.add(version_key)
            notes = (release.get("body") or "").strip() or (release.get("name") or "").strip()
            version = {
                "version": meta["version"],
                "buildVersion": meta["buildVersion"],
                "date": release_date(release),
                "localizedDescription": notes or f"{meta['name']} {meta['version']}",
                "downloadURL": asset["browser_download_url"],
                "size": asset["size"],
            }
            if meta.get("minOSVersion"):
                version["minOSVersion"] = meta["minOSVersion"]
            versions.append(version)
            if len(versions) >= MAX_VERSIONS:
                break

        newest = items[0]
        meta = newest["meta"]
        icon = absolute(overrides.get("iconURL"))
        if not icon and newest["iconAsset"]:
            icon = newest["iconAsset"]["browser_download_url"]
        if not icon:
            icon = next((absolute(e["meta"]["icon"]) for e in items if e["meta"].get("icon")), None)
        name = overrides.get("name", meta["name"])
        apps.append({
            "name": name,
            "bundleIdentifier": bundle_id,
            "developerName": overrides.get("developerName", config.get("developerName", OWNER)),
            "subtitle": overrides.get("subtitle", ""),
            "localizedDescription": overrides.get("description")
            or (newest["release"].get("body") or "").strip()
            or f"{name}, from {config.get('name', 'my store')}.",
            "iconURL": icon or store_icon,
            "tintColor": overrides.get("tintColor", tint),
            "category": overrides.get("category", "other"),
            "screenshots": [],
            "versions": versions,
            "appPermissions": {
                "entitlements": meta["entitlements"],
                "privacy": meta["privacy"],
            },
        })

    source = {
        "name": config.get("name", f"{OWNER}'s Store"),
        "identifier": config.get("identifier", f"io.github.{OWNER}.store"),
        "subtitle": config.get("subtitle", ""),
        "description": config.get("description", ""),
        "iconURL": store_icon,
        "website": f"https://github.com/{REPO}",
        "tintColor": tint,
        "nsfw": False,
        "featuredApps": [app["bundleIdentifier"] for app in apps[:5]],
        "apps": apps,
        "news": [],
    }
    with open("source.json", "w", encoding="utf-8") as f:
        json.dump(source, f, indent=2, ensure_ascii=False)
        f.write("\n")
    with open(CACHE_FILE, "w", encoding="utf-8") as f:
        json.dump(cache, f, indent=2, ensure_ascii=False, sort_keys=True)
        f.write("\n")

    print(f"source.json: {len(apps)} app(s)")
    for app in apps:
        print(f"  {app['name']} ({app['bundleIdentifier']}): {', '.join(v['version'] for v in app['versions'])}")


if __name__ == "__main__":
    main()
