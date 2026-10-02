"""Kubernetes Secret provider with local detection
and bounded lookup,  K8S prover allow caller store secrete
in k8s clusters, in simple form without complex routing or multiply kubeconfig
that single get, with more complex routing preference caller can specify more advanced
routing decision, see examples.

Mustafa Bayramov mbayramov@cisco.com / spyroot@gmail.com
"""

from __future__ import annotations

import base64
import os
from collections.abc import Mapping
from dataclasses import dataclass, field
from itertools import islice
from pathlib import Path
from typing import Any, ClassVar, TypeAlias

import yaml
from kubernetes import client, config
from kubernetes.client.exceptions import ApiException

from hiddengems.abstract_provider import AbstractGemProvider
from hiddengems.abstraction import (
    DetectedProvider,
    DetectionEvidence,
    EvidenceSource,
    Gem,
    GemReference,
    ProviderLookupError,
    ProviderNotApplicable,
    ProviderState,
)
from hiddengems.gems.types import PathValue

_KubeconfigCandidate: TypeAlias = tuple[str | Path, EvidenceSource]
_KubeconfigPath: TypeAlias = tuple[Path, EvidenceSource]
_KubeconfigWarning: TypeAlias = tuple[Path, str]
_KubeconfigPathResult: TypeAlias = tuple[
    tuple[_KubeconfigPath, ...],
    tuple[_KubeconfigWarning, ...],
]
_ContextCandidate: TypeAlias = tuple[
    str | None,
    ProviderState,
    str | None,
    tuple[str, ...],
]


@dataclass
class _KubeconfigRecord:
    """Merged non-secret observations for one kubeconfig context."""

    state: ProviderState
    cluster_name: str | None
    aliases: list[str] = field(default_factory=list)
    evidence: list[DetectionEvidence] = field(default_factory=list)
    namespace_candidates: list[str] = field(default_factory=list)


