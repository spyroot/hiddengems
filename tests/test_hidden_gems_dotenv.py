"""Named-string storage in a caller-selected dotenv file, using fake secrets."""

from __future__ import annotations

import importlib
import stat
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Event
from unittest.mock import Mock

import pytest

from hiddengems.hidden_gems import AmbiguousGemError, HiddenGems


@pytest.fixture
def dotfile(monkeypatch):
    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[1]))
    return importlib.import_module("hiddengems.gems.dotenv_provider")


def file_state(path):
    content = path.read_bytes()
    metadata = path.stat()
    return content, metadata.st_mtime_ns, stat.S_IMODE(metadata.st_mode)


@pytest.mark.parametrize(
    "value",
    ["fake-api-token", "fake 'single' \"double\"\n${GEM_LITERAL} $literal", ""],
)
def test_roundtrip_preserves_literal_strings_and_never_prints_values(
    dotfile, tmp_path, monkeypatch, capsys, value
):
    monkeypatch.setenv("GEM_LITERAL", "must-not-expand")
    target = tmp_path / "selected.env"
    gems = dotfile.HiddenDotFileGem(target)

    gems.hide_gem("API_TOKEN", value)

    assert gems.dig_gem("API_TOKEN") == value
    assert stat.S_IMODE(target.stat().st_mode) == 0o600
    assert list(tmp_path.iterdir()) == [target]
    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err == ""


def test_caller_parser_can_decode_one_dotenv_value(dotfile, tmp_path):
    target = tmp_path / "selected.env"
    target.write_text(
        "LOGIN='username:test password:test'\n",
        encoding="utf-8",
    )
    parser = Mock(return_value={"username": "test", "password": "test"})
    gems = dotfile.HiddenDotFileGem(target, parser_fn=parser)

    value = gems.dig_gem("LOGIN")

    assert value == {"username": "test", "password": "test"}
    parser.assert_called_once_with("username:test password:test")


def test_falsey_parser_is_not_replaced_by_default(dotfile, tmp_path):
    target = tmp_path / "selected.env"
    target.write_text("X='fake-value'\n", encoding="utf-8")

    class FalseyParser:
        def __bool__(self):
            return False

        def __call__(self, value):
            return {"parsed": value}

    gems = dotfile.HiddenDotFileGem(target, parser_fn=FalseyParser())

    assert gems.dig_gem("X") == {"parsed": "fake-value"}


def test_parser_must_be_callable(dotfile, tmp_path):
    with pytest.raises(TypeError, match="parser_fn must be callable"):
        dotfile.HiddenDotFileGem(tmp_path / ".env", parser_fn="not-callable")


def test_provider_rejects_pathlike_values_that_resolve_to_bytes(dotfile):
    class BytesPath:
        def __fspath__(self):
            return b".env"

    with pytest.raises(TypeError, match=r"path must be a string or PathLike\[str\]"):
        dotfile.DotEnvProvider(instance_id="dotenv:invalid", path=BytesPath())


def test_provider_rejects_non_string_issue_messages(dotfile):
    with pytest.raises(TypeError, match="scan_issue must be a string"):
        dotfile.DotEnvProvider(instance_id="dotenv:invalid", scan_issue=object())


def test_missing_file_and_missing_name_raise_key_error_without_writes(
    dotfile, tmp_path
):
    target = tmp_path / "selected.env"
    gems = dotfile.HiddenDotFileGem(target)

    with pytest.raises(KeyError) as missing_file:
        gems.dig_gem("MISSING")
    assert missing_file.value.args == ("MISSING",)
    assert not target.exists()
    target.write_text("OTHER='unchanged'\n", encoding="utf-8")
    before = target.read_bytes()
    with pytest.raises(KeyError) as missing_name:
        gems.dig_gem("MISSING")
    assert missing_name.value.args == ("MISSING",)
    assert target.read_bytes() == before


def test_add_and_overwrite_preserve_other_entries_and_comments(dotfile, tmp_path):
    target = tmp_path / "selected.env"
    original = "# Keep this comment.\nOTHER='keep $literal' # Inline comment.\n"
    target.write_text(original, encoding="utf-8")
    target.chmod(0o644)
    gems = dotfile.HiddenDotFileGem(target)

    gems.hide_gem("API_TOKEN", "fake-first-token")
    gems.hide_gem("API_TOKEN", "fake-replacement-token")

    assert gems.dig_gem("API_TOKEN") == "fake-replacement-token"
    assert gems.dig_gem("OTHER") == "keep $literal"
    content = target.read_text(encoding="utf-8")
    assert content.startswith(original)
    assert content.count("API_TOKEN=") == 1
    assert stat.S_IMODE(target.stat().st_mode) == 0o600
    assert list(tmp_path.iterdir()) == [target]


