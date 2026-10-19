"""
Stockage des artefacts du modèle — abstraction configurable.

Pourquoi une abstraction ?
--------------------------
La logique métier (entraînement, scoring, décision) ne doit pas dépendre de
l'endroit où le fichier du modèle est rangé. Plusieurs instances applicatives
peuvent partager le même artefact :

* ``file://chemin/absolu`` (défaut) : stockage local au processus ;
* ``s3://bucket/prefix`` : stockage partagé (nécessite le paquet facultatif
  ``boto3``) — indispensable derrière un load-balancer multi-instances.

Chaque implémentation fournit aussi un **verrou d'entraînement** : deux
instances ne peuvent pas réentraîner le modèle en même temps, et un verrou
abandonné expire au bout de ``ML_TRAINING_LOCK_TTL_SECONDS`` (pas de blocage
définitif si un processus meurt en cours d'entraînement).
"""

from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Protocol

from app.config import settings

ARTIFACT_NAME = "isolation_forest.joblib"
LOCK_NAME = "training.lock"

# Format du contenu sérialisé : à incrémenter si la structure du bundle change.
SUPPORTED_ARTIFACT_FORMAT = 2


class ArtifactStoreError(RuntimeError):
    """Erreur de stockage (source non configurée, dépendance manquante…)."""


class ArtifactStore(Protocol):
    """Contrat minimal d'un stockage d'artefacts."""

    def describe(self) -> str:
        """Description lisible (affichée dans l'interface « Modèle & données »)."""

    def read(self) -> bytes | None:
        """Contenu de l'artefact, ou None s'il n'existe pas encore."""

    def write(self, payload: bytes) -> None:
        """Écrit l'artefact de manière atomique."""

    def last_write_age_seconds(self) -> float | None:
        """Âge du dernier artefact écrit (None si aucun)."""

    def acquire_training_lock(self, ttl_seconds: int, holder: str) -> bool:
        """Tente de réserver l'entraînement ; False si un autre l'a déjà pris."""

    def release_training_lock(self) -> None:
        """Libère le verrou d'entraînement (sans erreur s'il est absent)."""


