"""Focused provider-routing checks using non-secret fake values."""

from __future__ import annotations

import importlib
from types import SimpleNamespace

import pytest

from hiddengems.abstraction import (
    DetectedProvider,
    GemReference,
    ProviderLookupError,
    ProviderState,
)
from hiddengems.gem_provider import GemProvider
from hiddengems.hidden_gems import HiddenGems, IncompleteGemLookupError


class FakeProvider:
    def __init__(self, record, value=None, issue=None):
        self.record = record
        self.value = value
        self.issue = issue

    def find_gem(self, name, *, criteria=None):
        if self.issue:
            raise self.issue
        return (GemReference(name, self.record.provider, self.record.instance_id),)

    def get_gem(self, reference):
        return [self.value]


def record(provider, state=ProviderState.CONFIGURED):
    return DetectedProvider(provider, f"{provider}:local", state, (), {})


def test_unchecked_onepassword_does_not_silently_return_old_keyring(
    monkeypatch, tmp_path
):
    keyring = record("keyring")
    onepassword = record("onepassword", ProviderState.DETECTED)
    instances = {
        keyring.instance_id: FakeProvider(keyring, "old-fake-token"),
        onepassword.instance_id: FakeProvider(
            onepassword,
            issue=ProviderLookupError(
                "1Password could not be checked",
                "Enable integration in Settings > Developer",
            ),
        ),
    }
    monkeypatch.setattr(
        GemProvider,
        "create",
        classmethod(lambda cls, item: instances[item.instance_id]),
    )
    gems = HiddenGems(
        providers=(keyring, onepassword), config_path=tmp_path / "missing.json"
    )

    result = gems.inspect_gem("X")
    report = result.as_dict()
    assert len(report["matches"]) == 1
    assert len(report["issues"]) == 1
    assert {item["provider"]: item["state"] for item in report["providers"]} == {
        "keyring": "ready",
        "onepassword": "detected",
    }
    assert "old-fake-token" not in str(report)
    with pytest.raises(IncompleteGemLookupError):
        gems.dig_gem("X")
    assert gems.dig_gem("X", provider="keyring") == ["old-fake-token"]


def test_detection_runs_once_and_list_valued_gem_stays_nested(monkeypatch, tmp_path):
    item = record("keyring")
    provider = FakeProvider(item, ["a", "b"])
    calls = []

    def detect(cls, **options):
        calls.append(options)
        return (item,)

    monkeypatch.setattr(GemProvider, "detect", classmethod(detect))
    monkeypatch.setattr(
        GemProvider, "create", classmethod(lambda cls, record: provider)
    )
    gems = HiddenGems(config_path=tmp_path / "missing.json")

    assert gems.dig_gem("X") == [["a", "b"]]
    assert gems.dig_gem("X") == [["a", "b"]]
    assert len(calls) == 1


def test_namespace_only_preference_resolves_unique_cluster(monkeypatch, tmp_path):
    first = DetectedProvider(
        "kubernetes", "cluster:a", ProviderState.CONFIGURED, (), {}
    )
    second = DetectedProvider(
        "kubernetes", "cluster:b", ProviderState.CONFIGURED, (), {}
    )

    class ClusterProvider(FakeProvider):
        def find_gem(self, name, *, criteria=None):
            assert criteria == {"location": {"namespace": "Y"}}
            if self.record.instance_id == "cluster:a":
                return ()
            return (GemReference(name, "kubernetes", "cluster:b"),)

    instances = {
        item.instance_id: ClusterProvider(item, f"value-from-{item.instance_id}")
        for item in (first, second)
    }
    monkeypatch.setattr(
        GemProvider,
        "create",
        classmethod(lambda cls, item: instances[item.instance_id]),
    )
    gems = HiddenGems(
        providers=(first, second),
        config_path=tmp_path / "missing.json",
        preferences={"X": {"location": {"namespace": "Y"}}},
    )

    assert gems.dig_gem("X") == ["value-from-cluster:b"]


def test_kubeconfig_environment_and_default_are_both_candidates(monkeypatch, tmp_path):
    kubernetes = importlib.import_module("hiddengems.gems.k8s_provider")
    home = tmp_path / "home"
    default = home / ".kube" / "config"
    default.parent.mkdir(parents=True)

    def write_config(path, context):
        path.write_text(
            f"current-context: {context}\n"
            f"contexts:\n  - name: {context}\n"
            f"    context:\n      cluster: {context}\n      user: {context}\n"
        )

    write_config(default, "default")
    environment = tmp_path / "env-kubeconfig"
    write_config(environment, "env")
    configured = tmp_path / "configured"
    configured.mkdir()
    secondary = configured / "secondary.yaml"
    test = configured / "test.yaml"
    write_config(secondary, "secondary")
    write_config(test, "test")
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("KUBECONFIG", str(environment))

    paths, warnings = kubernetes.KubernetesProvider._paths(None, include_local=True)

    assert [path for path, _ in paths] == [environment, default]
    assert warnings == ()
    records = kubernetes.KubernetesProvider.detect(
        clusters={
            "abc": {},
            "secondary": {"kubeconfig_paths": [str(secondary)]},
            "test": {"kubeconfig_paths": [str(test)]},
        }
    )
    assert len(records) == 4
    by_path = {record.settings["kubeconfig"]: record for record in records}
    assert set(by_path) == {environment, default, secondary, test}
    assert by_path[environment].settings["clusters"] == ("abc", "local")
    assert by_path[default].settings["clusters"] == ("abc", "local")
    hinted = kubernetes.KubernetesProvider.detect(
        preferences=[{"kubeconfig_paths": [str(secondary)]}]
    )
    assert secondary in {item.settings["kubeconfig"] for item in hinted}


