"""Detection of physically attached cards and drives.

The point of this module is that a person with a failing SD card should not have
to know what a disk image is. They plug the card in, the app notices it, and the
only decision left is which card to look at.

Enumeration is per-platform because there is no portable way to ask an operating
system what is plugged into it: macOS answers through `diskutil`, Linux through
`lsblk`, Windows through WMI. All three are shelled out to rather than reached
through a binding, because every one of those bindings is a compiled dependency
that would have to build on the user's machine, and the parsing here is small.

Two design points worth stating. First, detection never opens a device for
writing and never mounts anything: a failing card gets read once, or not at all.
Second, an unreadable device is reported as a first-class result with the reason
attached rather than being filtered out of the list, because "your card is there
but this needs administrator rights" is the single most useful thing the app can
say, and silently hiding the card would leave the user staring at an empty
screen wondering whether the reader is broken.
"""

from __future__ import annotations

import json
import logging
import os
import platform
import plistlib
import re
import shutil
import subprocess
from dataclasses import asdict, dataclass, field

from .image import human_bytes

logger = logging.getLogger(__name__)

COMMAND_TIMEOUT = 15.0

# Filesystem names, as each platform spells them, that this tool can parse.
SUPPORTED_FILESYSTEMS = {
    "fat32": "FAT32",
    "msdos": "FAT",
    "fat16": "FAT16",
    "fat": "FAT",
    "vfat": "FAT",
    "exfat": "exFAT",
    "windows_fat_32": "FAT32",
    "windows_fat_16": "FAT16",
    "windows_ntfs": "NTFS",
    "ntfs": "NTFS",
    "apfs": "APFS",
    "hfs+": "HFS+",
    "hfs": "HFS+",
    "ext4": "ext4",
}

PARSEABLE = {"FAT32", "FAT16", "FAT", "exFAT"}


@dataclass
class DetectedDevice:
    """One physical disk the operating system can currently see."""

    identifier: str
    path: str
    label: str
    size_bytes: int
    removable: bool
    internal: bool
    filesystems: list[str] = field(default_factory=list)
    mount_points: list[str] = field(default_factory=list)
    readable: bool = False
    reason: str = ""
    protocol: str = ""

    @property
    def size_human(self) -> str:
        return human_bytes(self.size_bytes) if self.size_bytes else "unknown size"

    @property
    def supported(self) -> bool:
        """Whether at least one filesystem on this device is one we parse.

        An unrecognised filesystem is not a refusal to look: carving works on raw
        bytes regardless. It only means the file names and folder structure will
        be missing, so the answer is honest about what the user will get back.
        """
        return not self.filesystems or any(name in PARSEABLE for name in self.filesystems)

    def to_dict(self) -> dict:
        data = asdict(self)
        data["size_human"] = self.size_human
        data["supported"] = self.supported
        data["likely_card"] = self.removable and not self.internal
        return data


def _run(command: list[str]) -> str:
    """Run a read-only system command, returning empty text on any failure."""
    if not shutil.which(command[0]):
        return ""
    try:
        result = subprocess.run(
            command,
            capture_output=True,
            timeout=COMMAND_TIMEOUT,
            check=False,
        )
    except (subprocess.SubprocessError, OSError) as error:
        logger.warning("device probe %s failed: %s", command[0], error)
        return ""
    if result.returncode != 0:
        logger.debug("%s exited %d: %s", command[0], result.returncode, result.stderr[:200])
    return result.stdout.decode("utf-8", "replace")


def _run_bytes(command: list[str]) -> bytes:
    if not shutil.which(command[0]):
        return b""
    try:
        result = subprocess.run(command, capture_output=True, timeout=COMMAND_TIMEOUT, check=False)
    except (subprocess.SubprocessError, OSError) as error:
        logger.warning("device probe %s failed: %s", command[0], error)
        return b""
    return result.stdout