# ---------------------------------------------------------------------------
# Stockage local (défaut)
# ---------------------------------------------------------------------------
class LocalArtifactStore:
    """Artefact et verrou stockés dans un dossier local."""

    def __init__(self, directory: Path | str) -> None:
        self.directory = Path(directory)
        self.artifact_path = self.directory / ARTIFACT_NAME
        self.lock_path = self.directory / LOCK_NAME

    def describe(self) -> str:
        return f"fichier local ({self.directory})"

    def read(self) -> bytes | None:
        if not self.artifact_path.exists():
            return None
        return self.artifact_path.read_bytes()

    def write(self, payload: bytes) -> None:
        self.directory.mkdir(parents=True, exist_ok=True)
        # Écriture atomique : un lecteur concurrent voit soit l'ancien fichier,
        # soit le nouveau, jamais un fichier partiel.
        temporary = self.artifact_path.with_suffix(".tmp")
        temporary.write_bytes(payload)
        os.replace(temporary, self.artifact_path)

    def last_write_age_seconds(self) -> float | None:
        if not self.artifact_path.exists():
            return None
        return max(0.0, time.time() - self.artifact_path.stat().st_mtime)

    def acquire_training_lock(self, ttl_seconds: int, holder: str) -> bool:
        self.directory.mkdir(parents=True, exist_ok=True)
        for attempt in (1, 2):
            try:
                descriptor = os.open(self.lock_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            except FileExistsError:
                # Verrou abandonné (processus mort) : il expire après le TTL.
                try:
                    age = time.time() - self.lock_path.stat().st_mtime
                except FileNotFoundError:
                    continue
                if age > ttl_seconds and attempt == 1:
                    try:
                        self.lock_path.unlink()
                    except FileNotFoundError:
                        pass
                    continue
                return False
            with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
                json.dump({"holder": holder, "acquired_at": time.time()}, handle)
            return True
        return False

    def release_training_lock(self) -> None:
        try:
            self.lock_path.unlink()
        except FileNotFoundError:
            pass


# ---------------------------------------------------------------------------
# Stockage S3 (multi-instances) — dépendance facultative
# ---------------------------------------------------------------------------
class S3ArtifactStore:
    """Artefact partagé sur S3 (ou tout service compatible)."""

    def __init__(self, uri: str) -> None:
        without_scheme = uri[len("s3://") :]
        bucket, _, prefix = without_scheme.partition("/")
        if not bucket:
            raise ArtifactStoreError(f"URI S3 invalide : {uri!r} (attendu s3://bucket/prefixe).")
        self.bucket = bucket
        self.prefix = prefix.strip("/")
        self._client = None

    # -- Client boto3 chargé à la demande ---------------------------------
    @property
    def client(self):
        if self._client is None:
            try:
                import boto3  # type: ignore[import-not-found]
            except ImportError as error:  # pragma: no cover - dépendance facultative
                raise ArtifactStoreError(
                    "MODEL_STORE_URI pointe vers S3 mais le paquet « boto3 » n'est pas "
                    "installé. Installez-le avec : pip install boto3"
                ) from error
            self._client = boto3.client("s3")
        return self._client

    def _key(self, name: str) -> str:
        return f"{self.prefix}/{name}" if self.prefix else name

    def describe(self) -> str:
        return f"S3 (s3://{self.bucket}/{self.prefix})"

    def read(self) -> bytes | None:
        from botocore.exceptions import ClientError  # type: ignore[import-not-found]

        try:
            response = self.client.get_object(Bucket=self.bucket, Key=self._key(ARTIFACT_NAME))
        except ClientError as error:
            if error.response.get("Error", {}).get("Code") in {"NoSuchKey", "404", "NotFound"}:
                return None
            raise ArtifactStoreError(f"Lecture S3 impossible : {error}") from error
        return response["Body"].read()

    def write(self, payload: bytes) -> None:
        self.client.put_object(Bucket=self.bucket, Key=self._key(ARTIFACT_NAME), Body=payload)

    def last_write_age_seconds(self) -> float | None:
        from botocore.exceptions import ClientError  # type: ignore[import-not-found]

        try:
            response = self.client.head_object(Bucket=self.bucket, Key=self._key(ARTIFACT_NAME))
        except ClientError:
            return None
        modified = response.get("LastModified")
        if modified is None:
            return None
        return max(0.0, time.time() - modified.timestamp())

    def acquire_training_lock(self, ttl_seconds: int, holder: str) -> bool:
        from botocore.exceptions import ClientError  # type: ignore[import-not-found]

        key = self._key(LOCK_NAME)
        body = json.dumps({"holder": holder, "acquired_at": time.time()}).encode("utf-8")
        try:
            # Écriture conditionnelle : échoue si le verrou existe déjà.
            self.client.put_object(Bucket=self.bucket, Key=key, Body=body, IfNoneMatch="*")
            return True
        except ClientError as error:
            code = error.response.get("Error", {}).get("Code", "")
            if code not in {"PreconditionFailed", "412", "ConditionalRequestConflict"}:
                raise ArtifactStoreError(f"Verrou S3 impossible : {error}") from error
        # Verrou existant : on vérifie qu'il n'a pas expiré (processus mort).
        age = self.last_write_age_seconds()  # artefact, pas le verrou
        try:
            response = self.client.head_object(Bucket=self.bucket, Key=key)
            lock_age = time.time() - response["LastModified"].timestamp()
        except ClientError:
            lock_age = 0.0
        if age is not None and lock_age > ttl_seconds:
            self.client.delete_object(Bucket=self.bucket, Key=key)
            try:
                self.client.put_object(Bucket=self.bucket, Key=key, Body=body, IfNoneMatch="*")
                return True
            except ClientError:
                return False
        return False

    def release_training_lock(self) -> None:
        try:
            self.client.delete_object(Bucket=self.bucket, Key=self._key(LOCK_NAME))
        except Exception:  # pragma: no cover - libération au mieux
            pass


# ---------------------------------------------------------------------------
# Fabrique
# ---------------------------------------------------------------------------
def build_store(uri: str | None = None) -> ArtifactStore:
    """Construit le stockage à partir de l'URI (défaut : ``MODEL_STORE_URI``)."""
    location = (uri if uri is not None else settings.resolved_model_store_uri).strip()
    if location.startswith("s3://"):
        return S3ArtifactStore(location)
    if location.startswith("file://"):
        location = location[len("file://") :]
    if not location:
        raise ArtifactStoreError("Emplacement d'artefact vide (voir MODEL_STORE_URI).")
    return LocalArtifactStore(location)


__all__ = [
    "ARTIFACT_NAME",
    "ArtifactStore",
    "ArtifactStoreError",
    "LocalArtifactStore",
    "S3ArtifactStore",
    "SUPPORTED_ARTIFACT_FORMAT",
    "build_store",
]
