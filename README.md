# itamar-tools

A Claude Code **plugin marketplace**. Add it once; every skill published here becomes
installable, now and in the future.

```
/plugin marketplace add ItamarElkayam/claude-plugins
/plugin install paperbase@itamar-tools
```

To see what is new later: `/plugin marketplace update itamar-tools`, then
`/plugin install <name>@itamar-tools` or `/plugin update <name>`.

## Plugins

| Plugin | Version | What it does |
|---|---|---|
| **paperbase** | 1.1.0 | Turns a directory of scientific papers (PDF, HTML, DOCX, Markdown, text) into a durable, incrementally maintainable knowledge base optimized for AI-agent context: structure-aware extraction with exact source locators, per-paper research profiles, atomic claims whose every quote is verified against the source, cross-paper relations, topic and corpus syntheses, local SQLite FTS retrieval, and content-hash-driven incremental updates. Sets up its own dependencies and tunes itself to the corpus on first run. |

### paperbase quick start

```bash
# Ask Claude to build a KB from a paper directory, or drive it directly:
kb=~/my-kb
python3 <plugin>/skills/paperbase/scripts/kb.py build ~/papers $kb
python3 <plugin>/skills/paperbase/scripts/kb.py context $kb "<question>" --budget 8000
```

Only Python 3.9+ is required. PDF support (PyMuPDF) installs itself on first run into
`~/.cache/paperbase/pylibs` — private to paperbase, no sudo, removable with one `rm -rf`;
disable with `--no-install`. `kb doctor` reports the environment. Full docs:
`plugins/paperbase/skills/paperbase/SKILL.md`.

## Repository layout

```
.claude-plugin/marketplace.json     the catalog: one entry per plugin
plugins/<name>/
  .claude-plugin/plugin.json        that plugin's manifest
  skills/<name>/SKILL.md            the skill itself (+ references/, scripts/, …)
new-plugin.sh                       add a new skill as a plugin
sync.sh                             release an update to an existing plugin
```

## Publishing a new skill

```bash
./new-plugin.sh my-skill ~/path/to/existing/skill   # or omit the path for a stub
git add -A && git commit -m "Add my-skill plugin" && git push
```

The script copies the skill in, writes its `plugin.json`, appends the marketplace entry at
version `0.1.0`, and validates both manifests. Users then run
`/plugin marketplace update itamar-tools && /plugin install my-skill@itamar-tools`.

## Releasing an update

```bash
./sync.sh paperbase                 # sync from the working copy, keep the version
./sync.sh paperbase "" 1.2.0        # sync and bump to 1.2.0
git add -A && git commit -m "paperbase 1.2.0" && git push
```

`sync.sh` re-syncs the skill, bumps both manifests, runs the plugin's own tests when it has
them, and validates. Tag a release with `claude plugin tag ./plugins/<name>`.

## Conventions

- One plugin per skill, so people install only what they want and each has its own version.
- The marketplace `name` (`itamar-tools`) is what users type in `plugin@marketplace`.
  **Never rename it** — that breaks every existing installation.
- Keep each skill's own docs inside its skill folder; this README is only the catalog.
