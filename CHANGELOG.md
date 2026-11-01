# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [0.9.0] - 2026-11-01

### Added
- Initial open-source release (Beta).
- Textual-based TUI with Cyberpunk and multi-palette themes (Tokyo Night, Dracula, Nord, CachyOS Emerald, Monokai).
- Headless CLI interface with subcommands: `scan`, `backup`, `restore`, `verify`, `append`, `diff`, `rollback`, and `schedule`.
- Cross-distro package translation engine supporting Arch Linux, Debian/Ubuntu, Fedora, and openSUSE, plus Flatpak, Snap, Pipx, Cargo, NPM, and VS Code/VSCodium extensions.
- Comprehensive desktop and application preservation: dotfiles (`~/.config`), browser profiles (Firefox, Thunderbird, Zen, LibreWolf), Flatpak app data (`~/.var/app`), GNOME `dconf`, KDE Plasma configs, and system state reference copies.
- In-place backup update/append capability (`distronomad append`) supporting `--path`, `--config`, and `--package`.
- Streaming SHA-256 backup verification (`distronomad verify`) without unpacking to disk, with FAT32 4 GB limit detection.
- Optional AES-256 backup encryption with password confirmation.
- Enhanced CLI password security: valueless `--password` / `-p` with interactive masked prompt (`getpass`), confirmation prompt for `backup` and `append`, `DISTRONOMAD_PASSWORD` environment variable support, command-line visibility warnings on `stderr`, and automatic terminal prompts for encrypted archives.
- Network secret protection: `/etc/NetworkManager/system-connections` and `/etc/wireguard` profiles (Wi-Fi passwords and private keys) are only captured in encrypted backups when executed as root, and safely skipped with a notification and note in unencrypted backups.
- Granular restore with conflict strategies (`backup`, `overwrite`, `skip`), `--dry-run`, and safe root/external path protection (`--allow-external-paths`).
- Zero-dependency bilingual standalone `restore.sh` generation with `--lang` support.
- Unified diff viewer and transaction rollback engine (`distronomad rollback`).
- Dual language support (Turkish and English) across TUI, CLI, and standalone restore script, with `--lang` flag and environment auto-detection.

[0.9.0]: https://github.com/muratboy/distronomad/releases/tag/v0.9.0
