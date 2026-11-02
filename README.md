# Store Plus

My own app store for **SideStore**, with a website: **https://sengarcia32-ux.github.io/store-plus/**

Add this source in SideStore (Sources › **+**):

```
https://raw.githubusercontent.com/sengarcia32-ux/store-plus/main/source.json
```

Every app here shows up in SideStore, and new versions appear as **Update**.

## Add or update an app

**Option A: a release (any size up to 2 GB, works from iPhone/iPad Safari)**

1. Open this repository on GitHub › **Releases** › **Draft a new release**.
2. **Choose a tag:** type any new name, for example `myapp-1.2`, and pick *Create new tag*.
3. Title and description are optional. The description becomes the "What's New" text.
4. Attach the `.ipa` (drag it in, or tap *Attach binaries*). Optionally also attach an
   `icon.png` if the app's icon doesn't show.
5. Click **Publish release**.

**Option B: upload into `incoming/` (IPAs up to 25 MB)**

Open the `incoming` folder › **Add file** › **Upload files**, drop the `.ipa`, and commit.

Either way, the *Update store* job reads the IPA (name, version, icon, permissions) and
updates `source.json` and the website within about two minutes. To remove an app version, delete its release.

New versions must have a higher version or build number than the one installed, or SideStore
won't offer them as an update.

## Website

The website (`web/`) is published to GitHub Pages by the same job. It lists every app with its icon,
version, What's New, permissions and version history, with *Get in SideStore* and *Download IPA*
buttons. On iPhone or iPad, Safari's Share › Add to Home Screen keeps it on the Home Screen.

## Settings

`store-config.json` holds the store's name and description, and optional per-app settings
(keyed by bundle ID): `name`, `subtitle`, `description`, `category`, `iconURL`.

## Important

- **This repository is public.** Anyone with the link can download every IPA here. Only upload
  apps you made or are allowed to share. No App Store apps, paid apps, or cracked apps.
- With a free Apple ID, SideStore can keep 3 sideloaded apps installed at once (SideStore
  counts as one) and register 10 new app IDs per week.
- Apps that need push notifications, iCloud, or app groups may lose those features, or fail
  to install, when signed with a free Apple ID.
- The Note+ build publishes Note+ here automatically (through `incoming/`).