def _normalise_filesystem(name: str | None) -> str | None:
    if not name:
        return None
    return SUPPORTED_FILESYSTEMS.get(name.strip().lower().replace(" ", "_"), name.strip())


def _check_readable(path: str) -> tuple[bool, str]:
    """Try one small read, so the UI knows before the user commits to a scan."""
    if not os.path.exists(path):
        return False, "the device disappeared before it could be read"
    try:
        with open(path, "rb") as handle:
            handle.read(512)
        return True, ""
    except PermissionError:
        return False, "needs administrator permission to read the raw device"
    except OSError as error:
        return False, f"the operating system refused the read: {error.strerror or error}"


# --------------------------------------------------------------------------- macOS


def _macos_devices() -> list[DetectedDevice]:
    raw = _run_bytes(["diskutil", "list", "-plist", "physical"])
    if not raw:
        return []
    try:
        listing = plistlib.loads(raw)
    except (plistlib.InvalidFileException, ValueError) as error:
        logger.warning("could not parse diskutil output: %s", error)
        return []

    devices: list[DetectedDevice] = []
    for entry in listing.get("AllDisksAndPartitions", []):
        identifier = entry.get("DeviceIdentifier")
        if not identifier:
            continue
        info = _macos_info(identifier)
        filesystems: list[str] = []
        mounts: list[str] = []
        for partition in entry.get("Partitions", []):
            name = _normalise_filesystem(partition.get("Content"))
            part_info = _macos_info(partition.get("DeviceIdentifier", ""))
            name = _normalise_filesystem(part_info.get("FilesystemType")) or name
            if name and name not in filesystems:
                filesystems.append(name)
            if part_info.get("MountPoint"):
                mounts.append(part_info["MountPoint"])

        path = f"/dev/{identifier}"
        readable, reason = _check_readable(path)
        label = (
            info.get("MediaName")
            or info.get("IORegistryEntryName")
            or entry.get("VolumeName")
            or identifier
        )
        devices.append(
            DetectedDevice(
                identifier=identifier,
                path=path,
                label=str(label).strip() or identifier,
                size_bytes=int(entry.get("Size") or info.get("TotalSize") or 0),
                removable=bool(info.get("Removable") or info.get("RemovableMediaOrExternalDevice")),
                internal=bool(info.get("Internal", True)),
                filesystems=filesystems,
                mount_points=mounts,
                readable=readable,
                reason=reason,
                protocol=str(info.get("BusProtocol") or ""),
            )
        )
    return devices


def _macos_info(identifier: str) -> dict:
    if not identifier:
        return {}
    raw = _run_bytes(["diskutil", "info", "-plist", identifier])
    if not raw:
        return {}
    try:
        return plistlib.loads(raw)
    except (plistlib.InvalidFileException, ValueError):
        return {}


# --------------------------------------------------------------------------- Linux


def _linux_devices() -> list[DetectedDevice]:
    raw = _run(
        [
            "lsblk",
            "--json",
            "--bytes",
            "--output",
            "NAME,PATH,SIZE,TYPE,RM,HOTPLUG,MODEL,VENDOR,FSTYPE,MOUNTPOINT,TRAN",
        ]
    )
    if not raw:
        return []
    try:
        listing = json.loads(raw)
    except json.JSONDecodeError as error:
        logger.warning("could not parse lsblk output: %s", error)
        return []

    devices: list[DetectedDevice] = []
    for entry in listing.get("blockdevices", []):
        if entry.get("type") != "disk":
            continue
        filesystems: list[str] = []
        mounts: list[str] = []
        for child in [entry, *entry.get("children", [])]:
            name = _normalise_filesystem(child.get("fstype"))
            if name and name not in filesystems:
                filesystems.append(name)
            if child.get("mountpoint"):
                mounts.append(child["mountpoint"])

        path = entry.get("path") or f"/dev/{entry.get('name')}"
        readable, reason = _check_readable(path)
        removable = bool(entry.get("rm") or entry.get("hotplug"))
        label = " ".join(
            part for part in [entry.get("vendor"), entry.get("model")] if part
        ).strip()
        devices.append(
            DetectedDevice(
                identifier=str(entry.get("name")),
                path=path,
                label=label or str(entry.get("name")),
                size_bytes=int(entry.get("size") or 0),
                removable=removable,
                internal=not removable,
                filesystems=filesystems,
                mount_points=mounts,
                readable=readable,
                reason=reason,
                protocol=str(entry.get("tran") or ""),
            )
        )
    return devices