@pytest.mark.parametrize("exists", [False, True])
def test_dry_run_neither_creates_nor_changes_files(
    dotfile, tmp_path, monkeypatch, exists
):
    target = tmp_path / "selected.env"
    if exists:
        target.write_text("API_TOKEN='fake-original-token'\n", encoding="utf-8")
        target.chmod(0o640)
        before = file_state(target)
    temporary = Mock(side_effect=AssertionError("dry-run attempted a write"))
    monkeypatch.setattr(dotfile, "NamedTemporaryFile", temporary)

    dotfile.HiddenDotFileGem(target).hide_gem(
        "API_TOKEN", "fake-planned-token", dry_run=True
    )

    temporary.assert_not_called()
    if exists:
        assert file_state(target) == before
    else:
        assert not target.exists()
    assert list(tmp_path.iterdir()) == ([target] if exists else [])


@pytest.mark.parametrize("name", [None, "", "BROKEN=NAME", "BROKEN\nNAME"])
def test_invalid_name_leaves_target_unchanged_and_no_temporary_files(
    dotfile, tmp_path, name
):
    target = tmp_path / "selected.env"
    target.write_text("API_TOKEN='fake-original-token'\n", encoding="utf-8")
    before = file_state(target)

    with pytest.raises(ValueError):
        dotfile.HiddenDotFileGem(target).hide_gem(name, "fake-new-token")

    assert file_state(target) == before
    assert list(tmp_path.iterdir()) == [target]


@pytest.mark.parametrize("operation", ["set_key", "replace"])
def test_write_failure_preserves_target_and_cleans_temporary_file(
    dotfile, tmp_path, monkeypatch, operation
):
    target = tmp_path / "selected.env"
    target.write_text("API_TOKEN='fake-original-token'\n", encoding="utf-8")
    before = file_state(target)
    failing_write = Mock(side_effect=OSError("fixture write failure"))
    owner = dotfile if operation == "set_key" else dotfile.os
    monkeypatch.setattr(owner, operation, failing_write)

    with pytest.raises(OSError, match="fixture write failure"):
        dotfile.HiddenDotFileGem(target).hide_gem("API_TOKEN", "fake-new-token")

    failing_write.assert_called_once()
    assert file_state(target) == before
    assert list(tmp_path.iterdir()) == [target]


def test_same_value_second_hide_is_a_true_noop(dotfile, tmp_path, monkeypatch):
    target = tmp_path / "selected.env"
    gems = dotfile.HiddenDotFileGem(target)
    gems.hide_gem("API_TOKEN", "fake-token")
    before = target.read_bytes(), target.stat()
    temporary = Mock(side_effect=AssertionError("no-op created a temporary file"))
    set_key = Mock(side_effect=AssertionError("no-op called set_key"))
    replace = Mock(side_effect=AssertionError("no-op replaced the target"))
    monkeypatch.setattr(dotfile, "NamedTemporaryFile", temporary)
    monkeypatch.setattr(dotfile, "set_key", set_key)
    monkeypatch.setattr(dotfile.os, "replace", replace)

    gems.hide_gem("API_TOKEN", "fake-token")

    after = target.read_bytes(), target.stat()
    assert after[0] == before[0]
    assert after[1].st_mtime_ns == before[1].st_mtime_ns
    assert after[1].st_ino == before[1].st_ino
    assert stat.S_IMODE(after[1].st_mode) == 0o600
    temporary.assert_not_called()
    set_key.assert_not_called()
    replace.assert_not_called()


def test_overlapping_distinct_hides_preserve_both_values(
    dotfile, tmp_path, monkeypatch
):
    target = tmp_path / "selected.env"
    first_ready, release_first, second_started, second_finished = (
        Event(),
        Event(),
        Event(),
        Event(),
    )
    original_set_key = dotfile.set_key

    def hold_first_after_snapshot(path, name, value, **kwargs):
        result = original_set_key(path, name, value, **kwargs)
        if name == "FIRST":
            first_ready.set()
            assert release_first.wait(timeout=5)
        return result

    def write_second():
        second_started.set()
        try:
            dotfile.HiddenDotFileGem(target).hide_gem("SECOND", "fake-second")
        finally:
            second_finished.set()

    monkeypatch.setattr(dotfile, "set_key", hold_first_after_snapshot)
    with ThreadPoolExecutor(max_workers=2) as writers:
        first = writers.submit(
            dotfile.HiddenDotFileGem(target).hide_gem, "FIRST", "fake-first"
        )
        try:
            assert first_ready.wait(timeout=2)
            second = writers.submit(write_second)
            assert second_started.wait(timeout=2)
            second_finished.wait(timeout=0.2)
        finally:
            release_first.set()
        first.result(timeout=2)
        second.result(timeout=2)

    gems = dotfile.HiddenDotFileGem(target)
    assert gems.dig_gem("FIRST") == "fake-first"
    assert gems.dig_gem("SECOND") == "fake-second"


