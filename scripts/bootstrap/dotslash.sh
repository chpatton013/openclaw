set -euo pipefail

if command -v dotslash >/dev/null 2>&1; then
  exit 0
fi

# Build the dotslash release URL.
dotslash_release_version="latest"
dotslash_release_url="https://github.com/facebook/dotslash/releases"
dotslash_release_url+="/$dotslash_release_version/download"

# Find the correct release asset for this kernel/architecture.
kernel="$(uname -s)"
case "$kernel" in
  Linux*)
    # Use an architecture-specific musl asset for Linux.
    arch="$(uname -m)"
    case "$arch" in
      aarch64)
        dotslash_release_url+="/dotslash-linux-musl-aarch64.tar.gz"
        ;;
      x86_64)
        dotslash_release_url+="/dotslash-linux-musl-x86_64.tar.gz"
        ;;
      *)
        echo Unsupported architecture: $arch >&2
        exit 1
        ;;
    esac
    ;;
  Darwin*)
    # Use the universal binary format asset for macOS.
    dotslash_release_url+="/dotslash-macos.tar.gz"
    ;;
  *)
    echo Unsupported kernel: $kernel >&2
    exit 1
    ;;
esac

# Download the dotslash release and unpack into user bin dir.
user_bin_dir="${XDG_BIN_HOME:-$HOME/.local/bin}"
curl -fSL "$dotslash_release_url" | tar fzx - -C "$user_bin_dir"
