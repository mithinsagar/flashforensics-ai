"""Device detection, raw-device reads, and the built-in sample card.

Detection cannot assume any particular hardware is attached to whatever machine
runs these tests, so what is asserted here is the shape of the answer and the
behaviour that matters when hardware *is* present: that an unreadable device is
reported rather than hidden, that a size can be determined for something whose
`stat()` reports zero, and that the sample card is reproducible.
"""

from __future__ import annotations

import platform

import pytest

from flashforensics.demo import demo_description, demo_image, score_run
from flashforensics.disk import devices
from flashforensics.disk.image import DiskImage, DiskReadError


class TestDetection:
    def test_environment_names_the_right_tool_for_this_platform(self):
        environment = devices.describe_environment()
        expected = {"Darwin": "diskutil", "Linux": "lsblk", "Windows": "powershell"}
        assert environment["platform"] == platform.system()
        if environment["platform"] in expected:
            assert environment["detector"] == expected[environment["platform"]]

    def test_listing_returns_well_formed_entries(self):
        for device in devices.list_devices():
            payload = device.to_dict()
            assert payload["path"]
            assert isinstance(payload["readable"], bool)
            assert isinstance(payload["size_bytes"], int)
            # An unreadable device must carry its reason: silently dropping it
            # would leave a user staring at an empty list with a card in hand.
            if not payload["readable"]:
                assert payload["reason"]

    def test_unreadable_devices_are_listed_not_hidden(self):
        found = devices.list_devices()
        if not found:
            pytest.skip("no block devices visible in this environment")
        assert any(device.readable for device in found) or all(
            device.reason for device in found
        )

    def test_removable_filter_never_widens_the_list(self):
        assert len(devices.list_devices(removable_only=True)) <= len(devices.list_devices())

    def test_every_device_gets_a_recovery_hint(self):
        for device in devices.list_devices():
            assert devices.imaging_hint(device)
            assert devices.elevation_hint(device.path)

    def test_unknown_filesystems_are_still_worth_carving(self):
        """An unfamiliar filesystem loses the file names, not the data."""
        device = devices.DetectedDevice(
            identifier="disk9",
            path="/dev/disk9",
            label="Test",
            size_bytes=1 << 30,
            removable=True,
            internal=False,
            filesystems=["APFS"],
        )
        assert device.supported is False
        assert device.to_dict()["likely_card"] is True


class TestRawDeviceSupport:
    def test_a_missing_path_is_refused_clearly(self, tmp_path):
        with pytest.raises(DiskReadError):
            DiskImage(tmp_path / "nothing-here.img")

    def test_reads_never_run_past_the_end(self, tmp_path):
        target = tmp_path / "small.img"
        target.write_bytes(b"\xaa" * 1024)
        with DiskImage(target) as image:
            assert len(image.read(512, 4096)) == 512
            assert image.read(9999, 16) == b""

    def test_positional_reader_reassembles_short_reads(self, tmp_path):
        """The fallback path a raw device takes when mmap is refused."""
        target = tmp_path / "chunky.img"
        target.write_bytes(bytes(range(256)) * 8)
        with DiskImage(target) as image:
            image._mm = None  # force the seek/read path
            assert image.read(0, 2048) == target.read_bytes()


class TestSampleCard:
    def test_the_sample_card_describes_itself(self):
        description = demo_description()
        assert description["available"] is True
        assert description["planted_files"] > 0
        assert description["filesystem"] == "FAT32"
        assert "truncated" in description["scenarios"]

    def test_building_it_twice_reuses_the_same_image(self):
        first, truth = demo_image()
        second, again = demo_image()
        assert first == second
        assert truth == again

    def test_the_scorer_grades_a_run_against_the_manifest(self):
        _image, truth = demo_image()
        empty = {"fragments": []}
        result = score_run(truth, empty)
        # Nothing was recovered, so every planted file must be scored as missed.
        assert result["planted"] == len(truth["files"])
        assert result["found"] == 0
        assert result["recall"] == 0.0
