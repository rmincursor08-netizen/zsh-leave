import logging
import os
from pathlib import Path

from django.conf import settings
from django.core.exceptions import SuspiciousFileOperation
from django.core.files import File
from django.core.files.storage import FileSystemStorage
from django.utils.deconstruct import deconstructible

logger = logging.getLogger(__name__)


@deconstructible
class PrivateAttachmentStorage(FileSystemStorage):
    def __init__(self):
        super().__init__(
            location=None,
            base_url=None,
            file_permissions_mode=0o600,
            directory_permissions_mode=0o700,
        )

    @property
    def location(self):
        return str(settings.ZSH_ATTACHMENT_ROOT)

    @property
    def base_url(self):
        return None

    def path(self, name):
        candidate = Path(super().path(name))
        root_path = Path(os.path.abspath(self.location))
        if root_path.is_symlink():
            raise SuspiciousFileOperation("Attachment storage root cannot be a symlink.")
        root = root_path.resolve(strict=False)
        try:
            relative = candidate.relative_to(root_path)
        except ValueError as exc:
            raise SuspiciousFileOperation("Attachment path escapes private storage.") from exc

        current = root
        for component in relative.parts:
            current /= component
            if current.is_symlink():
                raise SuspiciousFileOperation("Symlinks are not allowed in attachment storage.")
        resolved = candidate.resolve(strict=False)
        if resolved != root and root not in resolved.parents:
            raise SuspiciousFileOperation("Attachment path escapes private storage.")
        return str(candidate)

    def _open(self, name, mode="rb"):
        return File(open(self.path(name), mode))

    def _save(self, name, content):
        root = self.location
        if os.path.islink(root):
            raise SuspiciousFileOperation("Attachment storage root cannot be a symlink.")
        os.makedirs(root, mode=0o700, exist_ok=True)
        self._ensure_private_permissions(root, 0o700)
        self.path(name)
        stored_name = super()._save(name, content)
        try:
            file_path = self.path(stored_name)
            directory = file_path.rsplit("/", 1)[0]
            while directory.startswith(root + "/"):
                self._ensure_private_permissions(directory, 0o700)
                directory = directory.rsplit("/", 1)[0]
            self._ensure_private_permissions(file_path, 0o600)
        except (OSError, SuspiciousFileOperation):
            try:
                super().delete(stored_name)
            except (OSError, SuspiciousFileOperation):
                logger.error("Failed to clean up an attachment after permissions failed.")
            raise
        return stored_name

    @staticmethod
    def _ensure_private_permissions(path, mode):
        if os.path.islink(path):
            raise SuspiciousFileOperation("Symlinks are not allowed in attachment storage.")
        os.chmod(path, mode)
