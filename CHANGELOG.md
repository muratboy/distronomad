# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Changed
- Hardware drivers that add a kernel module and disable the open-source driver (NVIDIA, Broadcom `wl`) are installed in a separate, checked step. The step is skipped when Secure Boot or kernel lockdown would reject an unsigned module, when the header package of the running or newest kernel cannot be determined, or when a package is missing from the repositories. Header packages for those kernels are installed with the driver.
- After installation the driver module is verified for the running and newest kernel. If it is missing, only the packages installed in that step are removed, so the system boots with the open-source driver instead of a black screen. If removal fails, the exact command to run before rebooting is shown.
- Before installing such a driver, the TUI and CLI show the packages, the command, the kernels, the Secure Boot state and the risks, and ask for confirmation. Without an interactive terminal the CLI skips the driver step.
- NVIDIA and Broadcom `wl` packages from the backup's package list are no longer installed directly, in the app or in `restore.sh`. Drivers come only from the live hardware scan through the checked step.
- On Debian-based systems, drivers are installed with `DEBIAN_FRONTEND=noninteractive` so a debconf prompt cannot stall the installation.

### Fixed
- `verify` no longer mistakes nested `manifest.json` files (browser extensions, Widevine) for the backup manifest.
- Tar archives hash each file from the same read that writes it. Files that change during the backup are listed, a file that shrinks no longer corrupts the archive, and hard links are stored as regular files.
- Backups warn (the TUI asks) when a browser or mail client whose profile is selected is running.
- In the TUI, a restore that only installs drivers now asks for sudo first and skips the drivers if authentication fails.

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

[Unreleased]: https://github.com/muratboy/distronomad/compare/v0.9.0...HEAD
[0.9.0]: https://github.com/muratboy/distronomad/releases/tag/v0.9.0
