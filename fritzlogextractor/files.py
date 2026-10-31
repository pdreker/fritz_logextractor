"""Files the tool writes: names derived from config values, private atomic writes."""

from __future__ import annotations

import os
import re
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from pathlib import Path

__all__ = ["safe_file_name", "write_private"]

_UNSAFE_RE = re.compile(r"[^A-Za-z0-9._-]")


def safe_file_name(text: str) -> str:
    """``text`` with every character that could leave the directory replaced."""
    name = _UNSAFE_RE.sub("_", text)
    return name if name.strip(".") else "device"


def write_private(path: Path, data: bytes) -> None:
    """Replace ``path`` atomically with ``data``, readable by the owner only."""
    partial = path.with_suffix(".tmp")
    fd = os.open(partial, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "wb") as f:
        # The mode of open() applies only to a new file, not to one left behind.
        os.fchmod(f.fileno(), 0o600)
        f.write(data)
        f.flush()
        os.fsync(f.fileno())
    partial.replace(path)


# Copyright 2026 Patrick Dreker <patrick@dreker.de>
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#   http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