def test_kubernetes_keeps_confirmed_match_when_other_namespace_is_unchecked(
    monkeypatch, tmp_path
):
    kubernetes = importlib.import_module("hiddengems.gems.k8s_provider")
    provider = kubernetes.KubernetesProvider(
        instance_id="kubernetes:fixture",
        kubeconfig=tmp_path / "unused",
        context="fixture",
        namespace_candidates=("Y", "default"),
    )
    status = 404

    def read_secret(*, name, namespace):
        if namespace == "Y":
            return SimpleNamespace(
                data={"X": "ZmFrZQ=="},
                metadata=SimpleNamespace(creation_timestamp=None),
            )
        raise kubernetes.ApiException(status=status)

    monkeypatch.setattr(
        provider, "_api", lambda: SimpleNamespace(read_namespaced_secret=read_secret)
    )
    assert [item.location["namespace"] for item in provider.find_gem("X")] == ["Y"]

    status = 403
    with pytest.raises(ProviderLookupError) as incomplete:
        provider.find_gem("X")
    assert [item.location["namespace"] for item in incomplete.value.matches] == ["Y"]


def test_sdk_vault_preference_filters_duplicate_titles(monkeypatch):
    onepassword = importlib.import_module("hiddengems.gems.onepassword_provider")
    provider = onepassword.OnePasswordProvider(
        instance_id="onepassword:account",
        sdk_installed=True,
        account_name="account",
    )

    class Vaults:
        async def list(self):
            return [SimpleNamespace(id="first"), SimpleNamespace(id="selected")]

    class Items:
        async def list(self, vault_id):
            return [SimpleNamespace(id=vault_id, title="X")]

    async def client():
        return SimpleNamespace(vaults=Vaults(), items=Items())

    monkeypatch.setattr(provider, "_sdk_client", client)
    matches = provider.find_gem("X", criteria={"vault_id": "selected"})
    assert [item.location["vault_id"] for item in matches] == ["selected"]


def test_onepassword_preserves_match_when_another_account_is_unchecked(monkeypatch):
    onepassword = importlib.import_module("hiddengems.gems.onepassword_provider")
    provider = onepassword.OnePasswordProvider(
        instance_id="onepassword:local", cli="/unused/op", sdk_installed=False
    )
    monkeypatch.setattr(provider, "_accounts", lambda wanted=None: ("a", "b"))

    def list_items(*args):
        if args[-1] == "a":
            return [{"title": "X", "id": "item", "vault": {"id": "vault"}}]
        raise ProviderLookupError("account b unavailable", "Enable CLI integration")

    monkeypatch.setattr(provider, "_run_cli", list_items)
    with pytest.raises(ProviderLookupError) as incomplete:
        provider.find_gem("X")
    assert [item.location["account"] for item in incomplete.value.matches] == ["a"]


def test_onepassword_unknown_cli_source_is_explicit(monkeypatch):
    onepassword = importlib.import_module("hiddengems.gems.onepassword_provider")
    monkeypatch.setattr(onepassword.shutil, "which", lambda command: None)
    monkeypatch.setattr(onepassword.OnePasswordProvider, "_known_cli_paths", lambda: ())
    monkeypatch.setattr(onepassword, "find_spec", lambda package: None)

    assert onepassword.OnePasswordProvider._find_cli(None) == (
        None,
        onepassword.EvidenceSource.UNKNOWN,
    )
    assert onepassword.OnePasswordProvider.detect() == ()


def test_bounded_scan_reports_incomplete_but_explicit_path_is_checked(
    monkeypatch, tmp_path
):
    kubernetes = importlib.import_module("hiddengems.gems.k8s_provider")
    home = tmp_path / "home"
    directory = home / ".kube"
    directory.mkdir(parents=True)
    candidate = directory / "large.yaml"
    candidate.write_text(
        "current-context: fixture\ncontexts:\n"
        "  - name: fixture\n    context:\n"
        "      cluster: fixture\n      user: fixture\n"
    )
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.delenv("KUBECONFIG", raising=False)
    monkeypatch.setattr(kubernetes.KubernetesProvider, "_MAX_AUTO_CONFIG_BYTES", 8)

    limited = kubernetes.KubernetesProvider.detect()
    assert any(item.settings.get("scan_issue") for item in limited)
    gems = HiddenGems(providers=limited, config_path=tmp_path / "missing.json")
    result = gems.inspect_gem("X")
    assert result.issues
    with pytest.raises(IncompleteGemLookupError):
        gems.dig_gem("X")

    explicit = kubernetes.KubernetesProvider.detect(
        clusters={
            "chosen": {"kubeconfig_paths": [str(candidate)]},
        }
    )
    assert any(item.settings.get("kubeconfig") == candidate for item in explicit)
    assert not any(item.settings.get("scan_issue") for item in explicit)

    monkeypatch.setattr(kubernetes.KubernetesProvider, "_MAX_AUTO_CONFIG_BYTES", 1024)
    monkeypatch.setattr(kubernetes.KubernetesProvider, "_MAX_DIRECTORY_ENTRIES", 1)
    (directory / "second.yaml").write_text("not-a-kubeconfig: true\n")
    truncated = kubernetes.KubernetesProvider.detect()
    assert any(item.settings.get("scan_issue") for item in truncated)

    def missing_secret(**_):
        raise kubernetes.ApiException(status=404)

    monkeypatch.setattr(
        kubernetes.KubernetesProvider,
        "_api",
        lambda self: SimpleNamespace(read_namespaced_secret=missing_secret),
    )
    truncated_gems = HiddenGems(
        providers=truncated, config_path=tmp_path / "missing.json"
    )
    assert truncated_gems.inspect_gem("X").issues
    with pytest.raises(IncompleteGemLookupError):
        truncated_gems.dig_gem("X")