class KubernetesProvider(AbstractGemProvider):
    """Read one selected cluster/context and namespace; never scan all clusters."""

    name: ClassVar[str] = "kubernetes"
    _MAX_DIRECTORY_ENTRIES: ClassVar[int] = 64
    _MAX_AUTO_CONFIG_BYTES: ClassVar[int] = 2 * 1024 * 1024

    def __init__(self, **settings: Any) -> None:
        self.instance_id = settings["instance_id"]
        self.clusters = tuple(settings.get("clusters", ("local",)))
        self.cluster_name = settings.get("cluster_name")
        self.scan_issue = settings.get("scan_issue")
        self.kubeconfig = (
            Path(settings["kubeconfig"]) if settings.get("kubeconfig") else None
        )
        self.context = settings.get("context")
        self.namespace = settings.get("namespace", "default")
        self.namespace_candidates = tuple(
            settings.get("namespace_candidates", (self.namespace, "default"))
        )

    @classmethod
    def _directory_kubeconfigs(
            cls,
            directory: Path,
    ) -> tuple[tuple[_KubeconfigPath, ...], tuple[_KubeconfigWarning, ...]]:
        """Collect bounded regular files from one conventional directory."""
        if directory.is_symlink() or os.path.ismount(directory):
            return (), (
                (directory, "Automatic kubeconfig directory is a link or mount"),
            )
        try:
            with os.scandir(directory) as entries:
                bounded = list(islice(entries, cls._MAX_DIRECTORY_ENTRIES + 1))
        except FileNotFoundError:
            return (), ()
        except OSError:
            return (), (
                (directory, "Automatic kubeconfig directory could not be read"),
            )

        warnings: list[_KubeconfigWarning] = []
        if len(bounded) > cls._MAX_DIRECTORY_ENTRIES:
            warnings.append(
                (directory, "Automatic kubeconfig directory entry limit reached")
            )
        candidates: list[_KubeconfigPath] = []
        for child in sorted(
                bounded[: cls._MAX_DIRECTORY_ENTRIES],
                key=lambda item: item.name,
        ):
            try:
                if child.is_file(follow_symlinks=False):
                    candidates.append((Path(child.path), EvidenceSource.KNOWN_PATH))
            except OSError:
                warnings.append(
                    (Path(child.path), "Automatic kubeconfig entry could not be read")
                )
        return tuple(candidates), tuple(warnings)

    @classmethod
    def candidates_kubeconfigs(
            cls,
            configured: Any,
            *,
            include_local: bool,
    ) -> tuple[tuple[_KubeconfigCandidate, ...], tuple[_KubeconfigWarning, ...]]:
        """Collect ordered kubeconfig candidates and discovery warnings.

        :param configured:
            An explicitly configured path, an iterable of paths, or ``None``.
            Explicit entries use ``EvidenceSource.CONFIG``.
        :type configured: str | Path | Iterable[str | Path] | None
        :param include_local:
            Whether to include ``KUBECONFIG``, ``~/.kube/config``, and files
            discovered in local ``.kube`` directories.
        :type include_local: bool
        :return:
            The ordered ``(path, evidence_source)`` candidates and non-fatal
            ``(directory, message)`` warnings.
        :rtype:
            tuple[
                tuple[tuple[str | Path, EvidenceSource], ...],
                tuple[tuple[Path, str], ...],
            ]
        """
        candidates: list[_KubeconfigCandidate] = []
        warnings: list[_KubeconfigWarning] = []
        if configured is not None:
            values = [configured] if isinstance(configured, (str, Path)) else configured
            candidates.extend((value, EvidenceSource.CONFIG) for value in values)
        if include_local:
            env_paths = os.environ.get("KUBECONFIG", "")
            candidates.extend(
                (value, EvidenceSource.ENVIRONMENT)
                for value in env_paths.split(os.pathsep)
                if value
            )
            candidates.append(("~/.kube/config", EvidenceSource.KNOWN_PATH))
            for directory in (Path.home() / ".kube", Path.cwd() / ".kube"):
                directory_candidates, directory_warnings = cls._directory_kubeconfigs(
                    directory
                )
                candidates.extend(directory_candidates)
                warnings.extend(directory_warnings)
        return tuple(candidates), tuple(warnings)

    @classmethod
    def _paths(cls, configured: Any, *, include_local: bool) -> _KubeconfigPathResult:
        """Canonicalize readable candidates and retain bounded-scan warnings.

        :param configured:  An explicitly configured path, an iterable of paths, or ``None``.
        :type configured: Any
        :param include_local: Whether to include environment and known local kubeconfig candidates.
        :return: The unique readable ``(path, evidence_source)`` pairs and the accumulated non-fatal ``(path, message)`` warnings.
        """
        candidates, candidate_warnings = cls.candidates_kubeconfigs(
            configured,
            include_local=include_local,
        )
        paths: list[_KubeconfigPath] = []
        warnings: list[_KubeconfigWarning] = list(candidate_warnings)
        seen: set[Path] = set()
        for candidate, source in candidates:
            try:
                path = Path(candidate).expanduser().resolve()
                if not path.is_file() or path in seen:
                    continue
                if not os.access(path, os.R_OK):
                    warnings.append((path, "Kubeconfig file is not readable"))
                    continue
                if source == EvidenceSource.KNOWN_PATH and (
                        path.stat().st_size > cls._MAX_AUTO_CONFIG_BYTES
                ):
                    warnings.append(
                        (path, "Automatic kubeconfig file size limit reached")
                    )
                    continue
            except OSError:
                warnings.append(
                    (Path(candidate).expanduser(), "Kubeconfig path could not be read")
                )
                continue
            seen.add(path)
            paths.append((path, source))
        return tuple(paths), tuple(warnings)

    @classmethod
    def candidate_clusters(
            cls,
            options: Mapping[str, Any],
    ) -> tuple[tuple[str, Mapping[str, Any]], ...]:
        """Collect ordered cluster targets without contacting a cluster.

        :param options:
            Kubernetes provider settings containing optional ``clusters`` and
            preference-derived ``kubeconfig_paths``.
        :type options: Mapping[str, Any]
        :return:
            Ordered ``(alias, settings)`` targets, including the bounded local
            target when it is not explicitly configured.
        :rtype: tuple[tuple[str, Mapping[str, Any]], ...]
        """
        clusters = options.get("clusters") or {"local": options}
        if not isinstance(clusters, Mapping):
            raise TypeError("Kubernetes clusters must be an object")
        targets: list[tuple[str, Mapping[str, Any]]] = []
        for alias, settings in clusters.items():
            if not isinstance(settings, Mapping):
                raise TypeError("Kubernetes cluster settings must be an object")
            targets.append((alias, settings))
        if "local" not in clusters:
            targets.append(("local", {}))
        for index, preference in enumerate(options.get("preferences", ())):
            if not isinstance(preference, Mapping):
                continue
            paths = preference.get("kubeconfig_paths")
            if paths:
                targets.append((f"preference:{index}", {"kubeconfig_paths": paths}))
        return tuple(targets)

    @staticmethod
    def candidate_namespaces(
            settings: Mapping[str, Any],
            context: Mapping[str, Any] | None,
    ) -> tuple[str, ...]:
        """Collect ordered namespace candidates for one context.

        :param settings:
            Cluster settings that may contain an explicit ``namespace``.
        :type settings: Mapping[str, Any]
        :param context:
            The selected kubeconfig context, or ``None`` when unresolved.
        :type context: Mapping[str, Any] | None
        :return:
            Deduplicated explicit, context, and ``default`` namespaces.
        :rtype: tuple[str, ...]
        """
        return tuple(
            dict.fromkeys(
                namespace
                for namespace in (
                    settings.get("namespace"),
                    context.get("namespace") if context else None,
                    "default",
                )
                if namespace
            )
        )

    @staticmethod
    def _kubeconfig_document(
            path: Path,
            parsed: dict[Path, dict[str, Any] | None],
    ) -> dict[str, Any] | None:
        """Read and cache one kubeconfig-shaped YAML document."""
        if path not in parsed:
            try:
                document = yaml.safe_load(path.read_text()) or {}
            except (OSError, UnicodeError, yaml.YAMLError):
                document = None
            parsed[path] = document if isinstance(document, dict) else None
        return parsed[path]

    @classmethod
    def _context_candidate(
            cls,
            document: Mapping[str, Any],
            settings: Mapping[str, Any],
    ) -> _ContextCandidate | None:
        """Resolve one selected context and its namespace candidates."""
        if not ("contexts" in document or "current-context" in document):
            return None
        items = document.get("contexts", [])
        if not isinstance(items, list):
            return None
        contexts = {
            item["name"]: item.get("context", {})
            for item in items
            if isinstance(item, dict)
               and "name" in item
               and isinstance(item.get("context"), dict)
        }
        selected = settings.get("context") or document.get("current-context")
        context = contexts.get(selected)
        if context is None:
            state = ProviderState.DETECTED
            cluster_name = None
        else:
            state = ProviderState.CONFIGURED
            cluster_name = context.get("cluster")
        return (
            selected,
            state,
            cluster_name,
            cls.candidate_namespaces(settings, context),
        )

    @staticmethod
    def _merge_context(
            found: dict[tuple[Path, str | None], _KubeconfigRecord],
            *,
            path: Path,
            source: EvidenceSource,
            alias: str,
            selected: str | None,
            state: ProviderState,
            cluster_name: str | None,
            namespaces: tuple[str, ...],
    ) -> None:
        """Merge one alias observation into its concrete context identity.
        :param found:
        :param path:
        :param source:
        :param alias:
        :param selected:
        :param state:
        :param cluster_name:
        :param namespaces:
        :return:
        """
        entry = found.setdefault(
            (path, selected),
            _KubeconfigRecord(state=state, cluster_name=cluster_name),
        )
        entry.aliases.append(alias)
        entry.namespace_candidates.extend(
            namespace
            for namespace in namespaces
            if namespace not in entry.namespace_candidates
        )
        entry.evidence.append(
            DetectionEvidence(
                source=source,
                description="Readable kubeconfig",
                path=path,
            )
        )

    @classmethod
    def _detected_records(
            cls,
            found: Mapping[tuple[Path, str | None], _KubeconfigRecord],
    ) -> list[DetectedProvider]:
        """Build provider records from merged kubeconfig contexts.
        :param found:
        :return:
        """
        return [
            DetectedProvider(
                provider=cls.name,
                instance_id=f"kubernetes:{path}:{context or 'unselected'}",
                state=entry.state,
                evidence=tuple(entry.evidence),
                settings={
                    "clusters": tuple(entry.aliases),
                    "cluster_name": entry.cluster_name,
                    "kubeconfig": path,
                    "context": context,
                    "namespace": entry.namespace_candidates[0],
                    "namespace_candidates": tuple(entry.namespace_candidates),
                },
            )
            for (path, context), entry in found.items()
        ]

    @classmethod
    def _warning_records(
            cls,
            scan_warnings: Mapping[PathValue, str],
            checked_paths: set[PathValue],
    ) -> tuple[DetectedProvider, ...]:
        """Represent unchecked paths without claiming the gem is absent.
        :param scan_warnings:
        :param checked_paths:
        :return:
        """
        return tuple(
            DetectedProvider(
                provider=cls.name,
                instance_id=f"kubernetes:unverified:{path}",
                state=ProviderState.DETECTED,
                evidence=(DetectionEvidence(EvidenceSource.KNOWN_PATH, reason, path),),
                settings={
                    "scan_issue": reason,
                    "clusters": ("local",),
                },
            )
            for path, reason in scan_warnings.items()
            if path not in checked_paths
        )

    @classmethod
    def detect(cls, **options: Any) -> tuple[DetectedProvider, ...]:
        """Inspect only declared/local kubeconfig files; never call a cluster.
        :param options:
        :return:
        """
        targets: tuple[tuple[str, Mapping[str, Any]], ...] = cls.candidate_clusters(
            options
        )

        found: dict[tuple[Path, str | None], _KubeconfigRecord] = {}
        scan_warnings: dict[Path, str] = {}
        local_paths: _KubeconfigPathResult | None = None
        parsed: dict[Path, dict[str, Any] | None] = {}

        for alias, settings in targets:
            include_local: bool = alias == "local" or not settings.get(
                "kubeconfig_paths"
            )
            paths: tuple[_KubeconfigPath, ...]
            warnings: tuple[_KubeconfigWarning, ...]

            if not settings.get("kubeconfig_paths"):
                if local_paths is None:
                    local_paths = cls._paths(None, include_local=True)
                paths, warnings = local_paths
            else:
                paths, warnings = cls._paths(
                    settings["kubeconfig_paths"], include_local=include_local
                )

            scan_warnings.update(warnings)
            for path, source in paths:
                document: dict[str, Any] | None = cls._kubeconfig_document(
                    path,
                    parsed,
                )
                if document is None:
                    continue
                candidate: _ContextCandidate | None = cls._context_candidate(
                    document,
                    settings,
                )

                if candidate is None:
                    continue

                selected, state, cluster_name, namespaces = candidate
                cls._merge_context(
                    found,
                    path=path,
                    source=source,
                    alias=alias,
                    selected=selected,
                    state=state,
                    cluster_name=cluster_name,
                    namespaces=namespaces,
                )

        records: list[DetectedProvider] = cls._detected_records(found)
        checked_paths: set[Path] = {path for path, _ in found}
        records.extend(
            cls._warning_records(
                scan_warnings,
                checked_paths,
            )
        )
        return tuple(records)

    def _api(self) -> client.CoreV1Api:
        if not self.context:
            raise ProviderLookupError(
                "Kubeconfig has no selected context",
                "Select a kubeconfig context or specify one for this cluster",
            )
        try:
            api_client = config.new_client_from_config(
                config_file=str(self.kubeconfig), context=self.context
            )
        except Exception as error:
            raise ProviderLookupError(
                f"Kubernetes configuration could not be loaded ({type(error).__name__})",
                "Check the selected kubeconfig and context",
            ) from error
        return client.CoreV1Api(api_client)

    def find_gem(
            self, name: str, *, criteria: Mapping[str, Any] | None = None
    ) -> tuple[GemReference, ...]:
        """Check one Secret by name and return only non-secret location data.

        :param name:
        :param criteria:
        :return:
        :raise ProviderNotApplicable
        """
        criteria = criteria or {}
        if set(criteria) - {"cluster", "kubeconfig_paths", "location", "namespace"}:
            raise ProviderNotApplicable

        if self.scan_issue:
            if criteria.get("kubeconfig_paths"):
                raise ProviderNotApplicable

            raise ProviderLookupError(
                self.scan_issue,
                "Specify the intended kubeconfig path explicitly",
            )
        if criteria.get("cluster") and criteria["cluster"] not in (
                *self.clusters,
                self.cluster_name,
        ):
            raise ProviderNotApplicable
        if criteria.get("kubeconfig_paths"):
            paths, _ = self._paths(criteria["kubeconfig_paths"], include_local=False)
            selected_paths = {path for path, _ in paths}
            if self.kubeconfig not in selected_paths:
                raise ProviderNotApplicable

        location = criteria.get("location") or {}
        if not isinstance(location, Mapping):
            raise TypeError("Kubernetes location must be an object")

        chosen_namespace = location.get("namespace", criteria.get("namespace"))
        namespaces = (
            (chosen_namespace,) if chosen_namespace else self.namespace_candidates
        )
        secret_name = location.get("secret_name", name)
        key = location.get("key")
        if secret_name != name and key is None:
            key = name

        api = self._api()
        matches: list[GemReference] = []
        failed: list[str] = []
        for namespace in namespaces:
            try:
                secret = api.read_namespaced_secret(
                    name=secret_name, namespace=namespace
                )
            except ApiException as error:
                if error.status != 404:
                    failed.append(f"{namespace} (HTTP {error.status})")
                continue
            except Exception as error:  # noqa: BLE001
                # Provider boundaries preserve third-party SDK failures as uncertainty.
                failed.append(f"{namespace} ({type(error).__name__})")
                continue
            if key is not None and key not in (secret.data or {}):
                continue
            matches.append(
                GemReference(
                    name=name,
                    provider=self.name,
                    instance_id=self.instance_id,
                    created_at=secret.metadata.creation_timestamp,
                    location={
                        "namespace": namespace,
                        "secret_name": secret_name,
                        "key": key,
                    },
                )
            )
        if failed:
            raise ProviderLookupError(
                f"Kubernetes Secret lookup could not check: {', '.join(failed)}",
                "Check cluster access and the named namespaces",
                tuple(matches),
            )
        return tuple(matches)

    def get_gem(self, reference: GemReference) -> list[Gem]:
        """Return a list containing one key or whole Secret mapping.
        :param reference:
        :return:
        """
        if reference.provider != self.name or reference.instance_id != self.instance_id:
            raise ValueError("Gem reference belongs to a different provider instance")
        location = reference.location
        if not isinstance(location, Mapping):
            raise TypeError("Kubernetes reference needs a Secret location")
        secret = self._api().read_namespaced_secret(
            name=location["secret_name"], namespace=location["namespace"]
        )
        data = secret.data or {}
        key = location.get("key")
        if key is not None:
            return [base64.b64decode(data[key], validate=True).decode("utf-8")]
        return [
            {
                name: base64.b64decode(value, validate=True).decode("utf-8")
                for name, value in data.items()
            }
        ]

    def put_gem(
            self,
            name: str,
            value: Gem,
            *,
            criteria: Mapping[str, Any] | None = None,
            dry_run: bool = False,
    ) -> GemReference:
        """Kubernetes writes need a separately specified update contract.
        :param name:
        :param value:
        :param criteria:
        :param dry_run:
        :return:
        """
        raise NotImplementedError("Kubernetes gem storage is not implemented")
