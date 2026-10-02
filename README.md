# Hidden Gems

Hidden Gems resolves a named credential across configured stores. It reports
which providers were checked and does not silently choose between duplicate
matches or ignore a provider it could not check.


## Instruction

```bash
conda env create -f environment.yml
conda activate galileo
```

## Install

Python 3.11 or newer is required. From this checkout:

Recommended use conda.

```bash
conda env create -f environment.yml
conda activate galileo
```

If you need globally.

```bash
python -m pip install .
```

Install the optional 1Password Python SDK integration with
`python -m pip install '.[onepassword]'`. The 1Password CLI is a separate option.
On macOS, building the wheel also requires CMake 4.3 or newer and a C++23
toolchain for the Keychain bridge.

## For developers

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

See [the specification](docs/README.md) for provider selection, detection,
configuration, and error semantics.
