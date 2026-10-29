---
name: Registry request
about: Ask for an app to get a short code in the official registry
title: "Registry: add <app>"
labels: registry
---

Tip: run `null-short submit <app>` and this is filled in for you.

### Registry request: <app name>

- Desktop ID: `<id>`  (from `null-short classify <app>`)
- Categories: `<categories>`

Proposed entry for `registry/registry.toml`:

```toml
[[app]]
name = "<app name>"
seq  = "↑ … …"
ids  = ["<id>"]
```
