# Contributing to DistroNomad

Thank you for your interest in contributing to DistroNomad!

## Development Setup

1. Clone the repository and create a virtual environment:
   ```bash
   git clone https://github.com/muratboy/distronomad.git
   cd distronomad
   python3 -m venv .venv
   source .venv/bin/activate
   ```
2. Install DistroNomad in editable mode with development and test dependencies:
   ```bash
   pip install -e ".[test]"
   ```

## Running Tests

DistroNomad includes comprehensive test fixtures in `tests/conftest.py` that automatically isolate test execution from your host system (`HOME`, `XDG_*`, desktop settings, and privileged package manager commands are redirected/faked).

Run tests with pytest:
```bash
pytest -q
```

### Optional Bubblewrap Sandbox
For an extra layer of system isolation and reproducibility, tests can optionally be run within a `bubblewrap` (bwrap) sandbox:
```bash
S="/tmp/dn-test"
mkdir -p "$S/home"
bwrap --ro-bind / / \
      --dev /dev \
      --proc /proc \
      --tmpfs /tmp \
      --bind "$S" "$S" \
      --setenv HOME "$S/home" \
      --setenv XDG_RUNTIME_DIR /nonexistent \
      --unsetenv DBUS_SESSION_BUS_ADDRESS \
      --unshare-pid \
      --unshare-net \
      --chdir "$(pwd)" \
      .venv/bin/python -B -m pytest -q -p no:cacheprovider
```

## Guidelines

- Ensure all changes are covered by tests.
- Keep pull requests focused on specific improvements or fixes.
