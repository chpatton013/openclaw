---
name: create-dotslash
description: Create a dotslash executable in bin/ for a third-party CLI tool. Use when the user asks to "add a dotslash for X", "make a dotslash executable for X", or install a versioned cross-platform binary via dotslash.
---

# Create a dotslash executable

A dotslash file is a text file starting with `#!/usr/bin/env dotslash` followed by a JSON manifest. When executed, `dotslash` downloads, verifies, caches, and runs the appropriate platform binary.

In this repo, dotslash executables live in `bin/<tool-name>` and are committed (hashed + version-pinned). `dotslash` itself is installed by `scripts/bootstrap.py`.

## Steps

### 1. Find the release assets

Resolve the tool's release URL pattern and latest-or-pinned version. Sources vary:

- GitHub releases: `curl -sL https://api.github.com/repos/OWNER/REPO/releases/latest`
- HashiCorp tools: `https://releases.hashicorp.com/<tool>/index.json`
- Other vendors: tool-specific URLs.

You need four asset URLs — one per platform dotslash supports in this repo:

- `macos-aarch64`
- `macos-x86_64`
- `linux-aarch64`
- `linux-x86_64`

Prefer musl-linked Linux builds when available (more portable across distros). This repo's `bin/uv` is the reference example.

### 2. Determine the `format` and `path`

`format` (string, optional) tells dotslash how to unpack:

| `format`   | Archive? | Decompress? |
| ---------- | -------- | ----------- |
| `tar.bz2`  | yes      | bzip2       |
| `tar.gz`   | yes      | gzip        |
| `tar.xz`   | yes      | xz          |
| `tar.zst`  | yes      | zstd        |
| `tar`      | yes      | none        |
| `zip`      | yes      | zip         |
| `bz2`      | no       | bzip2       |
| `gz`       | no       | gzip        |
| `xz`       | no       | xz          |
| `zst`      | no       | zstd        |
| _omitted_  | no       | none        |

For archives, `path` is the relative path of the binary **inside** the archive. Inspect it directly:

```bash
# tar archives
curl -fsSL "$URL" | tar tzf - | head

# zip archives
curl -fsSL "$URL" | python3 -c "import sys,zipfile,io; print(zipfile.ZipFile(io.BytesIO(sys.stdin.buffer.read())).namelist())"
```

For raw single-file binaries (no archive), omit `format` and set `path` to the filename dotslash should write in its cache (e.g., `"path": "shfmt"`).

### 3. Generate size + digest

`dotslash -- create-url-entry <URL>` downloads the asset, computes blake3, and prints a valid platform block with `TODO` in `format`/`path` fields. Run it in parallel for all four URLs:

```bash
for url in URL1 URL2 URL3 URL4; do
  dotslash -- create-url-entry "$url" &
done
wait
```

Never hand-author digests. Always derive from the real upstream bytes.

### 4. Assemble `bin/<tool>`

```
#!/usr/bin/env dotslash

{
  "name": "<tool>",
  "platforms": {
    "macos-aarch64": { "size": ..., "hash": "blake3", "digest": "...", "format": "tar.gz", "path": "...", "providers": [{"url": "..."}] },
    "macos-x86_64":  { ... },
    "linux-aarch64": { ... },
    "linux-x86_64":  { ... }
  }
}
```

The shebang must be the literal first line followed by `\n` or `\r\n`. The JSON body supports `//` and `/*` comments and trailing commas (jsonc).

### 5. Mark executable and verify

```bash
chmod +x bin/<tool>
dotslash -- parse bin/<tool>   # validates manifest without running
bin/<tool> --version            # runs against the current platform
```

## Common pitfalls

- **Wrong archive path.** `tar tzf` or `zipfile.namelist()` is the source of truth; guessing by tool name often wrong (e.g., `uv` is at `uv-aarch64-apple-darwin/uv`, not `uv`).
- **Using `.tgz` as format.** Only the values in the table above are valid; `.tgz` files use `"format": "tar.gz"`.
- **Raw binary with `format` set.** When the release asset IS the binary (no compression, no archive), omit `format` entirely. `create-url-entry` prints `"format": "TODO: ... could not guess ..."` in this case — delete the line.
- **Missing platforms.** Some tools (e.g., `rustfmt` v1.6.0) only ship x86_64. Include only the platforms that exist; dotslash will reject unsupported platforms at runtime with a clear message.
- **Python-only tools.** Packages like `pyupgrade` have no pre-built binaries. Don't try to dotslash them — write a thin wrapper that delegates to `bin/uv tool run <name> "$@"`.

## Reference: existing dotslash files

- `bin/uv` — tar.gz archive with nested binary path
- `bin/shellcheck` — tar.gz archive with nested binary path
- `bin/terraform` — zip archive, flat binary path
- `bin/shfmt` — raw binary, no `format` field
- `bin/rustfmt` — tar.gz archive, partial platform coverage