def test_selected_backup_keeps_previous_contents_and_skips_dry_run_and_noop(
    dotfile, tmp_path
):
    target, backup = tmp_path / "selected.env", tmp_path / "previous.env"
    original = "# Preserve this comment.\nAPI_TOKEN='fake-old-token'\n"
    target.write_text(original, encoding="utf-8")
    gems = dotfile.HiddenDotFileGem(target, backup_filename=backup)

    gems.hide_gem("API_TOKEN", "fake-new-token", dry_run=True)
    assert target.read_text(encoding="utf-8") == original
    assert not backup.exists()
    gems.hide_gem("API_TOKEN", "fake-new-token")
    assert backup.read_text(encoding="utf-8") == original
    assert stat.S_IMODE(backup.stat().st_mode) == 0o600
    before = file_state(target), file_state(backup)
    gems.hide_gem("API_TOKEN", "fake-new-token")
    assert (file_state(target), file_state(backup)) == before

    previous = target.read_bytes()
    gems.hide_gem("API_TOKEN", "fake-third-token")
    assert backup.read_bytes() == previous
    assert gems.dig_gem("API_TOKEN") == "fake-third-token"
    assert set(tmp_path.iterdir()) == {target, backup}


def test_new_gem_file_has_no_previous_contents_to_back_up(dotfile, tmp_path):
    target, backup = tmp_path / "selected.env", tmp_path / "previous.env"
    dotfile.HiddenDotFileGem(target, backup_filename=backup).hide_gem(
        "API_TOKEN", "fake-first-token"
    )
    assert target.is_file()
    assert not backup.exists()


def test_backup_failure_preserves_original_and_cleans_staged_files(dotfile, tmp_path):
    target = tmp_path / "selected.env"
    target.write_text("API_TOKEN='fake-original-token'\n", encoding="utf-8")
    before = file_state(target)
    gems = dotfile.HiddenDotFileGem(
        target, backup_filename=tmp_path / "missing" / "previous.env"
    )
    with pytest.raises(FileNotFoundError):
        gems.hide_gem("API_TOKEN", "fake-new-token")
    assert file_state(target) == before
    assert list(tmp_path.iterdir()) == [target]


def test_backup_cannot_replace_the_selected_gem_file(dotfile, tmp_path):
    target = tmp_path / "selected.env"
    with pytest.raises(ValueError, match="different"):
        dotfile.HiddenDotFileGem(target, backup_filename=target)
    assert not target.exists()


def test_empty_config_detects_conventional_dotenv_and_resolves_key(
    dotfile, tmp_path, monkeypatch
):
    from hiddengems.gem_provider import GemProvider

    monkeypatch.chdir(tmp_path)
    (tmp_path / ".env").write_text("X='fake-value'\n", encoding="utf-8")
    config = tmp_path / "gem_provider.json"
    config.write_text("{}\n", encoding="utf-8")
    monkeypatch.setattr(GemProvider, "provider_types", (dotfile.DotEnvProvider,))

    gems = HiddenGems(config_path=config)

    assert gems.dig_gem("X") == ["fake-value"]
    assert gems.records[0].instance_id == f"dotenv:{(tmp_path / '.env').resolve()}"


def test_dotenv_provider_is_in_the_production_registry(dotfile):
    from hiddengems.gem_provider import GemProvider

    assert dotfile.DotEnvProvider in GemProvider.provider_types