# --------------------------------------------------------------------------- Windows

WINDOWS_QUERY = (
    "Get-Disk | Select-Object Number,FriendlyName,Size,BusType,IsBoot,OperationalStatus "
    "| ConvertTo-Json -Compress"
)


def _windows_devices() -> list[DetectedDevice]:
    raw = _run(["powershell", "-NoProfile", "-Command", WINDOWS_QUERY])
    if not raw.strip():
        return []
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError as error:
        logger.warning("could not parse Get-Disk output: %s", error)
        return []
    if isinstance(payload, dict):
        payload = [payload]

    devices: list[DetectedDevice] = []
    for entry in payload:
        number = entry.get("Number")
        if number is None:
            continue
        bus = str(entry.get("BusType") or "")
        removable = bus.upper() in {"USB", "SD", "MMC", "1394"}
        path = rf"\\.\PhysicalDrive{number}"
        readable, reason = _check_readable(path)
        devices.append(
            DetectedDevice(
                identifier=f"PhysicalDrive{number}",
                path=path,
                label=str(entry.get("FriendlyName") or f"Disk {number}"),
                size_bytes=int(entry.get("Size") or 0),
                removable=removable,
                internal=bool(entry.get("IsBoot")) or not removable,
                filesystems=[],
                mount_points=[],
                readable=readable,
                reason=reason,
                protocol=bus,
            )
        )
    return devices


# --------------------------------------------------------------------------- public API


def list_devices(removable_only: bool = False) -> list[DetectedDevice]:
    """Every physical disk the platform reports, newest-looking cards first."""
    system = platform.system()
    if system == "Darwin":
        devices = _macos_devices()
    elif system == "Linux":
        devices = _linux_devices()
    elif system == "Windows":
        devices = _windows_devices()
    else:
        devices = []

    if removable_only:
        devices = [device for device in devices if device.removable and not device.internal]

    # A removable, unmounted device is the likeliest thing the user just plugged
    # in and wants looked at, so it sorts to the top of the list.
    devices.sort(key=lambda d: (d.internal, not d.removable, bool(d.mount_points), d.identifier))
    return devices


def elevation_hint(path: str) -> str:
    """The exact command a user can run when a device needs more privilege."""
    system = platform.system()
    if system == "Windows":
        return "Close this, right-click your terminal and choose 'Run as administrator', then start FlashForensics again."
    return f"sudo -E flashforensics serve   # then reopen the dashboard, {path} will be readable"


def imaging_hint(device: DetectedDevice, destination: str = "~/card-backup.img") -> str:
    """A copy-and-paste command that snapshots the card before anything else."""
    system = platform.system()
    if system == "Windows":
        return (
            f"Use a free imaging tool such as Win32DiskImager to copy {device.identifier} "
            f"to {destination}, then load that file here."
        )
    source = device.path
    if system == "Darwin":
        source = re.sub(r"^/dev/disk", "/dev/rdisk", device.path)
    return f"sudo dd if={source} of={destination} bs=4m conv=noerror,sync status=progress"


def describe_environment() -> dict:
    """What the detector can see on this machine, for the health endpoint."""
    system = platform.system()
    tool = {"Darwin": "diskutil", "Linux": "lsblk", "Windows": "powershell"}.get(system)
    return {
        "platform": system,
        "detector": tool or "unsupported",
        "detector_available": bool(tool and shutil.which(tool)),
        "elevated": hasattr(os, "geteuid") and os.geteuid() == 0,
    }
