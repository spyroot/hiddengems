# Hidden Gems

Hidden Gems resolves a named credential across configured stores. It reports
which providers were checked and does not silently choose between duplicate
matches or ignore a provider it could not check.

## Install

Python 3.11 or newer is required. From this checkout:

We recommend the project conda environment:

```bash
conda env create -f environment.yml
conda activate hiddengems
```

To install in another Python environment instead:

```bash
python -m pip install .
```

Install the optional 1Password Python SDK integration with
`python -m pip install '.[onepassword]'`. The 1Password CLI is a separate option.
On macOS, building the wheel also requires CMake 4.3 or newer and a C++23
toolchain for the Keychain bridge.

## Use

Configure a credential in a private `.env` file, then select that file
explicitly:

```python
from hiddengems.hidden_gems import HiddenGems

gems = HiddenGems(provider_settings={"dotenv": {"path": "/path/to/.env"}})
result = gems.inspect_gem("SERVICE_API_KEY", provider="dotenv")
values = gems.dig_gem("SERVICE_API_KEY", provider="dotenv")
```

`inspect_gem` returns non-secret match and issue metadata. `dig_gem` returns a
list of values from exactly one resolved location; it raises when the name is
missing, ambiguous, or cannot be checked completely. Do not print or log the
returned values. Writing a value uses `hide_gem` with an explicit provider.

## Contribution

- Run `bless.sh` before each commit or merge. CI repeats the static checks.

```sh
make bless
make install-hooks
```

`make install-hooks` installs the optional repository pre-commit hook.

- Remember what can be computed at runtime MUST be computed at runtime. Do not hardcode
  tokens, values, secrets, or observed state. if you do have CONSTS.

- When the code, exception handler, or branch, edge case, CI pipeline, or CI job can HEAL a failure, or recover
  implement that recovery logic. DO NOT FAIL without reason.

- requires the serialized execution, backup or last-known-good state, independent verification, sanitized
  evidence, and cleanup proof.

Before commit and PR check before calling a package change complete, build its wheel, install it outside
the source tree, and test imports and the relevant provider behavior.

Use isolated, non-secret fixtures for provider tests. what was verified
and what still requires a platform or credential that was not available.

go-no-go should include evidence for all functionality.

See [the specification](docs/README.md) for provider selection, detection,
configuration, and error semantics.