def test_empty_config_finds_nested_dotenv_files(dotfile, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    nested = tmp_path / "service"
    nested.mkdir()
    target = nested / ".env.local"
    target.write_text("X='fake-nested'\n", encoding="utf-8")

    records = dotfile.DotEnvProvider.detect()

    assert [record.settings["path"] for record in records] == [target.resolve()]


def test_discovery_accepts_only_dotenv_filename_family(dotfile, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    expected = tmp_path / ".env.local"
    expected.write_text("X='fake-value'\n", encoding="utf-8")
    for name in (".envrc", "foo.env", ".environment"):
        (tmp_path / name).write_text("X='wrong-value'\n", encoding="utf-8")

    records = dotfile.DotEnvProvider.detect()

    assert [record.settings["path"] for record in records] == [expected.resolve()]


def test_equivalent_configured_paths_collapse_to_one_instance(dotfile, tmp_path):
    target = tmp_path / ".env"
    target.write_text("X='fake-value'\n", encoding="utf-8")
    nested = tmp_path / "nested"
    nested.mkdir()

    records = dotfile.DotEnvProvider.detect(
        instances=[
            {"path": target},
            {"path": nested / ".." / ".env"},
        ]
    )

    assert len(records) == 1
    assert records[0].settings["path"] == target.resolve()
    assert len(records[0].evidence) == 2


def test_same_name_in_multiple_dotenv_instances_preserves_ambiguity(
    dotfile, tmp_path, monkeypatch
):
    monkeypatch.chdir(tmp_path)
    first = tmp_path / "first.env"
    second = tmp_path / "second.env"
    first.write_text("X='fake-first'\n", encoding="utf-8")
    second.write_text("X='fake-second'\n", encoding="utf-8")
    records = dotfile.DotEnvProvider.detect(
        instances=[{"path": first}, {"path": second}]
    )
    gems = HiddenGems(providers=records, config_path=tmp_path / "missing.json")

    result = gems.inspect_gem("X")

    assert {match.location["path"] for match in result.matches} == {
        str(first.resolve()),
        str(second.resolve()),
    }
    with pytest.raises(AmbiguousGemError):
        gems.dig_gem("X")

    preferred = HiddenGems(
        providers=records,
        config_path=tmp_path / "missing.json",
        preferences={
            "X": {
                "provider": "dotenv",
                "instance_id": f"dotenv:{second.resolve()}",
            }
        },
    )
    assert preferred.dig_gem("X") == ["fake-second"]


def test_dotenv_detection_does_not_read_values(dotfile, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    target = tmp_path / "selected.env"
    (tmp_path / ".env").write_text("X='wrong-default'\n", encoding="utf-8")
    target.write_text("X='fake-value'\n", encoding="utf-8")
    read = Mock(side_effect=AssertionError("detection read dotenv values"))
    monkeypatch.setattr(dotfile, "dotenv_values", read)

    records = dotfile.DotEnvProvider.detect(path=target)

    assert [record.settings["path"] for record in records] == [target.resolve()]
    read.assert_not_called()


def test_dotenv_provider_put_returns_only_reference_metadata(dotfile, tmp_path):
    target = tmp_path / "selected.env"
    target.write_text("OTHER='keep'\n", encoding="utf-8")
    record = dotfile.DotEnvProvider.detect(path=target)[0]
    provider = dotfile.DotEnvProvider(
        **record.settings,
        instance_id=record.instance_id,
    )

    reference = provider.put_gem("X", "fake-value")

    assert reference.name == "X"
    assert reference.location == {"path": str(target.resolve())}
    assert "fake-value" not in str(reference)
    assert provider.get_gem(reference) == ["fake-value"]


def test_provider_factory_passes_parser_only_to_get(dotfile, tmp_path):
    from hiddengems.gem_provider import GemProvider

    target = tmp_path / "selected.env"
    target.write_text("X='fake-value'\n", encoding="utf-8")
    record = dotfile.DotEnvProvider.detect(path=target)[0]
    parser = Mock(return_value={"parsed": "fake-value"})
    provider = GemProvider.create(record, parser_fn=parser)

    references = provider.find_gem("X")

    parser.assert_not_called()
    assert provider.get_gem(references[0]) == [{"parsed": "fake-value"}]
    parser.assert_called_once_with("fake-value")


def test_bounded_dotenv_scan_reports_unchecked_provider(dotfile, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "first").mkdir()
    (tmp_path / "second").mkdir()
    monkeypatch.setattr(dotfile.DotEnvProvider, "_MAX_SCAN_ENTRIES", 1)

    records = dotfile.DotEnvProvider.detect()

    assert len(records) == 1
    assert records[0].settings["scan_issue"] == "Dotenv discovery entry limit reached"
    gems = HiddenGems(providers=records, config_path=tmp_path / "missing.json")
    result = gems.inspect_gem("X")
    assert result.matches == ()
    assert [issue.reason for issue in result.issues] == [
        "Dotenv discovery entry limit reached"
    ]


def test_visible_but_unreadable_dotenv_is_reported_as_uncertain(
    dotfile, tmp_path, monkeypatch
):
    target = tmp_path / "selected.env"
    target.write_text("X='fake-value'\n", encoding="utf-8")
    monkeypatch.setattr(dotfile.os, "access", lambda path, mode: False)

    record = dotfile.DotEnvProvider.detect(path=target)[0]

    assert record.state.value == "detected"
    assert record.settings["access_issue"] == "Dotenv file is not readable"
    provider = dotfile.DotEnvProvider(
        **record.settings,
        instance_id=record.instance_id,
    )
    with pytest.raises(dotfile.ProviderLookupError, match="not readable"):
        provider.find_gem("X")
